# spec 049 観測記録: 24h SC 判定 = SC-1 未達 (仮説は概ね棄却、feature は net-positive で残置)

**Deploy**: 2026-07-03 06:27 JST、version `1.0.0+d86bd5b`
**判定時刻**: 2026-07-04 08:44 JST (trailing [24h]、deploy 後に収まる)

## 24h 実測

| 項目 | 値 | 備考 |
|---|---|---|
| noise_minimal_polls | 20 件 (0.8/h) | noise_adaptive_skips 20 と完全一致 = 全 noisy cycle が minimal poll 化 ✓ |
| success | **4 件 (回収率 20%)** | SC-1 基準 50% に未達 |
| timeout | 16 件 | erxudp_timeouts / force reconnect には計上されず (FR-004 設計どおり) ✓ |
| wisun_reconnects | 284 件 (11.8/h) | threshold=2 (spec 050) の設計値。spec 049 起因の悪化なし = SC-2 実質 ✅ |
| live | ~39.5/h | 26/h 帯以上を維持 = SC-3 ✅ |
| 参考: inf_ignored | 12/h | reconnect 頻度 (11.8/h) と一致 = INF は reconnect 毎の随伴通知と確定 |
| 参考: rescued_esv_inf | 0 | spec 048 の INF 排除は 24h 継続 ✅ |

## 判定

- **SC-1 未達**: 「noise 中でも OPC=1 なら通る」仮説は概ね棄却 (回収率 20%)。noise はやはり RX を殺す
- **ただし feature は残置**: 完全 skip (回収 0%) に対し minimal poll は +4 件/24h の純益で、コストは無視可能な airtime のみ (timeout は reconnect 判定から分離済み、cycle は skip でも失われていた)。kill switch (`noise_skip_tier1_minimal_poll_enabled=false`) はいつでも使える
- 副産物 2 つ: (1) inf_ignored 12/h = reconnect 頻度と一致し「INF は reconnect 随伴通知」と確定 (spec 047 の H2 解釈を補強)、(2) timeouts 21.5/h (< 29/h baseline) = threshold=2 が timeout streak を短く切っている

**spec 049 close (SC-1 未達を明記の上、有益な観測 counter として運用継続)。**
