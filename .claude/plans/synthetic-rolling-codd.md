# Plan: spec 018 Cumulative Energy Fixed (EPC 0xEA/0xEB) Tier 4

## Context

当方の spec 011 C tier rotation で polling EPC を 3 tier に分け、 瞬時電力 (tier1) はリアルタイム、 積算電力量 (tier2 = 0xE0/0xE3、 5 サイクル毎) はゆっくり、 係数 (tier3 = 0xD3/0xE1、 60 サイクル毎) はほぼ静的に取れている。 しかし HA Energy ダッシュボードは **30 分粒度の累積電力量** を期待しており、 現状の tier2 「ポーリング時点の累積値」では:
- ポーリング jitter で 30 分境界がずれる
- bridge 再起動の前後で欠損

ECHONET Lite の **0xEA (定時積算電力量 forward)** / **0xEB (reverse)** は メーター側が記録した 30 分境界の値 + 内蔵 timestamp を返す:
- 11 byte レスポンス: bytes 0-6 = year/month/day/hour/min/sec、 bytes 7-10 = 累積値 uint32 (係数 × 単位適用後 kWh)
- メーター内蔵 clock で記録するので bridge 側の jitter 影響ゼロ
- HA Energy の 30 分粒度仕様に完全一致

hals5412 fork `c884ec3` の発想を取り込む (fork 自体は「全 EPC まとめて Get」思想で当方 tier rotation と非互換なので、 当方は **tier4 として独立に組み込む**)。

## Approach

- 新 `TIER4_EPCS = [0xEA, 0xEB]` (定時積算 fwd/rev)
- `decide_epc_tier(cycle, tier2_every=5, tier3_every=60, tier4_every=30)` 拡張: 優先順 `tier4 > tier3 > tier2 > tier1`
- `apply_defaults` に `epc_tier4_every` (default 30) 追加
- `decode_measurements` に 0xEA/0xEB decoder 追加: meter timestamp + raw uint32 を返す
- `apply_energy_scale` で `_fixed_kwh` 変種に係数 × 単位適用
- `publish_measurements` で 2 新 topic + timestamp 属性発行
- `SENSOR_DEFS` に 2 sensor 追加 (energy, total_increasing)

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **pure helper** (decode_measurements の前、 line 2526 付近):
   ```python
   def decode_cumulative_energy_fixed(epc_bytes):
       """spec 018: parse ECHONET Lite EPC 0xEA / 0xEB (定時積算電力量).

       11-byte response:
         bytes 0-1: year (big-endian uint16)
         byte  2:   month
         byte  3:   day
         byte  4:   hour
         byte  5:   minute
         byte  6:   second
         bytes 7-10: raw cumulative value (uint32)

       Returns (timestamp_iso, raw_value) or None for short / invalid
       input. dig Round 1 決定 1: ISO8601 に `+09:00` JST suffix を付与
       (日本のスマートメーターは JST 設定が標準で、 実機検証で device
       clock が UTC でも meter B-route 側は JST と推定。 deploy 後の
       実機ログで `energy_forward_fixed_ts` の値と device 時刻を **手動
       で突き合わせ**、 明らかにズレているなら +09:00 を見直す)。
       dig Round 1 決定 2: year < 2000 を invalid と判定し None を返す
       (メーター clock 未設定の場合のスキップ動作)。
       """
       if len(epc_bytes) < 11:
           return None
       year = struct.unpack(">H", bytes(epc_bytes[0:2]))[0]
       if year < 2000:
           return None
       month, day, hour, minute, second = (
           int(epc_bytes[2]), int(epc_bytes[3]), int(epc_bytes[4]),
           int(epc_bytes[5]), int(epc_bytes[6]))
       raw = struct.unpack(">I", bytes(epc_bytes[7:11]))[0]
       ts = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+09:00".format(
           year, month, day, hour, minute, second)
       return (ts, raw)
   ```

2. **TIER 定数** (line 2410 付近、 既存 TIER3_EPCS の隣):
   ```python
   TIER4_EPCS = [0xEA, 0xEB]         # 定時積算電力量 fwd/rev — meter timestamp
   ```

3. **`decide_epc_tier`** (line 2415 付近、 シグネチャに `tier4_every` 追加):
   ```python
   def decide_epc_tier(cycle_number, tier2_every=5, tier3_every=60,
                      tier4_every=30):
       """spec 018: tier4 (cumulative energy fixed) wins over tier3 wins
       over tier2 — when multiple cycle-multiple conditions match the
       rarer tier is selected so least-frequent EPCs still refresh."""
       if tier4_every > 0 and cycle_number % int(tier4_every) == 0:
           return "tier4"
       if cycle_number % int(tier3_every) == 0:
           return "tier3"
       if cycle_number % int(tier2_every) == 0:
           return "tier2"
       return "tier1"
   ```
   注: `tier4_every <= 0` で機能無効化 (kill switch)、 既存挙動互換

4. **`epcs_for_tier`** 拡張:
   ```python
   def epcs_for_tier(tier):
       if tier == "tier2":
           return TIER2_EPCS
       if tier == "tier3":
           return TIER3_EPCS
       if tier == "tier4":
           return TIER4_EPCS
       return TIER1_EPCS
   ```

5. **`apply_defaults`** (line 167 付近、 spec 017 ブロックの直後):
   ```python
   # spec 018: cumulative energy fixed (EPC 0xEA/0xEB) tier4. Meter
   # records 30-min-boundary values with internal timestamp. Default
   # every=30 cycles ≈ 30 min at poll_interval=60s. Set 0 to disable.
   out.setdefault("epc_tier4_every", 30)
   ```

