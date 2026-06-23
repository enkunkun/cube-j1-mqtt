# Plan: spec 023 Burst Mode Fast ERXUDP Timeout

## Context

spec 022 v1 deploy (`a62bbdc2`) で burst mode は core 動作確認済 (counter, mode transition, catch-up routing OK)。 ただし実機検証で burst 中 (interval=5s) の poll が `erxudp_timeout` で 30 秒 lag、 5 秒周期が実質無効化されることが判明。 原因: spec 011 follow-up で採用した `erxudp_timeout_sec=30` と `erxudp_intra_cycle_retries=1` が burst の cycle_interval=5s と競合。

ユーザコメント「burst できるようになったら (spec 020 は) いらない」 = burst を本当に動かす方が priority、 spec 020 (TID mismatch late publish) は pivot で実装スキップ。

spec 023 で:
- 新 config `realtime_burst_erxudp_timeout_sec` (default 5) と `realtime_burst_erxudp_intra_cycle_retries` (default 0)
- mode=burst (or catch-up 中) なら短 timeout / 0 retry を採用
- mode=off は spec 011 既存挙動 (30s timeout, 1 retry) 維持

## Approach

### v1 の構成

1. **pure helper 2 つ** (TDD): `compute_erxudp_timeout(mode, base, burst) -> int` と `compute_intra_cycle_retries(mode, base, burst) -> int` (後者は v1 では main loop 配線なし、 v1.5 用 dormant)
2. **main loop 配線**: 既存 `_erxudp_timeout = int(cfg.get("erxudp_timeout_sec", 30))` を `compute_erxudp_timeout` 経由に。 mode 引数は「burst モード or catch-up 中」を1つの string にまとめて pass (例 `"burst" if _rt_mode == "burst" or catchup_remaining > 0 else "off"`)
3. **`apply_defaults`** に 1 config (`realtime_burst_erxudp_timeout_sec` default=5) + floor 5s (0 は kill switch 例外)
4. **kill switch**: `realtime_burst_erxudp_timeout_sec=0` で base timeout 使用 (sentinel)、 `compute_erxudp_timeout` 内で 0 判定
5. **retry は v1 で触らない**: 既存 `erxudp_intra_cycle_retries=0` (spec 012 で 1→0 に変更済) が default、 burst でも自然に 0 effect。 retry helper は実装残置 (v1.5 用)、 main loop 配線と新 config 追加は v1 ではしない (= 不要な複雑化回避)

### 設計上の判断

- **pure helper のシンプルさ**: 全引数 plain int/str、 mode string 比較のみ。 既存 `compute_effective_poll_interval` と同じ責務 (mode 依存切替) で対称
- **catch-up を burst 扱い**: catch-up は burst の余韻、 高速 cycle を維持して 4 iter を 20 秒で済ます意図と一致。 main loop で 1 行 `_effective_mode = "burst" if _rt_mode == "burst" or catchup_remaining > 0 else "off"` を用意
- **kill switch `timeout_sec=0` sentinel**: 「0 は無意味な値」を逆手に取って kill switch 化。 既存 `epc_tier4_every=0` (spec 018) と同じパターン
- **retry=0 が default**: burst で「失敗即次」 が筋。 ただし config で 1 にもできる (= 確実性 vs スピード trade-off ユーザ可変)

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **pure helper 2 つ** (compute_effective_poll_interval の直下、 line ~447 付近):
   ```python
   def compute_erxudp_timeout(mode, base_timeout, burst_timeout):
       """spec 023: burst (or catch-up) なら burst_timeout、 それ以外 base.
       burst_timeout が 0 (sentinel) なら base を返す (kill switch)."""
       if mode == "burst" and burst_timeout > 0:
           return int(burst_timeout)
       return int(base_timeout)

   def compute_intra_cycle_retries(mode, base_retries, burst_retries):
       """spec 023: burst (or catch-up) なら burst_retries、 それ以外 base."""
       if mode == "burst":
           return int(burst_retries)
       return int(base_retries)
   ```

