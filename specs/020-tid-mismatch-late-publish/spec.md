# Feature Specification: Recover TID Mismatch Frames as Late-Publish Data

**Feature Branch**: `020-tid-mismatch-late-publish`
**Created**: 2026-06-22
**Status**: **Not Implemented (Pivoted)** — 2026-06-23 ユーザ判断「burst できるなら spec 020 いらない」 で着手中止。 spec 023+025+026 で burst mode が 5 秒周期で機能し、 Grafana 穴埋めの主要シナリオ (= polling 失敗の reconnect 連発による穴) は自動解消。 本 spec.md は**設計記録として保存** (将来メーター応答性悪化等で late publish 救済を再検討する場合の出発点)。
**Input**: User description: "spec 014 で破棄している TID mismatch frame は実は『過去送信した TID=A の send 時刻に近い時点でメーターが計測した値』。 単純に捨ててるのは勿体無い、 Grafana の穴を補完できないか?"

## Background

spec 014 で実装した ERXUDP TID 検証は、 cycle N で send TID=A → timeout、 retry で send TID=B、 そこにメーターから TID=A の遅延応答が先着するシナリオで TID 不一致 frame を **破棄** している。 この破棄が `erxudp_tid_mismatch_total` として記録されており、 deploy 後の実機ログでも実際に発生していることが確認済 (例: `WARN: ERXUDP TID mismatch expected=0001 got=0000, discarding`)。

しかし、 破棄されている frame の中身は **無価値ではない**:

- ECHONET Lite の応答は「メーターが応答 frame を生成した時点でメーター側で計測した値」
- メーターが応答を再送している場合、 中身は **生成時の値そのまま** (= 過去送信した TID=A の send 時刻 ≒ メーター応答時刻 に近い時点の measurement)
- 0xE0/0xE3 (積算電力量、 spec 011 tier2) や 0xEA/0xEB (定時積算、 spec 018 tier4) は **累積値** + (spec 018 ではメーター内蔵 timestamp 付き) なので、 過去時刻の値として publish しても害なし、 むしろ Grafana 穴補完に有用
- 0xE7 (瞬時電力、 tier1) は HA state machine が `last_changed = now` 基準で動くので、 過去 ts での back-fill は限定的。 ただし InfluxDB / Grafana の直接データソースとしては有用

spec 014 の「混入を防ぐ」設計は維持しつつ、 破棄せず「**遅延 publish (= late publish)**」として活用する拡張。

## Scope

### A. 送信履歴 ring buffer

- 新クラス `SendHistoryRing(maxlen=10)`: TID → `(send_ts, epc_list)` の dict + FIFO eviction
  - `record(tid, send_ts, epc_list)`: 新規送信を記録
  - `lookup(tid) -> Optional[(send_ts, epc_list)]`: TID で履歴検索、 hit なら entry 返却
  - maxlen 超で古い entry を eviction
- main loop の `send_el_get` 呼び出し直後に `history.record(sent_tid, time.time(), cycle_epcs)`

### B. read_erxudp の TID mismatch path を「破棄 vs late publish」に分岐

- 既存 spec 014: TID mismatch → 破棄 + `on_erxudp_tid_mismatch()` カウンタ +1
- spec 020 追加: TID mismatch だが ring buffer に **hit がある** 場合は破棄せず「late frame」として caller に返却 (新シグネチャ or 新返却 mode)
- read_erxudp の戻り値拡張: `(payload, send_ts)` tuple もしくは新 named tuple
  - send_ts=None: 通常 (TID 一致) の即時応答
  - send_ts=<過去時刻>: late frame、 caller が timestamp 指定で publish すべし

### C. main loop での late publish

- read_erxudp が `(payload, send_ts)` 返却で send_ts is not None なら:
  - `decode_measurements(parse_el_response(payload))` で値抽出
  - `apply_energy_scale(...)` で換算
  - `publish_measurements_with_timestamp(mqtt, device_id, measurements, send_ts)` で publish
  - HA への topic は同じ (e.g., `cubej/cubej1/energy_forward`) だが MQTT message に `timestamp` 属性を含める (HA は state を上書き、 ただし attribute として残る)
  - InfluxDB / Telegraf 経路では timestamp が活用される
- `diag_state.on_erxudp_recovered_from_mismatch(send_ts_now_delta_sec)` でカウンタ + delay 統計を更新

### D. 観測 (DiagState 拡張)

- `erxudp_recovered_from_mismatch_total` (counter): late publish で救済した frame 数
- `erxudp_recovered_lag_seconds_recent` (deque maxlen=100): late frame の遅延秒の rolling window、 percentile 公開 (spec 011 follow-up 2 の `erxudp_tid_mismatch_lags_recent` と類似 pattern)

### E. Kill switch

- `tid_mismatch_recover_enabled` (default `True`): false で従来通り単純破棄 (= spec 014 挙動互換、 escape hatch)

## Non-Scope

