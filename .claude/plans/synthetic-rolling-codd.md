# Plan: spec 020 TID Mismatch Late Publish (Revived 2026-06-24)

## Context

2026-06-24 grafana dashboard snapshot (gcx cubej1-smart-meter --since 6h) + メトリクス解析で **真因確定**:
- 物理層完璧 (LQI=241、 EEDSCAN pan_channel_energy=13、 SK EVENT 24/29=0)
- **`erxudp_tid_mismatch_lag p50=5, p95=9, max=9 frame`** = メーター ECHONET 内部 queue が **9 周期 (= 9 分) 遅延蓄積**
- bridge 30s timeout で諦め → 次 cycle で新 TID で send → メーターは 9 周期前の TID で応答 → bridge は spec 014 で破棄 (= mismatch)
- 結果 60s 周期 polling の大半が timeout、 24h 78% 欠損

spec 020 で TID mismatch frame を「9 分前のメーター応答」 として late publish 救済すれば、 grafana の穴を直接補完可能。 一度 pivot 中止 (2026-06-23) したが、 burst が機能しない real env でこそ spec 020 が救済策と判明。

spec 020 spec.md (= specs/020-tid-mismatch-late-publish/spec.md) は前回 dig 結果が反映済 (= 累積系のみ late publish、 `_late_publish_ts` 別 topic, retain=True)。 plan を復元して TDD/deploy。

## Approach

### v1 MVP の構成

1. **`SendHistoryRing` class** (pure、 collections.OrderedDict ベース、 FIFO eviction)
   - `record(tid, send_ts, epc_list)`
   - `lookup(tid)` → `(send_ts, epc_tuple)` | None
2. **`read_erxudp` 戻り値拡張**: `bytearray | None` → `(payload, send_ts) | None`
   - signature に `send_history=None` 追加
   - TID mismatch path: ring lookup → hit なら `(payload, send_ts_A)` 返却 (= late frame)、 miss なら既存 discard
3. **main loop**:
   - `send_el_get` 直後に `history.record(sent_tid, time.time(), cycle_epcs)`
   - `read_erxudp(..., send_history=send_history)` で result unpack
   - `send_ts is not None` (= late frame) なら 累積系のみ filter → `publish_measurements(..., timestamp=send_ts)`
   - `diag.on_erxudp_recovered_from_mismatch(now - send_ts)` で counter + lag 更新
4. **`publish_measurements` 拡張**: `timestamp=None` 引数追加、 not None なら **累積系 key のみ** `_late_publish_ts` 別 topic (retain=True) 発行
5. **DiagState 拡張**:
   - `erxudp_recovered_from_mismatch_total` (counter)
   - `erxudp_recovered_lag_seconds_recent` (deque maxlen=100) + p50/p95/max emit
6. **Kill switch**: `tid_mismatch_recover_enabled` (default True)

### 設計上の判断 (前回 dig 反映)

- **dig 決定 A: 累積系のみ late publish**: `CUMULATIVE_PUBLISH_KEYS = frozenset(("energy_forward", "energy_reverse", "energy_forward_fixed_kwh", "energy_reverse_fixed_kwh", ...))`、 瞬時 (0xE7/E8) は HA グラフ歪みリスクで除外
- **dig 決定 B: `_late_publish_ts` 別 topic + retain=True**: spec 018 pattern 踏襲、 既存 value topic 形式は壊さない
- **filter 責務 = main loop**: late frame 時に `m` を CUMULATIVE_PUBLISH_KEYS のみで filter してから `publish_measurements(..., timestamp=send_ts)` 呼出
- **`SendHistoryRing` thread-safety**: main loop only、 lock 不要
- **read_erxudp 戻り値 tuple 化の callers**: main loop 2 箇所 (= 初回 + retry ループ)、 unpack 修正で互換

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **`SendHistoryRing` class** (DiagState 周辺、 RealtimeModeState の隣):
   ```python
   class SendHistoryRing(object):
       """spec 020: TID → (send_ts, epc_tuple) bounded FIFO ring buffer.
       Main loop only — no thread safety required."""
       def __init__(self, maxlen=10):
           self._maxlen = int(maxlen)
           self._entries = collections.OrderedDict()
       def record(self, tid, send_ts, epc_list):
           if tid in self._entries:
               del self._entries[tid]
           self._entries[tid] = (float(send_ts), tuple(epc_list))
           while len(self._entries) > self._maxlen:
               self._entries.popitem(last=False)
       def lookup(self, tid):
           return self._entries.get(tid)
       def __len__(self):
           return len(self._entries)
   ```

2. **`read_erxudp` 拡張** (signature + TID mismatch path):
   - signature に `send_history=None` 追加
   - 戻り値全 path で tuple 化: `return (payload, None)` (= 正常) / `return (payload, send_ts_A)` (= late) / `return None` (= no data)
   - TID mismatch path:
     ```python
     if expected_tid is not None and tid != expected_tid:
         if diag_state is not None:
             diag_state.on_erxudp_tid_mismatch(expected_tid, tid)
         if send_history is not None:
             hit = send_history.lookup(tid)
             if hit is not None:
                 send_ts_A, _epcs = hit
                 return (payload, send_ts_A)
         continue  # discard (既存 spec 014)
     ```

3. **`CUMULATIVE_PUBLISH_KEYS`** 定数追加 (publish_measurements 周辺):
   ```python
   CUMULATIVE_PUBLISH_KEYS = frozenset((
       "energy_forward", "energy_reverse",
       "energy_forward_fixed_kwh", "energy_reverse_fixed_kwh",
       "energy_forward_fixed_ts", "energy_reverse_fixed_ts",
       "energy_forward_fixed_raw", "energy_reverse_fixed_raw",
   ))
   ```

