# Plan: spec 016 HA Discovery Auto-Republish

## Context

HA の MQTT discovery は broker の retained store に依存する。 当方 bridge は起動時に `publish_ha_discovery()` を 1 度しか呼ばないため、 Mosquitto を `docker compose down -v && up -d` 等で再構築すると retained が消えて HA からセンサーが消失する。 復旧には bridge プロセスの手動再起動が要る。 hals5412 fork `a3c3637` の発想を取り込み、 **MQTT 再接続時 + 24h 周期** で自動再 publish する。

新規スレッドは増やさない (main loop tick で十分な精度) ことで運用追跡コストを抑える。

## Approach

- pure helper `should_republish_discovery(now, last_ts, pending, interval_sec)` を抽出して timer 判定を test 可能化
- DiagState に「pending flag」「last publish ts」「republish counter」を追加
- 既存 `on_mqtt_reconnect()` を minimal 拡張: counter inc に加えて pending flag セット
- main loop の cycle 入口で helper を call、 true なら `publish_ha_discovery()` + `on_discovery_republish()`
- config キー `discovery_republish_interval_sec` (default 86400、 0 で周期無効化) を追加

### Thread safety (Round 2 注記)

`pending_discovery_republish` (bool) は `on_mqtt_reconnect()` (MQTTClient sender/keepalive スレッド) と main loop スレッドの両方から触る。 GIL 下で bool への単純な代入は atomic で、 spec 011 の `consecutive_erxudp_timeouts` (int) も同じ構造で Lock なし運用しているため踏襲する。 確実な mutex は不要、 「次 cycle までに値が反映される」程度の semantics で問題ない設計。

### `on_mqtt_reconnect` の発火タイミング (Round 2 注記)

Explore 結果では `MQTTClient._reconnect_socket_unlocked()` から呼ばれる。 initial connect 時にも呼ばれるかは曖昧だが、 `mark_initial_discovery_publish()` が pending を **強制クリア** するため、 「初回 connect で pending=True がセット → mark_initial でクリア → main loop tick で publish 不要」「あるいは初回 connect では呼ばれない → pending=False のまま、 同じく publish 不要」のどちらの挙動でも plan は正しく動く。 defensive design で挙動分岐を吸収。

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **pure helper** (line 2200 付近、 `decide_epc_tier` の隣):
   ```python
   def should_republish_discovery(now, last_publish_ts, pending, interval_sec):
       """spec 016: True iff a reconnect is pending, the interval has
       elapsed, or no publish has ever been recorded.

       interval_sec <= 0 disables periodic republish (reconnect / first
       publish still trigger). last_publish_ts=None is treated as "never
       published" → True so callers without an initialised ts get a publish
       on the first tick (defensive; main loop seeds the ts at startup so
       this path normally does not fire).
       """
       if pending:
           return True
       if last_publish_ts is None:
           return True
       if interval_sec <= 0:
           return False
       return (now - last_publish_ts) >= interval_sec
   ```
   (Round 1 決定 1: None ガード defensive。 main loop 経路では実発生させない)

2. **`apply_defaults`** (line 101 付近、 mqtt_* セクション):
   ```python
   out.setdefault("discovery_republish_interval_sec", 86400)
   ```

3. **`DiagState.__init__`** (line 1810 付近):
   ```python
   # spec 016: HA discovery auto-republish state.
   self.last_discovery_publish_ts = None
   self.pending_discovery_republish = False
   self.discovery_republish_total = 0
   ```

4. **`DiagState.on_mqtt_reconnect`** (line 1843 付近、 既存メソッドを拡張):
   ```python
   def on_mqtt_reconnect(self):
       self.mqtt_reconnects_total += 1
       self.pending_discovery_republish = True   # spec 016: trigger republish
   ```

5. **新メソッド 2 本** (line 1858 付近) (Round 1 決定 2: counter は再 publish のみ計上、 startup は ts だけ更新する):
   ```python
   def mark_initial_discovery_publish(self, now):
       """spec 016: startup publish — seed last_ts so the periodic check
       doesn't fire on the first main-loop tick. counter is NOT touched
       (counter only counts actual re-publishes, so SC-002 stays
       meaningful)."""
       self.last_discovery_publish_ts = float(now)
       self.pending_discovery_republish = False

   def on_discovery_republish(self, now):
       """spec 016: an actual re-publish ran (reconnect or interval)."""
       self.discovery_republish_total += 1
       self.last_discovery_publish_ts = float(now)
       self.pending_discovery_republish = False
   ```

6. **`_DIAG_SNAPSHOT_KEYS`** (line 1431 付近) に追加:
   ```python
   "last_discovery_publish_ts",
   "discovery_republish_total",
   ```
   (Round 1 決定 4: `pending_discovery_republish` は出さない — 内部実装詳細、 HA 側に不要な sensor を増やさない)

