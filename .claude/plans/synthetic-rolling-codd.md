# Plan: spec 017 Wi-SUN Rejoin Exponential Backoff + Serial Reopen + EVENT 24/29 Trigger

## Context

spec 011 (`erxudp_timeout_force_reconnect_threshold = 5`) で「連続 ERXUDP timeout 5 回で wisun_connect やり直し」までは実装済。 ただし再接続の中身は **固定 30 秒 sleep** (`production_tool/mqtt_bridge.py:3506`) で、 長時間障害シナリオで以下が課題:

1. **指数 backoff なし** — 30 秒で永遠リトライ、 メーターに無駄な SKJOIN 負荷
2. **シリアル port reopen なし** — BP35CX 側 UART ハングから永遠に復帰しない
3. **EVENT 24/29 (PANA 失敗) 即時トリガなし** — timeout 待ちの 30+ 秒を浪費

hals5412 fork `2779679` (production_tool_v2) の発想 (initial=30 / max=300 / multiplier=2.0、 N=5 で serial reopen、 EVENT 24 即時 trigger) を取り込む。

## Approach

- pure helper `compute_rejoin_backoff(attempt, initial, multiplier, max_sec)` を抽出 (testable な核)
- main loop の外側 except 経路は **既存の soft-retry-loop 構造を維持** (dig Round 1 決定 1): `time.sleep(30)` を `time.sleep(compute_rejoin_backoff(counter, ...))` に置換、 counter は DiagState `consecutive_wisun_connect_failures` で永続化 (1 outer except = 1 try)
- 連続 N 回失敗で `os.close(fd)` + `open_serial(...)` で UART reopen (失敗時 WARN ログ + 元 fd 保持、 致命的扱いせず)
- `read_erxudp` に EVENT 24/29 検出を入れ、 DiagState の `pending_wisun_rejoin` flag を立てて即時 rejoin
- DiagState に `serial_reopen_total` counter (publish) + `consecutive_wisun_connect_failures` counter (内部) + `pending_wisun_rejoin` bool (内部) + 対応メソッド
- `pending_wisun_rejoin` クリアは **raise 直前** (signal consume パターン、 dig Round 1 決定 2)
- snapshot 公開は `serial_reopen_total` のみ (dig Round 1 決定 3: minimal schema 維持、 consecutive は transient state で長期観測価値が低い)

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **pure helper** (line 2200 付近、 `decide_epc_tier` の隣):
   ```python
   def compute_rejoin_backoff(attempt, initial_sec, multiplier, max_sec):
       """spec 017: exponential backoff for wisun_connect retries.

       attempt 0 → initial, 1 → initial*mult, ..., clamped at max_sec.
       Negative attempt is treated as 0. multiplier <= 1 → linear at
       initial. initial >= max → always max."""
       a = max(0, int(attempt))
       backoff = float(initial_sec) * (float(multiplier) ** a)
       return int(min(backoff, float(max_sec)))
   ```

2. **`apply_defaults`** (line 153 付近、 spec 019 ブロックの直後):
   ```python
   # spec 017: Wi-SUN rejoin exponential backoff + serial port reopen.
   out.setdefault("wisun_rejoin_backoff_initial_sec", 30)
   out.setdefault("wisun_rejoin_backoff_max_sec", 300)
   out.setdefault("wisun_rejoin_backoff_multiplier", 2.0)
   out.setdefault("wisun_serial_reopen_after_rejoin_failures", 5)
   ```

3. **`DiagState.__init__`** (line 1820 付近、 spec 016 ブロックの隣):
   ```python
   # spec 017: Wi-SUN rejoin observability.
   self.serial_reopen_total = 0
   self.consecutive_wisun_connect_failures = 0
   self.pending_wisun_rejoin = False
   ```

4. **`DiagState` 新メソッド 2 本** (既存 on_wisun_reconnect の隣):
   ```python
   def on_serial_reopen(self):
       self.serial_reopen_total += 1

   def on_wisun_pana_fail(self, event_id):
       """spec 017: PANA fail (EVENT 24/29) — signal force-rejoin to
       the main loop, also bump the SK event counter (existing path)."""
       self.on_sk_event(event_id)
       self.pending_wisun_rejoin = True
   ```

