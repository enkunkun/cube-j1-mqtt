# spec 047 観測記録: H1/H2/H3 本判定 (deploy +6h、n=66)

**Deploy**: 2026-07-02 10:17 JST、version `1.0.0+83f11fb`
(= uptime_seconds の Prometheus reset 逆算による実時刻。当初 10:35 JST と誤記、
checkpoint 13:43/16:41 の trailing 窓は全て deploy 後に収まるため判定への影響なし)
**判定時刻**: 2026-07-02 16:42 JST (= +6h、一次判定 13:43 の +3h n=35 と比率一致)

## 6h 増分 raw data

| 軸 | counter | 件 | 比率 |
|---|---|---|---|
| ESV | inf | 39 | 59% |
| | get_res | 27 | 41% |
| | get_sna / other | 0 | 0% |
| TID | zero | 34 | 52% |
| | ring_hit | 32 | 48% |
| lag | lt5s | 34 | 52% |
| | 5to60s | 1 | 1.5% |
| | 60to300s | 26 | 39% |
| | gt300s | 5 | 8% |
| 空 | empty_measurement | 39 | 59% |

同窓の context: live power_watts 75 件 (= 12.5/h、不変)、 timeouts 155 件 (= 25.9/h)、 recovered 66 件 (= 11/h)。

## marginal の完全一致から joint 構造が一意に解ける

- `empty_measurement (39) = esv_inf (39)` — INF frame は全件 measurement 空
- `tid_zero (34) = lag_lt5s (34)` — TID=0 frame は全件 <5s 着信 (= lookup_latest が現 cycle send_ts を割当てるため)
- `esv_get_res (27) = lag_5to60s (1) + lag_60to300s (26)` — 正規 Get_Res 救済は**全件が真の遅延応答**
- `lag_gt300s (5) = esv_inf (39) − tid_zero (34)` — ring 偶然 hit した INF 5 件が stale bucket に落ちた

## 判定

| 仮説 | 判定 | 比率 | 内容 |
|---|---|---|---|
| **H2 (INF 混入)** | ✅ **支配的** | **59%** | メーター自律通知 (ESV 0x73、 TID=0) が read window 内に着信すると lookup_latest fallback が「遅延応答」と誤認 → cycle を poll_success 扱いで潰す (データゼロ)。 6.5/h = wisun_reconnects 6.7/h とほぼ一致 = **PANA 再認証後のインスタンス通知**が正体 |
| **H3 (chain 自走)** | ✅ 実在 | 41% | 正規 Get_Res 救済 27 件は全件 lag 60s+ = 1 cycle 以上遅れの chain。 4.3/h |
| **H1 (TID=0 即時応答の誤分類)** | ❌ **棄却** | 0% | lt5s 峰 34 件は全件 INF。 「実質 live の Get_Res が backfill に誤分類」は存在しない。 事前分析の recovered_lag_p50=1s の読みは INF を見ていた |

## 改修方向 (= 次 spec への input)

`read_erxudp` の 1 箇所の構造変更で H2/H3 両方を解消できる:

1. **ESV filter を TID rescue より前に**: ESV=0x73 (INF) 等の非 Get 応答 frame は rescue 対象外 (= counter だけ inc して読み飛ばし、 deadline まで待ち続ける)。 got_tid=0 → lookup_latest fallback は正体が 100% INF と判明したので**撤去**
2. **正規 late frame rescue 後も expected TID を待ち続ける**: rescued payload は side-channel (diag bus の list 化) で backfill publish し、 read は継続。 chain の伝播を断つ

効果予測: INF に潰されていた 6.5 cycle/h + chain 4.3 cycle/h が「本来の応答を待つ」cycle に戻る。 メーター応答率 ~58% を掛けて **live +6〜10/h (= 12.5 → 19〜22/h、 取得率 21% → 32〜37%)** を見込む。

## SC 判定

- **SC-1** ✅: esv 4 本合計 66.2 = recovered_from_mismatch 増分 66.2 (分類漏れなし)
- **SC-2** ✅: 支配要因 H2 (59%) 確定、 改修 spec の設計判断に直結
- **SC-3** ✅: /api/diag (deploy +0h) と gcx (deploy +6h) の両方で観測済