7. **snapshot `raw` dict** (line 1888 付近):
   ```python
   "last_discovery_publish_ts":
       format_iso8601_utc(self.last_discovery_publish_ts)
       if self.last_discovery_publish_ts is not None else None,
   "discovery_republish_total": self.discovery_republish_total,
   ```

8. **main loop** (line 3123 startup publish の直後 + 毎 cycle 入口):
   - 起動時の既存 `publish_ha_discovery(mqtt, device_id)` の直後に `diag_state.mark_initial_discovery_publish(time.time())` を呼ぶ (Round 1 決定 2: counter はインクリせず ts のみ初期化)
   - main loop の cycle 入口 (3128 行付近、 normal/probe 分岐の前) で:
     ```python
     # spec 016: HA discovery auto-republish on MQTT reconnect or every
     # discovery_republish_interval_sec.
     _now = time.time()
     if should_republish_discovery(
             _now, diag_state.last_discovery_publish_ts,
             diag_state.pending_discovery_republish,
             int(cfg.get("discovery_republish_interval_sec", 86400))):
         try:
             publish_ha_discovery(mqtt, device_id)
             diag_state.on_discovery_republish(_now)
         except Exception as e:
             # Round 1 決定 3: シンプル WARN のみ、 backoff なし。
             # publish は spec 005 の queue 経由なので broker 切断中も
             # fail-fast せず、 復旧時に flush される。 即 fail は queue
             # overflow 等の稀ケース、 次 cycle で自然リトライ
             log("WARN: HA discovery republish failed: {}".format(e))
             # pending は意図的にクリアしない → 次 cycle で再試行
     ```
   - 注: `_now` を 1 度キャプチャして helper と `on_discovery_republish` で共用 (sub-second 差防止 + 可読性)

### `tests/unit/test_discovery_republish.py` (新規)

pure helper の TDD:
- `test_returns_true_when_pending_flag_set`
- `test_returns_true_when_interval_elapsed`
- `test_returns_false_when_interval_not_elapsed_and_no_pending`
- `test_returns_false_when_interval_zero_disables_periodic`
- `test_pending_overrides_zero_interval_disable` (pending=True, interval=0 で True 返却)
- `test_none_last_ts_returns_true_as_first_publish` (Round 1 決定 1: None ガード)
- `test_pending_overrides_none_last_ts` (両条件 True なら True)

### `tests/unit/test_diag_state.py` (拡張)

baseline `test_initial_snapshot_includes_zero_counters_uptime_and_version` (現在は noise_adaptive_skips_total と erxudp_tid_mismatch_total を含む dict 比較) に追加:
- `"discovery_republish_total": 0` の expectation 行

新規テスト:
- `test_on_mqtt_reconnect_also_sets_pending_discovery_republish`
- `test_on_discovery_republish_increments_counter_clears_pending_updates_ts`
- `test_mark_initial_discovery_publish_seeds_ts_without_incrementing_counter` (Round 1 決定 2)
- `test_snapshot_includes_last_discovery_publish_ts_as_iso_when_set`
- `test_snapshot_omits_last_discovery_publish_ts_when_none`

## Test list (TDD 順)

1. **Red**: `test_returns_false_when_interval_not_elapsed_and_no_pending` → helper 未実装で AttributeError
2. (Green) helper を追加
3. **Red**: `test_returns_true_when_pending_flag_set`
4. **Red**: `test_returns_true_when_interval_elapsed`
5. **Red**: `test_returns_false_when_interval_zero_disables_periodic`
6. **Red**: `test_pending_overrides_zero_interval_disable`
7. **Red**: `test_on_mqtt_reconnect_also_sets_pending_discovery_republish` → DiagState 拡張
8. **Red**: `test_on_discovery_republish_increments_counter_clears_pending_updates_ts`
9. **Red**: `test_snapshot_includes_last_discovery_publish_ts_as_iso_when_set`
10. **Red**: `test_snapshot_omits_last_discovery_publish_ts_when_none`
11. **Red** (baseline update): `test_initial_snapshot_includes_zero_counters_uptime_and_version` に新キー追加
12. main loop integration はテストせず (mock 過大、 実機で確認)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 (現在 322) + 新規 ~7 = ~329 件すべて pass
2. ruff check で新ファイル + 改修ファイルが既存以上のエラー無し
3. 実機デプロイ (lab-ub01 経由) で `production_tool/mqtt_bridge.py` を更新
4. Cube J1 上 で `/api/diag` を curl して `discovery_republish_total: 0` と `last_discovery_publish_ts: "<ISO8601>"` が新しく見える
5. Mosquitto を `docker compose restart mosquitto` (or down/up) で再構築、 60 秒以内 (= 1 main loop tick) に HA からセンサーが復活、 `discovery_republish_total` が +1
6. 24h 連続稼働で `discovery_republish_total >= 1` (周期再 publish の証跡)

## Commit

`feat(bridge): HA discovery を MQTT 再接続+24h 周期で自動再 publish (spec 016)`

spec.md は impl と同じ commit に同梱 (既存 spec 013/014 と同じ運用)。 plan ファイル (本ファイル) も同梱。
