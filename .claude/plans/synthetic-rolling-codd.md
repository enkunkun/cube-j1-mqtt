# Plan: spec 029 Cumulative Energy Recovery Backfill (= spec 020 v1.5 可視化完成)

## Context

2026-06-25 user 指摘: 「救済率 (= panel-81) 90%+ 上がってるのに panel-1 で見れない、 telegraf consumer 未 subscribe を含めて実装なのでは = 片手落ち」。

spec 020 v1.5 で TID mismatch 救済率 90%+ 達成、 累積系 `_late_publish_ts` topic 過去時刻 retain publish するが telegraf 未 subscribe で grafana に来ない (= 設計 split 上の見送り、 memory [[feedback-compose-telegraf-pipeline]] の 「bridge + compose 両方修正必須」 ルール違反)。

spec 028 で瞬時系 (= `power_w`) を別 topic `_recovered_json` JSON + telegraf JSON parser + prometheus client-supplied timestamp backfill (= grafana cloud 2h 受理) する完全可視化経路を確立済。 spec 029 はこの同一 pattern を累積系 (= `energy_*_kwh`) に適用、 spec 020 v1.5 可視化を完成させる。

完成後: grafana panel-10 Cumulative Energy に refId=B/C `_recovered` series overlay = 累積系 救済点が過去時刻 plot、 panel-80 で累積系 backfill 発火 rate も可視化 (= panel-80 + 81 + 82 で救済 dashboard 完成)。

## Approach

### v1 MVP の構成

1. **`publish_recovery_backfill` ヘルパーを generic 化**: `counter_attr` 引数追加で increment 対象 counter を caller 指定、 spec 028 既存 caller は default 引数で互換 (= breaking change なし)
2. **`CUMULATIVE_BACKFILL_KEYS` 定数追加** (= 4 keys): `energy_forward_kwh`, `energy_reverse_kwh`, `energy_forward_fixed_kwh`, `energy_reverse_fixed_kwh`
3. **main loop 配線**: spec 028 backfill 分岐の **直後** に spec 029 累積系 backfill (= `cumulative_recovery_backfill_enabled` gate)
4. **DiagState 拡張**: `cumulative_recovered_backfill_total` counter (= snapshot key + DIAG_SENSOR_DEFS 登録)
5. **`apply_defaults`**: `cumulative_recovery_backfill_enabled = True`
6. **telegraf**: 既存 spec 028 mqtt_recovery_backfill consumer の topics + starlark SUFFIX dict 拡張 (= consumer / processor 新規ではなく既存拡張で済む = 軽量)、 diag-num consumer に新 counter topic 追加
7. **grafana**: panel-10 Cumulative Energy に refId=B/C `_recovered` overlay

### 設計上の判断 (dig round 1 で確定済)

- **既存 helper generic 化 (= `counter_attr` 引数)**: 2 helper 並列より 1 helper + 引数で extensibility 高、 spec 028 既存 9 test が default 引数で互換 pass
- **counter 別 metric**: `power_w_recovered_backfill_total` 既存維持 + `cumulative_recovered_backfill_total` 新規 = 「瞬時系 vs 累積系」 の発火頻度を別 panel で観察可能、 rename しない
- **既存 `_late_publish_ts` retain topic は触らない**: spec 020 v1.5 既存挙動完全保護、 新経路 spec 029 と並列存在 (= broker に「最後の救済時刻」 retain は util)
- **`_fixed_kwh_recovered` も bridge 側 publish 対象** (= 構造的に reconnect 直後 cycle 0 = tier4 で必ず mismatch + `m["*_fixed_kwh"]` 含む → spec 029 で初めて可視化 = bonus 効果)
- **dig A 決定 — Step 0 config 事前確認**: 実装着手 step 0 で lab-ub01 経由 SSH で `cumulative_recovery_backfill_enabled` explicit override 有無確認、 spec 027/028 と同型の落とし穴前倒し回避 (= [[feedback-config-setdefault-override]] 教訓)
- **dig B 決定 — rate panel**: **新 panel-82 追加** で `cumulative_recovered_backfill_total` rate /5m を別 visualize、 panel-80 (= spec 028 `power_w`) は維持 = 「瞬時系 vs 累積系」 比較容易、 将来 0xE8 拡張で panel-83 と整合
- **dig C 決定 — panel-10 overlay**: **forward + reverse の 2 series のみ** (= `energy_forward_kwh_recovered` + `energy_reverse_kwh_recovered`)、 `_fixed_kwh` 系は bridge 側 publish するが panel overlay は skip (= panel-10 内 series 5+2=7 で視認性維持、 spec 018 meter timestamp で別経路カバー)

