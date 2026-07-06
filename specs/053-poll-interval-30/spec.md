# Feature Specification: threshold=1 + poll_interval=30 の default 昇格

**Feature Branch**: `053-poll-interval-30`
**Created**: 2026-07-06
**Status**: Draft
**Input**: 2026-07-06 の 2 段 config override A/B (ユーザー承認済み)。live 41.3/h → 98.7/h (2.39×) をコード変更ゼロで実証。observation-2026-07-06.md および specs/050-erxudp-force-reconnect-threshold-2/observation-2026-07-06-threshold1.md 参照。

## A/B 実証 (2026-07-06、いずれも 3h 窓)

| 構成 | live (dedupe) | reconnect | timeout/poll 比 | gap>90s |
|---|---|---|---|---|
| th=2 + 60s (前日同帯) | 41.3/h | 11.3/h | ~40% | 40 件 |
| th=1 + 60s (06:10〜09:10) | 51.2/h (+24%) | 14.7/h | ~24% | 5 件 |
| th=1 + 30s (09:22〜12:22) | **98.7/h (+139%)** | 18.7/h | **~16%** | 5 件 |

- threshold=1 成立の前提: spec 048 (INF filter で誤分類排除) + spec 049 (noise timeout の consecutive counter 除外)。この 2 つ以前は noise 帯で reconnect storm になるため単独 backport 不可
- 「poll 頻度が session death を加速する」仮説は棄却: death rate +27% に対し poll は +100%、poll あたり timeout 率はむしろ半減
- 死 1 回の実質欠損 ~28s (速い経路 ~21s = timeout 8s + backoff 5s + rejoin ~8s が 90%)

## Requirements

- **FR-001**: `apply_defaults` の `erxudp_timeout_force_reconnect_threshold` default を **2 → 1** に変更
- **FR-002**: `apply_defaults` の `poll_interval` default を **60 → 30** に変更 (`MIN_POLL_INTERVAL_SEC = 30` の floor とちょうど一致、clamp ロジック不変)
- **FR-003**: main loop の inline fallback `cfg.get("erxudp_timeout_force_reconnect_threshold", 5)` を 1 に整合 (apply_defaults が常に key を埋めるため挙動不変の tidy)
- **FR-004**: deploy 後、実機 config.json から `erxudp_timeout_force_reconnect_threshold` (override) と `poll_interval` (upstream key、明示 60→削除で default 委譲) の両 key を削除し、全 tuning を repo default に再集約 ([[feedback-config-setdefault-override]] 回避)
- **NFR-001**: cycle 数基準の周期 (`epc_tier4_every=30` ≈ 旧 30 分 → 新 15 分、spec 033 tier 判定等) は wall time が半減するが**変更しない** — A/B 3h はこの挙動込みで実測済み、tier4 の重複読みは無害 (30 分境界値の再読)
- **NFR-002**: ARIB STD-T108 duty cycle (spec 013 コメントの 360s/h 懸念): 120 poll/h の TX airtime は数秒/h オーダーで上限に対し 2 桁の余裕。poll_interval < 30 への引き下げは引き続き不可 (floor 維持)
- **NFR-003**: burst mode (spec 022、5s 間隔) の各種 default は対象外

## Success Criteria

- **SC-1**: deploy + override 削除後、挙動不変 (= override 値と default 値が同一のため)。/api/config で両 key 不在を確認
- **SC-2**: 以後 24h で live ≥ 90/h (夜間・noise 帯込みの平均)、300s 超 gap が 2 件/3h 以下
- **SC-3**: reconnect ≤ 30/h 維持 (新監視線。旧 20/h 線は poll 倍増に伴い再定義)

## 監視条件の再定義 (本 spec 以降)

| 項目 | 旧 (60s 時代) | 新 |
|---|---|---|
| 理想 live rate | 60/h | 120/h |
| reconnect 異常線 | 20/h | 30/h |
| death 判定 gap | >90s | >50s |
| dashboard rate 窓 | 更新間隔×4 = 4m | 2m に短縮可 (別途) |

## 関連参照

- spec 050 (threshold 6→2)、spec 052 (backoff 30→5)、spec 048/049 (threshold=1 の前提)
- observation-2026-07-06.md (poll 30s A/B)、specs/050-.../observation-2026-07-06-threshold1.md (threshold=1 A/B、gcx maxDataPoints の測定罠含む)
