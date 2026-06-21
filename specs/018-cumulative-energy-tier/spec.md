# Feature Specification: Cumulative Energy at Fixed-Time (Tier 4)

**Feature Branch**: `018-cumulative-energy-tier`
**Created**: 2026-06-21
**Status**: Draft
**Input**: User description: "hals5412 fork (c884ec3) の定時積算電力量 (EPC 0xEA/0xEB) 取得を、 当方 spec 011 C tier rotation の枠組みに tier4 として組み込み、 HA Energy ダッシュボードの精度を上げる"

## Background

当方の spec 011 C tier rotation で polling EPC を 3 tier に分けている:

- **tier1** (毎サイクル): `[0xE7, 0xE8]` 瞬時電力 + 瞬時電流
- **tier2** (5 サイクルに 1 回): `[0xE0, 0xE3]` 積算電力量 forward / reverse
- **tier3** (60 サイクルに 1 回): `[0xD3, 0xE1]` 係数 / 単位

これで HA に瞬時電力をリアルタイムで流せている。 しかし HA の Energy ダッシュボードは **30 分粒度** の積算電力量を期待しており、 現状の tier2 `0xE0/0xE3` は「ポーリング時点の累積値」を都度差分計算する形になる。 ポーリング間隔が一定でないとグラフが歪む。

ECHONET Lite には **0xEA (定時積算電力量 forward) / 0xEB (定時積算電力量 reverse)** という、 メーター側で 30 分境界で記録された値 + 内部 timestamp を返す EPC がある。 これを使うと:

- メーター側が記録した 30 分境界の値をそのまま流せる → ポーリング誤差ゼロ
- メーター側 timestamp 付きなので、 ネットワーク遅延の影響なし
- HA Energy ダッシュボードの仕様 (30 分境界の累積値) に完全一致

hals5412/cube-j1-mqtt の `c884ec3` で 0xEA/0xEB の取得が実装されているが、 fork 設計は「1 サイクルで全 EPC まとめて Get」モデルで当方とは思想が逆。 当方は tier 分割を保ったまま、 **tier4 (30 分周期 = 1800 秒周期 = 1800/60=30 サイクル目)** として 0xEA/0xEB を組み込む。

## Scope

### tier4 の新設

- `production_tool/mqtt_bridge.py` で:
  - `TIER4_EPCS = [0xEA, 0xEB]` 定数を追加
  - `decide_epc_tier(cycle_number, ...)` を拡張:
    - `tier4_every` (default 30) パラメータを追加
    - tier4 が tier2/tier3 と衝突した場合の優先順序を明確化 (例: tier4 > tier3 > tier2 > tier1)
  - `epcs_for_tier()` に `"tier4"` ブランチ追加
  - `apply_defaults` に `epc_tier4_every` (default 30) 追加

### decode + publish

- `decode_measurements()` に 0xEA/0xEB のデコーダ追加:
  - 11 byte response: `YYYY MM DD HH mm` (5 byte timestamp) + `cumulative_energy` (4 byte) + `unit_byte` などのフォーマット (実装時に ECHONET Lite 仕様確認)
  - 結果は `{"cumulative_energy_forward_fixed": float, "cumulative_energy_fixed_ts": "YYYY-MM-DDTHH:mm:00Z"}` のような専用キーで返す
- `apply_energy_scale()` で coefficient と unit を適用
- HA discovery (`DIAG_SENSOR_DEFS` または別領域) に 2 sensor 追加:
  - `cumulative_energy_forward_fixed_kwh` (device_class=energy, state_class=total_increasing)
  - `cumulative_energy_reverse_fixed_kwh` (同上)
- 既存 `cumulative_energy_forward_kwh` (0xE0 由来) と併存 (新は HA Energy 専用、 旧は瞬時の累積値確認用)

### MQTT publish

- 既存 `publish_measurements()` の流れに乗せる、 tier4 cycle のときだけ値が入る
- `last_changed` timestamp はメーター側 timestamp を使う (内部 clock より正確)

## Non-Scope

- 0x88 (異常通知): 当方 spec 006 で sk_event 経由の error counter で十分カバー、 追加不要
- 0x97 (時刻) / 0x98 (日付): NTP で十分、 メーター clock を信用しない
- 過去 30 分の履歴 (0xE2 / 0xE4): メーター側で 48 timestamp 分保持できるが HA との同期コストが高い、 当面 0xEA/0xEB のみ
- 一括 Get への切り替え: tier rotation 設計を維持

## User Scenarios *(mandatory)*

### Primary User Story

ユーザが HA Energy ダッシュボードを開くと、 30 分粒度の電力消費グラフがメーター記録通りの値で表示される。 ポーリングのジッタや bridge 再起動による欠損が無く、 月次のエネルギー集計がメーター実測と一致する (誤差 ±0.1 kWh 以内)。

### Acceptance Scenarios

