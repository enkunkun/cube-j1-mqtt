# Feature Specification: Cumulative Energy Recovery Backfill (= spec 020 v1.5 可視化完成)

**Feature Branch**: `029-cumulative-energy-recovery-backfill`
**Created**: 2026-06-25
**Status**: **Deployed 2026-06-25** — compose `9769a088` + cube `73c22f33` で deploy 完了 (= compose 先 → cube 後 順序、 dig E 決定踏襲)。 配線確証: bridge code reflect + main loop `_late_ts` 分岐に spec 029 backfill 配線 + telegraf JSON consumer topics 1→5 件拡張 + starlark SUFFIX dict 5 entry + diag-num consumer cumulative counter 追加 + dashboard panel-10 refId=C/D `_recovered` overlay + 新 panel-82 cumulative backfill rate。 cloud verify: `cube_j1_smart_meter_cumulative_recovered_backfill_total=0` (= telegraf pipeline 健全証跡)、 `energy_forward_kwh_recovered` series 空 (= deploy 直後 uptime 79s で mismatch 未発火、 整合)。 e2e wire: spec 028 と違って **tier2/4 cycle で構造的に必ず hit** (= 既存 10/10 mismatch すべて tier2/4 だった事実から deploy 後最初の mismatch でほぼ確実に発火、 自然 verify 容易)。 副次成果: Step 0 で device config.json 5 key 全 `<not set>` 確認 (= spec 027 残骸も既消失維持)、 副次修正 commit 不要 = pure spec 029 単独 deploy。 副次発見: subagent 報告で `bridge_version=None` 観測 (= 別 spec 候補、 spec 029 と無関係、 todo 候補)。
**Input**: ユーザ指摘 (= 2026-06-25): 「救済率は上がってるのに panel で見れないのは片手落ち。 telegraf consumer 設定でこの topic 未 subscribe も含めて実装なのでは」

## Background

spec 020 v1.5 で TID mismatch 救済率 90%+ を達成 (= panel-81 で確認)、 累積系 (= `energy_forward_kwh` 等) の救済値を `_late_publish_ts` topic に過去時刻 ISO8601 で retain publish していたが、 **telegraf consumer 設定でこの topic は未 subscribe** だった → grafana / prometheus に来ない → panel-10 Cumulative Energy で「救済値そのもの」 は通常 series に上書きされるだけで「救済由来」 識別不可、 さらに「救済発生時刻」 も grafana から取得不能。

これは memory `feedback-compose-telegraf-pipeline` の「新 metric は bridge + compose 両方修正必須」 ルールに **違反した片手落ち deploy** (= spec 020 v1.5 deploy 時の plan に telegraf 側修正を含めなかった)。

spec 028 で瞬時系 (= `power_w`) を別 topic `_recovered_json` に JSON publish + telegraf JSON parser + prometheus client-supplied timestamp backfill (= grafana cloud 2h backfill 受理) する完全可視化経路を確立済 ([[reference-prometheus-remote-write-backfill]])。 spec 029 はこの **同一 pattern を累積系にも適用**、 spec 020 v1.5 の可視化を完成させる。

完成後の grafana で:
- panel-10 Cumulative Energy に refId=B `<key>_recovered` series が overlay、 救済点が過去時刻に plot
- `cumulative_recovered_backfill_total` counter で発火頻度可視化 (= spec 028 panel-80 と並列)

## Scope

### A. 既存 `publish_recovery_backfill` ヘルパーを generic 化

- spec 028 helper の現状:
  ```python
  def publish_recovery_backfill(mqtt, device_id, m, send_ts, diag_state=None):
      ...
      if diag_state is not None:
          diag_state.power_w_recovered_backfill_total += 1
  ```
- spec 029 拡張: `counter_attr` 引数追加で increment 対象 counter を caller 指定:
  ```python
  def publish_recovery_backfill(mqtt, device_id, m, send_ts, diag_state=None,
                                counter_attr="power_w_recovered_backfill_total"):
      ...
      if diag_state is not None:
          setattr(diag_state, counter_attr,
                  getattr(diag_state, counter_attr, 0) + 1)
  ```
- spec 028 既存 caller は default 引数で互換 (= breaking change なし)

