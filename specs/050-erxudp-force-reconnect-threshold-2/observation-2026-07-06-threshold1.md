# threshold=1 A/B 記録 (spec 050 後継): 合格、live +24%

**override**: `erxudp_timeout_force_reconnect_threshold: 1` を 2026-07-06 06:08:44 JST に適用 (config override のみ、コード変更なし)。
**判定**: 2026-07-06 09:16 JST。A 窓 = 06:10〜09:10 JST (threshold=1)、B 窓 = 前日同帯 07-05 06:10〜09:10 JST (threshold=2)。

## 3h 実測 (前日同帯比較)

| 指標 | B: threshold=2 | A: threshold=1 |
|---|---|---|
| live rate (timestamp dedupe) | 41.3/h | **51.2/h (+24%)** |
| reconnect | 11.3/h | 14.7/h |
| erxudp timeout | 24.3/h | **14.3/h (-41%)** |
| gap>90s | 40 件 (120s/140s cluster) | **5 件** |
| max gap | 309s | 334s |

## 判定

- **合格** (live 同等以上 + reconnect ≤20/h を両方満たす)。default 昇格 (2→1) を spec 化する
- gap 分布の解釈: threshold=2 の 120s/140s cluster = 「死 → 失敗 poll 2 回 → reconnect」の署名。threshold=1 はこれを 79〜86s の slip (= slot 損失ゼロ、遅延 ~21s のみ) に変換した。死 1 回の実質欠損は加重平均 **~28s** (速い経路 ~21s × 90% + reconnect 一発失敗の遅い経路 ~92s × 10%)
- timeout が半減したのは会計上の当然 (死 1 回が消費する timeout が 2→1)。真の死 rate は reconnect /h ≈ 14.7 で、増分 +3.4/h は「単発 timeout も即 reconnect に変換される」設計どおりのコスト。storm 化なし
- threshold=1 成立の前提は spec 049 (noise timeout の counter 除外) と spec 048 (INF 誤分類排除)。この 2 つ以前に threshold=1 にすると noise 帯で reconnect storm になるはずで、default 昇格の spec には前提依存として明記する

## 測定手法の注意 (再発防止)

gcx の range query は **maxDataPoints 上限 (~90-120 点) で step を silent に引き延ばす**。3h 窓 + step 15s 指定が実効 step ~2 分になり、初回集計で live 51.2/h → 28.2/h、gap 5 件 → 68 件に化けた (dedupe 法は step < poll 周期が前提)。対策 = 窓を 20 分 chunk に分割して 15s step を守る (scratchpad の ab_window.py 参照)。同一手法の B 窓も等しく壊れるため「比較だから相殺される」は成り立たない (gap 分布が完全に artifact になる)。

## 次段

同日 09:2x JST から poll_interval 60→30 の override A/B を開始 (ユーザー承認済み)。検証仮説 = 「poll 頻度を倍にしても session death の wall-clock rate (~15/h) は不変」。
