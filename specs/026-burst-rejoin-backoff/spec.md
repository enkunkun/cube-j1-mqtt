# Feature Specification: Burst Mode 中の wisun rejoin backoff 短縮

**Feature Branch**: `026-burst-rejoin-backoff`
**Created**: 2026-06-23
**Status**: Draft
**Input**: spec 025 deploy 後 verify で burst 5 分中 reconnect 2 回 = 1 回あたり 30 秒 backoff + SKJOIN re-establish ≒ 1 分消費、 burst 5 分の 1/3 が reconnect で潰れる。 ユーザ判断「spec 026 起票: burst 中 reconnect backoff 短縮 (= 30s → 5s)」。

## Background

spec 017 で `compute_rejoin_backoff(attempt, initial=30, multiplier=2.0, max=300)` を導入、 連続失敗時の exponential backoff (30 → 60 → 120 → 240 → 300 clamp)。 通常 60s polling 環境では妥当な保護期間。

しかし spec 022 burst (5s polling) 環境では:
- reconnect 1 回目: 30s backoff + 約 30s SKJOIN re-establish ≒ 60 秒
- 2 回目: 60s backoff + 30s ≒ 90 秒
- burst 5 分 (300 秒) のうち reconnect で 60-90 秒消費 = 20-30% loss
- 5s 周期の体感が途切れる

spec 023/025 で reconnect **発火頻度** を抑えたが、 reconnect が起きた時の **所要時間** は依然 30+ 秒。 spec 026 で burst (and catch-up) 中の `initial_sec` を 5s に短縮し、 reconnect 所要時間を半減以下にする。

## Scope

### A. Burst 中 rejoin backoff initial 短縮 config

- 新 config `realtime_burst_rejoin_backoff_initial_sec` (default `5`): burst (and catch-up) mode 中のみ適用、 base は既存 `wisun_rejoin_backoff_initial_sec=30` 維持
- multiplier / max は base のまま (= burst 中も exponential 維持、 5 → 10 → 20 → 40 → 300 clamp)
- 効果: burst 中 1 回目 reconnect で backoff 5 秒 (= 30 秒 → 5 秒、 6 倍速)

### B. Pure helper 抽出

- `compute_burst_aware_backoff_initial(mode, base_initial, burst_initial) -> int`
- spec 023/025 と同じ mode 依存 pattern

### C. Main loop 配線

- main loop except 経路 (line 4020-4030) で `_effective_initial = compute_burst_aware_backoff_initial(_effective_mode, base, burst)` 計算
- `compute_rejoin_backoff(attempt, _effective_initial, multiplier, max)` で実 backoff 取得
- `_effective_mode` は except ブロックでも scope 内アクセス可能か要確認 (= 実装時)

### D. Kill switch

- `realtime_burst_rejoin_backoff_initial_sec=0` で kill switch (= base 採用、 spec 023/025 と同じ sentinel pattern)

## Non-Scope

- multiplier / max の mode 依存化: burst 中も exponential 維持 (= reconnect 連発の保護機構)、 v1 では initial だけ短縮
- メーター応答性の根本改善: cube-j1 側でできない、 別レイヤー
- SKPING probe で「wisun 生存確認 → reconnect skip」: spec 024 fix 候補 (iv)、 future work

## v1 / 将来検討

**v1 (本 spec 範囲)**:
- A の 1 config
- B の 1 pure helper
- C の main loop 1 行配線

**将来検討 (別 spec)**:
- multiplier / max の mode 依存化
- SKPING probe による reconnect skip 判定
- backoff の動的調整 (= 失敗率に応じて自動 tune)

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Admin UI で「5 分間 burst 開始」 を押し、 5 分間中 reconnect が 1-2 回起きても、 1 回あたり 5 + 数秒 = 10 秒以内で復帰し、 burst polling が大半 (= 280-290 秒) 実行されることを HA Energy / Grafana で観察する。 spec 025 までだと burst 5 分中 60-90 秒 reconnect で潰れていた。

### Acceptance Scenarios