### B. `CUMULATIVE_BACKFILL_KEYS` 定数 + main loop 配線

- `CUMULATIVE_BACKFILL_KEYS = frozenset(("energy_forward_kwh", "energy_reverse_kwh", "energy_forward_fixed_kwh", "energy_reverse_fixed_kwh"))`
- main loop の late publish 経路 (= `_late_ts is not None` 分岐内) で、 spec 028 の **後** に spec 029 backfill 追加:
  ```python
  # spec 028: 瞬時系 backfill (= 既存)
  if cfg.get("power_w_recovery_backfill_enabled", True):
      _m_bf = dict((k, v) for k, v in m.items() if k in RECOVERY_BACKFILL_KEYS)
      if _m_bf:
          publish_recovery_backfill(mqtt, device_id, _m_bf, _late_ts, diag_state)
  # spec 029 新規: 累積系 backfill
  if cfg.get("cumulative_recovery_backfill_enabled", True):
      _m_cum_bf = dict((k, v) for k, v in m.items() if k in CUMULATIVE_BACKFILL_KEYS)
      if _m_cum_bf:
          publish_recovery_backfill(mqtt, device_id, _m_cum_bf, _late_ts, diag_state,
                                    counter_attr="cumulative_recovered_backfill_total")
  ```

### C. DiagState 拡張

- `cumulative_recovered_backfill_total` (counter) 追加
- `_DIAG_SNAPSHOT_KEYS` + snapshot raw dict 登録
- DIAG_SENSOR_DEFS entry 追加 (= [[feedback-diag-sensor-defs-publish]] 教訓必読)

### D. `apply_defaults`

- `cumulative_recovery_backfill_enabled = True` (= kill switch、 default ON)

### E. telegraf 修正

- 既存 spec 028 JSON consumer の `topics` に 4 件追加:
  ```toml
  topics = [
    "cubej/+/power_w_recovered_json",
    "cubej/+/energy_forward_kwh_recovered_json",
    "cubej/+/energy_reverse_kwh_recovered_json",
    "cubej/+/energy_forward_fixed_kwh_recovered_json",
    "cubej/+/energy_reverse_fixed_kwh_recovered_json",
  ]
  ```
- 既存 starlark `SUFFIX` dict 拡張:
  ```python
  SUFFIX = {
      "power_w_recovered_json": "power_watts_recovered",
      "energy_forward_kwh_recovered_json": "energy_forward_kwh_recovered",
      "energy_reverse_kwh_recovered_json": "energy_reverse_kwh_recovered",
      "energy_forward_fixed_kwh_recovered_json": "energy_forward_fixed_kwh_recovered",
      "energy_reverse_fixed_kwh_recovered_json": "energy_reverse_fixed_kwh_recovered",
  }
  ```
- 既存 diag-num consumer の topics に `cubej/+/diag/cumulative_recovered_backfill_total` 追加

### F. grafana dashboard

- panel-10 "Cumulative Energy" に refId=B/C 追加:
  - B: `cube_j1_smart_meter_energy_forward_kwh_recovered{device_id=~"$device"}` legend="Recovered (forward)"
  - C: `cube_j1_smart_meter_energy_reverse_kwh_recovered{device_id=~"$device"}` legend="Recovered (reverse)"
  - `_fixed_kwh_recovered` は別 panel か注目度低いので skip (= MVP)
- panel-80 (= spec 028 backfill rate) の type を「multi」 にして spec 029 series も追加: もしくは新 panel-82 で `cumulative_recovered_backfill_total` rate 別表示
  - MVP: panel-80 を generic 化 = `recovery_backfill_total` 系全体 rate / 5m で 1 panel に bar overlay

## Non-Scope

- `_fixed_kwh` 系 (= spec 018 tier4 meter timestamp) の panel B overlay: 既に accurate meter timestamp 持ってるので backfill 必要性低い、 ただし topic publish は cube-j1-mqtt 側で行う (= ts と value 揃ってる完全 backfill 経路、 別 series で grafana 側 query 可能)
- `_late_publish_ts` topic の telegraf 受信 (= 別経路): spec 029 で本命の value+ts JSON 経路を提供するので、 既存 `_late_publish_ts` retain topic は触らず (= ただし新 ts JSON 経路で過去時刻 backfill 可能になる)
- spec 020 v1.5 の累積系 `publish_measurements(..., timestamp=_late_ts)` の `_late_publish_ts` topic publish 自体は **継続** (= retain で broker に「最後の救済時刻」 が残るのが util、 削除しない)

