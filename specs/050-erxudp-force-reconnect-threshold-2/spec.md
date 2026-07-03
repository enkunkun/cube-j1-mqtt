# Feature Specification: erxudp_timeout_force_reconnect_threshold default 6 → 2

**Feature Branch**: `050-erxudp-force-reconnect-threshold-2`
**Created**: 2026-07-03
**Status**: Draft
**Input**: 2026-07-03 09:30 の gap 解剖 (= 「blackout の約 7 割は bridge 側の連続 timeout 待ちが作っている」) → 同日 09:42 に実機 config override (= threshold 2) で試行 → 12:48 の 3h 測定で効果確定。ユーザー事前承認 (= 「12:47の測定結果が良ければspec 050まで進めて」) に基づき default へ昇格する。

## Background

### gap 解剖 (2026-07-03 06:30〜09:20 JST、threshold=6)

- メーターは PANA reauth 周期 (~10 分毎) に無応答窓を作る → bridge は timeout を 6 回連続 (= ~6.8 分) 待ってから force reconnect → reconnect (= spec 035 cached path、~30s) 直後の poll は即成功
- 結果: **5.5〜8 分の blackout が 16 回/3h**、live 17.8/h (= raw sample dedupe)
- gap 終端 = reconnect 完了時刻と全件一致 = reconnect が有効な回復手段であることの直接証拠

### override 試行の 3h 測定 (09:47〜12:48 JST、threshold=2)

| 指標 | before (=6) | after (=2) |
|---|---|---|
| live rate (dedupe) | 17.8/h | 23.3/h (+31%) |
| 300s 超 blackout | 16 回/3h | **2 回/3h** |
| 最長 gap | 495s | 436s |
| reconnect | 7.1/h | 11.1/h (= rollback 閾値 20/h の半分) |

gap の主成分が「2 timeout + reconnect ≈ 2.5〜3 分」の設計予測どおりの形に変化。live 不足分 (gate 25/h に対し 23.3) は同窓の noise バースト由来 (= spec 049 の担当領域)。

## Requirements

### Functional Requirements

- **FR-001**: `apply_defaults` の `erxudp_timeout_force_reconnect_threshold` default を **6 → 2** に変更する (= spec 032 の 30→6 に続く 2 段目の反応性 tuning)
- **FR-002**: config.json での明示 override は従来どおり優先される (= 挙動不変、test で担保継続)
- **FR-003**: deploy 後、実機 config.json から 2026-07-03 09:42 の暫定 override key を**削除**する (= default 2 が効く状態に戻し、memory feedback-config-setdefault-override の「override が将来の default 変更を隠す」落とし穴を残さない)

### Non-Functional / 制約

- **NFR-001**: burst 中の threshold (= `realtime_burst_force_reconnect_threshold`、spec 025) は対象外・変更しない
- **NFR-002**: 実機は既に override で threshold=2 稼働中のため、deploy 前後で挙動は変わらない (= 低 risk deploy)

### Success Criteria

- **SC-1**: deploy + override 削除後、`/api/config` で threshold=2 (= default 由来) を確認
- **SC-2**: 以後 24h で reconnect ≤ 20/h 維持 + 300s 超 blackout が 3h あたり数回以下の水準維持 (= 既存巡回 cron で確認)

## 関連参照

- spec 027 (= threshold 30 の導入)、spec 032 (= 30→6)、spec 035 (= reconnect 高速化が本 spec の前提)
- specs/048/observation-2026-07-03.md (= INF filter で chain 消滅、gap の残りが本 spec の対象)
- memory `feedback-config-setdefault-override` (= override 落とし穴)、`feedback-deploy-timestamp-derive-not-estimate`
