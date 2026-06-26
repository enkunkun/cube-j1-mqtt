# Feature Specification: Aggressive Polling Defaults (= broute-mqtt 並み短 timeout + 早期 reconnect + retry 増)

**Feature Branch**: `032-aggressive-polling-defaults`
**Created**: 2026-06-26
**Status**: **Deployed 2026-06-26** — cube `42d00805` で deploy 完了 (= compose 不要、 bridge のみ修正)。 PoC override → spec 化 → default 化 → device override 削除 で純粋計測体制成立。 1h 実機 verify 結果:
- ✅ polling success 2 分以内更新継続 (= 沈黙ループ脱出)
- ✅ wisun_reconnects 4/h ≤ 5/h (= threshold 6 想定通り、 暴走無し)
- ✅ 救済率 100% (= 6/6)
- ✅ 沈黙時間 17-25 分 → **5-8 分** (= 3 倍短縮)
- ✅ cumulative backfill 2 件発火 (= spec 029 e2e wire 継続)
- ⚠️ timeouts 29/h で目標 ≤ 20/h **部分未達** (= PoC 前 49/h から 40% 改善は達成)
- ⚠️ 1h で 4 cluster (= success rate 約 30%)、 sparseness 完全解消ではない (= architectural cap が software 改善で 1/3 緩和できる、 ただし 1 分粒度連続瞬時電力には依然不足)

副次成果: TDD で「spec 011/027 hidden default test」 4 件 catch + delete (= spec 027/011 既存 file + tid_lag 内に散在)、 dig 漏れを TDD で補完。 副次: `bridge_version=None` 問題 (= spec 030 候補) は `embed_git_hash.sh` 経由で自然解消、 spec 030 候補は削除可。

残課題: spec 011 E (= tier batch OPC=4) と spec 011 F (= SKSCAN 固定) で追加改善可能性、 別セッションで着手。 完全解消には spec 031 (= CT hardware) も依然有効選択肢。
**Input**: 2026-06-26 PoC で「短 timeout (= 30→6s) + 早期 force_reconnect (= 30→6) + intra-cycle retry (= 0→3)」 を device explicit override で実証、 33 分で timeouts/h 1/3 改善 + 沈黙ループ脱出 + backfill 発火率 5.7 倍を観測。 default 化で恒久反映。

## Background

cube-j1-mqtt は他社 OSS 実装 (= broute-mqtt 等) と比べて polling 戦略が **保守的すぎ**:
- `erxudp_timeout_sec=30s` (= broute-mqtt 5s の 6 倍長い)
- `erxudp_intra_cycle_retries=0` (= broute-mqtt retry 3 / 5s 間隔)
- `erxudp_timeout_force_reconnect_threshold=30` (= 30 分沈黙してから SKRESET、 broute-mqtt は数分復帰)

結果として「reconnect → 3-6 分 polling 成功 → 15-25 分連続消息断」 のループに陥る。 spec 028 v1.1 hotfix (= qos=0 削除) で reconnect 強制誘発バグ解消後も pattern 改善小、 真因は **保守的設定が逆に沈黙ループを長期化** していたこと。

2026-06-26 tako 5 agent 合議 (= 当初「architectural cap」 結論) は user 反論「Nature Remo E が成立してる」 で再評価、 subagent 調査で他社 OSS 4 実装 (= hsakoh/broute-mqtt, yufeikang/b-route-meter, seotaro/smart-power-meter, teldren/WiSUN-SmartMeter) と詳細対比、 cube-j1-mqtt の 4 軸 suboptimal を特定。 device override PoC で効果実証済。

完成後の運用見込み: panel-1 Instantaneous Power の sparseness が大幅改善 (= backfill 発火率 5.7 倍 → 黄色 dot plot 密度上昇)、 spec 031 (= CT クランプ hardware 追加) の必要性低下。

## Scope

### A. `apply_defaults` の 3 default 変更

```python
out.setdefault("erxudp_timeout_sec", 6)  # 30 → 6 (broute-mqtt 並み)
out.setdefault("erxudp_intra_cycle_retries", 3)  # 0 → 3 (短期集中 retry)
out.setdefault("erxudp_timeout_force_reconnect_threshold", 6)  # 30 → 6 (= 6 cycle × 60s = 6 分死で reconnect)
```

### B. main loop / read_erxudp の挙動確認

既存ロジック互換性確認 = config 値だけ変更で動作するはず:
- `erxudp_timeout_sec` は `read_erxudp(fd, timeout=...)` 引数経由で渡る
- `erxudp_intra_cycle_retries` は main loop の retry ループに渡る
- `erxudp_timeout_force_reconnect_threshold` は `consecutive_erxudp_timeouts >= threshold` 判定で参照

これら全て **既存実装で参照済 config**、 新規コード不要。

### C. spec 027 / spec 023 / spec 025 既存挙動との整合性

- spec 023 (= burst mode 中 erxudp_timeout 30→5s): burst 中は別 default、 spec 011 D で base 30→6 にしても burst 専用 default 維持
- spec 025 (= burst 中 force_reconnect threshold 5→30): 同上、 burst 専用 default 維持
- spec 027 (= base force_reconnect threshold 5→30): **base default を 30→6 に巻き戻し**、 spec 027 v2 の代替案として位置付け (= 30 分死より 6 分 reconnect を選ぶ)
- spec 020 v1.5 + spec 028/029 (= TID 救済 + backfill): 設定変更で挙動変わらず、 既存挙動完全保護

