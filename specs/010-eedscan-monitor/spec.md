# Feature Specification: Periodic Energy Detection Scan for 920MHz Noise Floor

**Feature Branch**: `010-eedscan-monitor`
**Created**: 2026-06-20
**Status**: Draft
**Input**: User description: "EEDSCAN を周期実行して 920MHz 各 channel のノイズフロアを時系列で追跡したい。 時間帯起因のレイテンシ悪化が物理層なのかメーター内部なのか切り分けるため"

## Background

spec 006-009 で「ECHONET 応答 RTT が時間帯で 30ms → 450ms に悪化する」 現象を観察したが、 firmware 経由で物理層の RF 状況を読む手段が無く、 真因を切り分けられなかった。

実機探索の結果、 BP35CX SKSTACK に **undocumented な `SKSCAN 0 <mask> <duration> 0` (SIDE 引数 0 付き)** で **EEDSCAN (Energy Detection Scan)** が動作することを確認:

```
SKSCAN 0 0FFFFFFF 4 0
OK
EVENT 1F FE80:...:BEE4 0
EEDSCAN
0 21 1A 22 19 23 13 24 0E 25 0E 26 0B ... 39 0B ... 3C 13
```

format: `<status=0> <ch> <energy> <ch> <energy> ...`、 28 channel × energy (hex 1 byte)。 1 sweep 約 12 秒、 sweep 中はメーター通信停止。

これでメーターを介さない **物理層 RF ノイズフロア直接観測** が可能になった。 周期実行で:
- 時間帯依存の混雑 (例: 朝 9-10 時、 検針時刻 など)
- ECHONET RTT 悪化と RF 環境の相関
- 自宅 RF 環境変動 (他家のスマートメーター、 LoRa, Sub-GHz IoT 機器)

を可視化できる。

## Scope

- bridge に EEDSCAN 周期実行を組み込む (default 5 分間隔、 設定可能)
- pure helper `parse_eedscan(raw_lines)` で `{channel: energy}` dict を抽出
- `EedScanState` で直近 N サンプル (channel 別 deque) を保持
- snapshot に追加:
  - `eedscan_ch<NN>_energy` (各 channel の最新値、 28 channel 分)
  - `eedscan_pan_channel_energy` (現使用 channel の最新 energy、 main metric)
  - `eedscan_max_energy` / `eedscan_min_energy` (sweep 内の最大・最小)
- MQTT publish 経由 Grafana で時系列追跡
- `/wisun` ページ に EEDSCAN 結果 (channel bar chart) を追加
- `/api/eedscan` endpoint で直近結果を返す

## Non-Scope

- リアルタイム sweep (1 sweep ~12 秒、 メーター通信中断するので頻度制限)
- 物理層からの真の RSSI/LQI 取得 (firmware 制約で不可確定)
- EEDSCAN trigger ボタンによる on-demand 実行 (将来の UI 追加候補)

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Grafana の `cubej1-smart-meter` ダッシュボードで 24h の `eedscan_pan_channel_energy` (現使用 ch57) と `erxudp_latency_p50` の重ね合わせグラフを見て、 時間帯依存のノイズ増 と ECHONET RTT 悪化が同期してるか確認する。 同期してれば「他家のメーター/IoT 機器による干渉が真因」、 同期してなければ「メーター内部処理の時間帯依存性が真因」 と切り分ける。

### Acceptance Scenarios

1. **Given** bridge 起動済み、 **When** EEDSCAN 周期が来る (default 5 分)、 **Then** main loop で `SKSCAN 0 0FFFFFFF 4 0` を実行、 EEDSCAN 結果を DiagState に保存
2. **Given** 直近の EEDSCAN 完了済み、 **When** `GET /api/diag`、 **Then** snapshot に `eedscan_pan_channel_energy` が含まれる
3. **Given** EEDSCAN 中、 **When** メーター通信が走ろうとした、 **Then** EEDSCAN 完了まで poll cycle が 1 回スキップされる (排他制御)
4. **Given** `parse_eedscan(["EEDSCAN", "0 21 1A 22 19 ..."])`, **Then** `{0x21: 0x1A, 0x22: 0x19, ...}` を返す
5. **Given** SKSCAN が ER05 等で失敗、 **When** main loop 続行、 **Then** bridge 全体は死なず、 次サイクルで通常 poll 再開

### Key Entities

- **`EedScanState`**: `last_run_ts`, `interval_sec`, `recent: deque[dict]`、 メソッド `should_run(now)`, `record(result, ts)`, `snapshot()`
- **`parse_eedscan(lines)`**: pure helper、 EEDSCAN レスポンス行を `{channel_int: energy_int}` dict にパース
- **`/api/eedscan`** endpoint: 直近 N 件の結果を JSON で返す
- **新 config キー**: `eedscan_enabled` (default True)、 `eedscan_interval_sec` (default 300)

## Edge Cases

- EEDSCAN が `OK` の後 EVENT 1F が来ない (タイムアウト): 1 sweep を 30 秒 deadline で打ち切り、 失敗 log。 main loop は次の poll に進む
- ER05 / ER04 で SKSCAN が拒否: 1 行 log で skip、 次回も試行 (firmware 一時 reject の可能性、 一度の失敗で機能 disable しない)
- EEDSCAN 中に poll deadline 過ぎる: 次 poll が遅延、 ただし deadline pacing で次サイクルが catch-up する
- parse 失敗 (新フォーマット): 旧データ維持、 log に raw lines

## Success Criteria *(mandatory)*

- **SC-001 [observability]**: 24h 連続で `eedscan_pan_channel_energy` が Grafana に値出る (5 分間隔、 288 サンプル)
- **SC-002 [no breakage]**: EEDSCAN sweep がメーター poll loop を死なせない (Constitution IV)
- **SC-003 [parser deterministic]**: `parse_eedscan` は pure 関数、 同入力に同出力、 unit test 可能
- **SC-004 [correlation visible]**: Grafana で EEDSCAN energy と `erxudp_latency_p50` を重ねた時、 時間帯依存性の有無が読み取れる

## Assumptions

- BP35CX firmware EVER 1.5.2 + EAPPVER rev15 で `SKSCAN 0 <mask> <dur> 0` が安定動作する (実機実証済み)
- 1 sweep が 12-15 秒で完了する (実測 ~12 秒、 channel mask `0FFFFFFF` + duration 4)
- 5 分に 1 回の 12 秒 sweep でメーター通信に有意な影響なし (poll_interval=60s なので 1/5 サイクル落ちる程度)

## Dependencies

- `production_tool/mqtt_bridge.py` の main loop, DiagState, AdminHandler, WISUN_HTML
- spec 005 (MQTT threading) - publish_diag が thread-safe
- spec 006 (Wi-SUN health) - DiagState 拡張パターン
