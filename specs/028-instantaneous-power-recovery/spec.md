# Feature Specification: Instantaneous Power Recovery (Backfill into Past Timestamps)

**Feature Branch**: `028-instantaneous-power-recovery`
**Created**: 2026-06-24
**Status**: Draft
**Input**: ユーザ要望: 「救済込みで見れる Instantaneous Power が欲しい」 + 「深い遅延も in-memory 内なら吸収できる仕組みにしたい」

## Background

spec 020 v1.5 で TID mismatch frame の 90% を late publish 救済できるようになった (= `erxudp_recovered_from_mismatch_total / erxudp_tid_mismatch_total = 65 / 72 ≈ 90.3%`, lag p50=1s, p95=60s, max=240s)。 ただし救済対象は **累積系のみ** (= `CUMULATIVE_PUBLISH_KEYS` = `energy_forward / energy_reverse / *_fixed_kwh / *_fixed_ts / *_fixed_raw`)、 **瞬時電力 (0xE7 = `power_w`) と瞬時電流 (0xE8 = `current_a*`) は意図的に除外** (= HA の state machine が `last_changed = now` 基準で動くため、 グラフ歪み回避)。

しかし grafana で「**救済込みの power_w を本来の時刻でプロット**」したい要件がある:
- 60s 周期 polling が timeout すると 60s 以上の穴ができる
- 次 cycle で来た late frame は「過去時刻の瞬時電力」 なので、 sparkline 本来の位置にプロットすれば穴が自然に埋まる
- HA 経路 (= `cubej/cubej1/power_w` topic) は触らず、 **別 series** で grafana 専用に backfill すれば HA 側を歪めない

ECHONET Lite + Prometheus remote_write の組合せで実現可能と確認済 (memory: `reference-prometheus-remote-write-backfill`):
- Prometheus remote_write は **client-supplied timestamp** で過去時刻 sample 投入可
- grafana cloud は **2h 以内** の sample なら受理 (retention 上の制約)
- telegraf JSON parser で `json_time_key` 指定すれば metric.time に自動 set
- `outputs.http data_format = prometheusremotewrite` が metric.time を client timestamp として送信

さらに **深い遅延 (= ring buffer maxlen 超過)** も吸収したい:
- 現状 spec 020 v1.5 の ring maxlen=10 cycle = 約 5-10 分の遅延までしか救済不可
- 残り 10% (= 72 - 65 = 7 件) の mismatch frame の一部はおそらく ring lookup miss (= 深い遅延 / 起動直後)
- maxlen を増やせば in-memory コストはわずか (= entry あたり TID 2 bytes + send_ts 8 bytes + epc_tuple 数 bytes、 100 entries = 数 KB)
- ただし grafana cloud 2h backfill 制限が事実上の上限 = `240 cycle (= 120 min @ 30s) ` を超えるエントリは「ring に残っていても backfill 不能」

## Scope

### A. ring buffer maxlen 拡張 (= 深い遅延吸収)

- `tid_mismatch_history_maxlen` config default を **10 → 240** に引き上げ (= 約 2h @ 30s 周期、 grafana cloud 2h backfill 制限と整合)
  - cube_j1 J1 端末の RAM 制約 (= 128MB or 256MB クラス) でも 240 entry = 数 KB なので無視できる
  - kill switch: `tid_mismatch_history_maxlen=0` または小さい値で従来挙動互換
- spec 020 v1.5 既存ロジック (= `lookup` / `lookup_latest`) は変更なし、 単に maxlen 拡大で「深い遅延も lookup hit する」 確率を上げる
- **ring 内エントリの「妥当性 TTL」 は導入しない** (= 2h 以上前のエントリは grafana 側で reject されるだけ、 害なし)

### B. 救済 frame で 0xE7 (瞬時電力) を別 topic に backfill 用 JSON publish

