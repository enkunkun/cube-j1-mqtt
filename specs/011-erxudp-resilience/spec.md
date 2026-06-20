# Feature Specification: ERXUDP Timeout Extension + Intra-Cycle Retry

**Feature Branch**: `011-erxudp-resilience`
**Created**: 2026-06-20
**Status**: Draft
**Input**: User description: "p50/p95 とは別の絶対的な穴 (publish が途切れる) を直したい。 ERXUDP timeout を伸ばすのとサイクル内即リトライ"

## Background

spec 005-010 で:
- MQTT 切断は完全解消 (mqtt_reconnects_total ≈ 0)
- Wi-SUN 通信品質 (LQI=208, 920MHz noise floor ch57=9) は良好
- EEDSCAN で物理層も静か

だが Grafana の `cube_j1_smart_meter_power_watts` には依然として穴 (時間帯依存の publish 抜け) が残る。 erxudp_timeouts_total が 1 件/分 程度発生していて、 1 サイクル丸ごと publish が落ちる。

実機観測:
- p50 RTT 30ms、 p95 5000ms (5 秒)、 max 6100ms (6 秒)
- 現状 `read_erxudp(fd, timeout=15)` で 15 秒 timeout → メーター応答が 15 秒超なら取りこぼす
- 1 timeout で 1 サイクル丸落ち (60 秒分の HA publish なし)

本 spec で 3 つの resilience を追加:

1. **ERXUDP timeout 拡張**: 15s → 30s。 メーター応答の確率分布 (p95=5s、 まれに 10-20s) を取りこぼさない
2. **サイクル内即リトライ**: timeout した直後 (1-2 秒待ち) に同 EPC 群を再送、 max 2 回。 散発失敗を 1 サイクルで回収して publish 穴を消す
3. **EPC 分割送信**: 現状 7 EPC 一括 → tier 別に分割
   - tier 1 (毎サイクル): `[0xE7, 0xE8]` (瞬時電力 + 瞬時電流) — 軽量パケット、 リアルタイム値
   - tier 2 (5 サイクルに 1 回): `[0xE0, 0xE3]` (積算電力量 forward/reverse) — ゆっくり変化
   - tier 3 (起動時 + 1 時間に 1 回): `[0xD3, 0xE1]` (係数、 単位) — ほぼ不変
   メーター応答時間短縮 + 不要なリクエスト削減 + 1 tier 失敗でも他 tier は残る

## Scope

- `config.json` に新キー:
  - `erxudp_timeout_sec` (default 30、 旧 hard-coded 15 を置き換え)
  - `erxudp_intra_cycle_retries` (default 2)
  - `erxudp_retry_backoff_sec` (default 2)
- `read_erxudp(fd, timeout=...)` の呼び出しを cfg 値で
- main loop で ERXUDP timeout 検知時:
  1. `time.sleep(erxudp_retry_backoff_sec)`
  2. 同じ tid+EPC 群を再送 (新 tid)
  3. `read_erxudp` 待ち
  4. max `erxudp_intra_cycle_retries` 回まで
- pure helper `should_retry_in_cycle(attempt, max_retries)` を抽出
- DiagState に `erxudp_intra_cycle_retries_total` / `erxudp_recovered_by_retry_total` を追加して publish
- リトライで救われたサイクルは **正常 publish** として処理 (HA から見ると遅延 publish)

## Non-Scope

- B-route メーターへの DoS 防止 (max 3 リクエスト/min なので軽微、 仕様内)
- EPC 分割送信 (パケットサイズ削減、 別 spec 候補)
- リトライ間隔の adaptive 調整 (固定 backoff で十分)

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Grafana で `cube_j1_smart_meter_power_watts` の 30 分グラフを見て、 4 分の穴が **無く連続描画されている** ことを確認する。 内部的にはたまにリトライが発生してるが、 HA / Grafana から見ると正常 publish が継続している。

### Acceptance Scenarios

1. **Given** メーター応答が 18 秒、 **When** read_erxudp(timeout=30)、 **Then** 18 秒で正常に受信、 publish 成功 (旧 15s では timeout で穴になっていた)
2. **Given** 1 回目の send で timeout (30s)、 **When** main loop が intra-cycle retry を発火、 **Then** 2 秒 sleep → 再送 → 成功で publish、 erxudp_recovered_by_retry_total が +1
3. **Given** 2 回のリトライすべて timeout、 **When** 3 回目を試さず諦める、 **Then** erxudp_timeouts_total +1、 次サイクル進む
4. **Given** リトライで 1 回成功、 **When** publish、 **Then** HA / Grafana のグラフに穴が出ない

### Key Entities

- **`should_retry_in_cycle(attempt, max_retries)`**: pure 関数、 attempt < max_retries で True
- **`erxudp_intra_cycle_retries_total`**: 計上カウンタ (試行回数の総数)
- **`erxudp_recovered_by_retry_total`**: 救われたサイクル数 (穴回避の効果指標)
- **`erxudp_timeout_sec`** / **`erxudp_intra_cycle_retries`** / **`erxudp_retry_backoff_sec`** config キー

## Edge Cases

- リトライ送信前にメーターが直前 reply を遅延送信 → 古い payload を新リトライの応答と誤認する可能性は低い (ECHONET TID で識別)
- 30s timeout × 3 retry = 最大 90s + backoff 4s = 94 秒、 poll_interval=60s を超える → 次サイクル即発火 (deadline pacing)
- リトライ中に SIGTERM → 中断、 bridge 終了処理
- WISUN reconnect 中に retry が走らないよう、 force_wisun_reconnect threshold は **総 timeout 数で判定** (現状通り)

## Success Criteria *(mandatory)*

- **SC-001 [hole reduction]**: 24h 連続稼働で `cube_j1_smart_meter_power_watts` の連続区間が baseline (穴あり) より +30% 以上長くなる
- **SC-002 [recovery rate]**: `erxudp_recovered_by_retry_total` が `erxudp_timeouts_total` の少なくとも 30% 以上 → リトライが効いてる証拠
- **SC-003 [no breakage]**: 既存 mqtt_reconnects_total / wisun_reconnects_total が増えない
- **SC-004 [pure helper]**: `should_retry_in_cycle` が unit test 可能、 同入力に同出力
- **SC-005 [config backward-compat]**: 新キー未設定の旧 config.json で default 値 (30/2/2) が適用される

## Assumptions

- B-route スマートメーターは 1 サイクル内に 3 回 SKSENDTO を受けても reject しない (DoS とみなさない、 仕様内)
- リトライ間隔 2 秒はメーター側の queue を消化させるのに十分
- ECHONET 応答の **TID マッチング** で旧応答と新応答を区別 (`read_erxudp` が wrong-TID を捨てる動作は今後の改善余地、 まず total throughput 改善優先)

## Dependencies

- `production_tool/mqtt_bridge.py` の main loop, read_erxudp, send_el_get, apply_defaults, DiagState
- spec 005 (MQTT threading) - publish が non-blocking
- spec 009 (deadline pacing) - 90s かかるサイクルが次の poll を急がせる
