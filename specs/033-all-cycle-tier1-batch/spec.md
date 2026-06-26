# Feature Specification: All-Cycle Tier1 Batch (= spec 011 E: 全 cycle で tier1 EPCs を OPC batch)

**Feature Branch**: `033-all-cycle-tier1-batch`
**Created**: 2026-06-26
**Status**: Draft
**Input**: 2026-06-26 spec 032 deploy 1h verify で「沈黙時間 3 倍短縮 + timeouts 40% 改善」 達成、 ただし sparseness 完全解消ならず + spec 028 backfill 1h で 0 件 (= tier1 cycle mismatch ゼロ偶発)。 subagent reference 調査で「broute-mqtt は OPC=4 batch (= 70s 周期 1 frame で複数 EPC まとめ取得)」 確認、 cube-j1-mqtt は排他 1 tier 送信 (= 1 cycle で 1-2 EPC のみ)。 spec 011 E (= 仮称) = 全 cycle に tier1 を含めて 1 frame batch する改善。

## Background

cube-j1-mqtt の現状 (= spec 011 C tier rotation):
- 排他 1 tier 送信: cycle 内で tier1 (= 0xE7/0xE8 瞬時) or tier2 (= 0xE0/0xE3 累積) or tier3 (= 0xD3/0xE1 係数) or tier4 (= 0xEA/0xEB 定時積算) のいずれか単独
- decide_epc_tier(cycle_number, ...) で cycle ごとに優先順位 (= tier4 > tier3 > tier2 > tier1) で 1 tier 決定

**問題**:
1. **mismatch 発生時の `m` dict は当該 tier の EPCs のみ含む** = tier2/3/4 cycle で mismatch 起きた frame は `m["power_w"]` 不在 → spec 028 backfill (= `RECOVERY_BACKFILL_KEYS = {"power_w"}`) skip
2. [[feedback-cycle-counter-reconnect-tier4]] 構造的問題: reconnect 直後 cycle 0 = `0%30==0` で **必ず tier4 (= cumulative-only)** → 直後の mismatch (= 頻発時期) は spec 028 backfill 必ず skip
3. spec 032 deploy 1h で spec 028 backfill **0 件発火** (= mismatch 6 件全部 tier2/3/4 cycle 偶発)、 「panel-1 黄色 dot」 が見えない継続原因

**改善案 (= spec 011 E)**: 全 cycle で **tier1 を batch で必ず含める** = OPC=4 で 1 frame に tier1 + tier-specific EPCs まとめ送信:
- tier1 cycle: `[0xE7, 0xE8]` (= 既存通り)
- tier2 cycle: `[0xE7, 0xE8, 0xE0, 0xE3]` (= OPC=4 batch)
- tier3 cycle: `[0xE7, 0xE8, 0xD3, 0xE1]` (= OPC=4 batch)
- tier4 cycle: `[0xE7, 0xE8, 0xEA, 0xEB]` (= OPC=4 batch)

効果:
- **全 cycle mismatch frame に `power_w` 含む** = spec 028 backfill 救済率 100% (= 偶発依存なし)
- reconnect 直後 cycle 0 (= tier4) でも tier1 含む = `feedback-cycle-counter-reconnect-tier4` 構造的問題解決
- 「panel-1 黄色 dot」 が **mismatch 発火時 必ず plot** される、 真の連続瞬時電力可視化

## Scope

### A. 新 helper `cycle_epcs_with_tier1(tier)` (= pure function)

```python
def cycle_epcs_with_tier1(tier):
    """spec 033 (= spec 011 E): 全 cycle で tier1 EPCs を含めて OPC batch.
    
    tier1 cycle はそのまま TIER1_EPCS、 それ以外は TIER1_EPCS + tier 固有 EPCs。
    1 frame で OPC=4 まとめ取得 = mismatch 発火時 100% spec 028 backfill 対象、
    [[feedback-cycle-counter-reconnect-tier4]] 構造的問題も解決.
    """
    if tier == "tier1":
        return list(TIER1_EPCS)
    return list(TIER1_EPCS) + list(epcs_for_tier(tier))
```

