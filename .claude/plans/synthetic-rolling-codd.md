# Plan: spec 028 Instantaneous Power Recovery (Backfill into Past Timestamps)

## Context

2026-06-24 spec 020 v1.5 deploy 後の grafana 観測で:
- `recovered_from_mismatch_total / tid_mismatch_total = 65 / 72 ≈ 90.3%` 達成
- `recovered_lag p50=1s, p95=60s, max=240s` = got_tid=0 fallback (= 直近 send 応答) 主経路 + 真の delayed past response が共存

ユーザ要望:
1. **救済込みで見れる Instantaneous Power**: 現状 spec 020 v1.5 は累積系のみ late publish (= `CUMULATIVE_PUBLISH_KEYS`)、 瞬時 0xE7 (`power_w`) は HA グラフ歪み回避で除外。 grafana 上では「**穴を power_w_recovered 別 series で本来時刻 backfill**」 すれば歪まず可視化できる。
2. **深い遅延も in-memory で吸収**: ring `maxlen=10` (= 約 5-10 分) では残り 10% mismatch を救えない。 maxlen 拡大は memory コスト無視できる (= 240 entry = 数 KB)、 grafana cloud の **2h backfill 制限** が事実上の上限 (= memory `reference-prometheus-remote-write-backfill`)。

設計起点: Prometheus remote_write は client-supplied timestamp で過去時刻 sample 投入可、 grafana cloud は 2h 以内なら受理。 telegraf JSON parser に `json_time_key` 指定で metric.time に自動 set → `outputs.http data_format = prometheusremotewrite` で client timestamp として送信。

## Approach

### v1 MVP の構成

1. **ring `maxlen` 拡張**: `tid_mismatch_history_maxlen` default `10 → 240` (`apply_defaults` 1 行変更)。 spec 020 v1.5 既存 `lookup` / `lookup_latest` ロジックは変更なし、 maxlen 拡大で「深い遅延も lookup hit する」 確率を上げる。
2. **`RECOVERY_BACKFILL_KEYS = frozenset(("power_w",))`** 定数追加 (= 将来 `current_a_*` 拡張余地)。
3. **`publish_recovery_backfill(mqtt, device_id, m, send_ts, diag_state)`** 新ヘルパー:
   - topic = `cubej/<id>/<key>_recovered_json`
   - payload = `{"value": <numeric>, "ts": "<ISO8601 UTC>"}`
   - retain=False, qos=0
   - `diag_state.power_w_recovered_backfill_total` を内部で increment
4. **main loop late publish 配線**: 既存 `_late_ts` 分岐 (= 行 4124-4135) の中で、 累積系 publish の **後ろに** backfill 系 publish を追加 (= `power_w_recovery_backfill_enabled` で gate)。
5. **DiagState 拡張**: `power_w_recovered_backfill_total` counter (= snapshot key + DIAG_SENSOR_DEFS 登録)。
6. **`apply_defaults`**: `tid_mismatch_history_maxlen=240`、 `power_w_recovery_backfill_enabled=True`。
7. **telegraf**: 新 `[[inputs.mqtt_consumer]] client_id="telegraf-cubej-recovery-backfill"` (= JSON parser、 `json_time_key=ts`) + starlark processor (= `value` field → `power_watts_recovered` rename)。 既存 diag-num consumer の topics に `cubej/+/diag/power_w_recovered_backfill_total` 追加。
8. **grafana**: 既存 `Instantaneous Power` panel (= panel id 不明、 dashboard get で確認) に series B `cube_j1_smart_meter_power_watts_recovered{device_id=~"$device"}` overlay 追加。

### 設計上の判断 (dig round 1 で確定済)

