# Feature Specification: wisun_rejoin_backoff_initial_sec default 30 → 5

**Feature Branch**: `052-rejoin-backoff-initial-5s`
**Created**: 2026-07-04
**Status**: Draft
**Input**: spec 051 A/B の副産物。無計画 reconnect (= threshold=2 で 11/h) が毎回 spec 017 の initial backoff 30s を払っており、~5.5 分/h の隠れ損失と judged (2026-07-03 23:00 分析)。2026-07-04 09:23〜12:30 の backoff=5 単独 override 窓で効果実証。

## A/B 実証 (時間帯 confound を排した同帯比較)

| 窓 (09:30-12:30 帯) | backoff | live (dedupe) | 無計画 reconnect |
|---|---|---|---|
| 2026-07-03 (spec 050 試行窓) | 30s | 23.3/h | ~11/h |
| 2026-07-04 (単独 override) | **5s** | **28.1/h (+21%)** | 11.4/h |

+4.8/h は理論回収値 (= 25s 短縮 × 11 回/h ≈ 4-5 samples/h) と一致。max gap 222s / 300s 超 0 で異常なし。

## Requirements

- **FR-001**: `apply_defaults` の `wisun_rejoin_backoff_initial_sec` default を **30 → 5** に変更
- **FR-002**: exponential escalation (= multiplier 2.0、max 300s clamp、spec 017) は不変 — 真の長期障害では 5→10→20→…→300 で従来どおり退避する。変わるのは「1 回目の再試行までの待ち」のみ
- **FR-003**: deploy 後、実機 config.json から 2026-07-04 06:14 の暫定 override key を削除 (= default 5 が効く状態に、[[feedback-config-setdefault-override]] の落とし穴回避)
- **NFR-001**: burst 用の `realtime_burst_rejoin_backoff_initial_sec` (= spec 026、既に 5s) は対象外

## Success Criteria

- **SC-1**: deploy + override 削除後、挙動不変 (= override 値と default 値が同一のため)。/api/config で key 不在確認
- **SC-2**: 以後 24h で reconnect の attempt>1 連鎖 (= escalation 発動) が異常増していない

## 関連参照

- spec 017 (= backoff 導入)、spec 026 (= burst 中 5s 前例)、spec 050/051 (= 本 spec の文脈)
- specs/051-proactive-session-refresh/observation-2026-07-04.md (= A/B 記録)
