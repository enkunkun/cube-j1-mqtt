# Feature Specification: PANA session の計画的 refresh (= 受動 blackout を計画 reconnect に変換)

**Feature Branch**: `051-proactive-session-refresh`
**Created**: 2026-07-03
**Status**: Draft
**Input**: spec 050 後の残欠損分析。ユーザー承認 2026-07-03 19:20頃「30秒停止なら計画的で問題なさそう。その方針でいけますか」。

## Background

- spec 050 後も残る gap = メーターの PANA session 失効 (~10-12 分周期) による無応答窓。現在は「2 timeout (2 分) + reconnect」= 2.5〜3 分の**不意の** blackout × ~6-7 回/h
- 失効タイミングは wall-clock でほぼ周期的 = **予測可能** → 失効前に先回りで full reconnect すれば、不意の 2.5 分停止を計画的な ~15 秒停止に変換できる
- 過去の試み: spec 040 Phase 2b の能動 SKREJOIN (= in-place 再認証) は **94-100% event_24 reject** で失敗 ([[feedback-skrejoin-event24-reject-94]]、tako 合議で本番 OFF 推奨)。**しかし tick は今も本番で有効のまま** (skrejoin_tick_enabled default True、480s 毎にほぼ確実に失敗する SKREJOIN を発行中)。一方 full reconnect (= cached SKJOIN、spec 035) は 1 日 ~160 回の実績で機能している
- 外側 reconnect path は backoff 30s を必ず挟む (spec 017) — 計画 refresh は失敗由来ではないので backoff 不要

## 設計

spec 040 Phase 2b と**同じ発火点 (cycle 開始時)・同じ base time (last_event_25_ts、spec 044 で全 reconnect 経路から更新済)** のまま、行動だけ差し替える:

- SKREJOIN (94% 拒否) → **専用例外 `ProactiveSessionRefresh` を raise して既存 full reconnect path を再利用**
- 外側 handler は計画 refresh の場合 **backoff を 0** にして即 wisun_connect (= cached path ~15s) → 停止は 1 poll slot 未満

## Requirements

### Functional Requirements

- **FR-001**: pure helper `should_fire_proactive_rejoin(last_event_25_ts, now, after_sec, skrejoin_active)` — last_event_25_ts 記録済 && 経過 > after_sec && skrejoin 非実行中で True
- **FR-002**: config `proactive_rejoin_enabled` (= default true) / `proactive_rejoin_after_sec` (= default 480、spec 040 hotfix の実測 tuning 値を引き継ぐ)
- **FR-003**: 発火時は `proactive_rejoin_total` counter を inc して `ProactiveSessionRefresh` (= RuntimeError 派生) を raise。外側 handler は該当例外なら **backoff sleep を skip** (= 0s) して即 reconnect
- **FR-004**: `skrejoin_tick_enabled` の default を **True → False** に変更 (= tako 合議 2026-07-01 の「本番 OFF 推奨」を実現、proactive が supersede)。code は残す (= config で復活可)
- **FR-005**: `proactive_rejoin_total` を DIAG_SENSOR_DEFS + snapshot (= zero-omit しない) + compose telegraf topics に登録

### Success Criteria (= deploy 後 3-6h)

- **SC-1**: `proactive_rejoin_total` ≈ 7-8/h で発火し、`skrejoin_total` 増分 = 0
- **SC-2**: 300s 超 gap ≈ 0/3h、**180s 超 gap も大幅減** (= 不意の 2 timeout 待ちが消える)
- **SC-3**: live ≥ 48/h (= 80% 帯、現状 40-41/h から +7/h 以上)
- **SC-4**: `wisun_reconnects_total` は ~7-8/h の計画分に置き換わり総数横ばい (= 15/h 超なら想定外)

## 関連参照

- spec 040 Phase 2b (= SKREJOIN 失敗の学び)、spec 035 (= cached reconnect 高速化)、spec 017 (= backoff)、spec 044 (= EVENT 25 hook 補填)
- memory `feedback-skrejoin-event24-reject-94`、`reference-tako-spec027-v2-design` (= wall-clock 方向の過去合議)、`project-power-acquisition-baseline-2026-07`