- 新 `RECOVERY_BACKFILL_KEYS = frozenset(("power_w",))` (= 将来的に `current_a_*` 追加可能)
- main loop の late publish 経路で、 既存 `CUMULATIVE_PUBLISH_KEYS` 経由 publish の **後ろに**、 `RECOVERY_BACKFILL_KEYS` 経由 publish を追加:
  ```python
  if _late_ts is not None:
      # 既存 (spec 020 v1.5): 累積系 late publish
      _m_late_cum = dict((k, v) for k, v in m.items() if k in CUMULATIVE_PUBLISH_KEYS)
      if _m_late_cum:
          publish_measurements(mqtt, device_id, _m_late_cum, timestamp=_late_ts)
      # spec 028 新規: 瞬時系 backfill JSON publish (別 topic)
      _m_late_bf = dict((k, v) for k, v in m.items() if k in RECOVERY_BACKFILL_KEYS)
      if _m_late_bf:
          publish_recovery_backfill(mqtt, device_id, _m_late_bf, _late_ts)
  ```
- 新ヘルパー `publish_recovery_backfill(mqtt, device_id, m, send_ts)`:
  - topic = `cubej/<id>/<key>_recovered_json` (= 例 `cubej/cubej1/power_w_recovered_json`)
  - payload = `{"value": <numeric>, "ts": "<ISO8601 UTC>"}` (= JSON)
  - `retain=False` (= backfill 用途、 最新値が常に有効ではない)
  - QoS 0 (= HA との一貫性)

### C. telegraf 側 JSON parser + 過去時刻 backfill

- compose/telegraf/telegraf.conf に新 `[[inputs.mqtt_consumer]]` 追加:
  ```toml
  [[inputs.mqtt_consumer]]
    name_override = "mqtt_recovery_backfill"
    servers = ["tcp://192.168.1.151:1883"]
    topics = ["cubej/+/power_w_recovered_json"]
    client_id = "telegraf-cubej-recovery-backfill"
    data_format = "json"
    json_time_key = "ts"
    json_time_format = "2006-01-02T15:04:05Z07:00"
    json_string_fields = []
    tag_keys = []
    [[inputs.mqtt_consumer.topic_parsing]]
      topic = "cubej/+/+"
      tags = "_/device_id/key"
  ```
- starlark processor で field rename (= `value` → `power_watts_recovered`)、 metric.time は JSON parser が自動 set 済なので保持
- 既存 `[[outputs.http]] data_format = prometheusremotewrite` が metric.time を client timestamp として送信
- 結果 prometheus に `cube_j1_smart_meter_power_watts_recovered` series が **過去時刻** で投入される

### D. grafana panel: power_watts overlay (本来 + 救済)

- 既存 `Instantaneous Power` panel に新 series を追加:
  - A: `cube_j1_smart_meter_power_watts` (= 通常、 scrape 時刻)
  - B: `cube_j1_smart_meter_power_watts_recovered` (= backfill、 client timestamp)
  - display: legend で B を「Recovered」 と表示、 sparkline 上で B が穴の位置にプロット
- 新 panel は **追加のみ**、 既存 `power_watts` panel/HA dashboard は変更なし

### E. DiagState 拡張

- `power_w_recovered_backfill_total` (counter): backfill JSON publish した frame 数
  - spec 020 の `erxudp_recovered_from_mismatch_total` とは別カウント (= 救済 frame 内で 0xE7 を含むものだけ)
- DIAG_SENSOR_DEFS 登録 (= spec 020 deploy 漏れの教訓: [[feedback-diag-sensor-defs-publish]] 必読)
- compose/telegraf/telegraf.conf の diag-num consumer topics に `cubej/+/diag/power_w_recovered_backfill_total` 追加

### F. Kill switch

- `power_w_recovery_backfill_enabled` (default `True`): false で B 経路全停止 (= 既存 v1.5 累積系のみ救済挙動互換)
- `tid_mismatch_history_maxlen=10` (= 旧 default) で従来 ring サイズに戻せる

## Non-Scope

- HA Energy dashboard への backfill: HA state machine は `last_changed = now` 基準のため、 過去時刻 backfill は HA 側で意味を成さない。 grafana 専用機能
- 0xE8 (= 瞬時電流 r/t 相) の backfill: 0xE7 で実証してから別 spec で拡張
- 累積系 (= 既存 spec 020 v1.5 経路) の backfill 化: 既存 publish_measurements + `_late_publish_ts` topic で十分、 client timestamp backfill 不要 (= 累積値は時刻ずれても integral 用途で問題なし)
- メーター側 timestamp (= spec 018 tier4 0xEA/0xEB) の活用: 既に正確な timestamp あり、 本 spec とは独立
- grafana cloud 2h backfill 制限を超えた frame の救済: 物理的に不可能、 諦める
- ring buffer 永続化 (= bridge 再起動で history 消失): 既存 spec 020 と同 trade-off

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは grafana の `Instantaneous Power` panel で:
1. 通常時 = `power_watts` series が 60s ごとに点として打たれる
2. polling timeout が起きた cycle = 通常 series に穴ができる
3. 次 cycle で late frame 受信 → `power_watts_recovered` series が **穴の位置** (= TID 送信時刻 ≒ メーター応答時刻) にプロット
4. sparkline / line 表示で穴が自然に塗られた状態を観察