5. **`should_force_wisun_reconnect`** (line 1234 付近、 pure helper を拡張):
   ```python
   def should_force_wisun_reconnect(consecutive, threshold, pending=False):
       """spec 017: pending overrides the threshold check (immediate
       rejoin signal from EVENT 24/29)."""
       if pending:
           return True
       if int(threshold) <= 0:
           return False
       return int(consecutive) >= int(threshold)
   ```
   (default 引数 pending=False で既存 callers 互換)

6. **`_DIAG_SNAPSHOT_KEYS`** (line 1451 付近) に追加:
   ```python
   "serial_reopen_total",
   ```
   `pending_wisun_rejoin` は内部状態なので公開しない (spec 016 と同じ minimal schema 判断)

7. **snapshot raw dict** (line 1949 付近):
   ```python
   "serial_reopen_total": self.serial_reopen_total,
   ```

8. **`read_erxudp`** (line 2627 付近、 `kind == "event"` 分岐):
   既存:
   ```python
   if kind == "event":
       if diag_state is not None:
           try:
               diag_state.on_sk_event(value)
           except Exception as e:
               log("diag on_sk_event error: {}".format(e))
       continue
   ```
   差し替え (spec 017 で EVENT 24/29 を special handling、 他 event は従来通り):
   ```python
   if kind == "event":
       if diag_state is not None:
           try:
               if value in ("24", "29"):
                   diag_state.on_wisun_pana_fail(value)
               else:
                   diag_state.on_sk_event(value)
           except Exception as e:
               log("diag on_sk_event error: {}".format(e))
       continue
   ```

9. **main loop の `should_force_wisun_reconnect` 呼び出し** (line 3458-3464 付近):
   ```python
   if should_force_wisun_reconnect(
           diag_state.consecutive_erxudp_timeouts,
           int(cfg.get("erxudp_timeout_force_reconnect_threshold", 5)),
           pending=diag_state.pending_wisun_rejoin):
       # dig Round 1 決定 2: signal consume — clear before raise.
       # If reconnect fails, EVENT 24/29 re-firing will re-set the flag,
       # and ERXUDP timeout threshold also provides a safety net.
       _was_pending = diag_state.pending_wisun_rejoin
       diag_state.pending_wisun_rejoin = False
       raise RuntimeError(
           "wisun reconnect forced: consecutive_erxudp_timeouts={}, "
           "pending={}".format(
               diag_state.consecutive_erxudp_timeouts, _was_pending))
   ```

10. **main loop 外側 except 経路** (line 3505-3516 付近、 既存型を保ったまま sleep を backoff に置換 + serial reopen 分岐追加、 dig Round 1 決定 1):
   ```python
   except Exception as e:
       attempt = diag_state.consecutive_wisun_connect_failures
       _backoff = compute_rejoin_backoff(
           attempt,
           int(cfg.get("wisun_rejoin_backoff_initial_sec", 30)),
           float(cfg.get("wisun_rejoin_backoff_multiplier", 2.0)),
           int(cfg.get("wisun_rejoin_backoff_max_sec", 300)))
       log("Main loop error (attempt {}): {} - reconnecting Wi-SUN in {}s"
           .format(attempt + 1, e, _backoff))
       time.sleep(_backoff)
       # spec 017: after N consecutive failures, reopen the serial port
       # to recover from BP35CX UART hangs.
       _reopen_after = int(cfg.get(
           "wisun_serial_reopen_after_rejoin_failures", 5))
       if _reopen_after > 0 and attempt + 1 >= _reopen_after:
           try:
               os.close(fd)
               fd = open_serial(serial_port)
               diag_state.on_serial_reopen()
               log("Serial port reopened after {} failures".format(
                   attempt + 1))
           except Exception as re:
               log("WARN: serial reopen failed (continuing with original fd): {}"
                   .format(re))
       try:
           ipv6 = wisun_connect(fd, br_id, br_pwd, diag_state=diag_state)
           log("Wi-SUN reconnected at {}".format(ipv6))
           diag_state.consecutive_wisun_connect_failures = 0
           try:
               diag_state.on_wisun_reconnect()
           except Exception as e3:
               log("diag on_wisun_reconnect error: {}".format(e3))
       except Exception as e2:
           diag_state.consecutive_wisun_connect_failures += 1
           log("Wi-SUN reconnect failed (will retry next cycle): {}".format(e2))
   ```

   注: serial_port は line 3220 で `cfg.get("serial_port", "/dev/ttyS1")` から派生し main() スコープに保持されている、 outer except から参照可能 (実コード確認済)。

