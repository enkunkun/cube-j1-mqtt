# Feature Specification: Instantaneous Power 主系 CT 経路化 (= B-route アーキテクチャ限界回避)

**Feature Branch**: `031-ct-clamp-instantaneous-power`
**Created**: 2026-06-26
**Status**: **Design only** (= hardware 設置は user 側別作業、 設計指針記録のみ)
**Input**: tako 5 agent 合議結果 (= 2026-06-26): 「B-route スマートメーターで Instantaneous Power 1 分粒度連続取得は仕様/物理限界、 CT クランプ / IoT 電力モニタ追加が根本解決」

## Background

spec 028/029 で「救済込み Instantaneous Power 可視化」 完成 (= panel-1 黄色 dot + panel-10 青/橙 dot)、 ただし実機運用で **Instantaneous Power が grafana 上スカスカで使い物にならない**:
- 9.5h 運用で polling timeout 率 80%
- 復活パターン: reconnect → 3-6 分 polling 成功 → 15-25 分連続消息断
- spec 028 救済 backfill = 9.5h で 3 件のみ、 spec 029 = 4 件
- spec 028 v1.1 hotfix (= `qos=0` 削除) で reconnect 強制誘発バグ解消後でも改善小

tako parallel judge (= 2026-06-26) で全 5 agent (codex / agy / kimi-code / deepseek-pro / qwen-plus) **強い consensus**: B-route 60s polling で 1 分粒度連続瞬時電力は **architectural cap** (= 設計上限)、 burst 5s 常時化は ARIB duty cycle + MAC backoff で悪化、 backfill 強化は完全沈黙期間を埋められない。 根本解決は **CT clamp / IoT 電力モニタ追加で計測経路を冗長化**。

## Scope

### A. Hardware 選定指針 (= user 側別作業)

候補 (= tako 合議で挙がった、 国内/HA 対応):

| 機器 | 接続 | 分解能 | コスト目安 | HA 連携 |
|---|---|---|---|---|
| **Shelly EM** | Wi-Fi + CT | 1s | ~10k 円 | MQTT / API native |
| **Sonoff POW Ring** | Wi-Fi + CT | 1s | ~5-8k 円 | MQTT (Tasmota) |
| **Emporia Vue 2** | Wi-Fi + CT (multi-channel) | 1s | ~15-25k 円 (= 海外輸入) | HA Add-on / MQTT |
| **Nature Remo E (lite)** | Wi-Fi (= B-route + WAN) | 1 分 | ~10-15k 円 | API、 ただし B-route 経由なので spec 031 趣旨と矛盾 |
| **ラトック RS-WFWATTCH2** | Wi-Fi (= 単機計測、 CT 不要) | 1 分 | ~10k 円 | MQTT |

選定基準:
- **CT 方式優先** (= 分電盤主幹/個別回路を非侵入計測)、 Nature Remo E は B-route 経由なので除外候補
- **HA 公式 integration あり or MQTT publish 機能** = bridge 自作不要
- **電気工事士不要の CT クランプ** (= 配線切断なし、 主幹に挟むだけ)
- **国内技適 + 100V 対応** 必須

### B. cube-j1-mqtt bridge 側変更 (= 最小)

bridge は **触らない** (= B-route polling は spec 028/029 + spec 020 v1.5 完成状態を維持):
- E0/E3/EA/EB (= 累積系) は引き続き B-route で取得 (= 電力会社の検針値の真値)
- E7 (= 瞬時系) も B-route polling は継続、 ただし「補助・検算」 用途に降格

### C. HA 連携 (= user 側設定、 spec 031 設計範囲)

CT 機器の MQTT publish:
- topic 例: `home/power/instant_w` (= 1 秒粒度の瞬時電力)
- 既存 `cubej/cubej1/power` topic は維持 (= spec 028 backfill 経由でも publish)

HA discovery:
- CT 機器側で自動 discovery (= Shelly / Tasmota は対応)
- もしくは telegraf 経由で prometheus に直接送る別 pipeline

### D. grafana panel-1 進化 (= spec 031 deploy 後、 別 spec で実装)

panel-1 "Instantaneous Power" を「ハイブリッド表示」 に進化:
- refId=A: 既存 `cube_j1_smart_meter_power_watts` (= B-route polling、 sparseness あり、 補助線)
- refId=B: 既存 `cube_j1_smart_meter_power_watts_recovered` (= spec 028 backfill 救済点)
- **refId=E (新)**: `home_power_instant_w` 等 CT 経由 series (= 主線、 1 秒粒度連続)

color/legend で「ct = 主」 「broute = 補助」 を明示。 spec 028 spec.md / panel-1 設計と併存。

### E. B-route polling 軽量化 (= spec 027 v3 候補、 spec 031 と独立)

tako consensus 「polling を **減らす** 方向」:
- polling 周期 60s → 90-120s 試行
- tier rotation 維持、 ただし tier3 (= 係数/単位、 near-static) skip 頻度上げる
- 「1 通信で複数 EPC まとめ取得」 (= spec 011 C tier rotation の見直し)

これは spec 031 とは独立、 spec 027 v3 候補 (= 既存 todo.md 別エントリで管理)。