2. **`apply_defaults`** (line ~178、 spec 022 ブロックの直後):
   ```python
   # spec 023: burst mode 中の ERXUDP timeout / retry 短縮。
   # 5 秒 cycle で 30 秒 timeout だと cycle_interval が無効化されるため。
   # timeout_sec=0 は kill switch (base timeout 使用、 例外扱い)。
   # それ以外の値は dig 決定 B: 5s floor で clamp (= burst_interval_min と同価)。
   out.setdefault("realtime_burst_erxudp_timeout_sec",
                  REALTIME_BURST_ERXUDP_TIMEOUT_SEC)
   if out["realtime_burst_erxudp_timeout_sec"] not in (0,):
       if out["realtime_burst_erxudp_timeout_sec"] < REALTIME_BURST_MIN_INTERVAL_SEC:
           out["realtime_burst_erxudp_timeout_sec"] = REALTIME_BURST_MIN_INTERVAL_SEC
   out.setdefault("realtime_burst_erxudp_intra_cycle_retries",
                  REALTIME_BURST_ERXUDP_RETRIES)
   ```

3. **定数追加** (REALTIME_BURST_* 群、 line ~448 周辺):
   ```python
   REALTIME_BURST_ERXUDP_TIMEOUT_SEC = 5
   REALTIME_BURST_ERXUDP_RETRIES = 0
   ```

4. **main loop 配線** (line 3836 周辺、 `_erxudp_timeout = ...` / `_max_retries = ...` の置換):
   ```python
   # spec 023: burst (and catch-up) 中は短 timeout / 0 retry で 5s cycle 維持。
   _effective_mode = ("burst"
                      if _rt_mode == "burst" or catchup_remaining > 0
                      else "off")
   _erxudp_timeout = compute_erxudp_timeout(
       _effective_mode,
       int(cfg.get("erxudp_timeout_sec", 30)),
       int(cfg.get("realtime_burst_erxudp_timeout_sec",
                   REALTIME_BURST_ERXUDP_TIMEOUT_SEC)))
   _max_retries = compute_intra_cycle_retries(
       _effective_mode,
       int(cfg.get("erxudp_intra_cycle_retries", 2)),
       int(cfg.get("realtime_burst_erxudp_intra_cycle_retries",
                   REALTIME_BURST_ERXUDP_RETRIES)))
   ```

### `tests/unit/test_compute_erxudp_timeout.py` (新規)

- `test_off_mode_returns_base`
- `test_burst_mode_returns_burst_timeout`
- `test_burst_timeout_zero_returns_base_kill_switch`
- `test_burst_timeout_negative_treated_as_zero` (defensive)
- `test_returns_int_for_float_input` (defensive cast)

### `tests/unit/test_compute_intra_cycle_retries.py` (新規)

- `test_off_mode_returns_base_retries`
- `test_burst_mode_returns_burst_retries`
- `test_burst_retries_zero_explicit` (= burst で 0 retry が意図的に動く)

## Test list (TDD 順)

1-5. **Red→Green**: `compute_erxudp_timeout` 5 件 (pure helper)
6-8. **Red→Green**: `compute_intra_cycle_retries` 3 件 (pure helper)
9. main loop 配線 + apply_defaults 拡張 (テストせず、 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 ~409 + 新規 ~8 = ~417 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy
4. 実機検証:
   - mode=off で 60s polling 正常 (spec 022 v1 互換)
   - burst 5 分起動 → bridge log で `poll_success` event が 30 件以上発生 (≒ 5 秒周期で値来る)
   - `/api/diag` で `erxudp_timeouts_total` の増加率 が spec 022 v1 deploy 時点より低い
5. SC-003 / SC-004 を満たすか体感確認 (Grafana で power_w sparkline が滑らか)
6. **dig 決定 A 監視項目**: `erxudp_tid_mismatch_total` の deploy 後 1 時間増加率が spec 022 v1 deploy 時点 (a62bbdc2 から 1 時間以内の値) より顕著に増えていないこと。 顕著増加なら meter queue 残留懸念が現実化 → 次 spec で `realtime_burst_erxudp_intra_cycle_retries=1` に default 変更検討

## Commit 戦略

- spec 020 spec.md は @ に残置 (pivot メモ)、 spec 023 commit には含めず stash 必要
  - `/tmp/spec020-stash2/` に backup → rm → spec 023 commit → restore
- spec 023 spec.md + plan + 実装 + tests を 1 commit
- redact-plans.sh
- jj commit
- jj git push --remote fork --bookmark main (forward only)
- lab-ub01 経由 deploy

## Commit message

`feat(bridge): burst mode 中の ERXUDP timeout 30s → 5s + retry 1 → 0 (spec 023)`
