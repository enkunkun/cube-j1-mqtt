# spec 051 A/B 記録: after_sec 180 は差し引きマイナス、backoff 5s 単独測定へ

**A/B override**: 2026-07-04 06:14 JST 適用 (`proactive_rejoin_after_sec` 480→180 + `wisun_rejoin_backoff_initial_sec` 30→5)
**判定**: 09:21 JST (trailing [3h] ≈ 06:15〜09:15)。09:23 に after_sec のみ revert (backoff=5 継続)。

## 3h 実測 (before = 昨夜 3h、threshold=2/480/30)

| 指標 | before | A/B (180/5) |
|---|---|---|
| proactive 発火 | ~0/h | 14.7/h (= 総 reconnect の 67%) |
| 無計画 reconnect | 11.4/h | 7.2/h (= **-4.2/h しか減らず**) |
| 総 reconnect | 11.4/h | 21.8/h |
| live (dedupe) | 27.0/h (夜間) | 26.7/h |
| timeouts | 23.9/h | 17.1/h |
| 300s 超 gap | 0 | 0 |

## 判定

- **機構は正常動作** (発火 14.7/h ≈ 180s 周期どおり) だが **経済が赤字**: 計画 refresh 1 回 = 約 1 poll slot 消費 (cycle 開始 raise 設計)。14.7 slot/h 払って防げた無計画死は 4.2 回/h = **防止率 29% < 採算ライン ~40%**
- 根本: session 死は join 経過に強く紐づかない (45 秒死〜8 分死のばらつき)。「wall-clock 先回り」は BP35A1+この メーターの死因分布に対して効率が悪い
- **after_sec=180 は revert** (default 480 = 実質不発の無害状態)。proactive 機構自体はコード残置 (発火しなければコスト 0)

## 学び (次の設計への input)

- 計画 refresh を成立させる唯一の道は **slot 消費ゼロ化** = cycle 開始 raise ではなく「poll 成功直後の idle 窓 (~55s) で inline reconnect」する設計。防止率 29% でも slot コスト 0 なら純益になる。ただし ipv6 更新・fd 状態管理が複雑化するため、backoff=5 の効果測定後に費用対効果を再評価
- backoff 30→5 は前提非依存の回収 (~25s × 7.2 回/h ≈ 3 min/h)。本窓では after_sec の赤字と混合して判定不能 → **09:23〜12:23 の backoff=5 単独窓で判定し、有効なら spec 052 (backoff default 30→5) で昇格**


## 追記 (2026-07-04 12:40): backoff=5 単独判定 = 有効、spec 052 昇格完了

同時間帯比較 (09:30-12:30 帯): backoff=30 の昨日 23.3/h → backoff=5 の今日 **28.1/h (+21%)**、理論回収値と一致。max gap 222s / 300s 超 0 で異常なし、proactive は 1 件 (= 不発確認 ✓)。spec 052 (`1005c732`) で default 30→5 に昇格し、実機 override を削除済み。**これで実機 config override はゼロ、全 tuning が repo の default に集約された。**