4. **`publish_measurements` 拡張** (optional `timestamp=None`):
   ```python
   def publish_measurements(mqtt, device_id, m, timestamp=None):
       # 既存 value topic publish (変更なし)
       ...
       # spec 020: late frame の timestamp を `_late_publish_ts` topic で発行
       if timestamp is not None:
           ts_iso = format_iso8601_utc(timestamp)
           base = "{}/{}".format(MQTT_TOPIC_PREFIX, device_id)
           for key in m.keys():
               topic = "{}/{}_late_publish_ts".format(base, key)
               mqtt.publish(topic, ts_iso, retain=True)
   ```

5. **DiagState 拡張**:
   - `__init__`: `self.erxudp_recovered_from_mismatch_total = 0`、 `self.erxudp_recovered_lag_seconds_recent = collections.deque(maxlen=100)`
   - method: `on_erxudp_recovered_from_mismatch(self, lag_sec)`:
     ```python
     self.erxudp_recovered_from_mismatch_total += 1
     self.erxudp_recovered_lag_seconds_recent.append(float(lag_sec))
     ```
   - `_DIAG_SNAPSHOT_KEYS` + raw dict に `erxudp_recovered_from_mismatch_total` 追加
   - snapshot 末尾の deque percentile セクションに `erxudp_recovered_lag_seconds_recent` 追加 (spec 011 follow-up 2 と同 pattern、 空時 omit)

6. **`apply_defaults`**: `out.setdefault("tid_mismatch_recover_enabled", True)`

7. **main 関数で SendHistoryRing 生成** (state init 周辺):
   ```python
   send_history = SendHistoryRing(maxlen=int(cfg.get(
       "tid_mismatch_history_maxlen", 10)))
   ```

8. **main loop 配線**:
   - `send_el_get` 直後 (= 初回 + retry 両方):
     ```python
     send_el_get(fd, ipv6, sent_tid, epc_list=cycle_epcs)
     if cfg.get("tid_mismatch_recover_enabled", True):
         send_history.record(sent_tid, time.time(), cycle_epcs)
     ```
   - `read_erxudp` callers (2 箇所) を tuple unpack に変更:
     ```python
     result = read_erxudp(fd, timeout=_erxudp_timeout,
                          diag_state=diag_state,
                          expected_tid=sent_tid,
                          send_history=send_history
                                       if cfg.get("tid_mismatch_recover_enabled", True)
                                       else None)
     if result is None:
         data, send_ts = None, None
     else:
         data, send_ts = result
     ```
   - publish chain で send_ts 分岐:
     ```python
     m = decode_measurements(props)
     m = apply_energy_scale(m, coeff, unit_kwh)
     if send_ts is not None:
         diag_state.on_erxudp_recovered_from_mismatch(time.time() - send_ts)
         m_late = {k: v for k, v in m.items() if k in CUMULATIVE_PUBLISH_KEYS}
         if m_late:
             publish_measurements(mqtt, device_id, m_late, timestamp=send_ts)
     else:
         publish_measurements(mqtt, device_id, m)
     ```

### `tests/unit/test_send_history_ring.py` (新規)

- `test_initial_len_is_zero`
- `test_record_stores_send_ts_and_epcs`
- `test_lookup_returns_recorded_entry`
- `test_lookup_returns_none_for_unknown_tid`
- `test_eviction_keeps_only_maxlen_entries`
- `test_record_same_tid_refreshes_and_does_not_evict_others`
- `test_record_overwrites_send_ts_for_same_tid`

### `tests/unit/test_diag_state.py` (拡張)

- baseline test expected dict に `erxudp_recovered_from_mismatch_total: 0` 追加
- `test_on_erxudp_recovered_from_mismatch_increments`
- `test_recovered_lag_percentiles_omitted_when_empty`
- `test_recovered_lag_percentiles_emitted_when_filled`

### main loop / read_erxudp / publish_measurements 配線

実機検証 (= memory `feedback-tdd-spec-template.md` の確立方針)

## Test list (TDD 順)

1-7. **Red→Green**: `SendHistoryRing` 7 件 (pure class)
8-11. **Red→Green**: DiagState 4 件 (拡張 + baseline 更新)
12. read_erxudp / main loop / publish_measurements / apply_defaults (実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 ~429 + 新規 ~11 = ~440 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy
4. 実機 1-2h で:
   - `/api/diag` で `erxudp_recovered_from_mismatch_total > 0` (= late publish 発火確認)
   - `erxudp_recovered_lag_seconds_recent` percentile が 60-600s 範囲 (= 1-10 周期遅延)
   - mosquitto_sub で `cubej/cubej1/energy_forward_late_publish_ts` topic 着信確認
   - grafana で `cube_j1_smart_meter_energy_forward_kwh_total` の穴がスムーズに塗られる
5. SC-003 (1 週間運用で `erxudp_recovered_from_mismatch_total > 0`) 達成

## Commit 戦略

- @ に spec 020 spec.md (Status revived) のみ、 stash 不要
- redact-plans.sh
- jj commit (spec.md + plan + 実装 + tests)
- jj git push --remote fork --bookmark main (forward only)
- lab-ub01 経由 deploy

## Commit message

`feat(bridge): TID mismatch frame を ring buffer 経由で late publish 救済 (spec 020)`