- **dig 既存決定踏襲**: 「`_recovered_json` 別 topic + retain=False」 (= spec 020 dig 結果、 累積系の `_late_publish_ts` と並列の概念)
- **HA 経路は触らない**: `power_w` topic (= 既存) は通常 publish のみ、 backfill は別 topic、 HA は新 topic を subscribe しない (= discovery 経由でない) ので影響なし
- **JSON 形式採用理由**: telegraf JSON parser が `json_time_key` で metric.time に自動変換、 starlark で字面操作する必要なし
- **filter 責務 = main loop**: `m` を `RECOVERY_BACKFILL_KEYS` でフィルタしてから `publish_recovery_backfill(..., send_ts)` に渡す (= 累積系 filter と並列)
- **ring maxlen=240 の memory cost**: TID (2B) + send_ts (8B float) + epc_tuple (= 10B 程度) per entry × 240 = ~5 KB、 cube_j1 J1 (= 128MB+) には無視できる
- **kill switch を 2 つ用意**: `power_w_recovery_backfill_enabled` (= 経路 ON/OFF)、 `tid_mismatch_history_maxlen` (= ring size、 10 に戻せば旧挙動)
- **dig A 決定 — ts 精度**: **秒精度 ISO8601 UTC** (= `2026-06-24T01:30:00Z`)。 telegraf default parse format `2006-01-02T15:04:05Z07:00` と一致、 spec 020 v1.5 `_late_publish_ts` topic 既存 format と整合。 millisecond は捨てる (= 60s 周期 polling では実用上問題なし)
- **dig B 決定 — grafana panel**: **既存 `Instantaneous Power` panel に series B overlay**。 query A = `cube_j1_smart_meter_power_watts`、 query B = `cube_j1_smart_meter_power_watts_recovered`、 legend B = `Recovered` (色を変える)。 sparkline 上で穴の位置に補完点プロット
- **dig C 決定 — config 事前確認**: 実装着手の **最初の step として** lab-ub01 経由 SSH で `/data/local/tmp/cube_j1_mqtt/config.json` の `tid_mismatch_history_maxlen` explicit 値の有無を確認。 残っていれば config.json 修正 commit を spec 028 deploy 前に挟む ([[feedback-config-setdefault-override]] 教訓)
- **dig D 決定 — ring TTL**: **件数固定 maxlen=240 のまま** (= 既存 `OrderedDict` FIFO 維持)。 base 60s 周期で 4h 相当の entry が ring に残るが、 2h 超 entry の lookup hit → publish_recovery_backfill → grafana cloud reject (= silent drop) で許容。 burst 中は 5s × 240 = 20min の救済 window で十分

## Step 0: 着手前確認 (= dig C 決定)

```bash
ssh lab-ub01 'adb shell cat /data/local/tmp/cube_j1_mqtt/config.json' \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); print("tid_mismatch_history_maxlen=", c.get("tid_mismatch_history_maxlen", "<not set>")); print("power_w_recovery_backfill_enabled=", c.get("power_w_recovery_backfill_enabled", "<not set>"))'
```

判定:
- `<not set>` (= explicit override 無し): apply_defaults の新 default (= 240 / True) がそのまま効く、 OK 着手
- 数値 (= 旧 `10` 等): config.json から該当 key を削除する commit を spec 028 deploy 前に投入 (= deploy 順序: config 修正 → bridge update)

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **`RECOVERY_BACKFILL_KEYS` 定数** (= `CUMULATIVE_PUBLISH_KEYS` 直下、 行 3764 周辺):
   ```python
   RECOVERY_BACKFILL_KEYS = frozenset(("power_w",))
   ```

2. **`publish_recovery_backfill` ヘルパー** (= `publish_measurements` の直後、 行 3803 周辺):
   ```python
   def publish_recovery_backfill(mqtt, device_id, m, send_ts, diag_state=None):
       """spec 028: 救済 frame の瞬時値を別 topic で過去時刻 backfill 用 JSON publish."""
       base = "cubej/{}".format(device_id)
       ts_iso = format_iso8601_utc(send_ts)  # 既存 helper を流用、 無ければ追加
       for key, value in m.items():
           if value is None:
               continue
           topic = "{}/{}_recovered_json".format(base, key)
           payload = json.dumps({"value": value, "ts": ts_iso})
           mqtt.publish(topic, payload, qos=0, retain=False)
           if diag_state is not None:
               diag_state.power_w_recovered_backfill_total += 1
   ```
   - `format_iso8601_utc` が既存なら流用、 無ければ `datetime.utcfromtimestamp(send_ts).strftime("%Y-%m-%dT%H:%M:%SZ")` で実装
   - `diag_state` 引数渡しで counter increment