6. **`decode_measurements`** (line 2526 付近、 既存 dict に追加):
   ```python
   # EA: cumulative forward energy at 30-min boundary (timestamped, scaled)
   if 0xEA in props:
       ea = decode_cumulative_energy_fixed(props[0xEA])
       if ea is not None:
           result["energy_forward_fixed_ts"] = ea[0]
           result["energy_forward_fixed_raw"] = ea[1]

   # EB: cumulative reverse energy at 30-min boundary (timestamped, scaled)
   if 0xEB in props:
       eb = decode_cumulative_energy_fixed(props[0xEB])
       if eb is not None:
           result["energy_reverse_fixed_ts"] = eb[0]
           result["energy_reverse_fixed_raw"] = eb[1]
   ```

7. **`apply_energy_scale`** (line 2571 付近、 既存ロジックに追加):
   ```python
   if "energy_forward_fixed_raw" in measurements:
       measurements["energy_forward_fixed_kwh"] = (
           measurements["energy_forward_fixed_raw"] * c * u)
   if "energy_reverse_fixed_raw" in measurements:
       measurements["energy_reverse_fixed_kwh"] = (
           measurements["energy_reverse_fixed_raw"] * c * u)
   ```

8. **`SENSOR_DEFS`** (line 3120 付近) 拡張:
   ```python
   ("energy_forward_fixed", "Cumulative Energy Fwd (30min)", "kWh", "energy", "total_increasing"),
   ("energy_reverse_fixed", "Cumulative Energy Rev (30min)", "kWh", "energy", "total_increasing"),
   ```

9. **`publish_measurements`** (line 3247 付近) 拡張:
   ```python
   if "energy_forward_fixed_kwh" in m:
       mqtt.publish("{}/energy_forward_fixed".format(base),
                    "{:.3f}".format(m["energy_forward_fixed_kwh"]))
   if "energy_reverse_fixed_kwh" in m:
       mqtt.publish("{}/energy_reverse_fixed".format(base),
                    "{:.3f}".format(m["energy_reverse_fixed_kwh"]))
   # spec 018: meter-side timestamp as a separate sub-topic.
   # dig Round 1 決定 3: retained=True for ts topics so the last meter
   # timestamp survives broker rebuild (relevant after spec 016 republish).
   # HA state machine itself does not back-fill state with this ts —
   # Grafana/InfluxDB direct queries can use it for historical accuracy.
   if "energy_forward_fixed_ts" in m:
       mqtt.publish("{}/energy_forward_fixed_ts".format(base),
                    m["energy_forward_fixed_ts"], retain=True)
   if "energy_reverse_fixed_ts" in m:
       mqtt.publish("{}/energy_reverse_fixed_ts".format(base),
                    m["energy_reverse_fixed_ts"], retain=True)
   ```
   注: `mqtt.publish(... retain=True)` の引数が既存 MQTTClient に対応しているか実装直前に確認 (既存 publish_ha_discovery が retain=True で呼んでいるので対応済の見込み)。

10. **main loop** (line 3068 付近、 既存の `cycle_epcs = epcs_for_tier(tier)` 経路): `decide_epc_tier` シグネチャ拡張で `tier4_every=int(cfg.get("epc_tier4_every", 30))` を渡すよう改修

### `tests/unit/test_decode_cumulative_energy_fixed.py` (新規)

pure helper の TDD:
- `test_decodes_normal_11_byte_payload_with_jst_suffix` (固定された 11 byte で `+09:00` 付き ts + raw uint32 取得)
- `test_returns_none_for_short_payload` (10 byte 以下)
- `test_returns_none_for_year_less_than_2000` (年=0、 年=1999 等 invalid を弾く、 dig Round 1 決定 2)
- `test_handles_extreme_uint32_value` (0xFFFFFFFF)
- `test_accepts_year_2000_as_lower_boundary` (= 2000 は valid、 strictly less than 2000 のみ invalid)

### `tests/unit/test_epc_tier.py` (拡張)

既存 tier rotation テストに追加:
- `test_tier4_at_every_30_cycles_default`
- `test_tier4_wins_over_tier3_when_both_match`
- `test_tier4_disabled_when_every_zero`
- `test_epcs_for_tier_tier4_returns_ea_eb`

### `tests/unit/test_existing_pure.py` または新規 `test_decode_measurements_fixed.py` (decode_measurements integration テスト)

- `test_decode_measurements_decodes_0xEA_forward`
- `test_decode_measurements_decodes_0xEB_reverse`
- `test_apply_energy_scale_applies_to_fixed_variants`

## Test list (TDD 順)

1-4. **Red**: `decode_cumulative_energy_fixed` 4 件 (pure helper TDD)
5-8. **Red**: `decide_epc_tier` / `epcs_for_tier` 拡張 4 件
9-11. **Red**: `decode_measurements` + `apply_energy_scale` integration 3 件
12. main loop integration (`decide_epc_tier` call site) はテストせず実機検証

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 377 + 新規 ~12 = ~389 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy
4. 実機で:
   - 30 分後に新 MQTT topic `cubej/cubej1/energy_forward_fixed` (値) + `..._ts` (timestamp) が publish される
   - HA discovery で `cumulative_energy_fwd_30min_kwh` sensor が出現、 device_class=energy で Energy ダッシュボードに選択可能
5. 長期効果 (1 ヶ月程度): HA Energy 月次集計 vs メーター物理表示値 (= 検針) の誤差が ±0.1 kWh 以内 (spec.md SC-003)

## Commit 戦略

これが最後の spec (017/018/019 から残り無し)、 stash 不要、 シンプル commit:
- redact plan
- jj commit (spec.md + plan + 実装 + tests)
- main forward push
- deploy

## Commit message

`feat(bridge): 定時積算電力量 (EPC 0xEA/0xEB) tier4 で HA Energy 精度向上 (spec 018)`
