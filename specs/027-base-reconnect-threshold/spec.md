# Feature Specification: Base mode の force_wisun_reconnect threshold 5 → 30

**Feature Branch**: `027-base-reconnect-threshold`
**Created**: 2026-06-23
**Status**: Draft
**Input**: ユーザコメント「相変わらず欠損が目立つ。 どうにかならないか」。 spec 022/023/025/026 で burst mode は機能完成したが、 grafana-cloud 実機データ確認で **24h power_watts 78% 欠損**、 **直近 1h 88% 欠損**、 主因は base mode (= 24h の 99%) の reconnect 連発 (= 24h 累計 430 回 reconnect = 1 回/3 分平均、 ピーク 280 件/h)。

## Background

spec 011 で導入した `erxudp_timeout_force_reconnect_threshold=5` は「メーター ERXUDP 5 連続失敗 = Wi-SUN セッション死亡判定 → 強制 reconnect」。 通常 60s polling × 5 = 5 分でメーター dead を検知する設計、 当時は妥当に見えた。

実機運用 (= spec 011 deploy 後数日) で判明:
- メーター応答 p50=3.7 秒、 p95=4.1 秒 (= 30s timeout 内だが余裕薄)
- ピーク時間帯 (= JST 22:00-24:00 = 高負荷時間) で 30s timeout 超 fail が頻発
- 5 連続 fail (= 150 秒) は 1 reconnect/3 分で常時発生 → reconnect → 30s backoff → SKJOIN → 再度応答悪い → 再 5 連続 → loop
- 結果 24h で power_watts サンプル 22% (= 78% 欠損)

spec 025 で burst 中のみ threshold 30 に緩和したが、 base mode (= 24h の 99% 以上) は手付かず。 **base mode の reconnect が欠損の主因** と実機データで確定。

spec 027 で base default を 30 に上げる (= spec 025 と同水準)。 メーター完全 dead 検知は 30 × 30s = 15 分に延びるが、 24h 78% 欠損より圧倒的に有利。

## Scope

### A. apply_defaults の default 変更

- `erxudp_timeout_force_reconnect_threshold`: default `5` → **`30`**
- 既存 helper (`should_force_wisun_reconnect`, `compute_force_reconnect_threshold`) は変更なし
- 既存 spec 025 mode 依存 helper は維持 (= 将来 burst 用に更に緩和したい時のため、 mode='burst' と 'off' で同じ 30 になるが将来別値設定可能)

### B. メーター完全 dead 検知遅延の許容

- 旧: 5 × 30s = **150 秒 (= 2.5 分)** で reconnect 発火
- 新: 30 × 30s = **900 秒 (= 15 分)** で reconnect 発火
- 15 分 dead でユーザ実害: HA Energy 月次集計影響軽微、 瞬時電力グラフ穴 (= 15 分穴、 24h 78% 欠損よりずっと小)

## Non-Scope

- `wisun_rejoin_backoff_initial_sec` 変更 (= reconnect 後の backoff 短縮): spec 026 で burst 中だけ短縮済、 base mode の backoff は別 spec 検討
- ARIB STD-T108 / Wi-SUN session 安定化: 物理層、 当方範囲外
- メーター物理交換 / ファームウェア update: 当方範囲外

## User Scenarios

### Primary User Story

ユーザは Grafana で 24h 連続の電力グラフを確認、 spec 027 deploy 後 数日で「欠損なく値が並んでる」 と観察する。 reconnect 回数は減るが、 万一メーター完全 dead 時は 15 分後に reconnect 発火 = 確実に復帰。

### Acceptance Scenarios

1. **Given** apply_defaults 呼出、 **When** config に override なし、 **Then** `erxudp_timeout_force_reconnect_threshold == 30`
2. **Given** メーター応答悪化で 5 連続 timeout、 **When** main loop iter、 **Then** **reconnect 発火しない** (= threshold 30 未満)
3. **Given** 30 連続 timeout、 **When** main loop iter、 **Then** reconnect 発火 (= 完全 dead 判定)
4. **Given** 既存 config で明示的に `erxudp_timeout_force_reconnect_threshold=5` セット、 **When** apply_defaults、 **Then** 5 を維持 (= setdefault の挙動、 ユーザ意図優先)

## Edge Cases

- 既存運用 config で `erxudp_timeout_force_reconnect_threshold` が override 設定済の場合: setdefault なので変更なし、 ユーザが明示的に 5 を維持していれば既存挙動。 default 30 が effect するのは config 未設定 (= 大半のユーザ) のみ
- 24h で常時 30 連続未達: メーター応答が極端に悪化しても reconnect しない、 だが ARIB / BP35CX 側で別の保護機構 (= EVENT 24/29 PANA fail) が effect、 spec 017 経由で reconnect 発火可能
- メーター完全 dead 15 分: HA Energy 集計に対する影響微少 (= 月次集計が ±0.3kWh ずれる可能性)、 spec 018 メーター内蔵 timestamp で補正可能

## Requirements

- **FR-001**: `apply_defaults` で `out.setdefault("erxudp_timeout_force_reconnect_threshold", 30)` に default 値変更
- **FR-002**: 既存テスト (test_should_force_reconnect.py 8 件) は threshold を引数 pass なので影響なし、 全 pass 維持
- **FR-003**: 新 test 追加: `test_apply_defaults_force_reconnect_threshold_is_30` で default value 確認
- **FR-004**: 既存 explicit override (config に `erxudp_timeout_force_reconnect_threshold` 明示設定) は維持

## Success Criteria

- **SC-001**: 単体テスト: apply_defaults default = 30 確認 (1 件)
- **SC-002**: 既存テスト全件 pass (= spec 011/017/025 既存挙動互換)
- **SC-003**: 実機 24h で `cube_j1_smart_meter_wisun_reconnects_total` の increase が 430 → **50 件未満** に減少
- **SC-004**: 実機 24h で `cube_j1_smart_meter_power_watts` のサンプル数が 315 → **1000 件以上** (= 欠損率 78% → 30% 未満)

## Assumptions

- メーター完全 dead は実用上稀 (= 1 回/月 程度)、 検知 15 分遅延は許容
- spec 017 EVENT 24/29 trigger は dead 検知の補助経路として効く
- 関連: [[spec-011-erxudp-resilience]] (元 threshold 5)、 [[spec-024-bridge-die-restart-investigation]] (調査で base threshold 主因と判明)、 [[spec-025-burst-reconnect-threshold]] (burst 30 と整合)
