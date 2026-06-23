# Plan: spec 026 Burst Mode 中の wisun rejoin backoff 短縮

## Context

spec 025 deploy (commit `e1533563`) verify: burst 5 分中 SKRESET 2 回 = 1 回あたり 30s backoff + SKJOIN ≒ 30-60 秒消費、 burst 5 分の 20-30% loss。 spec 026 で `compute_rejoin_backoff` の `initial_sec` を burst (and catch-up) 中だけ 5s に short circuit、 reconnect 所要時間を 6 倍速。

既存パターン把握 (grep ベース、 Explore 省略):
- `compute_rejoin_backoff(attempt, initial=30, multiplier=2.0, max=300)` line 2758 (推定)
- main loop except 経路 line 4020-4030 で呼出
- config: `wisun_rejoin_backoff_initial_sec` (30) / `_multiplier` (2.0) / `_max_sec` (300) at apply_defaults line 168-170
- `attempt = diag_state.consecutive_wisun_connect_failures` line 4025

## Approach

### v1 の構成

1. **pure helper 1 つ** (TDD): `compute_burst_aware_backoff_initial(mode, base_initial, burst_initial) -> int`
   - mode='burst' かつ burst_initial > 0 → burst_initial
   - それ以外 → base_initial (= sentinel 0 で kill switch、 spec 023/025 と同じ pattern)
2. **main loop 配線**: except 経路 line 4026 の `compute_rejoin_backoff` 呼出直前で `_effective_initial = compute_burst_aware_backoff_initial(_effective_mode, base, burst)` 計算、 第 2 引数に pass
3. **`apply_defaults`** に 1 config (`realtime_burst_rejoin_backoff_initial_sec` default=5) 追加、 floor なし
4. **kill switch**: `realtime_burst_rejoin_backoff_initial_sec=0` で base 採用

### 設計上の判断

- **initial だけ短縮、 multiplier / max はそのまま**: burst 中 reconnect 5 連発の異常状態は exponential 保護に任せる (5 → 10 → 20 → 40 → 80 = 155s、 burst 5 分の半分で警告水準)
- **既存 `compute_rejoin_backoff` は変更しない**: caller (main loop) で第 2 引数だけ動的化、 spec 017 既存テスト 8 件全 pass 保証
- **`_effective_mode` scope 確認 (FR-006)**: 実装時に except ブロックから `_effective_mode` が参照可能か grep で確認、 try ブロック内変数なので **except でも参照可能** (Python の scope) のはずだが要実装時 verify

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **pure helper 1 つ** (compute_force_reconnect_threshold の直下、 line ~2080 周辺):
   ```python
   def compute_burst_aware_backoff_initial(mode, base_initial, burst_initial):
       """spec 026: burst (and catch-up) なら burst_initial、 それ以外 base.

       burst_initial <= 0 は kill switch sentinel として base 採用 (spec 023/025 と同じ pattern)。
       caller (main loop) は spec 023 で計算済の `_effective_mode` を渡す。"""
       if mode == "burst" and burst_initial > 0:
           return int(burst_initial)
       return int(base_initial)
   ```

2. **定数追加** (REALTIME_BURST_* 群、 line ~458 周辺):
   ```python
   REALTIME_BURST_REJOIN_BACKOFF_INITIAL_SEC = 5
   ```

3. **`apply_defaults`** (line ~197、 spec 025 ブロックの直後):
   ```python
   # spec 026: burst (and catch-up) 中の rejoin backoff initial 短縮。
   # base 30s × 2 回 reconnect = 60+ 秒消費を防ぐ。 5s で 1 回 reconnect 約 10 秒。
   # 0 は kill switch (= base 採用)。 floor なし。
   out.setdefault("realtime_burst_rejoin_backoff_initial_sec",
                  REALTIME_BURST_REJOIN_BACKOFF_INITIAL_SEC)
   ```

4. **main loop 配線** (line 4026 周辺、 `compute_rejoin_backoff` 呼出):
   ```python
   # spec 026: burst (and catch-up) 中は initial backoff を 5s に短縮、
   # reconnect 1 回あたりの時間ロスを 30s → 5s に減らす。
   # _effective_mode は spec 023 で try ブロック内で計算済、 Python scope で
   # except からも参照可 (= main loop 1 iter 内で定義)。
   _effective_initial = compute_burst_aware_backoff_initial(
       _effective_mode,
       int(cfg.get("wisun_rejoin_backoff_initial_sec", 30)),
       int(cfg.get("realtime_burst_rejoin_backoff_initial_sec",
                   REALTIME_BURST_REJOIN_BACKOFF_INITIAL_SEC)))
   _backoff = compute_rejoin_backoff(
       attempt,
       _effective_initial,
       float(cfg.get("wisun_rejoin_backoff_multiplier", 2.0)),
       int(cfg.get("wisun_rejoin_backoff_max_sec", 300)))
   ```

### `tests/unit/test_compute_burst_aware_backoff_initial.py` (新規)

- `test_off_mode_returns_base`
- `test_burst_mode_returns_burst_initial`
- `test_burst_initial_zero_returns_base_kill_switch`
- `test_burst_initial_negative_returns_base` (defensive)
- `test_returns_int_for_float_input` (defensive cast)

## Test list (TDD 順)

1-5. **Red→Green**: `compute_burst_aware_backoff_initial` 5 件 (pure helper)
6. main loop 配線 + apply_defaults + 定数 (テストせず、 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 ~422 + 新規 ~5 = ~427 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy
4. 実機検証:
   - mode=off で reconnect 時 log に `reconnecting Wi-SUN in 30s` (= spec 017 既存)
   - burst 5 分起動 → reconnect 発生時 log に `reconnecting Wi-SUN in 5s` (= spec 026 効果)
   - burst 5 分 SKRESET 2 回でも reconnect 1 回あたり ≦ 10 秒で復帰
5. SC-002 / SC-003 を満たす

## Commit 戦略

- spec 020 spec.md は @ に残置、 spec 026 commit には含めず stash 必要
  - `/tmp/spec020-stash6/` に backup → rm → spec 026 commit → restore
- spec 026 spec.md + plan + 実装 + tests を 1 commit
- redact-plans.sh
- jj commit
- jj git push --remote fork --bookmark main (forward only、 e1533563 → 新 commit)
- lab-ub01 経由 deploy

## Commit message

`feat(bridge): burst (and catch-up) 中の rejoin backoff initial 30s → 5s (spec 026)`