3. **main loop late publish 経路** (= 行 4120-4135 周辺、 既存 `_late_ts` 分岐内):
   ```python
   if _late_ts is not None:
       # 既存 spec 020 v1.5: 累積系 late publish (= 変更なし)
       _m_late = dict((k, v) for k, v in m.items() if k in CUMULATIVE_PUBLISH_KEYS)
       if _m_late:
           publish_measurements(mqtt, device_id, _m_late, timestamp=_late_ts)
       # 新規 spec 028: 瞬時系 backfill JSON publish
       if cfg.get("power_w_recovery_backfill_enabled", True):
           _m_bf = dict((k, v) for k, v in m.items() if k in RECOVERY_BACKFILL_KEYS)
           if _m_bf:
               publish_recovery_backfill(mqtt, device_id, _m_bf, _late_ts, diag_state)
   else:
       publish_measurements(mqtt, device_id, m)
   ```

4. **DiagState 拡張** (= 行 2264 周辺の `__init__`、 行 2362 周辺の method、 行 1672 周辺の `_DIAG_SNAPSHOT_KEYS`):
   - `__init__`: `self.power_w_recovered_backfill_total = 0`
   - `_DIAG_SNAPSHOT_KEYS`: 末尾に `"power_w_recovered_backfill_total"` 追加
   - snapshot の raw dict 出力にも entry 追加
   - method 不要 (= `publish_recovery_backfill` 内で直接 `+=` するため)

5. **`DIAG_SENSOR_DEFS`** (= 行 3653-3656 の `erxudp_recovered_from_mismatch_total` 隣):
   ```python
   ("power_w_recovered_backfill_total", "Power W Recovered Backfill",
    None, None, "total_increasing", "diagnostic"),
   ```

6. **`apply_defaults`** (= 行 206-207、 spec 020 既存 setdefault の隣):
   ```python
   out.setdefault("tid_mismatch_history_maxlen", 240)  # 10 → 240 (spec 028)
   out.setdefault("power_w_recovery_backfill_enabled", True)  # spec 028
   ```
   - **注意**: deploy 環境の `config.json` に既存 `"tid_mismatch_history_maxlen": 10` が無いか deploy 前に確認 ([[feedback-config-setdefault-override]] 教訓)

### `tests/unit/test_apply_defaults_spec_028.py` (新規)

- `test_tid_mismatch_history_maxlen_default_is_240`
- `test_power_w_recovery_backfill_enabled_default_is_true`
- `test_config_explicit_override_respected` (= 既存 cfg に明示 key あれば setdefault が上書きしない)

### `tests/unit/test_publish_recovery_backfill.py` (新規)

- `test_topic_format_is_recovered_json_suffix`
- `test_payload_includes_value_and_ts_iso8601`
- `test_retain_false_qos_0`
- `test_diag_state_counter_incremented` (= FakeDiagState)
- `test_none_value_skipped`
- `test_multiple_keys_each_published`

### `tests/unit/test_diag_state.py` (拡張)

- baseline test の expected dict に `power_w_recovered_backfill_total: 0` 追加
- `test_power_w_recovered_backfill_total_snapshot_key`

### main loop late publish (= 配線):

- pytest で main loop 全体は test しづらいので、 実機検証で SC-005 確認 ([[feedback-tdd-spec-template.md]] の確立方針)

### compose/telegraf/telegraf.conf

```toml
# spec 028: 瞬時電力 backfill 受信 (JSON 形式、 client timestamp 経由)
[[inputs.mqtt_consumer]]
  name_override = "mqtt_recovery_backfill"
  servers = ["tcp://192.168.1.151:1883"]
  topics = ["cubej/+/power_w_recovered_json"]
  client_id = "telegraf-cubej-recovery-backfill"
  data_format = "json"
  json_time_key = "ts"
  json_time_format = "2006-01-02T15:04:05Z07:00"
  json_string_fields = []
  [[inputs.mqtt_consumer.topic_parsing]]
    topic = "cubej/+/+"
    tags = "_/device_id/key"

[[processors.starlark]]
  namepass = ["mqtt_recovery_backfill"]
  source = '''
def apply(metric):
    # value → power_watts_recovered (= prometheus metric name)
    v = metric.fields.get("value")
    if v != None:
        metric.fields["power_watts_recovered"] = v
        metric.fields.pop("value")
    return metric
'''
```

