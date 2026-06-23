# Plan: spec 025 Burst Mode 中の force_wisun_reconnect threshold 緩和

## Context

spec 024 調査結果 (commit `11dedcbe`) で「die-restart loop」 の真因 = `consecutive_erxudp_timeouts=5` で force_wisun_reconnect 発火、 と判明。 spec 023 (burst 5s timeout) と組み合わせると 25 秒で発火 = 6 倍速、 burst の 5 秒周期更新が機能しない。

spec 025 で burst (and catch-up) mode 中だけ threshold を緩和 (= 30、 5 倍に)。 base mode (off) は spec 011 既存 threshold=5 のまま、 通常運用の保護機構維持。

Explore (a673832b22da31396) で既存パターン把握:
- `should_force_wisun_reconnect(consecutive, threshold, pending=False)` line 1437
- DiagState `consecutive_erxudp_timeouts` line 2143 init、 line 2200 (`on_erxudp_timeout` で +=1)、 line 2283 (`on_poll_success` で =0)
- main loop 呼出 line 3938-3945、 threshold 取得 line 3940 (`int(cfg.get("erxudp_timeout_force_reconnect_threshold", 5))`)
- apply_defaults line 119 default=5
- test_should_force_reconnect.py 8 件 (spec 011 5 + spec 017 pending 3)

## Approach

### v1 の構成

1. **pure helper 1 つ** (TDD): `compute_force_reconnect_threshold(mode, base_threshold, burst_threshold) -> int`
   - mode='burst' かつ burst_threshold > 0 → burst_threshold
   - それ以外 → base_threshold (= sentinel 0 で kill switch、 spec 023 と同じ pattern)
2. **main loop 配線**: `should_force_wisun_reconnect` 呼出直前で `_effective_threshold = compute_force_reconnect_threshold(_effective_mode, base, burst)` 計算 (_effective_mode は spec 023 で既に定義済)
3. **`apply_defaults`** に 1 config (`realtime_burst_force_reconnect_threshold` default=30) 追加、 floor なし (= ユーザ自由度)
4. **kill switch**: `realtime_burst_force_reconnect_threshold=0` で base 採用 (spec 023 と同じ sentinel)

### 設計上の判断

- **base threshold は変えない**: spec 011 の 5 はメーター完全 dead 検知期間 150 秒として妥当、 変更は spec 026 等の別 spec で検討
- **burst default 30 の根拠**: burst 5s × 30 = 150 秒 = base mode の 5 × 30 = 150 秒と同水準、 「mode 切替で保護期間が変わらない」 設計意図
- **catch-up は burst 扱い**: spec 023 と同じ `_effective_mode` 使用、 catch-up 4 iter (20 秒) で threshold 30 に達しないので reconnect 抑制が継続
- **既存 helper `should_force_wisun_reconnect` は変更しない**: pending path は維持 (= spec 017 EVENT 24/29 即時 reconnect)、 threshold だけ caller 側で動的に決める分離

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **pure helper 1 つ** (compute_erxudp_timeout の直下、 line ~458 周辺):
   ```python
   def compute_force_reconnect_threshold(mode, base_threshold, burst_threshold):
       """spec 025: burst (and catch-up) なら burst_threshold、 それ以外 base.

       burst_threshold <= 0 は kill switch sentinel として base 採用 (spec 023 と同じ pattern)。
       caller (main loop) は `_effective_mode` を渡す (= burst or catch-up なら 'burst')。"""
       if mode == "burst" and burst_threshold > 0:
           return int(burst_threshold)
       return int(base_threshold)
   ```

2. **定数追加** (REALTIME_BURST_* 群、 line ~452 周辺):
   ```python
   REALTIME_BURST_FORCE_RECONNECT_THRESHOLD = 30
   ```

3. **`apply_defaults`** (line ~192、 spec 023 ブロックの直後):
   ```python
   # spec 025: burst (and catch-up) 中の force_wisun_reconnect threshold 緩和。
   # base 5 × burst_timeout 5s = 25 秒で発火を防ぐ。 30 × 5s = 150 秒 (base mode
   # 5 × 30s と同水準)。 0 は kill switch (= base 採用、 spec 022 互換)。
   out.setdefault("realtime_burst_force_reconnect_threshold",
                  REALTIME_BURST_FORCE_RECONNECT_THRESHOLD)
   ```

4. **main loop 配線** (line 3938-3945 周辺、 `should_force_wisun_reconnect` 呼出):
   ```python
   # spec 025: burst (and catch-up) 中は threshold 緩和で reconnect 連発を防ぐ。
   # _effective_mode は spec 023 で iter 冒頭で計算済。
   _force_threshold = compute_force_reconnect_threshold(
       _effective_mode,
       int(cfg.get("erxudp_timeout_force_reconnect_threshold", 5)),
       int(cfg.get("realtime_burst_force_reconnect_threshold",
                   REALTIME_BURST_FORCE_RECONNECT_THRESHOLD)))
   if should_force_wisun_reconnect(
           diag_state.consecutive_erxudp_timeouts,
           _force_threshold,
           diag_state.pending_wisun_rejoin):
       raise RuntimeError(...)
   ```

### `tests/unit/test_compute_force_reconnect_threshold.py` (新規)

- `test_off_mode_returns_base`
- `test_burst_mode_returns_burst_threshold`
- `test_burst_threshold_zero_returns_base_kill_switch`
- `test_burst_threshold_negative_returns_base` (defensive、 spec 023 と同じ)
- `test_returns_int_for_float_input` (defensive cast)

## Test list (TDD 順)

1-5. **Red→Green**: `compute_force_reconnect_threshold` 5 件 (pure helper)
6. main loop 配線 + apply_defaults 拡張 + 定数 (テストせず、 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 ~417 + 新規 ~5 = ~422 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy
4. 実機検証:
   - mode=off で base threshold=5、 既存 reconnect 動作 (= 5 連続 timeout で 150 秒以内発火)
   - burst 5 分起動 → SKRESET 回数 0-1 回に収束 (spec 023 単独時は 14 分 8 回)
   - `/api/diag` で `consecutive_erxudp_timeouts` が burst 中 ~30 まで上がることがあっても reconnect 発火しない
5. SC-002 を満たす (= burst 中 reconnect 0-1 回)

## Commit 戦略

- spec 020 spec.md は @ に残置、 spec 025 commit には含めず stash 必要
  - `/tmp/spec020-stash5/` に backup → rm → spec 025 commit → restore
- spec 025 spec.md + plan + 実装 + tests を 1 commit
- redact-plans.sh
- jj commit
- jj git push --remote fork --bookmark main (forward only、 11dedcbe → 新 commit)
- lab-ub01 経由 deploy

## Commit message

`feat(bridge): burst (and catch-up) 中の force_wisun_reconnect threshold 5 → 30 (spec 025)`