## User Scenarios

### Primary User Story

ユーザは grafana の `Cumulative Energy` panel で:
1. 通常 polling での `energy_forward_kwh` series が線で表示 (= 既存)
2. TID mismatch 救済発生 cycle = 過去時刻に **別色の点** (= `energy_forward_kwh_recovered` series) が overlay plot
3. panel-80 (rate /5m) で「累積系 backfill 発火」 と「瞬時系 backfill 発火」 の bar が見える
4. spec 020 v1.5 救済率 90%+ (= panel-81) と整合する形で実際の値も plot される = **救済の効果が grafana で全経路可視化**

### Acceptance Scenarios

1. **Given** cycle N で tier2 (= `[0xE0, 0xE3]`) send → timeout、 retry で別 TID send、 メーターから前 TID の遅延応答先着、 **When** read_erxudp が late frame 救済 (= `_late_ts` 設定)、 **Then** main loop で:
   - 累積系: `publish_measurements(..., timestamp=_late_ts)` で既存 `_late_publish_ts` retain topic 発行 (= spec 020 v1.5 既存)
   - 累積系 spec 029: `publish_recovery_backfill(mqtt, device_id, {"energy_forward_kwh": V}, _late_ts, diag_state, counter_attr="cumulative_recovered_backfill_total")` で別 topic `cubej/cubej1/energy_forward_kwh_recovered_json` に `{"value": V, "ts": ISO}` JSON publish
   - `cumulative_recovered_backfill_total += 1`
2. **Given** telegraf が `energy_forward_kwh_recovered_json` topic を JSON 受信、 **When** JSON parser + starlark rename + prometheus remote_write、 **Then** prometheus に `cube_j1_smart_meter_energy_forward_kwh_recovered{device_id="cubej1"}` series が **過去時刻 sample** として投入
3. **Given** grafana panel-10 を開く、 **When** refId=B query 実行、 **Then** `energy_forward_kwh_recovered` series が過去時刻 dot で overlay 表示、 既存 line に重ねて救済点が見える
4. **Given** `cumulative_recovery_backfill_enabled=false`、 **When** late frame 検知、 **Then** spec 020 v1.5 既存経路 + spec 028 経路のみ動作、 spec 029 経路無効化
5. **Given** spec 028 既存 caller (= `RECOVERY_BACKFILL_KEYS` filter)、 **When** `publish_recovery_backfill` 呼出、 **Then** default `counter_attr="power_w_recovered_backfill_total"` で互換動作

## Edge Cases

- 1 cycle で累積系 + 瞬時系両方 mismatch 救済 (= 救済 frame に複数 EPC 含む): 各々 spec 028 / spec 029 backfill 経路で別 topic publish、 両 counter increment
- `_fixed_kwh` 系 (= meter timestamp 持ち) で救済発生: spec 029 backfill 経路で grafana plot される、 ただし `_fixed_ts` の方が accurate (= meter 内蔵 clock)、 視覚上は spec 029 line と spec 018 line が重複可能性、 panel design で legend 区別必要
- bridge restart 直後 cycle 0 = tier4 (= [[feedback-cycle-counter-reconnect-tier4]]) で mismatch 発生: tier4 = `[0xEA, 0xEB]` cumulative-only、 `m` には `energy_forward_fixed_kwh` が含まれる → spec 029 で `_fixed_kwh_recovered` 経路で plot される (= 仕様通り、 cycle 0 mismatch を初めて可視化可能になる)
- prometheus remote_write 2h backfill 制限: ring maxlen=240 (= 約 2-4h) 内なら受理、 超えたら silent drop ([[reference-prometheus-remote-write-backfill]])

## Requirements

### Functional Requirements

