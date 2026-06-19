# Feature Specification: Real-time Wi-SUN Quality Page

**Feature Branch**: `007-wisun-quality-page`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "LQI がリアルタイムで見られる Web ページを作って、 Cube J1 の物理位置調整に使いたい"

## Background

spec 006 で `erxudp_latency_p50/p95_ms` を 200 件 rolling window で取れるようにしたところ、 実機の **p95 = 5.4 秒** と判明 (`read_erxudp` timeout=15s の 1/3)。 Wi-SUN の通信品質自体が悪く、 timeout 連発で 4 分の穴が定期的に発生している (`wisun_reconnects_total` が 30 分で +3)。

物理的な位置調整 (アンテナ向き、 メーターからの距離、 干渉源回避) を試したいが、 Grafana の 30 秒 refresh では位置を動かしながらリアルタイムに変化を見られない。

note: SKSTACK 実機調査の結果 (`/api/erxudp_raw` で token_count=10 を確認) ERXUDP には LQI/RSSI フィールドが含まれない。 真の LQI を取るには `SKSCAN` 再実行 (5-60 秒) が必要だが、 これは破壊的操作。

そこで既存の `erxudp_latency_ms_recent` を「Wi-SUN 品質の代理指標」として使い、 admin UI に位置調整専用の高頻度更新ページを追加する。

## Scope

- admin UI に `/wisun` ページを追加
- 表示要素:
  - 現在の p50 / p95 / max (大きな数字、 閾値ベースの色分け 200/500/1000 ms)
  - 直近 N サンプル (default 100) の RTT 折れ線 (SVG sparkline、 stdlib のみ)
  - サンプル数 / uptime / 最新 raw ERXUDP 行 (compact)
- JS で 1.5 秒間隔で `/api/wisun_quality` を fetch して再描画
- `/api/wisun_quality` endpoint: 直近 N 件の RTT 配列 + p50/p95/max を JSON で返す

## Non-Scope

- SKSCAN 再実行による真の LQI 取得 (将来オプション、 spec 008 候補)
- ヒートマップやプロットツール (位置記録は人間が紙メモで)
- 認証の独自実装 (既存の admin Basic Auth を踏襲)

## User Scenarios *(mandatory)*

### Primary User Story

開発者として Mac / スマホで http://192.168.100.1:8080/wisun (AP モード経由) を開き、 Cube J1 を抱えながら家の中を移動する。 リアルタイムで RTT の色変化 / 数字の挙動を見て、 メーター隣 / リビング / 玄関などで p50 がどう変わるかを比較し、 最も静かな位置に固定設置する。

### Acceptance Scenarios

1. **Given** ブラウザで `/wisun` を開く、 **When** 認証 OK、 **Then** タイトル `Wi-SUN Quality` の HTML が返り、 初期データが描画される
2. **Given** ページ表示中、 **When** 1.5 秒経過、 **Then** `/api/wisun_quality` が再 fetch され sparkline と数字が更新される
3. **Given** RTT 観測値が 200 ms 以下、 **When** ページ表示、 **Then** p50 が緑系の色で表示
4. **Given** RTT 観測値が 1000 ms 超、 **When** ページ表示、 **Then** p50 が赤で表示
5. **Given** Cube J1 を別の位置に移動、 **When** 数十秒待つ、 **Then** rolling window が入れ替わり数字に反映される

### Key Entities

- **`erxudp_latency_ms_recent`** (既存): `collections.deque(maxlen=200)`、 直近 RTT
- **`/api/wisun_quality`**: 新 endpoint。 `{"samples": [..], "p50_ms": .., "p95_ms": .., "max_ms": .., "sample_count": .., "uptime_seconds": ..}`
- **`WISUN_HTML`**: 新 HTML テンプレート、 admin UI の独立ページ
- **`render_sparkline(samples, w, h)`**: pure helper、 SVG path 文字列を返す

## Edge Cases

- サンプル 0 件: sparkline は描画しない、 数字は "—" 表示
- サンプル 1 件のみ: 折れ線は描画せず点表示 (or 数字のみ)
- 200 件超: deque maxlen で自動 truncate (spec 006 と同じ)
- ネットワーク断: JS fetch エラー時は前回値表示維持 + 「offline」 表示

## Success Criteria *(mandatory)*

- **SC-001 [interactivity]**: ページ開いてから 2 秒以内に最新値で更新される
- **SC-002 [zero install]**: 外部 JS / CSS / フォント無し、 全部 inline (admin UI 一貫性 + offline 想定)
- **SC-003 [no measurement impact]**: `/api/wisun_quality` 1.5 秒呼び出しがメインの poll loop に影響しない (既存 admin UI と同じく独立スレッド)
- **SC-004 [pure helper]**: `render_sparkline(samples, w, h)` が unit test 可能で、 同じ入力に同じ SVG path を返す (deterministic)

## Assumptions

- 既存 admin UI が動いていて Basic Auth がかかっている
- `diag_state_provider()` 経由で `erxudp_latency_ms_recent` を取れる (spec 006 で追加済み)
- Cube J1 を持ち運ぶ際は AP モード (192.168.100.1) で接続、 Wi-Fi 経由でも同じ admin UI ポート 8080

## Dependencies

- spec 003 admin UI (`AdminHandler`, `ADMIN_HTML`, Basic Auth)
- spec 006 `erxudp_latency_ms_recent` deque
- spec 006 `_percentile` helper
