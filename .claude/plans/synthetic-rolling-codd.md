# Plan: spec 022 Realtime Power Burst Mode

## Context

spec 022 v1 MVP: HA/Admin UI から「リアルタイム表示 (5 分)」を ON にすると 5 分間だけ 0xE7 (瞬時電力) を 5 秒間隔で polling、 期限切れで自動 off 復帰。 ARIB STD-T108 920MHz duty cycle (360s/h) は BP35CX 物理層が遮断するので v1 はホスト側 abort logic 不要。

probe (`/tmp/spec022-probe.py`) で確証:
- 0x9D (StatusAnnouncementPropertyMap) = `0x80, 0x81, 0x88` のみ → 0xE7 INF (push) 不可、 polling のみ
- 既存 SKJOIN を維持して polling 間隔だけ変える設計が必要 (連続 SKJOIN は不安定)

Explore (a2cf5d165f5d4d3f6) で既存パターン把握:
- AdminHandler line 732 (`BaseHTTPRequestHandler`)、 `_do_post_dispatch` line 1036-1049、 `_read_json_body` line 802-813
- AdminHandler `lock = threading.Lock()` line 1267 (class-level)
- Main loop poll_interval line 3360、 tier decide line 3520-3523、 sleep line 3508 (`compute_next_poll_sleep`)
- DiagState: `_DIAG_SNAPSHOT_KEYS` line 1477-1495、 `__init__` line 1893、 on_X line 1939、 snapshot line 2039
- `apply_defaults` line 101 (setdefault pattern)
- Admin HTML: inline `ADMIN_HTML` 定数 line 567-1863

## Approach

### v1 MVP の構成

1. **pure helper** `compute_effective_poll_interval(now, base_interval, mode_state) -> int` — burst active なら burst_interval、 expired/off なら base_interval
2. **`RealtimeModeState` class** with threading.Lock — `start_burst(now, duration_sec, interval_sec)` / `stop_burst()` / `tick(now)` / `snapshot()`
   - `tick(now)`: expires_at 過ぎてたら off に自動遷移、 戻り値 `(current_mode, expired_at_this_tick)` で caller に catch-up trigger 通知
3. **Admin API**: POST `/api/realtime/start` / POST `/api/realtime/stop` / GET `/api/realtime/status`
4. **Main loop 配線**: 毎 iter で `mode_state.tick(now)` → `compute_effective_poll_interval` で sleep 計算、 burst 中は tier1 固定 + `normal_cycle_count` 進めず、 burst 期限切れ iter で `catchup_remaining=4` フラグ立てて翌 4 iter で tier4/3/2/1 を順次 polling
5. **DiagState** 5 拡張: `realtime_mode_current` (str gauge) / `realtime_burst_started_total` / `realtime_burst_completed_total` / `realtime_burst_aborted_total` / `realtime_effective_interval_seconds` (int gauge)
6. **Admin UI**: 既存 form 群の下に「リアルタイム表示 (5 分)」button + status display

### 設計上の判断

- **state mutation 責任分離**: `compute_effective_poll_interval` は pure (mutation なし)、 mode 遷移は `RealtimeModeState.tick()` が atomic に行う。 caller (main loop) は tick の戻り値 `(mode, transition)` で `transition ∈ {None, 'expired', 'aborted'}` を判定して catch-up trigger
- **catch-up logic**: 「transition != None の次 iter から catchup_remaining = 4 (tier4/3/2/1) で連続消費」、 catch-up 中の eff_interval は **直前の burst_interval を引き継ぐ** (dig 決定 A、 5s × 4 = 20s で復帰)。 cycle counter とは独立に保持
- **API thread と main loop thread の境界**: `RealtimeModeState` 内部 lock で全 method 保護。 main loop は `tick()` と `snapshot()` のみ呼ぶ、 lock 取得時間は数 µs
- **重複 start (dig 決定 D)**: 常に新 duration で expires_at 上書き、 catch-up 中の再 start は catch-up クリア
- **EEDSCAN 取り扱い (dig 決定 E)**: burst 中は EEDSCAN を skip、 catch-up 中は skip しない (catch-up は 20s で完了、 EEDSCAN 占有 数秒は許容)
- **Wi-SUN reconnect 中の burst (dig 決定 F)**: mode='burst' のまま継続、 rejoin 完了後 polling 自然再開。 expires_at は absolute time なので rejoin 中の時間も経過 = 残り時間短縮 (シンプル設計優先)
- **status endpoint (dig 決定 H)**: `effective_interval_seconds` は DiagState の `realtime_effective_interval_seconds` を読む (main loop が随時更新する gauge を 1 ソース of truth)
- **body 空 / 不正 (dig 決定 C + G)**: Content-Length 0 → `_read_json_body` が None → `body = body or {}` で default 適用、 floor/cap 違反のみ 400

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **定数** (apply_defaults 周辺、 line ~100):
   ```python
   REALTIME_BURST_DEFAULT_DURATION_SEC = 300
   REALTIME_BURST_DEFAULT_INTERVAL_SEC = 5
   REALTIME_BURST_MIN_INTERVAL_SEC = 5
   REALTIME_BURST_MAX_DURATION_SEC = 3600
   ```