### B. main loop の cycle_epcs 設定統一

既存 line 4083 + 4090 + 4101 で `cycle_epcs = ...` 個別計算 → 統一 helper 経由:
- line 4083 (= 普通 probe-not tier): `cycle_epcs = cycle_epcs_with_tier1(tier)`
- line 4090 (= 既存 TIER1_EPCS): `cycle_epcs = cycle_epcs_with_tier1("tier1")` (= 結果 TIER1_EPCS と同等)
- line 4101 (= 通常 path): `cycle_epcs = cycle_epcs_with_tier1(tier)`

probe cycle (= line 4076) は **除外** (= PROBE_EPCS = [0x80] のみ)、 既存挙動完全保護。

### C. 既存実装活用

- `build_el_get(tid, epcs)` (= line 2889) は既に list 対応 (= OPC=N)、 変更不要
- `send_el_get(fd, ipv6, tid, epc_list=None)` (= line 3059) は同じく list 対応、 変更不要
- `decode_measurements(parse_el_response(payload))` も 1 frame 内複数 EPC parse 対応 (= 既存実装)
- send_history.record(sent_tid, time.time(), cycle_epcs) も list 対応 = ring に 4 EPC list 保存、 spec 020 v1.5 lookup_latest fallback とも整合

### D. EPC 数増加で発生し得る trade-off

- **メーター応答 frame size 増加**: 4 EPC で 1 frame = 通常 100-200 bytes、 ECHONET Lite MTU 内 OK
- **メーター応答時間増加**: 1 EPC vs 4 EPC で応答時間差は通常微小 (= 数 ms)、 spec 032 erxudp_timeout_sec=6s に対し十分余裕
- **MQTT/HA publish trafic 増加**: 既存 publish_measurements は `m` dict を key 単位で publish、 4 EPC 含む `m` で publish 件数 2 倍 = MQTT broker / telegraf に微増負荷、 1 hour 60 cycle × 4 EPC = 240 publish/h、 既存 telegraf pipeline で問題なし

## Non-Scope

- tier2 / tier3 / tier4 の rotation 周期変更 (= 既存 spec 011 C 維持、 5/60/30 cycle)
- polling 周期変更 (= 60s 維持、 spec 032 の defaults とも整合)
- SKSCAN 固定 / 適応 (= spec 011 F 候補で別 spec)
- spec 020 v1.5 / spec 028 / spec 029 救済ロジック変更 (= 既存挙動完全保護)
- spec 027 v2 (= wall-clock + health probe) との統合 (= 別 spec、 spec 032 で代替済)

## User Scenarios

### Primary User Story

ユーザは spec 033 deploy 後の grafana で:
1. **panel-1 Instantaneous Power** で mismatch 発火時 **必ず黄色 dot (= spec 028 backfill)** が plot される (= 偶発依存解消)
2. **bridge restart / wisun reconnect 直後** も初 cycle で tier1 含む → 直後の mismatch も 100% backfill 対象
3. **spec 028 backfill 発火率** が現状 (= 1h 0 件 / 9.5h 3 件) から **mismatch 発生数 × 100%** に上昇 (= 救済率と同等の信頼性)

### Acceptance Scenarios

1. **Given** cycle が tier2 cycle (= cycle % 5 == 0、 非 tier3/4)、 **When** `cycle_epcs_with_tier1("tier2")`、 **Then** `[0xE7, 0xE8, 0xE0, 0xE3]` (= OPC=4)
2. **Given** cycle が tier4 cycle、 **When** `cycle_epcs_with_tier1("tier4")`、 **Then** `[0xE7, 0xE8, 0xEA, 0xEB]`
3. **Given** cycle が tier1 cycle、 **When** `cycle_epcs_with_tier1("tier1")`、 **Then** `[0xE7, 0xE8]` (= 既存通り、 重複なし)
4. **Given** 実機 deploy 後 mismatch 発生、 **When** spec 028 backfill 経路、 **Then** `power_w_recovered_backfill_total` increment (= 偶発でなく必ず発火)
5. **Given** reconnect 直後 cycle 0 (= tier4)、 **When** mismatch 発生、 **Then** `m["power_w"]` 含む → spec 028 backfill 発火 (= 過去の [[feedback-cycle-counter-reconnect-tier4]] 構造的問題解消)