1. **Given** bridge 起動後、 cycle 番号が 0,1,...,29、 **When** `decide_epc_tier(30, ..., tier4_every=30)`、 **Then** "tier4" 返却
2. **Given** cycle 番号が 60 (tier3 と tier4 両方の条件成立)、 **When** decide_epc_tier、 **Then** "tier4" を優先 (頻度低い順)
3. **Given** tier4 cycle で 0xEA/0xEB の応答受信、 **When** decode_measurements、 **Then** `cumulative_energy_forward_fixed`, `cumulative_energy_reverse_fixed`, `cumulative_energy_fixed_ts` が返却される
4. **Given** `epc_tier4_every=0`、 **When** decide_epc_tier、 **Then** tier4 を発火させない (= 機能無効化、 既存挙動)
5. **Given** HA discovery 発行後、 **When** HA Energy ダッシュボード設定、 **Then** `cumulative_energy_forward_fixed_kwh` が選択肢に出てくる

### Key Entities

- **`TIER4_EPCS = [0xEA, 0xEB]`**: モジュール定数
- **`epc_tier4_every`**: config キー (default 30)
- **`decide_epc_tier(cycle, tier2_every, tier3_every, tier4_every)`**: 拡張シグネチャ
- **`epcs_for_tier("tier4")`**: 新ブランチ
- **`cumulative_energy_forward_fixed`** / **`cumulative_energy_reverse_fixed`** / **`cumulative_energy_fixed_ts`**: measurement dict キー

## Edge Cases

- メーターが 0xEA/0xEB を未対応: Get_SNA 応答 → 既存の None 扱い、 publish せず (snapshot から omit)
- メーター timestamp が未来 / 過去すぎる (5 分以上の差): WARN ログ、 ただし値は publish (HA 側で表示判定)
- tier4 と tier3 と tier2 が全て揃う cycle: tier4 採用 (最も頻度低い + 価値高い)
- 30 分境界に bridge 再起動: 起動後最初の tier4 cycle で値が取れるので欠損なし
- coefficient/unit が未取得状態 (tier3 まだ走っていない初回): 既存挙動通り、 raw 値で publish + WARN ログ

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `TIER4_EPCS = [0xEA, 0xEB]` を定義する
- **FR-002**: `decide_epc_tier(cycle, tier2_every=5, tier3_every=60, tier4_every=30)` に tier4 を追加、 優先順は tier4 > tier3 > tier2 > tier1
- **FR-003**: `epcs_for_tier("tier4")` で `TIER4_EPCS` を返す
- **FR-004**: `apply_defaults` で `epc_tier4_every` (default 30) を設定
- **FR-005**: `decode_measurements()` で 0xEA/0xEB を decode し、 専用キーで返す
- **FR-006**: `apply_energy_scale()` で 0xEA/0xEB の値にも coefficient と unit を適用
- **FR-007**: HA discovery で `cumulative_energy_forward_fixed_kwh` / `cumulative_energy_reverse_fixed_kwh` を device_class=energy, state_class=total_increasing で publish
- **FR-008**: メーター timestamp (`cumulative_energy_fixed_ts`) を MQTT attribute として publish
- **FR-009**: `epc_tier4_every=0` で機能無効化 (既存挙動互換)

### Key Entities

- 上記 Scope 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: `decide_epc_tier` の優先順序が `(60, tier2_every=5, tier3_every=60, tier4_every=30) = "tier4"` (tier3 と tier4 が衝突しても tier4 採用)
- **SC-002**: 単体テスト: `decode_measurements` が 11 byte の 0xEA レスポンスから正しく timestamp + 累積値を抽出
- **SC-003**: 実機 1 週間運用後、 HA Energy ダッシュボードでの月次集計が メーター物理表示の値と ±0.1 kWh 以内で一致
- **SC-004**: tier4 cycle 中も `cube_j1_smart_meter_power_watts` (tier1) のグラフに穴が出ない (tier4 cycle が tier1 を完全に置き換えるわけでない場合の確認、 もしくは 30 サイクル目 = 30 分に 1 回の small jitter は許容)
- **SC-005**: 既存テスト全件 pass、 既存 sensor (`cumulative_energy_forward_kwh` = 0xE0 由来) の挙動に変化なし

## Assumptions

- メーター (B-route 接続先) が 0xEA/0xEB をサポートしている (大半の現行スマートメーターは対応、 未対応なら本 spec の効果はゼロ)
- ECHONET Lite 仕様書の 0xEA/0xEB のフォーマット定義が安定
- 30 分粒度で HA Energy が記録すれば実用上十分 (1 分粒度等は対象外)
- tier4 を `every=30` に設定したとき、 30 サイクル = ポーリング間隔 60s × 30 = 30 分で 0xEA の境界とほぼ一致 (厳密に同期はメーター側 timestamp で吸収)
- coefficient/unit が安定 (tier3 が機能していて、 値が取得済み)