## Non-Scope

- bridge コード変更 (= 必要なし、 spec 028/029 + spec 020 v1.5 完成状態維持)
- B-route 廃止 (= 累積/検針/補正用途は継続必須、 電力会社一致値の真値)
- spec 028/029 panel-1/panel-10 既存設計の置換 (= 補助線として残す)
- 自家消費 dashboard 完全実装 (= spec 031 deploy 後の別 spec で UI/UX 詳細)
- 既存 HA Energy dashboard 連携の自動移行 (= user 側設定)

## User Scenarios

### Primary User Story

ユーザは grafana panel-1 で:
1. **主線 = CT 経路** (= 1 秒粒度の滑らかな sparkline、 瞬時電力リアルタイム)
2. **補助線 = B-route polling** (= 散発的、 累積補正 + B-route 直値の検算)
3. **救済点 = spec 028 backfill** (= さらに散発的、 B-route mismatch 救済)

3 系統が overlay で表示、 主線で「使い物になる Instantaneous Power」 を取得、 B-route は「電力会社一致値の検算」 として補助。

### Acceptance Scenarios

1. **Given** CT クランプを分電盤主幹に設置、 Shelly EM (= 例) を Wi-Fi 接続、 MQTT publish 設定、 **When** HA / telegraf 経由で prometheus に流入、 **Then** grafana panel-1 に 1 秒粒度の連続線が表示
2. **Given** B-route polling 沈黙期間 (= 15-25 分) 発生、 **When** その間 CT 経路は 1 秒粒度継続、 **Then** panel-1 主線 = 連続線、 B-route 補助線 = 沈黙、 spec 028 backfill = 0 件で「主線で正常 visualization 維持」
3. **Given** CT 機器電源/Wi-Fi 障害、 **When** 主線消失、 **Then** B-route 補助線 + spec 028 backfill で「最低限の visualization」 維持 (= redundancy)
4. **Given** HA Energy dashboard、 **When** 累積 kWh は B-route (= spec 018 tier4 0xEA/0xEB)、 瞬時 W は CT、 **Then** 両系統で精度/粒度別役割分担

## Requirements

### Functional Requirements

- **FR-001**: bridge コード変更なし (= spec 028/029 + spec 020 v1.5 既存挙動完全保護)
- **FR-002**: CT 機器選定 (= user 側、 spec 031 設計指針に従う、 hardware 設置工事 user 責任)
- **FR-003**: CT 機器の MQTT publish 設定 (= user 側、 機器ごとの公式手順)
- **FR-004**: telegraf 経由 or HA 経由で prometheus に CT metric 流入 (= 別 spec で実装、 設計指針のみ)
- **FR-005**: grafana panel-1 ハイブリッド表示 (= 別 spec、 spec 031 deploy 後)

### Key Entities

- CT 機器 (= user 別作業で選定/設置)
- MQTT topic: `home/power/instant_w` (= 例、 機器依存)
- prometheus metric: `home_power_instant_w` (= 例)
- panel-1 refId=E (= 別 spec で追加)

## Success Criteria

- **SC-001**: tako 合議結果 を memory に記録 (= 「architectural cap learning」、 後日 hardware 選定 reference)
- **SC-002**: spec 031 spec.md 自体が「Design only」 として残る、 hardware 設置完了後に Status 更新
- **SC-003**: user 側 hardware 選定/設置完了後、 別 spec (= spec 032 等) で MQTT 連携実装 + panel-1 進化
- **SC-004**: 既存 spec 028/029 panel/挙動 すべて維持 (= breaking change なし)

## Assumptions

- B-route スマートメーター単独で 1 分粒度連続瞬時電力は **architectural cap** (= tako consensus、 5 agent 一致)
- CT クランプ式 IoT 電力モニタは技術的に成熟 (= 国内/海外で複数選択肢)
- 分電盤への CT 設置は **電気工事士不要** (= クランプ式は配線切断なし、 ただし主幹/個別回路の判別に知識要)
- HA / telegraf / prometheus のいずれかで MQTT → metric 変換可能
- 関連 spec: [[spec-028-instantaneous-power-recovery]] (= B-route backfill 救済、 補助役)、 [[spec-029-cumulative-energy-recovery-backfill]] (= 累積系 backfill、 補助役)、 [[spec-018-cumulative-energy-tier]] (= B-route 累積系 tier4、 検針値の真値)
- 関連 memory: [[reference-tako-instantaneous-power-architectural-cap]] (= 本 spec の起点、 5 agent 合議結果)、 [[feedback-cycle-counter-reconnect-tier4]] (= B-route の構造的制約)

## Future Work

- **spec 027 v3 (= 独立)**: B-route polling 軽量化 (= 60s → 90-120s、 tier rotation 見直し)、 spec 031 と独立進行可
- **spec 032 (= 仮)**: CT 機器 MQTT 連携実装 + panel-1 ハイブリッド進化、 spec 031 hardware 設置完了後着手
- **spec 028 v2 候補**: cycle counter shift で reconnect 直後を tier1 化、 spec 031 後でも残価値あり (= 既存 backfill 救済率向上)