## Requirements

### Functional Requirements

- **FR-001**: `cycle_epcs_with_tier1(tier)` pure helper を `epcs_for_tier` の隣 (= 約 line 2856) に追加
- **FR-002**: main loop の `cycle_epcs = ...` 3 箇所 (= line 4083 / 4090 / 4101) を統一 helper 経由に変更
- **FR-003**: PROBE cycle (= line 4076) は変更しない (= PROBE_EPCS = [0x80] 維持)
- **FR-004**: 既存 `build_el_get` / `send_el_get` / `decode_measurements` / `send_history.record` は変更不要 (= 既に OPC=N list 対応)
- **FR-005**: 既存 spec 020 v1.5 / spec 028 / spec 029 救済ロジック完全保護
- **FR-006**: 既存 spec 011 C tier rotation (= tier2_every / tier3_every / tier4_every) 完全保護
- **FR-007**: 既存 spec 032 aggressive polling defaults (= timeout 6 / retries 3 / threshold 6) 完全保護

### Key Entities

- `cycle_epcs_with_tier1` (= 新 pure helper)、 `TIER1_EPCS` / `epcs_for_tier` (= 既存利用)

## Success Criteria

- **SC-001**: 単体テスト: `cycle_epcs_with_tier1` の 4 tier (+ 未知 tier?) で期待 EPCs 返却確認、 計 4-5 件
- **SC-002**: 既存テスト全件 pass (= 457 → ~461 件、 spec 028/029/032 既存挙動互換)
- **SC-003**: 実機 deploy 後 1h 観察:
  - `power_w_recovered_backfill_total` が mismatch 発火と同期 (= 発火率 = mismatch 発火率)
  - panel-1 黄色 dot が 1h 内に plot される (= spec 032 1h で 0 件 → spec 033 で >= 1 件期待)
  - 既存 polling 安定度 (= timeouts/h / reconnects/h / 沈黙時間) は spec 032 1h verify と同等以上維持
- **SC-004**: 1 週間 long-term 観察で「panel-1 黄色 dot 密度 = mismatch 発火密度」 整合確認、 ARIB duty cycle / メーター rate-limit による副作用無し

## Assumptions

- ECHONET Lite frame で OPC=4 batch は BP35CX / メーター双方で安定動作 (= broute-mqtt 等 reference 実装で実証済、 1 frame size 100-200 bytes は MTU 内)
- メーター応答時間は EPC 数増加でも数 ms 増程度、 spec 032 erxudp_timeout_sec=6s に十分余裕
- spec 011 C tier rotation の「tier2/3/4 低頻度更新」 思想は維持 (= 累積/係数/定時積算は毎回取らない)、 ただし「tier1 を 全 cycle 含めて batch」 で情報密度向上
- 関連 spec: [[spec-011-c-tier-rotation]] (= 元 tier rotation)、 [[spec-028-instantaneous-power-recovery]] (= backfill 発火対象拡大)、 [[spec-029-cumulative-energy-recovery-backfill]] (= 同じく)、 [[spec-032-aggressive-polling-defaults]] (= 並列改善、 spec 032 1h で 0 件発火だった backfill 問題を spec 033 で構造的に解消)
- 関連 memory: [[feedback-cycle-counter-reconnect-tier4]] (= 構造的問題が spec 033 で解決)、 [[reference-tako-instantaneous-power-architectural-cap]] (= software 改善余地の最終形態として spec 033 が位置付け)
