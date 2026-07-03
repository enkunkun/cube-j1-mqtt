# spec 051 観測記録: 3h SC 判定 = SC-1 不達 (前提モデルの崩壊を検出)

**Deploy**: 2026-07-03 20:02 JST (= 11:02 UTC)、version `1.0.0+da699fe`
**判定時刻**: 2026-07-03 23:17 JST (trailing [3h] ≈ 20:17〜23:17、deploy 後に収まる)

## 結果

| SC | 基準 | 実測 | 判定 |
|---|---|---|---|
| SC-1 | proactive 21-24 件 + skrejoin 0 | **proactive 0 件** (窓外の初回 1 件のみ) / skrejoin 0 ✅ | ❌ |
| SC-2/3 | live ≥ 48/h、300s 超 ≈ 0 | live **27.0/h**、300s 超 0 ✅、180s 超 11、max 268s | ❌ (live) |
| SC-4 | reconnect ~7-8/h | **11.4/h** (全部無計画) | ❌ |

timeouts 71.7/3h = 23.9/h。夜間帯は live が昼 (40/h) より低い = 無計画 reconnect 頻度が高い時間帯。

## 不達の root cause (= 実機ログ 22:07〜22:54 JST の解剖)

- 無計画 reconnect (= `consecutive_erxudp_timeouts=2` forced) が **5〜7 分間隔**で発生し、そのたび EVENT 25 で `last_event_25_ts` がリセット → **session が 480s 生き延びないため計画 refresh の発火機会が来ない** (発火は 487s 生存した 1 session のみ)
- `sk_event_25_total ≈ wisun_reconnects` = 受動 EVENT 25 によるリセット説は否定
- **重要な訂正**: 「PANA 失効 10-12 分周期」モデルは threshold=6 時代 (= 検出 6 分遅れ) の観測アーティファクト疑い。threshold=2 の実データでは **session 実寿命 3〜6 分** (45 秒の例もあり)
- 追加発見: 無計画 reconnect は毎回 backoff 30s を払う (= 11 回/h × 30s ≈ 5.5 分/h の隠れ損失)

## 次アクション (= 2026-07-04 07:03 cron、ユーザー承認済)

1. `proactive_rejoin_after_sec` 480→180 override (= session 死が join 経過に紐づくかの A/B)
2. `wisun_rejoin_backoff_initial_sec` 30→5 override (= 前提非依存の確実回収)
3. 3h 測定で: proactive/無計画比率 → 紐づき仮説判定 → 有効分だけ spec 052 で default 昇格

spec 051 の機構自体は無害 (発火しないだけ) なので rollback せず、A/B の結果で after_sec を tuning する。
