# Feature Specification: 瞬時電力 (0xE7) 高頻度更新モード — burst / always

**Feature Branch**: `022-realtime-power-burst`
**Created**: 2026-06-23
**Status**: Draft
**Input**: User description: "natu remo e lite だとアプリ開いてる間は 4-5 秒に 1 回瞬間電力の更新ができてる。 定常的に更新をとってきて、 recent なものを publish する仕組みにできないだろうか?"

## Background

現状 spec 013 で `poll_interval >= 30s` の下限ガードを設けており、 通常運用は 60s 周期で 0xE7 (瞬時電力) を polling している。 これは ARIB STD-T108 920MHz 帯 duty cycle (360s/hour = 10%) の安全マージン確保と Wi-SUN 他機器衝突回避が目的。

一方で Nature Remo E Lite のようなユーザ向けデバイスは「アプリを開いている間 4-5 秒に 1 回」の高頻度更新を実現している。 これは HA 経由で「家電を ON/OFF した直後の変化を確認したい」「家を出る前に電力使用状況をリアルタイムで見たい」というユースケースに有効。

2026-06-23 実施した実機 probe (`/tmp/spec022-probe.py`) で以下が判明:

1. **0x9D StatusAnnouncementPropertyMap = 0x80, 0x81, 0x88 のみ**: メーターは 0xE7 の状変通知 (INF push) をサポートしない → ECHONET Lite subscribe 経路は使えず、 polling アプローチが唯一
2. **BP35CX 自体が ARIB STD-T108 duty cycle 制御を実装**: 過剰送信時は `ER10` / `EVENT 32` で送信ブロックされる → ホスト側で 5s polling を流しても物理層で違反は起きない
3. **連続 SKJOIN は不安定**: メーター側冷却で advertise window が渋るため、 既存 SKJOIN 維持しつつ polling 間隔だけ変えるアプローチが必要

## Scope

### A. Mode 切替 config

3 種類の polling mode を `realtime_power_mode` config キーで切替:

- `off` (default): 既存 60s polling 維持、 機能無効
- `burst`: on-demand mode — HA/Admin UI API trigger で N 分間 (default 5 分) だけ短間隔 polling、 期限切れで自動 off 復帰
- `always`: 常時 short interval — `realtime_always_interval_sec` (default 10s) で常時 polling (**v2 検討**)

### B. Burst mode 用 API endpoint

- `POST /api/realtime/start` — burst 開始、 body で `duration_sec` (default 300) / `interval_sec` (default 5) 指定可
- `POST /api/realtime/stop` — burst 即時停止
- `GET /api/realtime/status` — `{"mode": "burst", "remaining_sec": 120, "interval_sec": 5}` 等

Admin UI に「リアルタイム表示 (5 分)」ボタンを追加。

### C. Polling loop の interval 切替

- 既存 main loop の `poll_interval = cfg.get("poll_interval", 60)` を `compute_effective_poll_interval(now, base_interval, mode_state) -> int` 経由に置換
- pure helper として抽出 (全引数 plain、 副作用なし、 unit test 容易):
  - `mode_state` dict: `{"mode": str, "expires_at": float|None, "burst_interval": int}`
  - burst mode かつ `now < expires_at` → `burst_interval` 返却
  - burst mode かつ `now >= expires_at` → off 復帰 (caller が mode_state mutation)、 `base_interval` 返却
  - off mode → `base_interval` 返却
- floor: spec 013 の `>= 30s` floor は **base_interval にのみ適用**、 burst interval は別 floor `REALTIME_BURST_MIN_INTERVAL_SEC = 5`

### D. ARIB STD-T108 安全策

- BP35CX が duty cycle 違反時に `ER10` / `EVENT 32` を返したら自動的に burst を停止 → off 復帰 (**v2 検討**)
- v1 は BP35CX の物理層遮断に依存、 abort counter は実装するが auto-stop logic は v2
- `realtime_burst_aborted_total` counter (v1 では手動 stop / API stop でのみ increment)

### E. tier rotation との関係

spec 011 C で tier rotation を導入し、 tier1 (毎 cycle) = 0xE7、 tier2 (5 cycle 毎) = 0xE0/0xE3、 tier3 (60 cycle 毎) = 0xD3/0xE1、 tier4 (spec 018, 30 cycle 毎) = 0xEA/0xEB。