DiagState の `power_w_recovered_backfill_total` が増えており、 救済 frame の 0xE7 が backfill 経路で grafana に届いていることが counter で分かる。

### Acceptance Scenarios

1. **Given** cycle N で send TID=A → timeout、 retry で TID=B、 メーターから TID=A の遅延応答 (= 60s 前の power_w=350W) 先着、 **When** read_erxudp が TID mismatch + ring hit、 **Then** main loop で late publish 経路:
   - 累積系: 既存 `energy_forward` 等を `publish_measurements(..., timestamp=send_ts_A)` で発行
   - 瞬時系: 新 `publish_recovery_backfill(mqtt, device_id, {"power_w": 350}, send_ts_A)` で `cubej/cubej1/power_w_recovered_json` に `{"value": 350, "ts": "2026-06-24T01:30:00Z"}` 発行
   - `power_w_recovered_backfill_total` +1
2. **Given** ring maxlen=240 (= 新 default)、 11 cycle 前 (= 約 5 分) の遅延 frame 受信、 **When** read_erxudp、 **Then** ring lookup hit → late publish 成功 (= v1.5 の maxlen=10 では miss していたケース)
3. **Given** 30 cycle 前 (= 約 15 分) の深い遅延 frame、 **When** 同上、 **Then** 同様に lookup hit、 ただし grafana cloud は 15 分前なので余裕で受理 (= 2h 以内)
4. **Given** `power_w_recovery_backfill_enabled=false`、 **When** late frame 検知、 **Then** 累積系 late publish のみ発火、 backfill 経路は無効化
5. **Given** telegraf が `power_w_recovered_json` topic を受信、 **When** JSON parse + metric.time set + prometheus remote_write、 **Then** prometheus 側で `cube_j1_smart_meter_power_watts_recovered{device_id="cubej1"}` が **過去時刻 sample** として投入される

### Key Entities

- **`RECOVERY_BACKFILL_KEYS`**: frozenset(("power_w",)) — 将来拡張用
- **`publish_recovery_backfill(mqtt, device_id, m, send_ts)`**: JSON publish ヘルパー
- **`DiagState.power_w_recovered_backfill_total`**: counter
- **`tid_mismatch_history_maxlen`**: config キー、 default 10 → 240
- **`power_w_recovery_backfill_enabled`**: config キー、 default True

## Edge Cases

- ring maxlen=240 だが TID 衝突 (= 16bit TID space で 240 entry でも衝突は実用上ほぼ無し): 既存 spec 020 の上書き挙動 (= 同 TID で最新 send_ts 保持) のままで OK
- bridge 再起動で ring 空、 起動直後の late frame: ring lookup miss → `lookup_latest()` fallback (= got_tid=0 path) で救えるならそれで OK、 そうでなければ既存 discard
- メーターが完全に時系列無視で frame を送ってきた (= 5 分前と 30 分前を交互に): backfill は両方プロットされる、 sparkline 表示上は穴埋め点として並ぶ
- grafana cloud 2h backfill 制限超過の frame (= 例: 3h 前の deep lag): prometheus remote_write が `out_of_order` reject → metric 計上はされないが、 bridge 側は失敗を知らない (= silent drop)。 lag p99/max metric で観測可能
- topic = `power_w_recovered_json` を HA が subscribe しないか?: HA discovery 経由でないので無視される (= 安全)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `tid_mismatch_history_maxlen` default を 10 → 240 に変更 (`apply_defaults` 内)
- **FR-002**: `RECOVERY_BACKFILL_KEYS = frozenset(("power_w",))` 定数を追加
- **FR-003**: `publish_recovery_backfill(mqtt, device_id, m, send_ts)` ヘルパーを実装、 topic `cubej/<id>/<key>_recovered_json` に JSON payload (= `{"value": V, "ts": "<ISO8601>"}`) 発行、 retain=False, qos=0
- **FR-004**: main loop late publish 経路で `RECOVERY_BACKFILL_KEYS` filter + `publish_recovery_backfill` 呼出、 `power_w_recovery_backfill_enabled=false` で skip
- **FR-005**: `DiagState.power_w_recovered_backfill_total` counter 追加、 `publish_recovery_backfill` 内で increment
- **FR-006**: DIAG_SENSOR_DEFS に `power_w_recovered_backfill_total` entry 追加 ([[feedback-diag-sensor-defs-publish]] 教訓)
- **FR-007**: `apply_defaults` に `power_w_recovery_backfill_enabled=True` 追加 ([[feedback-config-setdefault-override]] 教訓: deploy 環境の config.json に既存 override が無いことを確認)
- **FR-008**: compose/telegraf/telegraf.conf に:
  - 新 `[[inputs.mqtt_consumer]]` (= recovery-backfill 専用、 JSON parser、 `json_time_key=ts`)
  - 新 `[[processors.starlark]]` (= `value` → `power_watts_recovered` rename)
  - diag-num consumer topics list に `cubej/+/diag/power_w_recovered_backfill_total` 追加