- HA 側 state machine の改造: HA の state 上書き挙動はそのまま、 attribute としての timestamp 提供にとどめる
- InfluxDB / Telegraf 側の back-fill 設定: 当方は bridge 側で正しい timestamp を発行するのみ、 receiver 側設定は別 (telegraf の `data_format` 等は別作業)
- 0xE7 (瞬時電力) の late publish 価値判定: 値も timestamp も出すが、 HA Energy 用途では tier4 (spec 018) の方が筋がいい
- ring buffer の永続化 (bridge 再起動で history は消える、 これは spec 014 と同じ trade-off)

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Grafana の `cube_j1_smart_meter_energy_forward_kwh` 時系列で「ERXUDP timeout retry のあった cycle」の値が直前 cycle の値と同じ平値で打たれるのを観測する (= late publish で穴が補完された)。 一方で `erxudp_recovered_from_mismatch_total` が増えており、 spec 014 が破棄していた frame の一部が「救済された frame として publish された」ことが分かる。

### Acceptance Scenarios

1. **Given** cycle N で send TID=A → timeout、 retry で send TID=B、 メーターから TID=A の遅延応答が先着、 **When** read_erxudp が TID mismatch を検知、 **Then** SendHistoryRing に TID=A の entry あり → 破棄せず `(payload, send_ts_A)` 返却、 `erxudp_recovered_from_mismatch_total` +1
2. **Given** TID mismatch だが ring buffer に hit 無し (= 完全に未知の TID)、 **When** read_erxudp、 **Then** 既存 spec 014 挙動: 破棄 + `erxudp_tid_mismatch_total` +1
3. **Given** late frame (`send_ts=過去時刻`) を caller が publish、 **When** MQTT message 構築、 **Then** message に `timestamp` 属性が含まれて HA / InfluxDB に渡る
4. **Given** `tid_mismatch_recover_enabled=false`、 **When** TID mismatch、 **Then** ring buffer hit に関係なく破棄 (= spec 014 完全互換)
5. **Given** ring buffer が maxlen=10 で 11 個目の send が来た、 **When** record、 **Then** 最古 entry が eviction

### Key Entities

- **`SendHistoryRing`**: TID → `(send_ts, epc_list)` の bounded dict、 ring buffer 風 FIFO eviction
- **`read_erxudp` の戻り値拡張**: 既存 `bytearray | None` → `(payload, send_ts) | None`、 send_ts は None or float
- **`DiagState.erxudp_recovered_from_mismatch_total`**: counter
- **`DiagState.erxudp_recovered_lag_seconds_recent`**: deque (percentile 公開)
- **`tid_mismatch_recover_enabled`**: config キー

## Edge Cases

- ring buffer に同 TID で重複 record (極めて稀): 上書きで最新 send_ts を保持
- メーターが ECHONET Lite 仕様外の TID を返した場合: ring lookup miss → 既存 spec 014 path で破棄
- send_ts と now の差が極端 (例: > 60 秒): まだ recover するか? → recover する、 ただし lag メトリックで観測可能にして長期 outlier を analyze 可
- bridge 再起動直後: ring 空、 retry frame が来ても hit なし → spec 014 path に fallback (これは想定通り)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `SendHistoryRing` クラスを抽出、 maxlen 引数で bounded、 record / lookup / __len__ メソッドを持つ
- **FR-002**: main loop で `send_el_get` 直後に `history.record(sent_tid, time.time(), cycle_epcs)` を呼ぶ
- **FR-003**: `read_erxudp` に `send_history=None` 引数を追加、 TID mismatch 検知時に lookup → hit なら late frame として返却 (None なら既存破棄 path)
- **FR-004**: `read_erxudp` の戻り値を `Optional[(payload, send_ts)]` に拡張、 既存 callers は `(payload, None)` 互換 unpack で対応
- **FR-005**: main loop で `send_ts is not None` の場合、 publish 時に timestamp を MQTT message に含める
- **FR-006**: `DiagState.erxudp_recovered_from_mismatch_total` counter を追加、 snapshot に公開
- **FR-007**: `DiagState.erxudp_recovered_lag_seconds_recent` deque で lag rolling window、 p50/p95/max を snapshot 公開 (spec 011 follow-up 2 pattern 踏襲)
- **FR-008**: `tid_mismatch_recover_enabled=false` で機能無効化 (kill switch)
- **FR-009**: spec 014 の既存挙動 (`erxudp_tid_mismatch_total` counter は維持、 ring miss 時の破棄も維持) を完全保護

### Key Entities

- 上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: SendHistoryRing の record/lookup/maxlen FIFO eviction
- **SC-002**: 単体テスト: read_erxudp で ring hit → late frame 返却、 ring miss → 既存破棄 (FakeDiagState で検証)
- **SC-003**: 実機 1 週間運用で `erxudp_recovered_from_mismatch_total > 0` が観測される (= spec 014 が破棄していた frame の一部が救済された証跡)
- **SC-004**: Grafana の `cube_j1_smart_meter_erxudp_recovered_lag_seconds_p95` が 30 秒以内 (再送遅延の妥当性確認)
- **SC-005**: 既存テスト全件 pass (spec 014 互換性)

## Assumptions

- ECHONET Lite メーターは再送時に同じ frame (同じ値) を再送する仕様 (= 値は send 時点で確定)
- ring buffer maxlen=10 は実機の spec 011 intra-cycle retry max=1 + 通常 cycle 数で十分 (深い遅延応答 = 10 cycle 前 = 約 5-10 分前の応答、 これより古いものは捨ててよい)
- HA / InfluxDB が `(value, timestamp)` 形式の MQTT message を受理できる
- 関連: [[spec-014-tid-validation]], [[spec-018-cumulative-energy-tier]] (メーター timestamp 受信パスが整っていればさらに正確な late publish 可能)