既存 `telegraf-cubej-diag-num` の `topics` に追加:
```toml
"cubej/+/diag/power_w_recovered_backfill_total",
```

### grafana dashboard

- `gcx --context cloud dashboards snapshot cubej1-smart-meter` で current state 確認
- `Instantaneous Power` panel (= panel id 確認) の targets に query B `cube_j1_smart_meter_power_watts_recovered{device_id=~"$device"}` 追加、 legend `Recovered`
- `gcx --context cloud dashboards update --uid <uid> --json-patch` で適用

## Test list (TDD 順)

1. **Red→Green**: `test_apply_defaults_spec_028` 3 件 (= apply_defaults pure)
2. **Red→Green**: `test_publish_recovery_backfill` 6 件 (= 新ヘルパー pure)
3. **Red→Green**: `test_diag_state` 拡張 2 件 (= counter + baseline)
4. main loop late publish 配線 + telegraf + grafana (= 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 ~442 + 新規 ~11 = ~453 件 pass
2. ruff check 新規エラー無し
3. **compose 側を先に commit + jj 5 step push** (= dig E 決定、 telegraf を bridge より先に準備して初回 rescue frame も漏らさない)
4. compose deploy-webhook で telegraf 自動 restart 完了待ち (= 30s〜数分、 watchtower)
5. telegraf 単独 deploy の安全確認 (= bridge は未対応なので何も起きず idle、 既存 metric pipeline 不変)
6. cube-j1-mqtt 側 commit + jj push (= main forward only)
7. **(Step 0 副次発見対応) spec 027 残骸の config.json key 削除**:
   ```bash
   ssh lab-ub01 'adb pull /data/local/config.json /tmp/cubej1-config.json'
   ssh lab-ub01 'python3 -c "import json; c=json.load(open(\"/tmp/cubej1-config.json\")); c.pop(\"erxudp_timeout_force_reconnect_threshold\", None); json.dump(c, open(\"/tmp/cubej1-config.json\", \"w\"), indent=2)"'
   ssh lab-ub01 'adb push /tmp/cubej1-config.json /data/local/config.json'
   ```
   spec 027 で `default 5 → 30` 変更したが deploy 環境の explicit override `=5` を削除し忘れた状態を是正、 threshold 30 が有効化される ([[feedback-config-setdefault-override]] 教訓)
8. lab-ub01 経由 deploy (= adb_push_update.sh、 bridge restart を含む = 7 の config 変更も同時 reload)
9. 実機 1h 観察:
   - `/api/diag` で `power_w_recovered_backfill_total > 0`
   - `mosquitto_sub -h 192.168.1.151 -t 'cubej/cubej1/power_w_recovered_json'` で JSON 着信確認
   - `gcx --context cloud metrics query 'cube_j1_smart_meter_power_watts_recovered'` で過去時刻 sample 確認
   - grafana で `Instantaneous Power` panel の穴の位置に Recovered 点プロット
9. SC-006 (= 1 週間運用で hit rate >= 95%) は long-term observation、 todo.md に積む
10. spec 027 修正の verification (= grafana で `cube_j1_smart_meter_wisun_reconnects_total` の rate /5m が 1-2h 観察で **減少傾向** に。 base mode 中の force_reconnect が 5 連続 timeout → 30 連続 timeout になり頻度ダウン)

## Commit 戦略

- @ に spec 028 spec.md (作成済) + plan (本ファイル、 内容 spec 028 化) + 実装 + tests
- `~/.claude/hooks/redact-plans.sh` で機密除去
- `jj commit` で `feat(bridge): 瞬時電力 power_w 救済 frame を別 topic で過去時刻 backfill plot (spec 028)` のような Conventional Commits
- `jj git push --remote fork --bookmark main` (= forward only)
- compose 側は別 commit / 別 repo / 別 push (= `feat(telegraf): cube-j1 recovery backfill JSON consumer + dashboard panel (spec 028)`)、 jj 5 step

## Commit message (cube-j1-mqtt 側)

`feat(bridge): 瞬時電力 power_w 救済 frame を別 topic で過去時刻 backfill plot + ring maxlen 10 → 240 で深い遅延吸収 (spec 028)`