2. **pure helper** (DiagState 周辺、 line ~1870 付近):
   ```python
   def compute_effective_poll_interval(now, base_interval, mode_state):
       """spec 022: burst active なら burst_interval、 そうでなければ base_interval。
       mode_state: dict {"mode": str, "expires_at": float|None, "burst_interval": int}
       状態 mutation はしない (caller が RealtimeModeState.tick() で別途処理)"""
       if mode_state.get("mode") == "burst":
           exp = mode_state.get("expires_at")
           if exp is not None and now < exp:
               return int(mode_state.get("burst_interval", base_interval))
       return int(base_interval)
   ```

3. **`RealtimeModeState` class** (DiagState の隣、 line ~1890 付近):
   - `start_burst(now, duration_sec, interval_sec)`: lock 内で mode=burst, expires_at=now+duration, burst_interval 更新、 `_pending_abort=False` リセット (= dig 決定 D: 常に新 duration で上書き、 残り時間は破棄、 catch-up クリアは main loop 側で行う)
   - `stop_burst()`: lock 内で `was_active = (mode=='burst')`、 was_active なら `_pending_abort=True` セット (mode はまだ burst のまま、 tick で 'aborted' transition + off 遷移する)、 was_active 返却
   - `tick(now)`: lock 内で **dig 決定 B** に従う:
     - mode='burst' + `_pending_abort=True` → mode=off、 expires_at=None、 `_pending_abort=False`、 戻り値 `("off", "aborted")`
     - mode='burst' + expires_at is not None + now >= expires_at → mode=off、 expires_at=None、 戻り値 `("off", "expired")`
     - それ以外 → 戻り値 `(mode, None)`
   - `snapshot()`: lock 内で dict コピー返却 (state のみ、 transition は含まない)

4. **`apply_defaults`** (line ~167 付近、 spec 018 ブロック直後):
   ```python
   out.setdefault("realtime_burst_default_duration_sec", REALTIME_BURST_DEFAULT_DURATION_SEC)
   out.setdefault("realtime_burst_default_interval_sec", REALTIME_BURST_DEFAULT_INTERVAL_SEC)
   ```

5. **DiagState 拡張** (line ~1893 init / ~1939 on_X / ~2039 snapshot):
   - `__init__`: `realtime_mode_current = "off"`、 `realtime_burst_started_total = 0`、 `realtime_burst_completed_total = 0`、 `realtime_burst_aborted_total = 0`、 `realtime_effective_interval_seconds = None`
   - methods: `on_realtime_burst_started()` / `on_realtime_burst_completed()` / `on_realtime_burst_aborted()` / `set_realtime_state(mode, eff_int)`
   - `_DIAG_SNAPSHOT_KEYS`: 5 key 追加

