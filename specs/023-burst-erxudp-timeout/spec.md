# Feature Specification: Burst Mode 中の ERXUDP timeout / retry 短縮

**Feature Branch**: `023-burst-erxudp-timeout`
**Created**: 2026-06-23
**Status**: Draft
**Input**: spec 022 v1 deploy (`a62bbdc2`) 後の実機検証で burst 中の poll が `erxudp_timeout` 連発で実質 30 秒以上の cycle になり、 burst の 5 秒間隔が機能しないことが判明。 ユーザコメント「burst できるようになったら (spec 020 は) いらないんだけどね」 = burst を本当に動かすことを優先する設計上の pivot。

## Background

spec 022 v1 で burst mode を実装し、 5 秒 interval / 60 秒 base / catch-up 4 iter のロジックは正常動作することを実機検証済 (`realtime_burst_started_total=1, completed_total=1`)。

しかし spec 011 follow-up で `erxudp_timeout_sec=30` を採用しており、 これは「通常 60 秒 cycle での p95 tail mask + intra-cycle retry の信頼性確保」が目的。 burst mode (cycle_interval=5s) と組み合わせると:

- 1 iter で `send_el_get` → `read_erxudp(timeout=30s)` で 30 秒 block
- intra-cycle retry が走るとさらに数十秒
- 結果として cycle_interval=5s は実質無効化、 burst が UX 上「滑らかな 5 秒更新」 にならない

2026-06-23 実機 deploy 後 60s burst で 3 連続 `poll_failure (reason=erxudp_timeout)` を 30 秒間隔で観測 = 1 iter ≒ 30 秒。 これは設計上の競合バグ。

## Scope

### A. Burst 中 ERXUDP timeout 短縮 config

- 新 config `realtime_burst_erxudp_timeout_sec` (default `5`): burst mode 中のみ適用、 base は既存 `erxudp_timeout_sec=30` 維持
- **(訂正 2026-06-23): intra-cycle retry config は v1 不採用**。 既存 `erxudp_intra_cycle_retries=0` (spec 012 で 1→0 に変更済) が default で、 burst 中も同じ 0 が自然に effect。 helper `compute_intra_cycle_retries` は将来 v1.5 (base!=0 のユーザ向け) のため実装残置、 main loop 配線と新 config は v1 では追加しない

### B. Pure helper 抽出

- `compute_erxudp_timeout(mode, base_timeout, burst_timeout) -> int`
- `compute_intra_cycle_retries(mode, base_retries, burst_retries) -> int`
- pure helper として TDD、 全引数 plain int/str、 副作用なし

### C. Main loop 配線

- main loop iter 冒頭で既に計算済の `_rt_mode` を使い、 `_erxudp_timeout` と `_max_retries` を mode 依存で決定
- catch-up 中 (`catchup_remaining > 0`) は burst と同じ短い timeout / retry を採用 (= burst の余韻として高速 catch-up)

### D. 観測 (DiagState 影響)

- 既存 counter (`erxudp_timeouts_total` 等) でカバー、 spec 023 専用 metric は追加しない (= シンプル維持)
- 必要なら将来「burst 中の timeout 比率」のような derived metric を Grafana 側で

### E. Kill switch

- `realtime_burst_erxudp_timeout_sec` を `0` に設定すると burst でも base timeout 使用 (= spec 023 機能無効、 spec 022 v1 互換)

## v1 / 将来検討

**v1 (本 spec 範囲)**:
- A の 2 config
- B の 2 pure helper
- C の main loop 配線 (mode 依存切替)
- catch-up も同じ短 timeout

**将来検討 (別 spec)**:
- `realtime_burst_erxudp_timeout_sec` の動的調整 (= 失敗率に応じて自動 tune)
- burst 中 EVENT 32 (BP35CX duty cycle) 検出時の auto-abort (spec 022 plan に v2 検討と記載済)

## Non-Scope

- spec 020 (TID mismatch late publish): burst で穴埋め自然解消するため不要判断 (spec.md は specs/020-tid-mismatch-late-publish/spec.md に残置)
- メーター側応答性の改善: BP35CX / メーターの仕様、 当方範囲外
- 5 秒未満の burst interval: ARIB STD-T108 と他機器衝突リスク、 v1 floor 5s 維持

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Admin UI で「5 分間 burst 開始」ボタンを押し、 5 分間 `cubej/cubej1/power` が **本当に 5 秒ごとに** 更新されることを HA / Grafana で観察する。 spec 022 v1 では erxudp_timeout 由来で 30 秒以上 lag したのが、 spec 023 で 5-7 秒に収まる (1 cycle = send + receive + 余裕)。

