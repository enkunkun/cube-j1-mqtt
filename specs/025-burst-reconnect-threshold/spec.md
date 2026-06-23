# Feature Specification: Burst Mode 中の force_wisun_reconnect threshold 緩和

**Feature Branch**: `025-burst-reconnect-threshold`
**Created**: 2026-06-23
**Status**: Draft
**Input**: spec 024 調査結果 (`docs(specs) 11dedcbe`) で「die-restart loop」 の真因が `consecutive_erxudp_timeouts=5` で force_wisun_reconnect 連発と判明。 spec 023 (burst 5s timeout) で fail 加速 = 25 秒で reconnect 発火、 burst の 5 秒周期更新が実質できない。

## Background

spec 011 の `should_force_wisun_reconnect(consecutive, threshold=5, pending=False)` は「メーター ERXUDP に 5 連続で応答しない → Wi-SUN セッション死亡判定 → 強制 reconnect」 の安全機構。 通常 60s polling では 5 × 30s timeout = 150 秒 (2.5 分) で発火、 妥当な保護期間。

ただし spec 022 burst mode + spec 023 burst_timeout=5s 環境では:
- 5 × 5s = 25 秒で reconnect 発火 = **6 倍速**
- burst 5 分中に 数回 reconnect → 30s backoff + SKJOIN re-establish で実質 polling が止まる
- 「burst で 5 秒間隔で見たい」 ユーザ意図が機能しない

spec 025 で burst (and catch-up) mode 中だけ threshold を緩和し、 reconnect 発火を遅らせて burst 体感を保つ。 base mode (off) は既存 threshold=5 のまま (= 通常運用の保護機構を維持)。

## Scope

### A. Burst 中 threshold 緩和 config

- 新 config `realtime_burst_force_reconnect_threshold` (default `30`): burst (and catch-up) mode 中のみ適用、 base は既存 `erxudp_timeout_force_reconnect_threshold=5` 維持
- 効果: burst 中 30 × 5s = 150 秒 (= 2.5 分) で reconnect 発火 → spec 011 base の 2.5 分と同水準に揃う

### B. Pure helper 抽出

- `compute_force_reconnect_threshold(mode, base_threshold, burst_threshold) -> int`
- pure helper TDD、 全引数 plain int/str、 副作用なし
- spec 023 `compute_erxudp_timeout` と同じ pattern

### C. Main loop 配線

- main loop の `should_force_wisun_reconnect` 呼出 (line 3938) 直前で `_effective_threshold = compute_force_reconnect_threshold(_effective_mode, base, burst)` 計算
- `_effective_mode` は spec 023 で既に main loop に定義済 (`"burst" if _rt_mode == "burst" or catchup_remaining > 0 else "off"`)

### D. Kill switch

- `realtime_burst_force_reconnect_threshold=0` で kill switch (= base threshold 使用、 spec 022 互換)、 spec 023 と同じ sentinel pattern

## Non-Scope

- **base `erxudp_timeout_force_reconnect_threshold` の変更** (= 5 → 10 等): v1 不採用。 メーター完全 dead 時の検知遅延 risk あり、 変更には spec 026 で別途検討
- メーター応答性の根本改善: cube-j1 側でできない可能性、 別レイヤー
- SKPING probe による wisun セッション生存確認: spec 024 fix 候補 (iv) として future work、 複雑度高い
- consecutive_erxudp_timeouts の rate 制限 (= 期間内 N 件): spec 024 fix 候補 (v)、 future work

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Admin UI で「5 分間 burst 開始」 を押し、 5 分間 5 秒周期で電力グラフが滑らかに更新されることを HA で観察する。 spec 023 だけだと burst 中 4-6 回 reconnect が走って実質 60 秒以上の穴が空くが、 spec 025 で reconnect 発火閾値を 6 倍に上げ、 burst 5 分中の reconnect 回数を 0-1 回に抑える。

### Acceptance Scenarios

