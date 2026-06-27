# Feature Specification: BP35A1 SKSTACK-IP 公式仕様整合 (= EVENT 32/33 ARIB ラベル訂正 + SKSCAN dwell time コメント訂正)

**Feature Branch**: `036-bp35a1-spec-doc-alignment`
**Created**: 2026-06-27
**Status**: In Progress (= TDD Red→Green 完了、 deploy + SC verify 待ち)
**Input**: 2026-06-27 audit (= [[audit-bp35a1-skstack-ip-vs-bridge]]) の P-NEW-2 + P-NEW-7。 ROHM 認証付き DL で取得した BP35A1 公式 SK コマンドリファレンス Ver 1.3.2 (2020.5 改訂) と bridge コードの初の系統的突き合わせで判明したドキュメントレベル誤記 2 件。

## Background

[[audit-bp35a1-skstack-ip-vs-bridge]] (= 2026-06-27 実施) で、 Cube J1 内蔵 Wi-SUN モジュール (= BP35C0 + SKSTACK-IP firmware EVER 1.5.2) の公式仕様書と bridge コードを初めて突き合わせた結果、 9 件の問題点 (= P-NEW-1 〜 P-NEW-9) が判明。 うち本 spec は **ドキュメント整合 2 件** (= 動作に影響しないが運用解釈・根拠説明に影響する記述誤り) を扱う:

### 問題 1: EVENT 0x32 / 0x33 メトリクスラベルが完全に逆

公式 `bp35a1_commandmanual_tr-j.pdf` p.51 (= 公式表記):

| EVENT 番号 | 公式仕様の意味 |
|---|---|
| **0x22** | アクティブスキャンが完了した |
| **0x1F** | ED スキャンが完了した |
| **0x32** | **ARIB108 の送信総和時間の制限が発動した** (= このイベント以後あらゆるデータ送信要求が内部で自動的にキャンセル) |
| **0x33** | **送信総和時間の制限が解除された** |

bridge `production_tool/mqtt_bridge.py:3932-3933` 現状:

```python
("sk_event_32_total",      "SK EVENT 32 (Scan Done)",     None, None, "total_increasing", "diagnostic"),
("sk_event_33_total",      "SK EVENT 33 (Scan Started)",  None, None, "total_increasing", "diagnostic"),
```

→ Grafana ダッシュボード / Prometheus メトリクスで 「sk_event_32_total が増えた = scan が完了した」 と読んでしまうが、 **実際は ARIB108 上限到達でデータ送信が全停止**している状態。 メトリクス意味の根本的な誤読を生む。

なお、 真の Scan 完了系 (= EVENT 0x22 / 0x1F) は bridge のメトリクス定義に **そもそも登録されていない** (= 「sk_event_22_total」 等は grep 0 件)。 EVENT 0x22 は SKSCAN 内部の loop break 条件として使われている (`skscan` L2735 周辺) が、 メトリクス化はされていない。

### 問題 2: SKSCAN dwell time のコメントが公式式と 5 倍乖離

公式 p.20:
- SKSCAN duration `<N>` の 1 ch あたり時間 = **`0.0096 sec * (2^<N> + 1)`**
- 由来: IEEE 802.15.4 `aBaseSuperframeDuration * (2^N + 1)` = 960 symbols * (2^N+1)、 100 kbps GFSK で 9.6 ms = 0.0096 s

bridge `production_tool/mqtt_bridge.py:2714` 現状コメント:

```python
# scan dwell time = (192 * 2^duration + 1) symbol times.
```

→ `192 * 2^N + 1` symbols は公式 `960 * (2^N + 1)` symbols と **5 倍乖離** (= 同じ値ではない、 N=6 で公式 62400 symbols vs コメント 12289 symbols)。 由来不明 (= 過去の commit 履歴で誰かが誤って書いたか、 別 firmware 系列の換算式を参照した)。

動作影響: なし (= bridge は duration の整数値だけ stack に渡しており、 コメントの計算式は判定ロジックに使われていない)。 ただし spec 034 (= 単 ch active scan) の dwell time 想定根拠として参照されると誤差 5 倍を引き継ぐ。

## Why it matters

- **過去 Grafana 観測の再解釈**: `sk_event_32_total` の急増を見て「Wi-SUN scan が頻発で接続不安定」 と読んでいた場合、 実際は「ARIB 送信時間 360s/h 上限到達で 1 時間データ送信できない状態」 が起きていた可能性がある。 これは memory 「Instantaneous Power architectural cap」 「ERXUDP timeout 主因」 等の過去判断にも影響しうる
- **将来 spec の根拠誤り防止**: SKSCAN dwell time のコメント数値を将来 spec で参照すると、 5 倍誤差を引き継ぐ。 今訂正することで spec 034/035 系列の後続最適化 spec が公式根拠で書ける

## Functional Requirements

plan dig Round 1 で当初 2 件 (= EVENT 32/33 + SKSCAN コメント) から **5 件のラベル + 1 件のコメント** に拡張。 EVENT 24/25 は公式仕様と既に整合しているため変更対象外。