- **FR-001**: `publish_recovery_backfill` に `counter_attr` 引数追加、 default `"power_w_recovered_backfill_total"` で spec 028 互換
- **FR-002**: `CUMULATIVE_BACKFILL_KEYS = frozenset(...)` 定数追加 (= 4 keys: `energy_forward_kwh`, `energy_reverse_kwh`, `energy_forward_fixed_kwh`, `energy_reverse_fixed_kwh`)
- **FR-003**: main loop の `_late_ts is not None` 分岐に spec 029 累積系 backfill 配線追加、 `cumulative_recovery_backfill_enabled` で gate
- **FR-004**: `DiagState.cumulative_recovered_backfill_total` counter + `_DIAG_SNAPSHOT_KEYS` + snapshot raw dict
- **FR-005**: `DIAG_SENSOR_DEFS` に `cumulative_recovered_backfill_total` entry 追加
- **FR-006**: `apply_defaults` に `cumulative_recovery_backfill_enabled = True`
- **FR-007**: compose/telegraf/telegraf.conf:
  - 既存 spec 028 mqtt_recovery_backfill consumer の topics に 4 件追加 (= 4 cumulative keys の `_recovered_json` topic)
  - 既存 starlark `SUFFIX` dict 拡張
  - 既存 diag-num consumer topics に `cubej/+/diag/cumulative_recovered_backfill_total` 追加
- **FR-008**: grafana panel-10 に refId=B/C 追加 (= forward / reverse の `_recovered` series overlay)
- **FR-009**: spec 028 既存挙動 (= `power_w` backfill 経路、 panel-1 refId=B、 panel-80 rate) を **完全保護**

### Key Entities

- 上記 Scope 参照

## Success Criteria

- **SC-001**: 単体テスト: `publish_recovery_backfill` の `counter_attr` 引数で increment 対象 counter が変わる
- **SC-002**: 単体テスト: spec 028 default caller が `power_w_recovered_backfill_total` を increment (= 互換)
- **SC-003**: 単体テスト: spec 029 caller が `cumulative_recovered_backfill_total` を increment
- **SC-004**: 単体テスト: DiagState `cumulative_recovered_backfill_total` baseline 0 + snapshot 反映
- **SC-005**: 単体テスト: `apply_defaults` の `cumulative_recovery_backfill_enabled` default True
- **SC-006**: 実機 deploy 後 1h 観察:
  - `/api/diag` で `cumulative_recovered_backfill_total > 0` (= 救済発生 cycle で 1 件以上発火、 tier2/4 cycle で確実、 spec 028 と違って構造的に hit する)
  - `mosquitto_sub -t 'cubej/cubej1/energy_forward_kwh_recovered_json'` で JSON 着信確認
  - `gcx --context cloud metrics query 'cube_j1_smart_meter_energy_forward_kwh_recovered'` で過去時刻 sample 確認
  - grafana panel-10 で Recovered 点プロット観察
- **SC-007**: 既存テスト全件 pass (spec 020 v1.5 + spec 022 + spec 023+025+026+028 互換)

## Assumptions

- `publish_recovery_backfill` を generic 化しても spec 028 既存テスト (= 9 件) は default 引数で互換 pass
- prometheus remote_write は cumulative 系も同じく client-supplied timestamp 受理 (= 数値型なので何でも OK、 grafana cloud 2h 制約のみ)
- 関連 spec: [[spec-020-tid-mismatch-late-publish]] (= 累積系 late publish の既存実装)、 [[spec-028-instantaneous-power-recovery]] (= 瞬時系 backfill の同一 pattern)、 [[spec-018-cumulative-energy-tier]] (= meter timestamp 持ちの `_fixed_kwh` 系)
- 関連 memory: [[feedback-compose-telegraf-pipeline]] (= 修正手順、 今回まさにこの教訓を spec 020 v1.5 で破った)、 [[feedback-diag-sensor-defs-publish]] (= deploy 漏れ回避)、 [[reference-prometheus-remote-write-backfill]] (= 設計起点)、 [[feedback-cycle-counter-reconnect-tier4]] (= 余談: reconnect 直後 cycle 0 = tier4 で必ず `_fixed_kwh_recovered` 発火 = 副次効果として「reconnect 直後の救済」 が初めて可視化される)