6. **AdminHandler 拡張** (line 1036 `_do_post_dispatch` + GET handler):
   - `/api/realtime/start` POST: **dig 決定 C**: Content-Length 0 / body 空 OK → default duration=300/interval=5。 body あれば JSON parse、 `_read_json_body` 流用。 validate (floor 5s / cap 3600s) NG なら 400 + `{"error": "..."}`、 OK なら `realtime_state.start_burst(time.time(), duration, interval)` → `diag.on_realtime_burst_started()` → 200 + status JSON 返却
   - `/api/realtime/stop` POST: `was_active = realtime_state.stop_burst()` → was_active なら `diag.on_realtime_burst_aborted()` (即時)、 catch-up trigger は main loop が次 tick で transition='aborted' を観測して行う。 200 + status JSON
   - `/api/realtime/status` GET: `realtime_state.snapshot()` + `remaining_sec = max(0, expires_at - now)` (mode=burst 時のみ) + **dig 決定 H**: `diag.realtime_effective_interval_seconds` を読んで `effective_interval_seconds` field に含める (catch-up 中も正確) → JSON `{"mode": ..., "expires_at": ..., "remaining_sec": ..., "burst_interval": ..., "effective_interval_seconds": ...}`

7. **Main loop 配線** (line 3360-3525):
   - main 関数内で `realtime_state = RealtimeModeState()` 生成、 AdminServer に渡す
   - 既存 `normal_cycle_count` の隣に `catchup_remaining = 0`、 `catchup_interval = poll_interval` (= base) 初期化
   - 毎 iter 冒頭 (dig 決定 A + B + D 反映):
     ```python
     now = time.time()
     mode, transition = realtime_state.tick(now)
     if transition == "expired":
         diag.on_realtime_burst_completed()
         catchup_remaining = 4
         catchup_interval = realtime_state.snapshot().get("burst_interval", 5)
     elif transition == "aborted":
         # diag.on_realtime_burst_aborted() は admin API thread が即時呼出済
         catchup_remaining = 4
         catchup_interval = realtime_state.snapshot().get("burst_interval", 5)
     snap = realtime_state.snapshot()
     # dig 決定 D: catch-up 中に新規 burst start が来たら catch-up クリア
     if mode == "burst" and catchup_remaining > 0:
         catchup_remaining = 0
     # eff_interval 計算: catch-up 中は catchup_interval を優先、 burst 中は snap、 off は base
     if catchup_remaining > 0:
         eff_interval = catchup_interval
     else:
         eff_interval = compute_effective_poll_interval(now, poll_interval, snap)
     diag.set_realtime_state(mode, eff_interval)
     ```
   - tier decide 部 (line 3520):
     ```python
     if catchup_remaining > 0:
         tier = ["tier4","tier3","tier2","tier1"][4 - catchup_remaining]
         catchup_remaining -= 1
     elif mode == "burst":
         tier = "tier1"
         # normal_cycle_count は進めない
     else:
         tier = decide_epc_tier(normal_cycle_count, ...)
         normal_cycle_count += 1
     ```
   - sleep 部 (line 3508): `time.sleep(compute_next_poll_sleep(last_poll_start, time.time(), eff_interval))`
   - **EEDSCAN skip (dig 決定 E)**: 既存 EEDSCAN cycle 判定箇所 (実装時に grep で確認、 candidate: spec 010 起因の `eedscan_cycle_every` 周辺) に `if mode == "burst": skip` を追加。 catch-up 中は skip しない

8. **Admin UI HTML** (line ~567-1863 の ADMIN_HTML 定数):
   - 既存 wifi-form 周辺に新 section:
     ```html
     <section><h3>リアルタイム表示</h3>
     <button id="realtime-start-btn">5 分間 burst 開始</button>
     <button id="realtime-stop-btn">停止</button>
     <div id="realtime-status">mode: off</div></section>
     ```
   - JS: fetch POST /api/realtime/start → updateRealtimeStatus、 setInterval で 3 秒毎 status refresh

### `tests/unit/test_compute_effective_poll_interval.py` (新規)

- `test_off_mode_returns_base`
- `test_burst_active_returns_burst_interval`
- `test_burst_expired_returns_base` (now >= expires_at)
- `test_burst_expires_at_none_returns_base` (safety)
- `test_burst_interval_overrides_base`

### `tests/unit/test_realtime_mode_state.py` (新規)

dig 決定 B (`tick` 戻り値 = `(mode, transition)`) + D (重複 start 上書き) に整合させた test set:

