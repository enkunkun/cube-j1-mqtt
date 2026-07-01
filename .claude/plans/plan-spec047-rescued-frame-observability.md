# Plan: spec 047 ERXUDP rescued frame 内訳観測

## Goal

rescued frame の sub-population (= ESV / TID / lag bucket / measurement 空) を 11 counter で分解し、H1 (TID=0 誤分類) / H2 (INF 混入) / H3 (chain 自走) の支配要因を 24h 観測で確定する。polling 挙動は不変 (FR-007)。

## 実装ステップ (TDD、pure helper → DiagState → 統合の型 = feedback-tdd-spec-template)

### Step 1: pure helper `classify_rescued_esv(payload)`

- 入力: bytearray (EL frame)。出力: "get_res" / "get_sna" / "inf" / "other" の str
- ESV は payload[10] (= parse_el_response と同じ offset、Python 2/3 両対応の int/ord 分岐)
- len < 11 は "other"
- テスト: tests/unit/test_rescued_frame_classify.py (新規)

### Step 2: pure helper `classify_rescued_lag_bucket(lag_sec)`

- 境界: <5 / 5-60 / 60-300 / >=300 → "lt5s" / "5to60s" / "60to300s" / "gt300s"
- 境界値テスト: 4.999 / 5.0 / 60.0 / 300.0

### Step 3: DiagState 拡張

- `__init__` に 11 counter 初期化
- `on_erxudp_rescued(esv_kind, tid_zero, lag_sec)` method 1 本に集約 (= 呼び出し側 3 counter 群を 1 call で inc、H1/H2/H3 判定に必要な直交軸を保つ)
- `on_erxudp_rescued_empty_measurement()` を別 method (= main loop 側からしか判定できないため)
- `snapshot()` に 11 key 追加 (= zero-omit しない)
- FR-006: `on_erxudp_tid_mismatch` の lag 記録を `got != 0` 条件付きに
- テスト: test_diag_state.py に追記

### Step 4: read_erxudp rescue path で発火

- `mqtt_bridge.py:3742-3755` の rescue 確定箇所で `on_erxudp_rescued(classify_rescued_esv(payload), got_tid == 0, time.time() - send_ts_a)` を try/except 付きで呼ぶ
- テスト: test_read_erxudp_tid.py の FakeDiag に counter 検証追加

### Step 5: main loop empty measurement 判定

- `_late_ts is not None` 分岐 (= mqtt_bridge.py:4765 付近) で、backfill 3 系統 (= RECOVERY/CUMULATIVE/CURRENT keys) のどれも publish しなかった場合に `on_erxudp_rescued_empty_measurement()`
- 判定は「_m_bf / _m_cum_bf / _m_cur_bf すべて empty」で近似 (= CUMULATIVE_PUBLISH_KEYS の late publish は対象外にしない: H2 の趣旨は「0xE7 系 data が何も取れていない cycle」の検出)
  - dig 論点: `_m_late` (CUMULATIVE_PUBLISH_KEYS) も含めるか → 含める (= 何か 1 つでも実 data が出たら「空」ではない)
- テスト: 統合テスト or DiagState 単体で近似

### Step 6: DIAG_SENSOR_DEFS 登録

- 11 entry 追加 (= diagnostic category)。memory feedback-diag-sensor-defs-publish の教訓: 登録漏れ = MQTT publish されない
- テスト: test_production_tool_layout.py 系の DEFS 網羅テストがあれば追記

### Step 7: compose repo telegraf topics 追加 (= 別 repo、deploy 時)

- `cubej/+/diag/erxudp_rescued_*` 11 topic
- jj 5 step push (= ~/.claude/rules/jj-workflow.md)

## Verify (deploy 後、= 2026-07-03 朝の SC 判定後)

1. bridge `/api/diag` (port 8080) で 11 key 存在
2. gcx series 存在 + SC-1 (= esv 4 本合計 ≒ recovered_from_mismatch 増分)
3. 24h 後 H1/H2/H3 比率判定 → 改修 spec 起票

## Risk

- rescue path は hot path (= 全 mismatch frame で通過)。try/except で diag 失敗を握りつぶす既存 pattern を踏襲し、観測が本流を殺さないこと
- FR-006 で tid_mismatch_lag の series 意味が変わる (= 意図的。docs 経緯は spec に記録済)
