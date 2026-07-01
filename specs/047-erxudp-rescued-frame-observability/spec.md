# Feature Specification: ERXUDP rescued frame 内訳観測 (= ESV / TID / lag bucket 分解)

**Feature Branch**: `047-erxudp-rescued-frame-observability`
**Created**: 2026-07-02
**Status**: Draft
**Input**: 2026-07-02 Fable 分析 (= metric + code 読み) で「瞬時電力取得率 20.7% の主犯は bridge 側 read_erxudp の救済ロジックが cycle を乗っ取る構造」の疑いが浮上。改修 (= rescue 後も expected TID を待ち続ける構造変更) の前に、救済 frame の sub-population を counter で分解して仮説を data で確定する。

## Background

### 観測 data (= 2026-07-02 08:00 JST、24h 集計)

- live `power_watts` = 12.4 件/h (= 想定 60/h の 20.7%)
- `erxudp_recovered_from_mismatch_total` = 265 件/24h、うち `power_w_recovered_backfill_total` = 117 件 = **rescued の 56% (= 148 件) が measurement 空**
- `erxudp_recovered_lag_p50` = **1 秒** / p95 = 240 秒 / max = 15,938 秒 = 分布が強い多峰性
- spec 020 v1.5 実機観察: mismatch frame の **100% が `got_tid=0`** (= メーター TID echo 不良)
- `erxudp_tid_mismatch_lag_{p50,p95,max}` は got_tid=0 のため「送信 counter の値」を写すだけのノイズと確定

### 仮説 (= 本 spec で data 確定させる対象)

- **H1 (誤分類)**: `got_tid=0` かつ lag ≈ 1s の rescued frame は「遅延応答」ではなく**実質 live の正常応答**。TID=0 quirk のせいで `lookup_latest` fallback (= `mqtt_bridge.py:3740-3741`) に落ち、backfill 系列 (= `power_watts_recovered`) に誤分類されている。真なら live 転換で +5/h 級。
- **H2 (INF 混入)**: `read_erxudp:3718` の TID check が ESV filter より先に走るため、メーター自律通知 (= ESV 0x73 INF 等) も「救済」対象になり、`parse_el_response` (= ESV 0x72/0x52 のみ受理) で measurement 空 → **poll_success 扱いで cycle を浪費**。rescued 148 件/24h の無 payload の説明候補。
- **H3 (chain 自走)**: rescue 即 return (= `read_erxudp:3755`) により自 cycle の本来の応答を放棄 → 次 cycle で再び mismatch 救済、の 1 cycle 遅れ chain が自走。lag ≈ 60-70s の rescued 峰が観測されれば真。

## User Scenarios & Testing

### Primary User Story

bridge 運用者 (= tendo) として、rescued frame の ESV / TID / lag 内訳を Grafana で見て、H1/H2/H3 のどれが取得率低迷の支配要因かを判定したい。判定後の改修 spec (= read_erxudp 構造変更) の設計根拠にする。

### Acceptance Scenarios

1. **Given** bridge が rescued frame を処理した、**When** `/api/diag` snapshot を見る、**Then** ESV 別 counter (= get_res / get_sna / inf / other) が rescued 総数と一致する
2. **Given** rescued frame の got_tid が 0、**When** snapshot を見る、**Then** `rescued_tid_zero_total` が inc され、ring 正規 hit (= got_tid≠0) と分離計上される
3. **Given** rescued frame の lag が 3s、**When** snapshot を見る、**Then** lag bucket `lt5s` が inc される (= 5s/60s/300s 境界の 4 bucket)
4. **Given** 24h 観測後、**When** gcx で各 counter を query、**Then** H1/H2/H3 の支配比率が判定できる

## Requirements

### Functional Requirements

- **FR-001**: `DiagState` に rescued frame の **ESV 内訳 counter** 4 本を追加する: `erxudp_rescued_esv_get_res_total` (= 0x72) / `erxudp_rescued_esv_get_sna_total` (= 0x52) / `erxudp_rescued_esv_inf_total` (= 0x73) / `erxudp_rescued_esv_other_total`。inc 箇所は `read_erxudp` の rescue path (= 3742-3755) で payload から ESV を抽出して分類。
- **FR-002**: rescued frame の **TID 内訳 counter** 2 本を追加する: `erxudp_rescued_tid_zero_total` (= got_tid=0 → lookup_latest fallback 経由) / `erxudp_rescued_tid_ring_hit_total` (= got_tid≠0 の正規 ring hit)。
- **FR-003**: rescued frame の **lag bucket counter** 4 本を追加する: `erxudp_rescued_lag_lt5s_total` / `..._5to60s_total` / `..._60to300s_total` / `..._gt300s_total`。境界の意味: <5s = H1 (実質 live)、5-60s = 8s timeout 直後の真の遅延、60-300s = H3 chain / queue 深滞留、>300s = stale (= reconnect 跨ぎ)。
- **FR-004**: **measurement 空判定 counter** 1 本: `erxudp_rescued_empty_measurement_total`。main loop 側 (= `parse_el_response` → `decode_measurements` 後) で rescued cycle (= `_late_ts is not None`) かつ m が publish 対象 key を 1 つも含まない場合に inc。H2 の直接証拠。
- **FR-005**: 全 11 counter を `DIAG_SENSOR_DEFS` に diagnostic entity として登録し、`snapshot()` に含める (= zero-omit しない: 比率計算に 0 も必要)。
- **FR-006**: `erxudp_tid_mismatch_lag_{p50,p95,max}` の記録を **got_tid≠0 の場合のみ** に修正する (= ノイズ化した既存 metric の最小修理。削除はしない = series 連続性維持)。
- **FR-007**: polling 挙動 (= 送信、timeout、rescue の判定と return) は**一切変更しない**。観測のみ。

### Non-Functional / 制約

- **NFR-001**: deploy は spec 032 の 24h SC 判定 (= 2026-07-03 朝) 完了後に行う。counter 追加自体は挙動不変だが、bridge restart が SC window の連続性を乱すため。
- **NFR-002**: Python 2.7 stdlib のみ (= Cube J1 ターゲット)。テストは host pytest。
- **NFR-003**: compose repo の telegraf `topics` 明示列挙に新 11 topic を追加する (= CLAUDE.md「DIAG metric 追加時の必須手順」4 段 pipeline checklist 遵守。spec 037/042/044 の 3 連続見落とし事故の再発防止)。

### Success Criteria

- **SC-1**: 24h 観測で `rescued_esv_*` 4 本の合計 = `erxudp_recovered_from_mismatch_total` の増分と一致 (= 分類漏れなし)
- **SC-2**: H1/H2/H3 のうち支配要因 (= rescued の 50% 超を説明するもの) が 1 つ以上確定し、改修 spec の設計判断 (= 「rescue 後も待つ」構造変更 or 「TID=0 即時応答を live 扱い」) に直結する
- **SC-3**: bridge `/api/diag` (= port 8080) と gcx の両方で新 counter が観測できる (= pipeline 4 段 verify)

## 関連参照

- 分析元 data: 2026-07-02 Fable 分析 (= 本 spec Input 節)
- `~/.claude/projects/-Users-tendo-git-cube-j1-mqtt/todo.md` Bug / 瞬時電力取得率 節 (= 改修候補の全リスト)
- spec 020 (= TID mismatch 救済の導入元)、spec 028/029/046 (= backfill publish 系)
- memory `feedback-compose-telegraf-pipeline` (= telegraf topics 追加手順)
- tako 合議 2026-07-02 (= 案1-5。本 spec の結果次第で案1 の前提を再検証)