1. **Given** mode=off (default)、 `compute_burst_aware_backoff_initial("off", 30, 5)`、 **When** main loop except、 **Then** 30 を返す (= spec 017 既存挙動互換)
2. **Given** mode=burst、 `compute_burst_aware_backoff_initial("burst", 30, 5)`、 **When**、 **Then** 5 を返す
3. **Given** `realtime_burst_rejoin_backoff_initial_sec=0` (kill switch)、 **When** mode=burst、 **Then** base 30 を返す (= spec 022 互換)
4. **Given** mode=burst attempt=2、 **When** `compute_rejoin_backoff(2, 5, 2.0, 300)`、 **Then** 5 × 2² = 20 秒 (= burst 中も exponential 維持)
5. **Given** mode=burst attempt=10 (= 5 × 2^10 = 5120)、 **When**、 **Then** 300 秒 clamp (= max 同じ、 burst 中も上限保護)

### Key Entities

- 新 pure helper: `compute_burst_aware_backoff_initial`
- 新 config: `realtime_burst_rejoin_backoff_initial_sec`
- 既存変更なし: `compute_rejoin_backoff` (multiplier / max はそのまま)

## Edge Cases

- burst 中 reconnect 5 連発: 5 → 10 → 20 → 40 → 80 = 計 155 秒。 burst 5 分の半分。 これは異常状態 (= メーター本当に dead) で、 base に戻すべきタイミング。 spec 026 では特別な処理せず exponential 任せ
- burst → off 遷移直後の reconnect: `_effective_mode` が off なら base initial 採用 (= 仕様通り)
- catch-up 中 reconnect (= `catchup_remaining > 0`): `_effective_mode='burst'` で burst initial 採用、 spec 023/025 と一貫
- `initial_sec=0` (= 即 retry): spec 017 base でも floor なし、 spec 026 でも sentinel 0 は kill switch、 1+ は許容 (1 秒以下 retry は妥当性低いがユーザ自由)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `compute_burst_aware_backoff_initial(mode, base_initial, burst_initial) -> int` pure helper、 mode=='burst' かつ burst_initial > 0 → burst_initial、 それ以外 base
- **FR-002**: main loop except 経路で `compute_burst_aware_backoff_initial(_effective_mode, base, burst)` を呼んで `compute_rejoin_backoff` の initial 引数に pass
- **FR-003**: `apply_defaults` で `realtime_burst_rejoin_backoff_initial_sec` (default 5) 追加、 floor なし (spec 025 と同じ)
- **FR-004**: `realtime_burst_rejoin_backoff_initial_sec=0` で kill switch (= base 採用)
- **FR-005**: spec 017 既存挙動互換 (mode=off で base 30s)、 multiplier / max は変更なし
- **FR-006**: `_effective_mode` が except ブロック scope 内でアクセス可能であることを実装時確認、 不可ならローカル再計算

### Key Entities

上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `compute_burst_aware_backoff_initial` 各 mode + kill switch (4-5 件)
- **SC-002**: 実機: burst 5 分間で reconnect 発生時の log に `reconnecting Wi-SUN in 5s` (spec 017 既存 log) が出る
- **SC-003**: 実機: burst 5 分 SKRESET 2 回でも reconnect 所要時間が 10 秒/回程度 (spec 025 までは 30+ 秒/回)
- **SC-004**: 既存テスト全件 pass (= spec 017 既存挙動互換)

## Assumptions

- BP35CX SKJOIN re-establish は backoff 自体と独立、 メーターが応答する限り 5-15 秒で完了 (実機計測ベース)
- burst 中の短 backoff が ARIB STD-T108 や Wi-SUN 仕様に違反しない (= 5 秒以上の間隔は十分)
- 5 連発 reconnect (= exponential で 155 秒消費) のような異常状態は spec 026 範囲外、 別 mechanism (= meter probe / 警告 log) で検知
- 関連: [[spec-017-wisun-rejoin-backoff]] (元 compute_rejoin_backoff)、 [[spec-022-realtime-power-burst]] (_rt_mode / catchup_remaining)、 [[spec-023-burst-erxudp-timeout]] (_effective_mode)、 [[spec-025-burst-reconnect-threshold]] (reconnect 発火抑制)
