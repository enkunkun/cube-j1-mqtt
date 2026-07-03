# Plan: spec 051 PANA session の計画的 refresh

## TDD ステップ

1. **pure helper + 例外 class** (Red→Green): `should_fire_proactive_rejoin(last_event_25_ts, now, after_sec, skrejoin_active)` / `class ProactiveSessionRefresh(RuntimeError)`。テスト: test_proactive_session_refresh.py 新規
2. **DiagState**: `proactive_rejoin_total` + `on_proactive_rejoin()` + snapshot (zero-omit しない) + DIAG_SENSOR_DEFS 1 entry + test_diag_state 期待 dict 更新
3. **apply_defaults**: `proactive_rejoin_enabled=True` / `proactive_rejoin_after_sec=480` / `skrejoin_tick_enabled` True→False (テストで 3 つとも assert)
4. **main loop 配線** (テストは helper 経由 + 全 suite green):
   - cycle 開始の skrejoin 判定 block を「proactive 優先 → (無効時のみ) skrejoin」の 2 段に
   - 発火時: on_proactive_rejoin + log + raise ProactiveSessionRefresh
   - 外側 except: `isinstance(e, ProactiveSessionRefresh)` なら `_backoff = 0` (log も「planned refresh」に分岐)
5. compose telegraf topic 1 本 (deploy 時)

## Risk

- proactive raise は consecutive_wisun_connect_failures を増やさない (= except 内で attempt 計算に使われるのは失敗 counter、reconnect 成功で 0 reset — 平常時は常に attempt=0 なので backoff=0 branch が無くても 30s だが、branch を入れて 0s に)
- reconnect 失敗が続く場合: ProactiveSessionRefresh 由来でも wisun_connect 失敗すれば通常の backoff 経路に入る (= 2 回目以降は通常例外) — 安全側
- after_sec=480 が実際の session 寿命 (~600-720s) より十分手前であることは spec 040 hotfix 実測で確認済
