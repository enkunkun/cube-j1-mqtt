# Plan: spec 033 All-Cycle Tier1 Batch (= spec 011 E: 全 cycle で tier1 EPCs を OPC batch)

## Context

spec 032 deploy 1h verify で「沈黙時間 3 倍短縮 + timeouts 40% 改善」 達成、 ただし **spec 028 backfill 1h で 0 件発火** (= mismatch 6 件全部 tier2/3/4 cycle 偶発)、 「panel-1 黄色 dot」 見えない継続。 真因 = cube-j1-mqtt は **排他 1 tier 送信** で tier2/3/4 cycle mismatch の `m` に `power_w` 不在 → spec 028 backfill skip + [[feedback-cycle-counter-reconnect-tier4]] 構造的問題 (= reconnect 直後 cycle 0 = tier4 で必ず skip)。

subagent reference 調査で「broute-mqtt は OPC=4 batch (= 70s 周期 1 frame で複数 EPC まとめ取得)」 確認、 cube-j1-mqtt も batch 化で改善余地大。 spec 033 = **「全 cycle で tier1 (= 0xE7/0xE8) を必ず含めて OPC batch」** で mismatch 発火時 100% spec 028 backfill 対象化、 構造的問題解決。

完成後の見込み: spec 028 backfill 発火率 = mismatch 発火率 (= 偶発依存解消)、 panel-1 黄色 dot が必ず plot される。

## Approach

### v1 MVP の構成

1. **新 pure helper `cycle_epcs_with_tier1(tier)`** を `epcs_for_tier` の隣 (= 約 line 2856) に追加
2. **main loop の cycle_epcs 設定 3 箇所統一**: line 4083 + 4090 + 4101 を `cycle_epcs = cycle_epcs_with_tier1(tier)` 経由に変更
3. **PROBE cycle (= line 4076)** は変更しない (= 既存挙動完全保護)

### 設計上の判断 (dig round 1 で確定済)

- **OPC=4 batch は既存 `build_el_get` / `send_el_get` / `decode_measurements` 全部対応済** (= subagent 調査 + grep 確認)、 新規実装不要
- **tier1 cycle path の `cycle_epcs_with_tier1("tier1")` = `[0xE7, 0xE8]`** (= 既存 TIER1_EPCS と同等、 重複なし)
- **send_history.record は list 対応** (= ring に 4 EPC list 保存、 spec 020 v1.5 lookup_latest fallback とも整合、 変更不要)
- **`m` dict は EPC 単位 key で複数 entries 含む可能性** (= 既存 decode_measurements が parse_el_response の loop で全 EPC parse)、 publish_measurements も既存通り key 単位で publish
- **trade-off**: MQTT publish 件数 微増 (= tier2/3/4 cycle で tier1 EPCs 2 件追加 publish)、 既存 telegraf pipeline で問題なし
- **既存 spec 011 C tier rotation 思想完全保護**: tier2/3/4 の rotation 周期 (= 5/60/30) 不変、 ただし tier1 を全 cycle 含めて情報密度向上 (= spec 011 C 「tier1 = real-time」 意図に完全合致)
- **dig A 決定 — 未知 tier 重複処理**: tier1 cycle と **同じ path にマージ** (= `if tier in ("tier1",): return list(TIER1_EPCS)` ではなく、 `epcs_for_tier(tier)` の戻り値が TIER1_EPCS と同一 (= 未知 tier fallback 含む) なら重複追加しない判定)。 シンプル実装: tier2/3/4 のいずれかのみ batch、 それ以外は単 tier1。 ECHONET Lite 重複 EPC risk (= BP35CX / メーター動作未検証) 回避
- **dig B 決定 — kill switch**: **不要**、 spec 033 は spec 011 C tier rotation 強化として完全置換 (= rollback config 不要、 YAGNI、 spec 028/029/032 の「新機能 escape hatch」 とは性質が違う、 既存挙動の認識ずれ修正)

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **`cycle_epcs_with_tier1` 新 pure helper** (= `epcs_for_tier` 直後、 約 line 2856、 dig A 決定で未知 tier 重複ゼロ化):
   ```python
   def cycle_epcs_with_tier1(tier):
       """spec 033 (= spec 011 E): 全 cycle で tier1 EPCs を含めて OPC batch.

       tier2/tier3/tier4 のみ tier1 EPCs を合成、 それ以外 (= tier1 / 未知 tier
       fallback) は TIER1_EPCS のみ返す = 重複 EPC 送信回避 (= ECHONET Lite
       重複 EPC グレーゾーン、 BP35CX/メーター動作未検証 risk 回避)。
       1 frame で OPC=4 まとめ取得 = mismatch 発火時 100% spec 028 backfill 対象、
       [[feedback-cycle-counter-reconnect-tier4]] 構造的問題も解決。
       """
       if tier in ("tier2", "tier3", "tier4"):
           return list(TIER1_EPCS) + list(epcs_for_tier(tier))
       return list(TIER1_EPCS)
   ```