burst mode 中は **tier1 (0xE7) のみ短間隔で polling**、 tier2/3/4 は cycle counter を進めず skip。 burst 終了時に skip 累積を 1 回 catch-up polling (= 即 tier2/3/4 を順次 GET) で補完。 catch-up logic は v1 に含める (累積積算の欠損は HA Energy 精度に直結するため)。

### F. 観測 (DiagState 拡張)

- `realtime_mode_current` (gauge, string snapshot): off/burst
- `realtime_burst_started_total` (counter): burst 起動回数
- `realtime_burst_completed_total` (counter): burst 正常終了 (expires_at 到達) 数
- `realtime_burst_aborted_total` (counter): burst 異常終了 (API stop 等) 数
- `realtime_effective_interval_seconds` (gauge, int): 現在の poll interval

### G. HA discovery (**v2 検討**)

v1 では Admin UI ボタンのみで OK、 HA switch entity 連携は v2:

- v2: `cubej1_realtime_burst` switch entity を publish、 ON 押下で `/api/realtime/start` 呼出、 bridge が 5 分後に MQTT topic OFF を送信して HA UI 反映

## v1 / v2 分割

**v1 MVP (本 spec 起票範囲)**:

- A の `off` / `burst` mode (always は v2)
- B の 3 API endpoint
- C の `compute_effective_poll_interval` pure helper + main loop 配線
- E の tier1 only + burst 終了時 tier2/3/4 catch-up
- F の DiagState 拡張全て
- Admin UI に「リアルタイム表示」ボタン (簡易 form POST で良い)

**v2 検討 (別 spec 起票)**:

- A の `always` mode
- D の ER10 / EVENT 32 自動 abort
- G の HA switch entity discovery

## Non-Scope

- mobile app 化: HA 経由で十分、 当方は MQTT topic + Admin UI のみ提供
- Nature Remo E Lite の「アプリ開いてる間自動」連動: HA automation で実現可能だが当方 spec 外
- burst 中の tier2/3/4 別タイマー (現状 skip + catch-up で済ます)
- 5s より短い interval: BP35CX 物理層遮断と他機器衝突を考えると意味薄、 v1 では floor 5s

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは HA の電力ダッシュボードを開いて家電を ON/OFF した時、 瞬時電力グラフが 60 秒 1 点で追従しないのが物足りない。 bridge の Admin UI で「リアルタイム表示 (5 分)」ボタンを押すと、 5 分間だけ 5 秒ごとに `cubej/cubej1/power` topic が更新され、 期限切れで自動的に通常 60s 間隔に戻る。 これで家電制御の効果を体感できる。

### Acceptance Scenarios

1. **Given** mode=off (default)、 **When** main loop が回る、 **Then** 既存 60s polling、 spec 013 floor 動作維持
2. **Given** mode=off で `/api/realtime/start` POST (body 空)、 **When** 直後の main loop iteration、 **Then** mode=burst に遷移、 effective_interval=5s で polling、 `realtime_burst_started_total` +1
3. **Given** mode=burst で expires_at 経過 (5 分後)、 **When** 次 iteration、 **Then** mode=off 自動復帰、 effective_interval=60s、 `realtime_burst_completed_total` +1、 tier2/3/4 catch-up polling が直後に実行
4. **Given** mode=burst 中に `/api/realtime/stop` POST、 **When** 直後 iteration、 **Then** mode=off 即復帰、 `realtime_burst_aborted_total` +1、 catch-up 実行
5. **Given** mode=burst 中に `/api/realtime/start` 再 POST (duration_sec=300)、 **When** 処理、 **Then** expires_at を `now + 300` で延長 (リセットでなく延長)
6. **Given** burst 中 tier2 cycle (5 cycle 毎) が来るタイミング、 **When** v1 実装、 **Then** tier1 のみ polling、 tier2/3/4 cycle counter は進めない
7. **Given** `/api/realtime/start?interval_sec=3` (floor 違反) POST、 **When** API validation、 **Then** 400 Bad Request + `{"error": "interval_sec must be >= 5"}`、 mode 変化なし

### Key Entities

- **`RealtimeModeState`** クラス: mode (str), expires_at (float|None), burst_interval (int)、 thread lock 保護 (admin API thread と main loop thread の境界)
- **新 admin API endpoint 3 種**: start / stop / status
- **`compute_effective_poll_interval`** pure helper

## Edge Cases