## Step 0: 着手前確認 (= dig A 決定)

```bash
ssh lab-ub01 'adb shell cat /data/local/config.json' \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); print("cumulative_recovery_backfill_enabled =", c.get("cumulative_recovery_backfill_enabled", "<not set>"))'
```

判定:
- `<not set>` (= 期待値、 新規 key): apply_defaults 新 default True がそのまま効く → OK 着手
- 数値 (= 想定外): explicit override 削除 commit を deploy 前に挟む

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **`CUMULATIVE_BACKFILL_KEYS` 定数** (= `RECOVERY_BACKFILL_KEYS` 直下):
   ```python
   CUMULATIVE_BACKFILL_KEYS = frozenset((
       "energy_forward_kwh", "energy_reverse_kwh",
       "energy_forward_fixed_kwh", "energy_reverse_fixed_kwh",
   ))
   ```

2. **`publish_recovery_backfill` 拡張** (= 既存 helper):
   ```python
   def publish_recovery_backfill(mqtt, device_id, m, send_ts, diag_state=None,
                                 counter_attr="power_w_recovered_backfill_total"):
       """spec 028/029: 救済 frame の値を <key>_recovered_json topic に JSON publish.
       counter_attr で increment 対象 DiagState attribute を caller 指定。
       """
       base = "cubej/{}".format(device_id)
       ts_iso = format_iso8601_utc(send_ts)
       for key, value in m.items():
           if value is None:
               continue
           topic = "{}/{}_recovered_json".format(base, key)
           payload = json.dumps({"value": value, "ts": ts_iso})
           mqtt.publish(topic, payload, qos=0, retain=False)
           if diag_state is not None:
               setattr(diag_state, counter_attr,
                       getattr(diag_state, counter_attr, 0) + 1)
   ```

3. **main loop 配線** (= `_late_ts is not None` 分岐内、 spec 028 後):
   ```python
   if cfg.get("power_w_recovery_backfill_enabled", True):
       _m_bf = dict((k, v) for k, v in m.items() if k in RECOVERY_BACKFILL_KEYS)
       if _m_bf:
           publish_recovery_backfill(mqtt, device_id, _m_bf, _late_ts, diag_state)
   # spec 029: 累積系 backfill
   if cfg.get("cumulative_recovery_backfill_enabled", True):
       _m_cum_bf = dict(
           (k, v) for k, v in m.items()
           if k in CUMULATIVE_BACKFILL_KEYS)
       if _m_cum_bf:
           publish_recovery_backfill(
               mqtt, device_id, _m_cum_bf, _late_ts, diag_state,
               counter_attr="cumulative_recovered_backfill_total")
   ```

4. **DiagState 拡張**:
   - `__init__`: `self.cumulative_recovered_backfill_total = 0`
   - `_DIAG_SNAPSHOT_KEYS`: `power_w_recovered_backfill_total` の隣に追加
   - snapshot raw dict: 同様

5. **`DIAG_SENSOR_DEFS`**:
   ```python
   ("cumulative_recovered_backfill_total", "Cumulative Recovered Backfill",
    None, None, "total_increasing", "diagnostic"),
   ```

6. **`apply_defaults`**:
   ```python
   out.setdefault("cumulative_recovery_backfill_enabled", True)  # spec 029
   ```

### `tests/unit/test_publish_recovery_backfill.py` (拡張)

- `test_default_counter_attr_is_power_w_for_spec028_compat` (= 既存 caller 互換確認)
- `test_counter_attr_argument_increments_specified_attribute` (= cumulative_recovered_backfill_total 指定)
- `test_counter_attr_missing_attribute_initializes_to_1` (= `setattr/getattr` の default 0 → +1)

### `tests/unit/test_apply_defaults_spec_028.py` (拡張、 spec 029 default も)

- `test_default_cumulative_recovery_backfill_enabled_is_true`
- `test_cumulative_explicit_override_preserved`

### `tests/unit/test_diag_state.py` (拡張)

- baseline test の expected dict に `cumulative_recovered_backfill_total: 0` 追加
- `test_cumulative_recovered_backfill_total_reflected_in_snapshot`