2. **main loop の cycle_epcs 設定統一** (= line 4083 + 4090 + 4101):
   - line 4083: `cycle_epcs = cycle_epcs_with_tier1(tier)`
   - line 4090: `cycle_epcs = cycle_epcs_with_tier1("tier1")` (= 結果 list(TIER1_EPCS) と同等)
   - line 4101: `cycle_epcs = cycle_epcs_with_tier1(tier)`

   line 4076 (= PROBE) は変更しない。

### `tests/unit/test_cycle_epcs_with_tier1.py` (= 新規)

6 件 (= dig A で未知 tier guard test 1 件追加):
- `test_tier1_returns_tier1_epcs_only` (= ["tier1"] → [0xE7, 0xE8])
- `test_tier2_returns_tier1_plus_tier2_epcs` (= ["tier2"] → [0xE7, 0xE8, 0xE0, 0xE3])
- `test_tier3_returns_tier1_plus_tier3_epcs` (= [0xE7, 0xE8, 0xD3, 0xE1])
- `test_tier4_returns_tier1_plus_tier4_epcs` (= [0xE7, 0xE8, 0xEA, 0xEB])
- `test_unknown_tier_returns_tier1_only_no_duplication` (= ["unknown"] → [0xE7, 0xE8]、 重複ゼロ guard)
- `test_returns_list_not_tuple` (= 既存 list 慣習保護、 mutable 操作可能)

## Test list (TDD 順)

1. **Red→Green**: `test_tier1_returns_tier1_epcs_only` (= 既存挙動互換)
2. **Red→Green**: `test_tier2_returns_tier1_plus_tier2_epcs` (= spec 033 core)
3. **Red→Green**: `test_tier3_returns_tier1_plus_tier3_epcs`
4. **Red→Green**: `test_tier4_returns_tier1_plus_tier4_epcs`
5. **Guard**: `test_returns_list_not_tuple`
6. main loop 配線 = 実機検証 (= SC-003 で「mismatch 発火 → spec 028 backfill 100% 発火」 確認)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 457 + 新規 5 = ~462 件 pass
2. ruff check 新規エラー無し
3. cube-j1-mqtt 1 commit + jj push --remote fork (= forward only)
4. lab-ub01 経由 deploy (= adb_push_update.sh cube-j1.home.arpa)
5. 実機 1h 観察:
   - `/api/diag` で `power_w_recovered_backfill_total` が mismatch 発火と同期 (= 発火率 = mismatch 発火率)
   - panel-1 黄色 dot が 1h 内 plot (= spec 032 1h で 0 件 → spec 033 で >= 1 件期待)
   - polling 安定度 (= timeouts/h / reconnects/h) は spec 032 と同等以上維持
6. SC-004 (= 1 週間 long-term 観察) は別セッションで継続

## Commit 戦略

- compose 不要 (= telegraf 変更なし)
- cube-j1-mqtt 1 commit + push (= 私が直接、 subagent rate limit risk 回避)
- redact-plans.sh
- jj git push --remote fork --bookmark main で forward only

## Commit message

`feat(bridge): 全 cycle で tier1 EPCs を OPC batch 含めて mismatch 発火時 100% spec 028 backfill 対象化 (spec 033 = spec 011 E)`
