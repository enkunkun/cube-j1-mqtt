# Feature Specification: EVENT 0x21 (UDP 送信結果通知) 捕捉 + ERXUDP timeout 一部を即 retry 化

**Feature Branch**: `038-event-21-tx-result-capture`
**Created**: 2026-06-28
**Status**: **Closed (= Phase 1 観察結果で ROI 0 確定) / 2026-06-30 JST**

deploy から 42h 経過で `sk_event_21_total` = **0 件** = EVENT 21 PARAM=0/1/2 が一度も発火していない (= gcx --context cloud metrics query 'cube_j1_smart_meter_sk_event_21_total{device_id="cubej1"}' で vector empty 確認)。 spec.md の ROI 判断基準では「0 件 / 12h → spec close」 該当、 42h 観察で確定。

ERXUDP timeout は同期間 1046 件累計と発火継続中 = ERXUDP timeout 主因は **TX 失敗ではなく RX 待ち (= メーター応答無し or 遅延)** ことが確定 ([[feedback-phase1-event21-zero-erxudp-rx-dominant]] に詳細)。 audit findings P-NEW-3 = Closed (= bridge アーキテクチャ非互換ではないが effect ゼロが実証)。

仮説の前提 (= 「ERXUDP timeout の一部は EVENT 21 PARAM=1 で 1-2s 以内に通知されていた」) は否定。 software 改善余地 0、 spec 042 (= SKADDNBR) / spec 044 (= EVENT 25 metric bug fix) など別 spec で残課題に対応。
**Input**: 2026-06-27 audit ([[audit-bp35a1-skstack-ip-vs-bridge]]) の P-NEW-3。 BP35A1 公式 Ver 1.3.2 p.51 で SKSENDTO 後の送信結果は EVENT 0x21 (PARAM=0/1/2) で 1-2 秒以内に通知されるが、 bridge は完全 ignore (= `grep "EVENT.*21"` 0 件) で常に ERXUDP 待ち 30s timeout に依存している。

## Background

### 公式仕様 (BP35A1 Ver 1.3.2 p.51)

> SKSENDTO 実行後、 結果通知として EVENT (0x21) が通知されます。 PARAM 値は以下:
> - **0**: 成功 (= キャリアセンス OK、 ユニキャストは Ack 確認済み)
> - **1**: 失敗 (= キャリアセンスビジー / ARIB 送信時間制限 / Ack 未受信)
> - **2**: アドレス要請後の自動再送 (= 待つだけで送信される)

通知は SKSENDTO 発行から **1-2 秒以内**。

### bridge 現状 (`production_tool/mqtt_bridge.py`)

- `skcommand("SKSENDTO ...")` → 即 return
- main loop は `_drain_serial_until_event` 等で **ERXUDP 30s 待ち**
- EVENT 0x21 PARAM=0/1/2 は **完全 ignore** (= classify_sk_line で sk_event_21_* メトリクスも未定義)

### 仮説

ERXUDP timeout 30s 件のうち、 一部は実は SKSENDTO 失敗 (= EVENT 0x21 PARAM=1) で 1-2 秒以内に通知されていた可能性。 これを拾えば即 retry で総待ち時間が大幅短縮する。

memory [[feedback-erxudp-timeouts-periodic-pana]] が「erxudp_timeouts 30 件/h baseline、 software 改善 4 spec で限界」 と結論しているが、 これは EVENT 0x21 を「観測すらしていない」 状態の結論。 観測すれば限界を更新可能性。

## Phase 1: 観察 (= 効果見極め)

実装着手前に **1 時間以上の実機 log で EVENT 0x21 の出現頻度** を grep で取得し、 ROI を確定する。

### 確認手順

```bash
# admin UI 経由で 1h log 取得
curl -s -u admin:<pw> http://cube-j1.home.arpa:8000/api/log?lines=20000 > /tmp/bridge-log-1h.txt

# EVENT 0x21 出現件数
grep -E "EVENT (21|0x21)" /tmp/bridge-log-1h.txt | wc -l

# PARAM 別の集計
grep -oE "EVENT 21 [^ ]+ [^ ]+ ([012])" /tmp/bridge-log-1h.txt | sort | uniq -c
```

### ROI 判断基準