- `test_initial_mode_is_off_transition_none`
- `test_start_burst_sets_mode_and_expires_at`
- `test_tick_before_expires_returns_burst_none` (= `(mode='burst', transition=None)`)
- `test_tick_at_or_after_expires_returns_off_expired` (= `(mode='off', transition='expired')`)
- `test_tick_only_once_emits_expired_transition` (idempotent: 2 回目以降 `(mode='off', transition=None)`)
- `test_stop_burst_returns_was_active_true` (burst 中に stop、 戻り値 True)
- `test_stop_burst_when_off_returns_false_no_transition`
- `test_stop_burst_then_tick_returns_aborted_transition` (= `(mode='off', transition='aborted')`)
- `test_aborted_transition_emitted_only_once` (= 次 tick は `(mode='off', transition=None)`)
- `test_snapshot_returns_dict_with_mode_expires_at_burst_interval`
- `test_start_burst_during_burst_overrides_expires_at` (dig 決定 D: 上書き)
- `test_start_burst_during_burst_clears_pending_abort` (= stop で `_pending_abort=True` の状態から再 start すると、 次 tick で `(mode='burst', transition=None)` を返す)

### `tests/unit/test_diag_state.py` (拡張)

- baseline test expected dict に 5 新 key 追加 (None / 0 / "off")
- `test_on_realtime_burst_started_increments`
- `test_set_realtime_state_updates_gauge`

### main loop 配線

mock 過大なので unit test せず、 実機検証 (memory `feedback-tdd-spec-template.md` 確立方針)

### Admin API endpoint

既存 admin endpoint も unit test されていない、 実機検証で十分

### Admin UI

実機 browser 検証

### Edge case 取り扱い (dig 派生)

- **Wi-SUN reconnect 中の burst**: mode='burst' のまま、 expires_at は absolute time で経過 → 例: 30s reconnect で 5 分 burst が実質 4 分 30 秒に短縮。 これは設計仕様 (シンプル優先)
- **EVENT 32 (BP35CX duty cycle 制限) が burst 中に出た場合の v1 挙動**: v1 はホスト側 auto-abort なし、 既存挙動 = log のみ + 次 SKSENDTO 失敗 → 既存 ER10 / retry / reconnect path に乗る。 v2 で auto-abort 追加検討
- **catch-up 中の EEDSCAN**: skip しない (catch-up は 20s で完了、 EEDSCAN 占有 数秒は許容)
- **`/api/realtime/status` を burst 期間外 (mode='off') に GET**: `remaining_sec=null`、 `expires_at=null`、 `effective_interval_seconds` は diag gauge から base (60s) を読む
- **bridge 起動直後 (DiagState gauge 未初期化)**: `realtime_effective_interval_seconds` 初期値は `None` → status response で null、 main loop が 1 iter 回った後で gauge セット

## Test list (TDD 順)

1-5. **Red→Green**: `compute_effective_poll_interval` 5 件 (pure helper)
6-17. **Red→Green**: `RealtimeModeState` 12 件 (lock-protected state、 transition path 含む)
18-20. **Red→Green**: DiagState 3 件 (拡張 + baseline 更新)
21. main loop 配線 + AdminHandler + UI + EEDSCAN skip (テストせず、 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 ~389 + 新規 ~18 = ~407 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy
4. 実機で:
   - Admin UI でボタン押下 → 5 分間 `cubej/cubej1/power` topic が 5 秒間隔で更新されることを mqtt subscribe で確認
   - 5 分経過後 60s に自動復帰、 catch-up で tier2 (0xE0/0xE3) と tier4 (0xEA/0xEB) が即 publish
   - `/api/diag` snapshot で realtime_burst_started_total = 1, completed_total = 1 確認
5. Grafana で `cube_j1_smart_meter_power_w` の sparkline が burst 期間中だけ滑らかであることを 1 日後確認

## Commit 戦略

- 前セッションから spec 020 spec.md が @ に残っている → /tmp/spec020-stash/ に backup → rm specs/020-...
- spec 022 commit (spec.md + plan + 実装 + tests)
- /tmp から spec 020 spec.md を mv 戻し (= 次 @ に置く、 次セッションで実装に同梱)
- redact-plans.sh で plan ファイル機密除去
- jj git push --remote fork --bookmark main (forward only)

## Commit message

`feat(bridge): 瞬時電力 burst mode で 5 秒間隔リアルタイム更新 (spec 022 v1)`