1. **Given** mode=off (default)、 consecutive_erxudp_timeouts=5、 **When** main loop iter、 **Then** `compute_force_reconnect_threshold("off", 5, 30) = 5` で reconnect 発火 (= spec 011 既存挙動互換)
2. **Given** mode=burst、 consecutive_erxudp_timeouts=5、 **When** main loop iter、 **Then** `compute_force_reconnect_threshold("burst", 5, 30) = 30` で reconnect 発火 **しない**
3. **Given** mode=burst、 consecutive_erxudp_timeouts=30、 **When** main loop iter、 **Then** reconnect 発火
4. **Given** `realtime_burst_force_reconnect_threshold=0` (kill switch)、 **When** mode=burst、 **Then** base threshold=5 で reconnect 発火 (= spec 022 互換)
5. **Given** EVENT 24/29 由来 `pending=True`、 **When** mode=burst、 **Then** threshold に関係なく reconnect 発火 (= spec 017 path 維持)

### Key Entities

- 新 pure helper: `compute_force_reconnect_threshold`
- 新 config: `realtime_burst_force_reconnect_threshold`
- 既存変更なし: `should_force_wisun_reconnect`、 `consecutive_erxudp_timeouts`、 `on_poll_success` reset

## Edge Cases

- catch-up 中 (mode='burst' as `_effective_mode`): burst と同じ緩和 threshold 適用、 catch-up 4 iter = 20s 中の連続 fail で reconnect 発火しない安心感
- burst → off 遷移直後: `_rt_mode` が off に戻り base threshold 適用、 burst 中の高 consecutive_erxudp_timeouts が残っていて 5 を既に超過なら即 reconnect 発火 (= 設計通り、 burst 終了で base 保護に戻る)
- threshold negative (< 0): defensive cast、 `should_force_wisun_reconnect` の既存ロジック (`threshold <= 0 で False`) で安全側
- burst 中 30 reconnect 発火後: backoff 30s + SKJOIN → reconnect、 base 復帰待ちなし (burst まだ active)、 再び burst threshold で reconnect 抑制継続

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `compute_force_reconnect_threshold(mode, base_threshold, burst_threshold) -> int` pure helper、 mode=='burst' なら burst_threshold (ただし 0 は kill switch sentinel で base 採用)、 それ以外 base
- **FR-002**: main loop の `should_force_wisun_reconnect(..., int(cfg.get("erxudp_timeout_force_reconnect_threshold", 5)), ...)` 計算を `compute_force_reconnect_threshold(_effective_mode, base, burst)` 経由に置換
- **FR-003**: `apply_defaults` で `realtime_burst_force_reconnect_threshold` (default 30) 追加。 floor は適用しない (= ユーザの自由度を上げる、 妥当性は SC で監視)
- **FR-004**: `realtime_burst_force_reconnect_threshold=0` で kill switch (base 採用)、 helper 内で sentinel 判定
- **FR-005**: spec 022 既存挙動互換 (mode=off で base threshold=5)、 spec 017 pending path 維持

### Key Entities

上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `compute_force_reconnect_threshold` 各 mode + kill switch (4-5 件)
- **SC-002**: 実機: burst 5 分間で SKRESET 回数が **0-1 回** に収束 (spec 023 + spec 025 deploy 後、 spec 023 単独時は 14 分で 8 回)
- **SC-003**: 実機: mode=off (通常運用) では既存 reconnect 動作 (= 妥当な間隔で発火、 5 連続 timeout で 150 秒以内に発火)
- **SC-004**: 既存 8 件 `test_should_force_reconnect.py` 全 pass (= spec 011 + spec 017 既存挙動互換)
- **SC-005**: 既存テスト全件 pass

## Assumptions

- spec 023 で burst 5s timeout が大半は成功 (= probemap latency 2.2 秒の実測ベース)、 まれな fail 連続で threshold 30 に達しないこと
- メーター完全 dead は通常 mode (off) で 5 連続 timeout = 150 秒で十分検知される、 burst 中の dead 検知遅延は許容範囲 (= 5 分 burst 中ずっと dead でも 2.5 分以内に検知)
- BP35CX の wisun セッション自体は 5 分以上保持される (= burst 中の threshold 30 = 150 秒は session timeout より十分短い)
- 関連: [[spec-011-erxudp-resilience]] (元 threshold)、 [[spec-017-wisun-rejoin-backoff]] (pending path)、 [[spec-022-realtime-power-burst]] (_rt_mode / catchup_remaining)、 [[spec-023-burst-erxudp-timeout]] (_effective_mode + burst 5s timeout)、 [[spec-024-bridge-die-restart-investigation]] (調査結果)
