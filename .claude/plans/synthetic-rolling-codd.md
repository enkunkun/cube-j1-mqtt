# Plan: spec 032 Aggressive Polling Defaults (= broute-mqtt 並み短 timeout + 早期 reconnect + retry 増)

## Context

2026-06-26 subagent 調査で他社 OSS 4 実装 (= hsakoh/broute-mqtt 等) との対比で **cube-j1-mqtt が保守的すぎる** ことが判明:
- `erxudp_timeout_sec=30s` (broute-mqtt 5s の 6 倍)
- `erxudp_intra_cycle_retries=0` (broute-mqtt 3)
- `erxudp_timeout_force_reconnect_threshold=30` (broute-mqtt 数分復帰、 cube は 30 分死)

同日 device override PoC (= 30→6 / 0→3 / 30→6) で 33 分実証:
- last_poll_success_ts: 28 分前 (= 沈黙) → **2 分前** ✅
- timeouts/h rate: 49/h → **18/h** (= 約 1/3 改善)
- backfill 発火率: 5.7 倍
- reconnect 頻度: ほぼ同等 (= polling 健全化で reconnect 必要性自体減少)

tako 「architectural cap」 結論 (= 2026-06-26 5 agent 合議、 spec 031 へ pivot しかけた) を user 反論「Nature Remo E が成立してる」 + reference 実装調査 + PoC で **完全反証**。 spec 011 D 系の改善余地が実証済、 default 化で恒久反映。

## Approach

最小実装 (= config 3 default 変更だけ、 main loop / read_erxudp / force_reconnect 既存実装互換):

1. **`apply_defaults`** 3 行変更:
   - line 122: `erxudp_timeout_force_reconnect_threshold` 30 → 6
   - line 146: `erxudp_timeout_sec` 30 → 6
   - line 147: `erxudp_intra_cycle_retries` 0 → 3
2. **既存 spec 023/025 burst 専用 default** 維持 (= burst 中だけ別値、 base のみ変更)
3. **Step 0**: device config.json で PoC override (= 3 keys explicit) を削除 (= default 化効果の純粋計測)
4. **tier batch** は **別 spec 011 E に分離** (= 既存構造変更要、 spec 032 とは独立進行可)

### 設計上の判断 (dig 待ち)

- **counter naming**: 既存 `erxudp_timeout_sec` / `erxudp_intra_cycle_retries` / `erxudp_timeout_force_reconnect_threshold` をそのまま使用、 rename しない
- **default 値 6 / 3 / 6 の根拠**: broute-mqtt (= reference) の 5s/3retry を踏襲、 ただし threshold は **6 cycle = 6 分 ≒ 反応性 + 安定性の中間** で 6 採用 (= broute-mqtt 直接対応値なし、 cube-j1 60s 周期ベースで判断)
- **PoC 33 分の小サンプル制約**: 1 週間運用で再評価 (= SC-005)
- **kill switch**: `setdefault` 標準挙動で device explicit override は尊重 (= 旧値復帰可、 緊急時 escape hatch)
- **spec 027 巻き戻し**: spec 027 で 5→30 にした threshold を 30→6 で巻き戻し、 spec 027 v2 (= wall-clock + health probe + 段階的 recovery) の **代替** 位置付け = 単純 threshold 調整で済むなら大規模再設計不要

## Files to modify

### `production_tool/mqtt_bridge.py`

3 行変更:
```python
# line 122
out.setdefault("erxudp_timeout_force_reconnect_threshold", 6)  # 30 → 6 (spec 032)
# line 146
out.setdefault("erxudp_timeout_sec", 6)  # 30 → 6 (spec 032)
# line 147
out.setdefault("erxudp_intra_cycle_retries", 3)  # 0 → 3 (spec 032)
```

### `tests/unit/test_apply_defaults_spec_032.py` (= 新規、 dig A 決定)

