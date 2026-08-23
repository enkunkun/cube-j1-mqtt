# spec 038 再観察結果 (= 2026-08-23、 Reopened → Closed)

## 検証方法

[[feedback-compose-telegraf-pipeline]] の教訓 (= gcx empty ≠ 0 件発火) を遵守し、 **bridge `/api/diag` 直接 snapshot と gcx (= Grafana Cloud Prometheus) の両側**で同一時点を確認した。 compose fix (= commit 0ba5dba1、 telegraf topics 追加) は 2026-06-30 完了済みで、 本検証まで 54 日 (= >> 必要要件 24h) の観察窓がある。

## 観察データ (= 2026-08-23 JST)

### bridge `/api/diag` (= port 8080)

| key | 値 |
|---|---|
| `uptime_seconds` | 4,196,318 (**~48.6 日** 無 restart) |
| `sk_event_21_total` | **147,109** |
| `sk_event_21_param0_total` (= TX 成功) | **147,109** (= 100%) |
| `sk_event_21_param1_total` (= TX 失敗) | **0** |
| `sk_event_21_param2_total` (= 自動再送) | **0** |
| `erxudp_timeouts_total` (同期間参考値) | 25,382 |

### gcx (= cloud context)

- series 存在: `cube_j1_smart_meter_sk_event_21_{total,param0_total,param1_total,param2_total}` の **4 系列すべて出現** (= SC-004 達成、 pipeline 全段健全)
- instant 値: total = param0 = 147,114 / param1 = 0 / param2 = 0 (= diag 値と一致、 query 間の自然 increment のみ)
- `increase(cube_j1_smart_meter_sk_event_21_param1_total[7d])` = **0**
- 発生レート: `increase(sk_event_21_total[7d])` ≈ 21,152 / 168h ≈ **126 件/h** (= poll_interval 30s の SKSENDTO ほぼ全件に対応)

## 判定

### ROI 表適用 (= spec.md Phase 1 判断基準)

「EVENT 0x21 PARAM=1 件数/h」 = **0 件/h** (= 147,109 件 / 48.6 日で 1 件も無し) → 判断基準表の第 1 行 **「spec close (= 効果ゼロ)」**。

- **FR-003 (= PARAM=1 検出時の即 retry) は非実装で確定**。 送信段での失敗が存在しないため retry path に効果余地がない
- 元仮説 (= 「ERXUDP timeout の一部は TX 失敗が 1-2s 内に通知されている」) は **否定**。 ERXUDP timeout 25,382 件が全て RX 側要因 (= session death / noise 帯 / メーター応答遅延) であることと整合
- audit findings P-NEW-3 の懸念「timeout の一部は『送信失敗』だった可能性」も同じく否定

### Success Criteria 着席

| SC | 判定 | 根拠 |
|---|---|---|
| SC-001 (Phase 1 再観察) | ✅ | 54 日窓で PARAM 分布確定 (= 上表)、 判断基準表で close 決定 |
| SC-002 (PARAM 分類実装) | ✅ | commit 409fd08 (= classify + DiagState + DIAG_SENSOR_DEFS 3 entry)、 実機稼働中の build に含まれる (= uptime 起点 2026-07-05 頃 deploy 分) |
| SC-003 (単体 test) | ✅ | `test_classify_sk_line_event_21_with_param` / `test_on_sk_event_21_param_increments` / `test_diag_state.py` snapshot key 断言 等 |
| SC-004 (Grafana 出現) | ✅ | gcx で 4 series 出現確認 (= 今回の再観察がまさにその確認) |
| SC-005 (timeout baseline 低下) | N/A | 即 retry (= FR-003) 非実装のため対象外。 ERXUDP timeout の低減は RX 系改善 chain (= spec 048 INF filter / 049 noise minimal poll / 050+053 threshold・poll 周期 / 051 proactive refresh / 052 backoff) が担う |

## 残置事項

なし (= 本 spec は完全 close)。 EVENT 21 PARAM 別 counter は diagnostic metric として継続 publish され、 将来 PARAM=1 が計上され始めたら (= 例えば ARIB 送信時間制限の新常態化) 本 spec の retry path 設計が再利用可能。
