# Feature Specification: ECHONET Probe Mode for high-frequency RTT sampling

**Feature Branch**: `009-echonet-probe-mode`
**Created**: 2026-06-20
**Status**: Draft
**Input**: User description: "位置調整中にリアルタイムで信号品質が見たい。 ECHONET の軽量プロパティ (0x80) を高頻度で叩いて RTT サンプルを稼ぐ"

## Background

spec 007/008 と一連の実機調査 (ROPT 1 / SKSREG / SKPING) で **firmware 経由で信号品質の生情報を取れない** ことが確定した。 残された唯一の品質指標は ECHONET 応答の RTT で、 既存の `erxudp_latency_ms_recent` に積まれている。

ただし通常 poll は 60 秒間隔・ EPC=0xE7 等の計測値プロパティ取得で、 1 分に 1 サンプルしか入らず、 メーター側の計測処理時間も乗ってしまう。 位置調整中は:

- もっと高頻度 (2-5 秒間隔) でサンプルを取りたい
- ECHONET 計測処理の影響を排して、 Wi-SUN 通信品質に近い RTT を測りたい

そこで **0x80 (動作状態) 1 byte リクエスト** を probe 専用に叩く一時モードを導入する。 0x80 はメーター側で内部 flag を返すだけなので、 packet サイズ / メーター処理時間が最小で、 reject されにくい (はず、 実機で確認)。

## Scope

- `ProbeState` in-memory class: `{active: bool, interval_sec: int, deadline_ts: epoch}` を持つ
- `/api/probe` GET → 現在の probe state、 PUT → probe を on/off (time-limited)
- 通常 poll loop に probe 分岐を入れる:
  - probe active かつ deadline 内: EPC=[0x80]、 sleep を `probe.interval_sec` で計算
  - probe inactive or expired: 既存の EPCS リスト・ `poll_interval` (60s) で動作
- probe ON 時に `erxudp_latency_ms_recent` を clear → probe 期間中は probe サンプルのみが sparkline に現れる
- `/wisun` ページに **Probe Mode** セクション: interval (default 5s) / duration (default 5 分) を選んで開始 / 停止
- 自動 OFF: deadline 経過で自動的に通常 mode に戻る

## Non-Scope

- 並列 probe (別スレッドで通常 poll と同時実行): serial port が 1 本なので排他必須、 単一 loop で時分割する
- 0xE7 以外の電力値プロパティでの probe (Future)
- probe 期間中の HA への電力値 publish 維持 (probe 中は 0x80 のみで電力値は更新されない、 これは仕様内で許容)

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは `/wisun` ページを開き、 **Start Probe (5 s × 5 min)** ボタンを押す。 sparkline が clear され、 以後 5 秒間隔で 0x80 リクエストの RTT がリアルタイム描画される。 Cube J1 を持ち運んで部屋を移動すると、 数十秒で sparkline と p50/p95 数字が変化する。 5 分経つと自動的に通常 mode に戻り、 60 秒間隔の 0xE7 poll が再開される。

### Acceptance Scenarios

1. **Given** probe inactive、 **When** `PUT /api/probe {enabled: true, interval_sec: 5, duration_sec: 300}`、 **Then** 200 OK で `{active: true, ...}` が返る
2. **Given** probe active、 **When** main loop 1 サイクル経過、 **Then** EPC=[0x80] で send_el_get、 sleep は 5 秒
3. **Given** probe active で deadline 経過、 **When** main loop 次サイクル、 **Then** probe 自動 OFF、 通常 EPCS と 60s 間隔に復帰
4. **Given** probe ON した瞬間、 **When** `GET /api/diag`、 **Then** `erxudp_latency_ms_recent` の sample_count が 0
5. **Given** probe active 中、 **When** `PUT /api/probe {enabled: false}`、 **Then** 即座に通常 mode に戻る

### Key Entities

- **`ProbeState`**: `active`, `interval_sec`, `deadline_ts`、 メソッド `start(interval, duration, now)`, `stop()`, `is_active(now)`, `snapshot(now)`
- **`/api/probe`**: GET (snapshot) / PUT ({enabled, interval_sec, duration_sec})
- **`send_el_get(fd, ipv6, tid, epc_list=None)`**: 既存関数を拡張。 None なら従来 EPCS、 リスト指定で probe 用 1 EPC

## Edge Cases

- duration_sec が 0 / 負: 400 エラー
- interval_sec が 1 未満: 400 (メーター reject リスク高すぎ)
- interval_sec > poll_interval (60s): 警告ログのみ、 動作はする
- probe 中にメーターが reject (ERXUDP timeout 連発): 既存の force_wisun_reconnect が走る → probe state は維持 (timeout 後の wisun reconnect 経由で通常 loop に戻ったら次サイクルで probe 復帰)
- probe 中に bridge restart: probe state は in-memory なので失われる → 起動直後は必ず通常 mode で開始 (safe default)

## Success Criteria *(mandatory)*

- **SC-001 [observable]**: probe ON 後 10 秒以内に sparkline が更新され、 sample_count が 2-3 になる
- **SC-002 [auto-off]**: duration_sec 経過後 1 サイクル以内に通常 mode に復帰、 通常 poll 再開
- **SC-003 [no breakage]**: probe 期間が終わったら 0xE7 計測値の publish が次サイクルで再開される
- **SC-004 [reject safety]**: probe 中に ERXUDP timeout 連発で `force_wisun_reconnect` が発火しても bridge 全体は死なない (既存挙動)
- **SC-005 [test only injection]**: ProbeState は pure object として unit test 可能、 time は外部注入

## Assumptions

- メーターは 0x80 リクエストを少なくとも 5 秒間隔では reject しない (実機で確認)
- 0x80 の応答 RTT は 0xE7 より短い (= 計測処理が無いので)
- probe 期間中の電力値非更新は許容できる (UX 上、 位置調整は短時間)

## Dependencies

- `production_tool/mqtt_bridge.py` の main loop と `send_el_get`
- `production_tool/mqtt_bridge.py` の `AdminHandler` (`/api/probe`)
- `production_tool/mqtt_bridge.py` の `WISUN_HTML` (probe UI)
- `DiagState.erxudp_latency_ms_recent` (clear 動作)