### D. PoC 結果記録 + Step 0 確認

Step 0 として device config.json で当該 3 keys が override されていないか確認:
- PoC で適用済の override (= explicit 値設定) を **削除** してから default 化 deploy
- 削除しないと「default 6 でも config explicit 6 で意味的に同じ」 だが、 spec 011 D 効果の純粋計測ができない

## Non-Scope

- tier batch (= OPC=4 で tier1+tier2 1 frame まとめ取得): **別 spec 011 E に分離** (= 既存 build_el_get / decide_epc_tier / main loop の構造変更要、 spec 011 D とは独立進行可)
- SKSCAN チャネル固定 / 適応 (= 別調査余地、 spec 011 F 候補)
- polling 周期 60→20s 短縮 (= spec 011 D 効果観測後判断、 別 spec 候補)
- spec 031 (= CT clamp hardware) は **保留**、 spec 011 D 効果次第で要不要再判定

## User Scenarios

### Primary User Story

ユーザは spec 011 D deploy 後の grafana で:
1. **panel-1 Instantaneous Power** の通常 series (= 緑) が **密度向上** (= 沈黙期間短縮)
2. **黄色 dot (= spec 028 backfill)** が今までの **5-6 倍密度** で plot
3. **wisun_reconnects/h** は微増 (= threshold 30→6 で頻発化、 ただし polling 健全化の trade-off)
4. **panel-43 ERXUDP Timeouts** rate /5m が **約 1/3** に改善

### Acceptance Scenarios

1. **Given** spec 011 D deploy + device config.json override 削除、 **When** bridge restart、 **Then** `/api/diag` で 3 default が新値 (= 6/3/6) になっていることを確認
2. **Given** 1h 以上運用、 **When** metrics 取得、 **Then** `rate(erxudp_timeouts_total[1h])` が PoC 前 (= 49/h) より大幅減 (= 20/h 以下目標)
3. **Given** burst mode 起動 (= spec 022 admin UI で ON)、 **When** burst 中の polling、 **Then** spec 023 の burst 専用 default (= 5s) が優先、 base default 6s に干渉なし
4. **Given** `erxudp_timeout_sec=6` device explicit override、 **When** apply_defaults、 **Then** override 尊重 (= setdefault 標準挙動、 kill switch として残す)

## Requirements

### Functional Requirements

- **FR-001**: `apply_defaults` の 3 default 変更 (= timeout 30→6 / retries 0→3 / threshold 30→6)
- **FR-002**: 既存 spec 023 / spec 025 / spec 027 既存実装互換性保護 (= burst 専用 default 不変、 base default のみ変更)
- **FR-003**: kill switch 維持 (= device explicit override で旧値復帰可能、 setdefault 標準挙動)
- **FR-004**: Step 0 で device config.json の 3 keys explicit override 確認 + PoC override 削除
- **FR-005**: spec 020 v1.5 + spec 028 v1.1 + spec 029 既存挙動完全保護 (= config 値のみ変更、 ロジック不変)

### Key Entities

- `apply_defaults` (= `production_tool/mqtt_bridge.py` line 122 / 146 / 147)

## Success Criteria

- **SC-001**: 単体テスト: `apply_defaults` の 3 default 値変更確認 (= 各 default value test、 計 3 件)
- **SC-002**: 単体テスト: explicit override が setdefault で尊重される確認 (= 1 件)
- **SC-003**: 既存テスト全件 pass (= ~458 件 → ~462 件、 spec 028 v1.1 + spec 029 互換)
- **SC-004**: 実機 deploy 後 1h 観察:
  - `rate(erxudp_timeouts_total[1h])` ≤ 20/h (= PoC 前 49/h から大幅改善)
  - `last_poll_success_ts` が 5 分以内に更新継続 (= 沈黙ループ脱出維持)
  - `wisun_reconnects/h` ≤ 5/h (= threshold 6 でも 5 件/h 以下、 過剰 reconnect 防止)
- **SC-005**: 1 週間運用で backfill 発火率上昇確認 (= panel-1 黄色 dot 密度 + panel-80/82 rate /5m)

## Assumptions

- broute-mqtt と同じ「短 timeout + 早期 reconnect + retry 増」 設定で cube-j1-mqtt 環境でも安定動作 (= PoC 33 分で実証済)
- threshold 30→6 で reconnect 頻発化のリスクは「polling 健全化で reconnect 必要性減少」 で相殺、 PoC で wisun_reconnects/h ≒ 同等観測
- tier rotation 排他 1 tier 送信 (= spec 011 C) は spec 011 D とは独立、 batch 化は別 spec 011 E
- 関連 spec: [[spec-011-c-tier-rotation]] (= 既存 polling pattern)、 [[spec-027-base-reconnect-threshold]] (= 30→6 巻き戻し、 spec 027 v2 候補と整合)、 [[spec-023-burst-erxudp-timeout]] / [[spec-025-burst-reconnect-threshold]] (= burst 専用 default 維持)、 [[spec-028-v1.1-hotfix]] (= qos=0 削除、 PoC 効果実証の前提)、 [[spec-031-ct-clamp-instantaneous-power]] (= 保留、 spec 011 D 効果次第)
- 関連 memory: [[reference-tako-instantaneous-power-architectural-cap]] (= tako 合議の reversed 結論、 reference 実装調査の起点)、 [[feedback-config-setdefault-override]] (= device config 事前確認の教訓)