| EVENT 0x21 PARAM=1 件数 / h | 判断 |
|---|---|
| 0 件 | spec close (= 効果ゼロ) |
| 1-5 件 | low ROI、 メトリクス追加のみで spec close |
| 6-20 件 | mid ROI、 メトリクス + log 出力のみ (= retry まではせず) |
| 21 件以上 | high ROI、 Phase 2 で即 retry 実装 |

## Phase 2: 実装 (= ROI 高なら)

### FR-001: classify_sk_line に EVENT 0x21 PARAM=0/1/2 の 3 ケース追加

```python
# 仕様上の正式表記: "EVENT 21 <SENDER_IPV6> <PARAM>"
("event_21_param0", r"^EVENT 21 \S+ 0$"),  # 成功
("event_21_param1", r"^EVENT 21 \S+ 1$"),  # 失敗
("event_21_param2", r"^EVENT 21 \S+ 2$"),  # 自動再送
```

### FR-002: DiagState / DIAG_SENSOR_DEFS 拡張

```python
self.sk_event_21_param0_count = 0  # SKSENDTO 成功
self.sk_event_21_param1_count = 0  # SKSENDTO 失敗 (即 retry 候補)
self.sk_event_21_param2_count = 0  # 自動再送 (待つだけ)
```

```python
("sk_event_21_param0_total", "SK EVENT 21 PARAM=0 (TX Success)", ...),
("sk_event_21_param1_total", "SK EVENT 21 PARAM=1 (TX Failed = CSMA/Ack)", ...),
("sk_event_21_param2_total", "SK EVENT 21 PARAM=2 (TX Auto-Retry)", ...),
```

### FR-003: ERXUDP 待ち loop で EVENT 0x21 PARAM=1 を検出 → 即 retry

`_drain_serial_until_event` 等の現状を確認し、 EVENT 0x21 PARAM=1 を受信したら即座に SKSENDTO 再発行 (= 30s 待ちせず 1-2s で次手)。 ただし retry 回数上限 (= 既存 backoff 機構との整合) は dig で確定。

### FR-004: regression test 追加 (`tests/unit/test_wisun_health.py`)

- classify_sk_line で EVENT 21 PARAM=0/1/2 が正しく分類される
- DiagState.on_sk_event("event_21_param1") で counter increment
- DIAG_SENSOR_DEFS に 3 件登録されている

## Out of Scope

- SKSENDTO 失敗時の root cause 分析 (= キャリアセンスビジー vs ARIB 上限 vs Ack 未受信、 PARAM=1 では区別不能)
- ARIB 送信時間上限の予測制御 (= 別 spec、 EVENT 32/33 メトリクスで観測中)
- SKSENDTO の SECURE 引数 (= 仕様確認は別 spec 候補)

## Success Criteria

### Phase 1 (= 観察)

- **SC-001 (Phase 1)**: 1h 以上の実機 log で EVENT 0x21 PARAM=0/1/2 の出現頻度を集計、 判断基準表に従って Phase 2 着手 or spec close を確定

### Phase 2 (= 実装、 ROI 高の場合のみ)

- **SC-002 (Phase 2)**: classify_sk_line + DiagState + DIAG_SENSOR_DEFS が EVENT 21 PARAM=0/1/2 の 3 件を正しく分類・集計・publish
- **SC-003 (Phase 2)**: 単体 test pass (= 既存 + 新規 ~10 件)
- **SC-004 (Phase 2)**: deploy 後 24h で `sk_event_21_param1_total` が Grafana に出現、 retry path 発火ログ確認
- **SC-005 (Phase 2)**: deploy 後 7 日間で erxudp_timeouts 30 件/h baseline が低下 (= ベースライン更新は別 dashboard で確認)

## Related

- audit findings: [[audit-bp35a1-skstack-ip-vs-bridge]] P-NEW-3
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` p.51 (EVENT 0x21)
- 関連 memory:
  - [[feedback-erxudp-timeouts-periodic-pana]] (= 既存 baseline、 本 spec で更新可能性)
  - [[feedback-bp35cx-reconnect-floor-11s]] (= reconnect 床値、 本 spec 対象外)
- 前 spec: spec 036/037 (= bp35a1 spec doc alignment 第一弾、 本 spec は第二弾)
- 並行 spec: spec 039 (P-NEW-4 SKSAVE/SFF)、 spec 040 (P-NEW-5 PANA 720s)