- **FR-001**: bridge `production_tool/mqtt_bridge.py:3926` の `sk_event_22_total` ラベルを `"SK EVENT 22 (Active Scan Done)"` に変更 (= 旧 `"PANA OK"` は誤り、 公式 0x22 = アクティブスキャン完了)
- **FR-002**: bridge `production_tool/mqtt_bridge.py:3929` の `sk_event_26_total` ラベルを `"SK EVENT 26 (Session Termination Requested by Peer)"` に変更 (= 旧 `"Re-auth"` は誤り、 公式 0x26 = 接続相手からセッション終了要求を受信)
- **FR-003**: bridge `production_tool/mqtt_bridge.py:3930` の `sk_event_28_total` ラベルを `"SK EVENT 28 (Session Termination Timeout)"` に変更 (= 旧 `"Session End"` は曖昧、 公式 0x28 = セッション終了要求への応答が無く timeout)
- **FR-004**: bridge `production_tool/mqtt_bridge.py:3931` の `sk_event_29_total` ラベルを `"SK EVENT 29 (Session Lifetime Expired)"` に変更 (= 旧 `"Session Timeout"` は曖昧、 公式 0x29 = セッションのライフタイムが経過)
- **FR-005**: bridge `production_tool/mqtt_bridge.py:3932` の `sk_event_32_total` ラベルを `"SK EVENT 32 (ARIB Transmit Limit Hit)"` に変更 (= 旧 `"Scan Done"` は完全に逆、 公式 0x32 = ARIB108 送信総和時間制限の発動)
- **FR-006**: bridge `production_tool/mqtt_bridge.py:3933` の `sk_event_33_total` ラベルを `"SK EVENT 33 (ARIB Transmit Limit Released)"` に変更 (= 旧 `"Scan Started"` は完全に逆、 公式 0x33 = 送信総和時間制限の解除)
- **FR-007**: bridge `production_tool/mqtt_bridge.py:2714` の SKSCAN dwell time コメントを公式式 `0.0096 * (2^duration + 1)` 秒 / ch に訂正、 実機 overhead で 1-2 倍の実時間が観測される注記を追加 (= BP35A1 Ver 1.3.2 p.20 引用)
- **FR-008**: 上記すべてのラベル / コメント変更について `tests/unit/test_wisun_health.py` に regression test を追加 (= 旧ラベル文字列が再混入しないことを assert)
- **FR-009**: `specs/006-wisun-health/spec.md` のメトリクス定義表のラベル説明 4 件 (= EVENT 22/26/32/33) を新ラベル意味に同期更新
- **FR-010**: `docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md` の status 行を `open` → `in progress (= P-NEW-2/P-NEW-7 を spec 036 で解消中)` → deploy 完了後 `P-NEW-2 / P-NEW-7 resolved by spec 036 commit <hash>` に段階遷移
- **FR-011**: メトリクス名 (`sk_event_NN_total`) は **変更しない** (= Prometheus 履歴の継続性確保、 entity_id 不変で HA dashboard クエリ無傷)。 ラベル (= HELP text) のみ変更

## Out of Scope

- P-NEW-1 (WOPT 毎回発行による FLASH 寿命): spec 037 で別途
- P-NEW-3 〜 P-NEW-9: 推奨実行順序 (= audit findings ファイル §4) で順次 spec 化
- 真の Scan 完了系 (= EVENT 0x22 / 0x1F) のメトリクス追加: 必要が確認された別 spec で実施
- 過去 Grafana log の遡及的再解釈作業 (= P-NEW-2 の影響範囲確認は本 spec の SC で 1 件サンプル検証のみ、 全期間の再解析は別タスク)
- compose/telegraf pipeline 側 (= memory「compose/telegraf pipeline (4 段)」) のラベル / metric 名変更: メトリクス名 (= `sk_event_32_total`) は変更しないので影響なし、 ラベル (= help text) の変更は Prometheus 側で自動反映

## Success Criteria

- **SC-001**: `production_tool/mqtt_bridge.py` 内の EVENT 0x32 / 0x33 のラベル文字列が公式 BP35A1 リファレンス Ver 1.3.2 p.51 の記述意味と整合する (= 「ARIB 送信時間制限の発動 / 解除」 を示す英訳ラベル)
- **SC-002**: `production_tool/mqtt_bridge.py:2714` のコメントが公式式 `0.0096 * (2^N + 1)` 秒と整合する
- **SC-003**: deploy 後の Prometheus メトリクス HELP text に新ラベルが反映されている (= `curl ... /metrics | grep sk_event_32_total` で 「ARIB transmit limit」 が含まれること)
- **SC-004**: deploy 後、 過去の grafana 観測期間で `sk_event_32_total` の急増 (= ARIB 上限到達) が記録されているサンプルを 1 件以上特定し、 同期間の SKSENDTO timeout / 失敗パターンと相関の有無を audit findings ファイルに追記する (= 「再解釈完了」 の記録)
- **SC-005**: docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md の status 行が `open` から進行状態に更新される (= P-NEW-2 / P-NEW-7 が resolved)

## Related

- audit findings: `docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md` (= P-NEW-2, P-NEW-7)
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` p.20 (SKSCAN), p.51 (EVENT)
- 関連メトリクス定義: `production_tool/mqtt_bridge.py` DIAG_SENSOR_DEFS (= L3900 周辺)
- 関連 memory: `feedback-diag-sensor-defs-publish.md` (= DIAG_SENSOR_DEFS 拡張時の注意)、 `feedback-compose-telegraf-pipeline.md` (= 新 metric は bridge + compose 両方修正、 ただし本 spec はラベルのみで metric 名不変)
- 後続 spec 候補: spec 037 (= P-NEW-1 WOPT 寿命)、 spec 038 (= P-NEW-3 EVENT 21 retry)、 spec 040 (= P-NEW-4 SKSAVE/SFF 活用)