spec 027 pattern 踏襲、 spec 単位 1 file:
- `test_default_erxudp_timeout_sec_is_6` (= 30→6 default)
- `test_default_erxudp_intra_cycle_retries_is_3` (= 0→3 default)
- `test_default_erxudp_timeout_force_reconnect_threshold_is_6` (= 30→6 default)
- `test_explicit_override_preserved_for_aggressive_polling_keys` (= 全 3 keys の override 維持 guard)

### spec 027 既存 test の扱い (= dig B 決定)

**delete**: `tests/unit/test_apply_defaults_force_reconnect_threshold.py` を完全削除。 spec 032 で default 巻き戻し済 = spec 027 test も意味失う、 codebase クリーン化。 spec 027 spec.md は **歴史記録として残す** (= delete しない、 「2026-06-25 deploy → 2026-06-26 spec 032 で巻き戻し」 の経緯記録)。

## Step 0: 着手前確認 + PoC override 削除

```bash
ssh lab-ub01 'adb shell cat /data/local/config.json' \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); [print("%s = %s" % (k, c.get(k, "<not set>"))) for k in ["erxudp_timeout_sec", "erxudp_intra_cycle_retries", "erxudp_timeout_force_reconnect_threshold"]]'
```

PoC で適用済 (= 6/3/6) なので all 3 keys が set されているはず。 **deploy 直前** に device config.json から該当 3 keys を **削除** (= default 化効果の純粋計測):
```bash
ssh lab-ub01 'adb pull /data/local/config.json /tmp/c.json && python3 -c "
import json
c = json.load(open(\"/tmp/c.json\"))
for k in [\"erxudp_timeout_sec\", \"erxudp_intra_cycle_retries\", \"erxudp_timeout_force_reconnect_threshold\"]:
    c.pop(k, None)
json.dump(c, open(\"/tmp/c.json\", \"w\"), indent=2)
" && adb push /tmp/c.json /data/local/config.json && adb shell pkill -f mqtt_bridge.py'
```

## Test list (TDD 順)

1. **Red→Green**: `test_default_erxudp_timeout_sec_is_6` (= 30→6 default 変更)
2. **Red→Green**: `test_default_erxudp_intra_cycle_retries_is_3` (= 0→3 default 変更)
3. **Red→Green**: `test_default_erxudp_timeout_force_reconnect_threshold_is_6` (= 既存 spec 027 test 更新、 30→6)
4. **Guard**: `test_explicit_override_preserved_for_aggressive_polling_keys` (= setdefault 標準挙動 retainment、 3 keys 同時)
5. main loop / read_erxudp / force_reconnect 配線は既存実装互換 = 実機検証

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 458 + 新規 ~4 = ~462 件 pass (= spec 027 既存 test 更新を含む)
2. ruff check 新規エラー無し
3. cube-j1-mqtt 側 commit + jj push (= main fork forward only)
4. **Step 0 deploy 前確認** (= device config.json で 3 keys explicit override 削除)
5. lab-ub01 経由 deploy (= adb_push_update.sh cube-j1.home.arpa)
6. 実機 1h 観察:
   - `/api/diag` で 3 default が新値 (= 6/3/6) 反映確認
   - `rate(erxudp_timeouts_total[1h])` ≤ 20/h (= PoC 前 49/h から大幅改善)
   - `last_poll_success_ts` が 5 分以内に更新継続
   - `wisun_reconnects/h` ≤ 5/h (= 過剰 reconnect 防止)
   - panel-1 黄色 dot (= spec 028 backfill) の密度上昇 (= PoC 5.7 倍)

## Commit 戦略

- compose 不要 (= telegraf 変更なし、 bridge のみ修正)
- cube-j1-mqtt 1 commit + push (= bg subagent)、 redact-plans.sh、 jj git push --remote fork
- Step 0 device config 削除は **deploy 直前** に挟む

## Commit message

`feat(bridge): polling defaults を broute-mqtt 並みに集約攻撃化 (timeout 30→6 / retries 0→3 / threshold 30→6) — spec 032 で spec 027 巻き戻し兼 reference 実装対比改善`
