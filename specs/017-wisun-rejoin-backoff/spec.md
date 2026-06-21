# Feature Specification: Wi-SUN Rejoin Exponential Backoff + Serial Reopen

**Feature Branch**: `017-wisun-rejoin-backoff`
**Created**: 2026-06-21
**Status**: Draft
**Input**: User description: "hals5412 fork (2779679 production_tool_v2) の長時間障害対応を取り込み、 Wi-SUN 再 join の指数 backoff と BP35CX のシリアル port reopen を spec 化する"

## Background

当方の Wi-SUN 再接続パスは spec 011 (`erxudp_timeout_force_reconnect_threshold` default 5) で「連続 ERXUDP timeout 5 回で `wisun_connect()` をやり直す」までは整備済み。 しかし再接続の中身は:

- `production_tool/mqtt_bridge.py:3270` で `RuntimeError` raise
- main loop 外側 `except` (L3317 付近) で **固定 30 秒 sleep** → `wisun_connect()` retry
- BP35CX のシリアル port (`/dev/ttyS1`) は開いたまま

これで多くのケース (一時的な PANA セッション失敗) は復旧するが、 長時間障害シナリオで次の問題が残る:

1. **指数 backoff なし** — 30 秒で永遠にリトライ、 障害が長引くと無駄な SKJOIN を撃ち続けてメーターに負荷
2. **シリアル port の reopen なし** — BP35CX 側のシリアル UART がハング (firmware bug or driver issue) しても永遠に再開しない、 reset 自体が届かなくなる
3. **EVENT 24/29 (PANA 失敗) の専用ハンドリングなし** — 一般的な timeout として処理してしまう

hals5412 fork の `2779679` (`production_tool_v2/`) では:
- `REJOIN_BACKOFF_INITIAL=30` / `REJOIN_BACKOFF_MAX=300` の指数 backoff (30→60→120→240→300)
- `SERIAL_REOPEN_AFTER=5` 回の rejoin 失敗で `/dev/ttyS1` を close → open
- `EVENT 24` (PANA fail) を即時 rejoin トリガに

の対策が入っている。 これらを spec 011 の上に重ねる。

## Scope

### 指数 backoff

- `apply_defaults` に追加:
  - `wisun_rejoin_backoff_initial_sec` (default 30)
  - `wisun_rejoin_backoff_max_sec` (default 300)
  - `wisun_rejoin_backoff_multiplier` (default 2.0)
- 再接続失敗が連続するたびに sleep 時間を倍々で延長、 上限で頭打ち
- 1 回でも `wisun_connect()` が成功したら backoff カウンタをリセット (初期値に戻す)

### シリアル port reopen

- `apply_defaults` に `wisun_serial_reopen_after_rejoin_failures` (default 5) を追加
- 連続 rejoin 失敗が閾値超えたら `close(fd)` → `os.open(serial_port, ...)` で port を取り直す
- reopen 自体が失敗した場合は元の fd で再試行を続ける (致命的 raise はしない)
- DiagState に `serial_reopen_total` カウンタ追加

### EVENT 24/29 即時トリガ

- `read_erxudp` 内で `kind == "event"` && value が `24` or `29` のとき:
  - `diag_state.on_sk_event(value)` を呼んだ上で、 caller に「PANA 失敗を検知した」シグナルを返す
  - main loop はそのシグナルを受けて consecutive_erxudp_timeouts を threshold まで上げて再接続をトリガ (= 即時 rejoin)
- 既存挙動 (event を counter として記録、 loop は通常通り) は維持しつつ、 強い signal を追加

## Non-Scope

- 再接続中の HA への状態通知 (例: bridge_status = "reconnecting") — 別途検討
- メーター側 B-route 認証情報の再取得 — spec 範囲外
- BP35CX firmware の更新 / フラッシュ — ハードウェア領域
- ネットワーク全体の WPS/SKJOIN 戦略変更

## User Scenarios *(mandatory)*

### Primary User Story

長時間 (1 時間以上) の Wi-SUN 障害が発生した場合、 bridge は 30 秒 → 60 → 120 → 240 → 300 (上限) の指数 backoff で SKJOIN を試み続け、 5 回連続失敗するとシリアル port を再 open して BP35CX UART のハングからも復帰する。 障害が解消したら次のトライで成功し、 backoff はリセット。 PANA 失敗 (EVENT 24/29) を検知したら timeout を待たず即時 rejoin に入る。

### Acceptance Scenarios