- bridge 再起動: mode_state は memory のみ (永続化不要、 spec 019 とは異なる方針)、 再起動後は off 始動 — 「burst は短期決定なので restart で消えても問題なし」
- API thread と main loop の race: `RealtimeModeState` は threading.Lock で保護、 read-modify-write は lock 内
- burst 終了直後の catch-up が tier2 GET 失敗: 既存 main loop の error path に従う (= log warn、 次 cycle 待ち、 catch-up 自体の retry は v1 不要)
- main loop が長時間 ERXUDP timeout 中に expires_at を超過: 次の iteration で off 復帰判定、 catch-up 1 回試行
- duration_sec の上限: 60 分まで (= 3600)、 超過は 400 Bad Request

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `compute_effective_poll_interval(now, base_interval, mode_state)` pure helper を抽出、 全引数 plain、 副作用なし、 burst 期限超過時は base_interval 返却 (mode_state mutation は caller responsibility)
- **FR-002**: main loop で `time.sleep(poll_interval)` 計算を `compute_effective_poll_interval` 経由に置換、 burst 終了検知時に `_on_burst_expired()` を呼んで tier2/3/4 catch-up + counter +1
- **FR-003**: 新 admin API endpoint 3 種:
  - `POST /api/realtime/start` (body JSON `{duration_sec?, interval_sec?}`、 default 300/5)
  - `POST /api/realtime/stop`
  - `GET /api/realtime/status`
- **FR-004**: `RealtimeModeState` class、 threading.Lock で thread-safe、 `start_burst(duration_sec, interval_sec)` / `stop_burst()` / `is_burst_expired(now)` / `snapshot()` メソッド
- **FR-005**: config key 追加: `realtime_burst_default_duration_sec` (300)、 `realtime_burst_default_interval_sec` (5)、 `realtime_burst_min_interval_sec` (5)、 `realtime_burst_max_duration_sec` (3600)
- **FR-006**: `apply_defaults` で上記 config の floor / cap 適用
- **FR-007**: API validation: interval_sec < min または duration_sec > max で 400、 mode 変化なし
- **FR-008**: DiagState 拡張: realtime_mode_current (gauge str) / realtime_burst_started_total / realtime_burst_completed_total / realtime_burst_aborted_total / realtime_effective_interval_seconds (gauge int)、 全 snapshot 公開
- **FR-009**: burst 終了 (正常 / 異常) 時に tier2/3/4 catch-up polling を 1 回即実行 (= 次 main loop iteration で tier4 → tier3 → tier2 を順次 GET 強制)
- **FR-010**: burst 中は tier rotation cycle counter を進めない (= tier1 のみ polling)
- **FR-011**: Admin UI のリアルタイム表示ボタン (簡易 form、 5 分固定)
- **FR-012**: spec 013 base_interval floor (>= 30s) は維持、 burst interval floor は別 (>= 5s)

### Key Entities

上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `compute_effective_poll_interval` で off / burst-active / burst-expired の 3 path
- **SC-002**: 単体テスト: `RealtimeModeState` の start_burst / stop_burst / is_burst_expired / 再 start 延長 / floor validation
- **SC-003**: 実機: Admin UI ボタン押下で 5 分間 `cube_j1_smart_meter_power_w` が 5 秒ごとに更新されることを Grafana で確認
- **SC-004**: 実機: 5 分経過後 60s に自動復帰、 catch-up で tier2 (累積値) が即 publish されることを log + Grafana で確認
- **SC-005**: 実機 1 週間運用で BP35CX duty cycle 由来の SK error (ER10 等) が発生しないこと (発生する場合は v2 の auto-abort が必要)
- **SC-006**: spec 013 既存挙動互換 (mode=off で 30s floor 維持)、 既存テスト全件 pass

## Assumptions

- BP35CX の ARIB STD-T108 duty cycle 違反保護は信頼できる (実装は spec ベース)、 v1 では物理層遮断に依存
- 5s 間隔 polling でも Wi-SUN PAN の継続安定性は維持 (probemap 直後の 1 回 GET は成功している、 ただし 5 分連続実測は未達 = SC-005 で運用検証)
- 5 分間 catch-up 待ちでも累積値の欠損は許容範囲 (HA Energy の 30 分粒度には影響なし、 spec 018 の tier4 メーター内蔵 timestamp で補正可能)
- 関連: [[spec-013-poll-interval-floor]], [[spec-011-erxudp-resilience]] (tier rotation), [[spec-017-wisun-rejoin-backoff]], [[spec-018-cumulative-energy-tier]], [[spec-021-sk-event-counters]] (EVENT 32 export 連携 v2)