- **FR-009**: grafana `cubej1-smart-meter` dashboard に新 panel または既存 `Instantaneous Power` panel に series B (= `cube_j1_smart_meter_power_watts_recovered`) overlay 追加
- **FR-010**: spec 020 v1.5 の既存挙動 (= 累積系 late publish、 ring lookup / lookup_latest) は完全保護

### Key Entities

- 上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `publish_recovery_backfill` の topic / payload / timestamp format 検証
- **SC-002**: 単体テスト: main loop late publish 経路で `power_w` が backfill 経由、 累積系は既存経路という分岐検証 (FakeMqtt で)
- **SC-003**: 単体テスト: `apply_defaults` の `tid_mismatch_history_maxlen` default が 240、 `power_w_recovery_backfill_enabled` default True
- **SC-004**: 単体テスト: DiagState `power_w_recovered_backfill_total` counter increment + snapshot 出力
- **SC-005**: 実機 deploy 後 1h で:
  - `/api/diag` で `power_w_recovered_backfill_total > 0`
  - mosquitto_sub で `cubej/cubej1/power_w_recovered_json` 着信確認
  - prometheus で `cube_j1_smart_meter_power_watts_recovered` series 存在確認 (= `gcx --context cloud metrics query` で空 vector でない)
  - grafana panel で `power_watts` の穴の位置に `power_watts_recovered` 点がプロットされる
- **SC-006**: 1 週間運用で ring maxlen 拡張効果確認: `erxudp_recovered_from_mismatch_total / erxudp_tid_mismatch_total >= 95%` (= v1.5 の 90% から improve)
- **SC-007**: 既存テスト全件 pass (spec 020 v1.5 + spec 022 + spec 023+025+026 互換)

## Assumptions

- ECHONET Lite メーターの 0xE7 応答は「メーターが応答 frame を生成した時点 (= send_ts ≒ 受信時刻) の瞬時電力」 (= 累積系と同様、 値は send 時点で確定)
- ring maxlen=240 (= 約 2h) の memory footprint は cube_j1 J1 にとって無視できる (= 数 KB)
- grafana cloud Prometheus instance の retention policy が 2h backfill を許可している (= memory `reference-prometheus-remote-write-backfill` で確認済)
- telegraf JSON parser + prometheusremotewrite output が metric.time をそのまま client timestamp として送信する
- 関連 spec: [[spec-020-tid-mismatch-late-publish]] (= prerequisite、 ring + lookup)、 [[spec-014-tid-validation]] (= TID 検証の元実装)、 [[spec-018-cumulative-energy-tier]] (= メーター内蔵 timestamp との対比)
- 関連 memory: [[reference-prometheus-remote-write-backfill]] (= 設計起点)、 [[feedback-compose-telegraf-pipeline]] (= 修正手順)、 [[feedback-diag-sensor-defs-publish]] (= deploy 漏れ回避)、 [[feedback-config-setdefault-override]] (= apply_defaults の落とし穴)