### main loop 配線 + telegraf + grafana (= 実機検証)

- 配線は実機 deploy で SC-006 確認

### `compose/telegraf/telegraf.conf`

既存 `mqtt_recovery_backfill` consumer の `topics` 拡張:
```toml
topics = [
  "cubej/+/power_w_recovered_json",
  "cubej/+/energy_forward_kwh_recovered_json",
  "cubej/+/energy_reverse_kwh_recovered_json",
  "cubej/+/energy_forward_fixed_kwh_recovered_json",
  "cubej/+/energy_reverse_fixed_kwh_recovered_json",
]
```

既存 starlark SUFFIX dict 拡張:
```python
SUFFIX = {
    "power_w_recovered_json": "power_watts_recovered",
    "energy_forward_kwh_recovered_json": "energy_forward_kwh_recovered",
    "energy_reverse_kwh_recovered_json": "energy_reverse_kwh_recovered",
    "energy_forward_fixed_kwh_recovered_json": "energy_forward_fixed_kwh_recovered",
    "energy_reverse_fixed_kwh_recovered_json": "energy_reverse_fixed_kwh_recovered",
}
```

既存 diag-num consumer の topics に追加:
```toml
"cubej/+/diag/cumulative_recovered_backfill_total",
```

### grafana dashboard

- `gcx --context cloud dashboards get cubej1-smart-meter | tail -1 > /tmp/dash.json`
- patch script で panel-10 に refId=B/C 追加:
  - B: `cube_j1_smart_meter_energy_forward_kwh_recovered{device_id=~"$device"}` legend="Recovered (forward)"
  - C: `cube_j1_smart_meter_energy_reverse_kwh_recovered{device_id=~"$device"}` legend="Recovered (reverse)"
- **新 panel-82** 追加 (= dig B 決定): `increase(cube_j1_smart_meter_cumulative_recovered_backfill_total{device_id="cubej1"}[5m])` rate、 title "Cumulative Recovery Backfill (spec 029, rate /5m)"、 panel-80 と同 row (= "Recovery & Realtime"、 y=24 で 4 段目 12x8) に並べる

## Test list (TDD 順)

1. **Red→Green**: `test_publish_recovery_backfill` 拡張 3 件 (= helper generic 化)
2. **Red→Green**: `test_apply_defaults_spec_028` 拡張 2 件 (= cumulative_recovery_backfill_enabled)
3. **Red→Green**: `test_diag_state` 拡張 2 件 (= cumulative counter baseline + snapshot)
4. main loop 配線 + telegraf + grafana (= 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 452 + 新規 ~7 = ~459 件 pass
2. ruff check 新規エラー無し
3. **compose 側を先に commit + jj 5 step push** (= dig E 決定踏襲、 telegraf を bridge より先に準備)
4. compose deploy-webhook で telegraf restart 完了待ち
5. cube-j1-mqtt 側 commit + jj push (= main fork forward only)
6. lab-ub01 経由 deploy (= scripts/adb_push_update.sh cube-j1.home.arpa)
7. dashboard panel-10 patch script で refId=B/C 追加、 新 panel-82 (= cumulative backfill rate) も追加
8. 実機 1h 観察:
   - `/api/diag` で `cumulative_recovered_backfill_total > 0` (= tier2/4 cycle で必ず発火、 spec 028 と違って構造的に hit する!)
   - `mosquitto_sub -h 192.168.1.151 -t 'cubej/cubej1/energy_forward_kwh_recovered_json'` で JSON 着信確認
   - `gcx --context cloud metrics query 'cube_j1_smart_meter_energy_forward_kwh_recovered'` で過去時刻 sample 確認
   - grafana panel-10 で Recovered (forward) / Recovered (reverse) 点プロット観察
9. SC-007 (= 既存全テスト pass) 確認

## Commit 戦略

- spec 028 と同じ pattern: compose 側 1 commit + cube-j1-mqtt 側 1 commit (= bg subagent 並列)
- redact-plans.sh
- jj git push --remote fork --bookmark main
- compose は jj 5 step (= fetch / log / rebase / set / push)

## Commit message

- compose: `feat(Telegraf): cube-j1 spec 029 累積系 backfill JSON consumer topics + SUFFIX dict 拡張`
- cube-j1-mqtt: `feat(bridge): 累積系 (energy_*_kwh) 救済 frame backfill 経路追加、 publish_recovery_backfill generic 化 (spec 029)`