### Acceptance Scenarios

1. **Given** mode=off (default)、 **When** main loop が回る、 **Then** 既存 `erxudp_timeout_sec=30` と `erxudp_intra_cycle_retries=1` が適用 (spec 022 v1 互換)
2. **Given** mode=burst、 **When** main loop iter、 **Then** `_erxudp_timeout=5` と `_max_retries=0` が適用 (= 1 send で失敗なら次 iter)
3. **Given** catch-up_remaining > 0、 **When** main loop iter、 **Then** burst と同じ短 timeout / retry を適用 (catch-up も高速で済ます)
4. **Given** `realtime_burst_erxudp_timeout_sec=0` (kill switch)、 **When** mode=burst、 **Then** base `erxudp_timeout_sec=30` が適用 (= spec 023 機能無効化)
5. **Given** burst 中 1 send が timeout、 **When** retry=0、 **Then** retry せず次 iter に進み、 全体 cycle 時間 = burst_timeout (= 5s) + 微小 overhead で済む

### Key Entities

- 既存: `_rt_mode` (str, main loop iter 冒頭で計算済)、 `catchup_remaining` (int)
- 新 helper: `compute_erxudp_timeout`, `compute_intra_cycle_retries`
- 新 config: `realtime_burst_erxudp_timeout_sec`, `realtime_burst_erxudp_intra_cycle_retries`

## Edge Cases

- burst 中に 5 秒で response 来ず、 timeout → 次 iter で send → メーター側で前 send の処理がまだ走っている → TID mismatch 続発の可能性。 これは spec 014 で discard、 spec 020 (boss) では救済しない判断
- catch-up 中の timeout: burst と同じ 5 秒、 catch-up 全体は 4 iter × 5 = 20 秒で完了 (失敗あっても 60 秒に戻る目処)
- `realtime_burst_erxudp_timeout_sec` を極端に小さく (例 1 秒) 設定: メーター応答が間に合わず全 iter timeout、 妥当性は config 設定者の責任、 v1 では floor 1s 程度のみ
- bridge 起動直後の最初の burst: SKJOIN まだ若い、 メーター warm-up中で timeout 多め発生の可能性。 観察で評価

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `compute_erxudp_timeout(mode, base_timeout, burst_timeout) -> int` pure helper、 mode=="burst" なら burst_timeout、 それ以外 base_timeout
- **FR-002**: `compute_intra_cycle_retries(mode, base_retries, burst_retries) -> int` pure helper、 同様の分岐
- **FR-003**: main loop の `_erxudp_timeout = int(cfg.get("erxudp_timeout_sec", 30))` 計算を `compute_erxudp_timeout(_effective_mode, base, burst)` 経由に置換
- **FR-004**: ~~retry の main loop 配線~~ (v1 不採用、 既存 default 0 が burst にも自然に effect)
- **FR-005**: `apply_defaults` で `realtime_burst_erxudp_timeout_sec` (default 5)、 floor 5s (kill switch `0` は例外)
- **FR-006**: `realtime_burst_erxudp_timeout_sec=0` で kill switch (base timeout 使用)、 `compute_erxudp_timeout` 内で sentinel 扱い
- **FR-007**: catch-up 中も burst と同じ短 timeout を採用 (= `_effective_mode = "burst" if _rt_mode == "burst" or catchup_remaining > 0 else "off"`)

### Key Entities

上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `compute_erxudp_timeout` 各 mode + kill switch (0)
- **SC-002**: 単体テスト: `compute_intra_cycle_retries` 各 mode
- **SC-003**: 実機: burst 5 分間で `poll_success` event が 30 件以上発生 (= 平均 10 秒以下 cycle、 5s 周期 + メーター応答性余裕)
- **SC-004**: 実機: burst 終了後 catch-up 4 iter が 25 秒以内に完了 (catch-up_interval=5s × 4 + 余裕)
- **SC-005**: spec 022 v1 既存挙動互換 (mode=off で 60s polling、 既存 30s timeout)
- **SC-006**: 既存テスト全件 pass

## Assumptions

- メーター側は 5 秒以内に応答する能力がある (実機で probe 確認、 spec 022 deploy 後の偶発 timeout は調査必要)
- BP35CX の ARIB STD-T108 duty cycle 制御は信頼できる (短 timeout で連続 send しても物理層遮断あり)
- spec 011 の 30 秒 timeout は通常 60 秒 cycle で正解、 burst 5 秒 cycle では別チューニングが妥当
- 関連: [[spec-022-realtime-power-burst]] (mode 切替の前提)、 [[spec-011-erxudp-resilience]] (元 timeout 設定)
