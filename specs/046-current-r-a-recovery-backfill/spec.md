# Feature Specification: current_r_a (0xE8) 救済 frame backfill 拡張 (= spec 028 pattern 踏襲)

**Feature Branch**: `046-current-r-a-recovery-backfill`
**Created**: 2026-07-01
**Status**: Draft
**Input**: 2026-07-01 tako 合議 (= Grafana 途切れ問題改善案) で「current_r_a (0xE8) が backfill 対象外 = power_w と同じ visual gap が残る」 と指摘。 backfill 監査結果で spec 028 (= power_w / 0xE7 のみ) + spec 029 (= 累積系 / energy_*_kwh) は完全機能確認済、 spec 046 で `current_r_a` に同 pattern 拡張。

## Background

### 観測 data (= 2026-07-01 監査結果)

- `power_w_recovered_backfill_total = 21 件` ✅ spec 028 動作中
- `cumulative_recovered_backfill_total = 16 件` ✅ spec 029 動作中
- `current_r_a_recovered_backfill_total = N/A` ❌ **未実装、 0xE8 は backfill 対象外**

### bridge code 現状 (`production_tool/mqtt_bridge.py`)

- `RECOVERY_BACKFILL_KEYS = frozenset(("power_w",))` (= L4341) = spec 028 で power_w (= 0xE7) のみ
- `CUMULATIVE_BACKFILL_KEYS = frozenset(("energy_forward_kwh", ...))` (= L4348) = spec 029 で累積系
- `publish_recovery_backfill(m, send_ts, counter_attr=...)` (= L4354) = helper、 `m` に渡された各 key を `<key>_recovered_json` topic に過去時刻 JSON で publish + counter inc

`current_r_a (= 0xE8)` は decode_measurements の結果に含まれるが、 RECOVERY_BACKFILL_KEYS に未登録のため late frame 救済時に publish_recovery_backfill から漏れる。 結果: ERXUDP TID mismatch recovery で TID 履歴は復活 (= 46 件) するが、 current_r_a 値は過去時刻に backfill されず Grafana で visual gap として残存。

### 仕様根拠

- BP35A1 公式 Ver 1.3.2: ECHONET Lite EPC 0xE8 = 瞬時電流 (= R/T 相)
- bridge で 0xE7 (= 瞬時電力) と 0xE8 (= 瞬時電流) は同一 ECHONET フレームに含まれて返却される (= 同じ TID = 同じ ERXUDP)
- 故に「0xE7 の遅延応答が届く = 0xE8 も同フレーム内」 = 救済可能だが publish path に乗っていない

## Functional Requirements

### FR-001: CURRENT_BACKFILL_KEYS 定数追加

```python
# spec 046: 電流値 (= 0xE8) の救済 frame backfill。 spec 028 と同じ pattern、
# 別 topic + 別 counter で観測解像度を保つ。
CURRENT_BACKFILL_KEYS = frozenset(("current_r_a", "current_t_a"))
```

`current_t_a` (= T 相、 単相 3 線式の片側) は契約により null の場合あり (= 単相 2 線式メーターでは取れない)、 publish_recovery_backfill 内で None ガードあり (= 既存 path で対応済) ので両方含めて OK。

### FR-002: DiagState 拡張

```python
self.current_r_a_recovered_backfill_count = 0  # 互換性のため _r_a_ 名で保持、
# ただし実体は r/t 両方の publish 件数合算 (= spec 028 power_w 同様)
```

### FR-003: snapshot 拡張

```python
out["current_r_a_recovered_backfill_total"] = self.current_r_a_recovered_backfill_count
```

### FR-004: DIAG_SENSOR_DEFS 追加

```python
("current_r_a_recovered_backfill_total",
 "Current R-A Recovered Backfill (= 0xE8)",
 None, None, "total_increasing", "diagnostic"),
```

### FR-005: main loop integration

spec 028 path (= L4712 周辺) の直後 or 同じ if block 内で、 同じ送信時刻 `_late_ts` を流用して `publish_recovery_backfill(m_current, send_ts=_late_ts, counter_attr="current_r_a_recovered_backfill_count")` を発行。

config flag `current_r_a_recovery_backfill_enabled` (= default True) で kill switch。

### FR-006: compose/telegraf.conf 拡張 (= CLAUDE.md ルール遵守)

- `cubej/+/diag/current_r_a_recovered_backfill_total` topic 追加
- 既存の `mqtt_recovery_backfill` consumer は `cubej/+/+_recovered_json` でワイルドカード subscribe しているはず → 確認、 wildcard ならコード変更不要、 明示列挙なら追加

### FR-007: regression test

- `RECOVERY_BACKFILL_KEYS` / `CURRENT_BACKFILL_KEYS` は別 frozenset
- DiagState.current_r_a_recovered_backfill_count 初期値 0 + snapshot 出力
- DIAG_SENSOR_DEFS 登録 label
- publish_recovery_backfill を `current_r_a` 含む m + `counter_attr="current_r_a_recovered_backfill_count"` で呼ぶと counter が inc

## Out of Scope

- 0xE7 / 0xE8 を 1 ECHONET 複数 EPC 取得に統合 (= tako 合議で別途提案、 spec 047 候補)
- 0xE8 の backfill が `power_w` と同 ERXUDP フレームから来る前提を崩すケース (= フレーム分割発生時、 仕様外)
- Grafana 側の current_r_a panel への `current_r_a_recovered_json` 配線 (= dashboard 設定、 別途)

## Success Criteria

- **SC-001**: bridge `RECOVERY_BACKFILL_KEYS` + `CURRENT_BACKFILL_KEYS` 別定義、 main loop で両 path 発行
- **SC-002**: 単体 test pass (= 既存 + 新規 ~3 件、 全体 ~511 件)
- **SC-003**: deploy 後の bridge `/api/diag` で `current_r_a_recovered_backfill_total` ≥ 0 publish 確認
- **SC-004**: deploy 後 24h で `current_r_a_recovered_backfill_total` が `power_w_recovered_backfill_total` と概ね同数 (= ±10%)、 同 ERXUDP フレームから両方救済が機能している実証
- **SC-005**: Grafana で current_r_a の visual gap が power_w 同様の頻度で backfill により埋まる

## Related

- 観測契機: 2026-07-01 tako 合議 + backfill 監査
- 関連 spec:
  - spec 020 (= TID mismatch late publish recovery、 ERXUDP フレーム救済の前段)
  - spec 028 (= power_w / 0xE7 backfill pattern、 本 spec の踏襲元)
  - spec 029 (= 累積系 backfill、 別 counter pattern の先例)
  - spec 040 (= PANA 720s 自動再認証 = 真の解、 本 spec はその補完)
- 関連 memory:
  - [[feedback-erxudp-timeouts-periodic-pana]] (= software 改善余地、 本 spec はその 1 つ)
  - tako 合議 reference (= 別途 memory 化候補)
- CLAUDE.md ルール (= 4 段 pipeline checklist) **4 回目適用**: bridge + compose 同時 update + bridge `/api/diag` + gcx 両 verify
