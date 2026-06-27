# Feature Specification: Wi-SUN Health Diagnostics

**Feature Branch**: `006-wisun-health`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "Wi-SUN の混み具合・応答性を観測できる指標を diag に追加し、 Grafana で poll 戦略を数値ベースで調整できるようにする"

## Background

spec 005 で MQTT 切断は解消したが、ECHONET poll 自体の失敗率は依然として観測されている。 poll_interval を短縮したり高速リトライを入れる戦略の前に、 「**Wi-SUN がどれだけ混んでいて、 どれだけ詰まっているか**」 を実測する必要がある。 現状の DiagState は `erxudp_timeouts_total` / `lqi` (初回スキャン時固定) のみで、 動的な応答性は見えない。

本 spec は ROHM SKSTACK から流れてくる `EVENT` / `FAIL` 行を分類して計上し、 `SKSENDTO` から `ERXUDP` までの実 RTT を 直近 N 件の latency 分布として診断値に追加する。 すべて measurement-only で、 制御ロジックには影響しない。

## Scope

- `SKSENDTO` 発行時刻と `ERXUDP` 受信時刻の差分（ms）を `erxudp_latency_ms_recent` として直近 N 件保持
- snapshot に `erxudp_latency_p50_ms` / `erxudp_latency_p95_ms` / `erxudp_latency_max_ms` を追加
- `read_erxudp` のループ内で `EVENT XX` / `FAIL ER<code>` を分類して `sk_event_XX_total` / `sk_error_ER<code>_total` の counter として積む
- 重要 EVENT を固定キーで publish (HA discovery 互換):
  - `sk_event_22_total` (Active Scan 完了 — BP35A1 Ver 1.3.2 p.51 [[spec-036]])
  - `sk_event_24_total` (PANA 接続失敗)
  - `sk_event_25_total` (PANA 接続完了)
  - `sk_event_26_total` (相手からセッション終了要求を受信 — [[spec-036]])
  - `sk_event_28_total` (セッション終了要求への応答が無く timeout — [[spec-036]])
  - `sk_event_29_total` (セッションのライフタイム経過 — [[spec-036]])
  - `sk_event_32_total` (ARIB 送信時間制限の発動 — [[spec-036]])
  - `sk_event_33_total` (ARIB 送信時間制限の解除 — [[spec-036]])
- 重要 SK_FAIL を固定キーで publish:
  - `sk_error_ER05_total` (invalid argument)
  - `sk_error_ER09_total` (UART busy)
  - `sk_error_ER10_total` (受信中)
- pure helper `classify_sk_line(line)` を抽出して `("erxudp", hex)` / `("event", "22")` / `("error", "05")` / `None` を返す

## Non-Scope

- 動的 LQI: ERXUDP には LQI フィールドが含まれない firmware のため、 別途 SKINFO ポーリングが必要 → 別 spec
- 高速リトライ実装（diag を見てから決定する → spec 007 想定）
- poll_interval 短縮（同上）
- Grafana ダッシュボードの自動更新（パネル追加は別 PR で手動）

## User Scenarios *(mandatory)*

### Primary User Story

開発者として Grafana の `cubej1-smart-meter` ダッシュボードに `Wi-SUN Health` パネル群を追加し、 24h 観察したい。 `erxudp_latency_p95_ms` の絶対値、 `sk_event_28_total` / `sk_event_29_total` の傾き、 `sk_error_*` の発生有無を見て、 「poll を短くしても OK か」 「リトライを何回まで許容するか」 を数値ベースで判断する。

### Acceptance Scenarios

1. **Given** poll サイクルが 5 秒で完了、 **When** 5 サイクル実行、 **Then** snapshot の `erxudp_latency_p50_ms` が 4500-5500 の範囲に入る
2. **Given** read_erxudp が ERXUDP の前に `EVENT 22` を 1 行受信、 **When** snapshot 取得、 **Then** `sk_event_22_total == 1` が出る
3. **Given** SK が `FAIL ER10\r\n` を返した、 **When** snapshot 取得、 **Then** `sk_error_ER10_total == 1` が出る
4. **Given** 一度も発生していない event_id、 **When** snapshot 取得、 **Then** その key は snapshot に出ない（HA に空 entity を作らない）

### Key Entities

- **`erxudp_latency_ms_recent`**: `collections.deque(maxlen=200)`、 直近 200 サイクル分の RTT
- **`sk_event_counts`**: `dict[str, int]`、 EVENT id を 2 桁 hex 大文字 key (例 `"22"`)
- **`sk_error_counts`**: `dict[str, int]`、 ER code を 2 桁 hex 大文字 key (例 `"05"`)
- **`classify_sk_line(line)`**: pure function。 SK の 1 行を `("erxudp", hex)` / `("event", "22")` / `("error", "05")` / `None` に分類

## Edge Cases

- `EVENT NN <ipv6>` のように引数付きの行も先頭 2 トークンで判定する（残りは ignore）
- `EVENT ` の後の id が大文字小文字混在 → 大文字に正規化
- `FAIL ER05` と `FAIL ER 05` の表記揺れ → 連結された方を採用、 空白入りは無視（仕様外）
- ERXUDP の前後で EVENT が連続発生 → すべて count、 latency 計測は ERXUDP 受信時刻基準
- ERXUDP timeout (data is None) → latency 記録しない、 timeout は既存 `on_erxudp_timeout` で計上済み
- 200 件超で latency deque が ローテーション → p50/p95 は新しい 200 件のみで計算（rolling window として意図的）
- 空 deque で p50/p95 → None → snapshot から省略

## Success Criteria *(mandatory)*

- **SC-001 [observability]**: 24h 連続稼働で `erxudp_latency_p50_ms` / `p95_ms` が Grafana に常に値が出る
- **SC-002 [classification]**: 既知の 8 EVENT + 3 ERROR を取りこぼさず分類、 unit test で全種類 carry
- **SC-003 [zero-cost]**: 計測パスのレイテンシ中央値が +5% 以内 (既存 SC-004 と同じ閾値)
- **SC-004 [no control impact]**: diag 失敗が main loop を止めない（既存 Constitution IV）
- **SC-005 [HA UX]**: 0 件の event/error は snapshot に出ない → HA に空 entity が増えない

## Assumptions

- ROHM SKSTACK が `EVENT NN ...\r\n` / `FAIL ER<NN>\r\n` 形式で出力する（実機で確認済み）
- `time.time()` の解像度が ms オーダー（Linux/Android で 1μs 以上、 問題なし）
- 200 件 = ~3.3 時間分の RTT 履歴（60s poll で）。Rolling window としては十分

## Dependencies

- `production_tool/mqtt_bridge.py` の `DiagState` クラス
- `read_erxudp` の SK 行ループ
- `publish_diag` / `_DIAG_SNAPSHOT_KEYS`
- Telegraf MQTT consumer → Prometheus remote_write（外側、 自動で拾う）
