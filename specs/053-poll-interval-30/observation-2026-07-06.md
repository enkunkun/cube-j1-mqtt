# poll_interval=30 A/B 記録 (spec 化前の観測、threshold=1 併用): 合格、live +93%

**override**: `poll_interval: 30` を 2026-07-06 09:20:44 JST に適用 (threshold=1 override は 06:08 から継続、コード変更なし)。
**判定**: 2026-07-06 12:30 JST。A 窓 = 09:22〜12:22 JST (poll 30s + th=1)。比較窓 = 同日朝 06:10〜09:10 JST (poll 60s + th=1、observation-2026-07-06-threshold1.md 参照)。

## 3h 実測

| 指標 | 朝: 60s + th=1 | A: 30s + th=1 |
|---|---|---|
| live rate (timestamp dedupe) | 51.2/h (理想比 85%) | **98.7/h (理想 120/h 比 82%)** |
| reconnect (≈ session death) | 14.7/h | 18.7/h |
| erxudp timeout | 14.3/h | 18.7/h |
| timeout / poll 比 | ~24% | **~16%** |
| max gap | 334s | 416s |

## gap 分布 (30s cadence、death 判定線 >50s)

- 正常 ~30s: 245 / 軽微 slip 35-50s: 13
- **fast death 50-90s: 36 件** — 50〜53s に鋭い cluster = 「30s + timeout 8s + backoff 5s + rejoin ~8s」。死 1 回の実質欠損 ~21s (≈ 0.7 slot) は 60s 時代と同一構造
- slow death >90s: 5 件 (102/107/109/183/416)
- 欠損収支: 死による損失 ≈ 57 slot/3h ≈ 19/h。理想 120 − 19 ≈ 101/h ≈ 実測 98.7 で**収支が閉じる**

## 判定

- **合格** (成功ライン live >60/h に対し 98.7/h、storm 線 reconnect ≤30/h に対し 18.7/h)
- **検証仮説「poll 頻度は session death を加速しない」はほぼ成立**: death rate 14.7→18.7/h (+27%) は上がったが、poll 数は 2 倍 (60→120/h) であり比例増ではない。増分は時間帯差 (朝 vs 昼) の可能性もある。poll あたり timeout 率はむしろ半減 (24%→16%) で、メーター側負荷の兆候なし
- ARIB STD-T108 床 (`MIN_POLL_INTERVAL_SEC = 30`) ちょうどで運用。これ未満は不可

## 経過整理 (2026-07-06 の 2 段 A/B)

| 構成 | live | 対 baseline |
|---|---|---|
| th=2 + 60s (前日) | 41.3/h | 1.0× |
| th=1 + 60s (06:10〜) | 51.2/h | 1.24× |
| th=1 + 30s (09:22〜) | **98.7/h** | **2.39×** |

## 次段 (未実施)

threshold=1 + poll_interval=30 の default 昇格 spec (両 override 削除まで)。昇格時の注意: threshold=1 は spec 048 (INF filter) + spec 049 (noise timeout 除外) が前提。監視条件の再定義が必要 (reconnect 異常線 20/h → 30/h、理想 rate 60/h → 120/h)。
