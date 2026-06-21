# Feature Specification: Noise-Adaptive Poll Skip

**Feature Branch**: `012-noise-adaptive-skip`
**Created**: 2026-06-21
**Status**: Draft
**Input**: User description: "EEDSCAN で ch57 が高ノイズ (≥100) の時間帯は poll を skip して wisun_reconnect 過剰発火を抑える"

## Background

spec 011 (timeout 30s + retry x2 + EPC tier 分割) を deploy して 34 時間運用したが:
- `erxudp_timeouts = 745` / `erxudp_recovered_by_retry = 23` → recovery rate 3% で **retry はほぼ無力**
- `wisun_reconnects = 137` (1.4 日) = 約 15 分に 1 回再接続
- `sk_error_ER10 = 216` (BP35CX 受信キュー詰まり)
- `eedscan_pan_channel_energy` が **bimodal** (静か 6-30 / うるさい 188-202)、 24h 全域に散在 (約 50 分に 1 回 5-10 分継続)

= ch57 に **断続的な外部干渉源** (近隣 B-route メーター / LPWA / 920MHz IoT)。 物理層で受信が失敗するので timeout/retry はどれも無力。

本 spec ではノイズ時間帯に poll を skip して、 受信失敗連発による wisun_reconnect 過剰発火を抑える。 静かな時間帯のみ poll するので publish 抜けは増えるが、 reconnect 回数が減って復帰後の安定動作期間が延びる純利益を狙う。

## Scope

- `EedScanState` に `is_noisy(threshold)` メソッド追加: 直近の pan_channel_energy を threshold と比較
- main loop の normal cycle 前に noise check:
  1. 直近 EEDSCAN が threshold (default 100) 以上なら poll skip
  2. 連続 skip 数を track、 max_consecutive_skip (default 3) を超えたら強制 poll
- 新 config キー:
  - `noise_adaptive_skip_enabled` (default True)
  - `noise_skip_threshold` (default 100)
  - `noise_skip_max_consecutive` (default 3)
- EEDSCAN interval を 5 分 → 2 分 (default 120) に短縮、 ノイズ判定をフレッシュに保つ
- DiagState に `noise_adaptive_skips_total` カウンタ追加 → publish_diag
- DIAG_SENSOR_DEFS に追加 → Grafana で可視化

## Non-Scope

- 周波数 hopping / channel migration (B-route 規格上不可)
- 自前 RSSI 計測のような高度な adaptive (現実装で十分)
- ノイズ源特定 / SDR スペクトラム分析

## User Scenarios *(mandatory)*

### Primary User Story

ノイズ時間帯 (約 50 分に 1 回 5-10 分継続) は poll を skip して、 静かな時間帯の poll 成功率を高める。 結果として wisun_reconnect 回数が大きく減り、 publish 抜け期間中も bridge が安定動作し続ける。

### Acceptance Scenarios

1. **Given** 直近 EEDSCAN で `eedscan_pan_channel_energy = 200` (high)、 **When** main loop の normal cycle 入口、 **Then** poll skip、 `noise_adaptive_skips_total` +1
2. **Given** 直近 EEDSCAN が `energy = 15` (low)、 **When** normal cycle、 **Then** 通常 poll 実行
3. **Given** 連続 3 cycle ノイズ高で skip、 **When** 4 cycle 目もノイズ高、 **Then** 強制 poll 実行 (= 5 分間 silence にしない fail-safe)
4. **Given** EedScanState に sample がまだ無い (起動直後)、 **When** noise check、 **Then** skip しない (== 通常 poll)
5. **Given** `noise_adaptive_skip_enabled = False`、 **When** normal cycle、 **Then** noise check せず常に通常 poll

### Key Entities

- **`EedScanState.is_noisy(threshold)`**: 直近 sweep の pan_channel_energy を threshold と比較 (None なら False)
- **`noise_skip_counter`**: main loop の local 連続 skip 数。 通常 poll 実行で 0 にリセット
- **新 config キー**: `noise_adaptive_skip_enabled`, `noise_skip_threshold`, `noise_skip_max_consecutive`
- **`noise_adaptive_skips_total`** counter on DiagState

## Edge Cases

- EedScanState が空 (起動直後、 EEDSCAN 未実行): noise check で False (= 通常 poll)
- EEDSCAN が古い (10 分前): 現実装では古さチェックなし。 force_run 経由で必ず新しい値を使う。 シンプル化のため判定不可時は通常 poll
- skip 連続が max_consecutive を超えたとき: 強制 poll で 1 回試す、 失敗してもカウンタはリセット (= 次の周期も強制 poll しない)
- 既存 force_wisun_reconnect threshold は変えない (連続 erxudp_timeout 5 回で発火) → noise skip も「成功」とみなさず、 既存ロジックに影響しない

## Success Criteria *(mandatory)*

- **SC-001 [reconnect reduction]**: 24h 連続稼働で `wisun_reconnects_total` の傾きが baseline (1.4 日で 137 = ~4/h) より **30% 以上減少**
- **SC-002 [skip activity visible]**: Grafana で `noise_adaptive_skips_total` が観測でき、 EEDSCAN ノイズスパイクと相関
- **SC-003 [pure helper]**: `is_noisy` は pure 関数、 同入力に同出力、 unit test 可能
- **SC-004 [fail-safe]**: max_consecutive_skip を超えたら 1 回強制 poll、 5 分連続 skip にしない

## Assumptions

- ノイズスパイクは 5-10 分継続 (実測)、 連続 3 skip = 約 3 分の skip で済む
- 強制 poll 後に retry / wisun_reconnect が発火するのは妥協ライン

## Dependencies

- `production_tool/mqtt_bridge.py` の main loop, EedScanState, DiagState, apply_defaults, DIAG_SENSOR_DEFS