### `tests/unit/test_compute_rejoin_backoff.py` (新規)

pure helper の TDD:
- `test_returns_initial_at_attempt_zero`
- `test_doubles_each_attempt_with_default_multiplier`
- `test_clamps_at_max_sec`
- `test_negative_attempt_treated_as_zero`
- `test_multiplier_one_returns_initial`
- `test_initial_greater_than_max_returns_max`

### `tests/unit/test_diag_state.py` (拡張)

baseline `test_initial_snapshot_includes_zero_counters_uptime_and_version` 更新:
- `"serial_reopen_total": 0` 追加 (`consecutive_wisun_connect_failures` は publish しないので expected dict には含めない、 dig Round 1 決定 3)

新規:
- `test_on_serial_reopen_increments_counter`
- `test_on_wisun_pana_fail_sets_pending_flag_and_increments_sk_event_counter`
- `test_pending_wisun_rejoin_starts_false`
- `test_consecutive_wisun_connect_failures_starts_at_zero`
- `test_snapshot_includes_serial_reopen_total_at_zero`

### `tests/unit/test_should_force_reconnect.py` (拡張)

既存テスト群に追加:
- `test_pending_overrides_threshold_check` (pending=True で threshold 未到達でも True 返却)
- `test_pending_false_keeps_existing_behaviour`

### `tests/unit/test_read_erxudp_tid.py` (拡張) — EVENT 24/29 検出のテスト

dig Round 2 注記: テスト用 `_FakeDiagState` を導入 (既存 read_erxudp テストは DiagState 渡していなかったので新規)。 `on_wisun_pana_fail` / `on_sk_event` の call 履歴を記録するシンプルな fake。

- `test_read_erxudp_event_24_calls_on_wisun_pana_fail` (EVENT 24 line → on_wisun_pana_fail("24") のみ呼ばれ、 on_sk_event は呼ばれない)
- `test_read_erxudp_event_29_calls_on_wisun_pana_fail` (同上)
- `test_read_erxudp_event_22_still_calls_on_sk_event` (既存 path 互換、 PANA 系以外の EVENT は従来通り)

## Pure helper のテスト先行 (TDD 順)

1. **Red**: `test_returns_initial_at_attempt_zero` → AttributeError on compute_rejoin_backoff
2. (stub: return initial_sec)
3. **Red**: `test_doubles_each_attempt_with_default_multiplier` → stub では fail
4. **Red**: `test_clamps_at_max_sec`
5. **Red**: `test_negative_attempt_treated_as_zero`
6. **Red**: `test_multiplier_one_returns_initial`
7. **Red**: `test_initial_greater_than_max_returns_max`
8-15. DiagState 拡張 (5 件) + should_force_wisun_reconnect 拡張 (2 件) + read_erxudp EVENT (2 件)
16. main loop integration: テストせず実機検証 (spec 016/019 と同じ判断、 mock 過大)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で **既存 361 + 新規 ~15 = ~376 件 pass**
2. ruff check 新規エラー 0
3. lab-ub01 経由 deploy
4. 実機で:
   - `/api/diag` (admin auth 経由) に `serial_reopen_total: 0` が見える
   - logcat / mqtt_bridge.log で WISUN 障害シナリオ発生時に「WARN: wisun_connect failed (attempt N): ... - sleep Ms」が出る
   - serial reopen も 5 回連続失敗で発火するはず (実機長期観測)
5. 長期効果: spec 011 の `wisun_reconnects_total` の伸びが緩やかになる (= 障害復帰が指数 backoff で抑制)

## Commit 戦略

前 4 回踏襲 (spec 014/015/016/019):
- 018 spec.md を /tmp に stash
- spec 017 関連 + spec.md + plan + redact-plans 適用で commit
- 018 spec.md を restore して @ に戻す
- 並行衝突あれば jj edit で cleanup
- push は forward only (新ルール遵守)

## Commit message

`feat(bridge): Wi-SUN rejoin 指数 backoff + serial reopen + EVENT 24/29 即時 trigger (spec 017)`