1. **Given** `wisun_connect()` が連続失敗、 **When** 5 回失敗、 **Then** sleep が `30 → 60 → 120 → 240 → 300` (clamp) と倍々で伸びる
2. **Given** 5 回失敗後の `wisun_connect()` 成功、 **When** 次サイクル、 **Then** backoff カウンタがリセット、 次の失敗時は 30 秒から再開
3. **Given** `wisun_serial_reopen_after_rejoin_failures=5` で 5 回 rejoin 失敗、 **When** 6 回目の rejoin 直前、 **Then** `/dev/ttyS1` を close → open、 `serial_reopen_total` +1
4. **Given** ERXUDP timeout 待ち中に EVENT 24 受信、 **When** read_erxudp が classify、 **Then** caller に signal、 main loop が consecutive_erxudp_timeouts を threshold まで上げて即時 rejoin
5. **Given** `wisun_rejoin_backoff_max_sec=300`、 **When** 7 回失敗、 **Then** 7 回目の sleep は 300 (clamp、 計算上 1920 ではない)

### Key Entities

- **`wisun_rejoin_backoff_initial_sec`** / **`max_sec`** / **`multiplier`**: config キー
- **`wisun_serial_reopen_after_rejoin_failures`**: config キー
- **`DiagState.serial_reopen_total`**: counter
- **`compute_rejoin_backoff(attempt, initial, multiplier, max_sec)`**: pure 関数、 attempt から sleep 秒を返す
- **`read_erxudp` の戻り値拡張**: 既存 payload/None に加えて「EVENT 24/29 検知」シグナル (例: 例外 or 専用 marker)

## Edge Cases

- backoff multiplier が 1.0: 線形 (初期値固定で繰り返し)
- backoff initial >= max: 常に max で動作 (clamp の挙動を保証)
- serial reopen 自体が `OSError`: ログ ERROR + 既存 fd で続行
- serial reopen 中に SIGTERM: 一貫した shutdown
- EVENT 24 を多発させる (= 即時 rejoin 連発): 既存 `should_force_wisun_reconnect` の threshold が hit してから再接続なので、 暴走しない
- 既存 spec 011 の `consecutive_erxudp_timeouts` リセット (poll_success 時): EVENT 24/29 シグナルによる threshold 押し上げと同様にリセット動作を維持

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `apply_defaults` で 4 つの新キーを default 設定する
- **FR-002**: `wisun_connect()` 失敗時の sleep 時間を `compute_rejoin_backoff()` で計算する
- **FR-003**: pure 関数 `compute_rejoin_backoff(attempt, initial, multiplier, max_sec)` を抽出 (unit test 可能)
- **FR-004**: `wisun_connect()` 成功時、 backoff カウンタを 0 にリセット
- **FR-005**: 連続失敗が `wisun_serial_reopen_after_rejoin_failures` 超で `/dev/ttyS1` を close→open
- **FR-006**: シリアル reopen の総数を `serial_reopen_total` として publish
- **FR-007**: `read_erxudp` 内で EVENT 24/29 を検知した場合、 main loop に即時 rejoin シグナルを返す
- **FR-008**: 既存 `consecutive_erxudp_timeouts` / `should_force_wisun_reconnect` ロジックを破壊せず重ねる

### Key Entities

- `compute_rejoin_backoff(attempt, initial, multiplier, max_sec) -> int`
- `DiagState.on_serial_reopen()`: counter inc
- `DiagState.serial_reopen_total`: int

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `compute_rejoin_backoff` が `(0,30,2.0,300)=30, (1,...)=60, (2,...)=120, (3,...)=240, (4,...)=300, (5,...)=300` を返す
- **SC-002**: 実機の 1 週間運用で `serial_reopen_total >= 0` (障害発生で増えることを確認)
- **SC-003**: 障害発生時のログから、 backoff 時間が 30/60/120/240/300 のシーケンスで増えていることを確認
- **SC-004**: 障害解消後の `wisun_connect()` 成功で、 次回失敗時の sleep が 30 秒に戻ることを実機 or 統合テストで確認
- **SC-005**: 既存テスト全件 pass

## Assumptions

- BP35CX のシリアル port は Python の `os.open` / `os.close` で安全に reopen 可能
- reopen 後の SKVER 等の初期化シーケンスは既存 `wisun_connect()` の中で再走する
- EVENT 24/29 のシリアル出力フォーマットは BP35CX firmware で安定
- 指数 backoff の上限 300s = 5 分は ARIB STD-T108 duty cycle の影響範囲外 (sleep のみで送信なし)
