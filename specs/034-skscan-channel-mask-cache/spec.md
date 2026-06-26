# Feature Specification: SKSCAN Channel Mask Cache (= spec 011 F: reconnect 時間 32s→4s で sparseness さらに改善)

**Feature Branch**: `034-skscan-channel-mask-cache`
**Created**: 2026-06-26
**Status**: Draft
**Input**: 2026-06-26 spec 033 deploy 後 panel-1 観察で user 指摘「リカバー一部増えてるが空白多い」、 tako 合議で出た spec 011 F 候補 (= SKSCAN 固定で reconnect 時間短縮) を subagent 調査 (= file:line + BP35CX 仕様 + 方針 3 案比較) で実装方針 C (= ハイブリッド: 初回全 scan、 reconnect は単 ch mask scan + fallback) 確定。

## Background

cube-j1-mqtt の reconnect path 内訳 (= spec 027 → spec 032 で threshold 30→6 短縮済):
- SKRESET (= 即時)
- SKVER / SKSETPWD / SKSETRBID (= 各 1-2s)
- **SKSCAN (= 32 秒 / `SKSCAN 2 FFFFFFFF 6 0`、 全 channel ch33-60)** ← 主要コスト
- SKJOIN (= 数秒)
- 計 35-40 秒 / reconnect

reconnect 頻度: spec 032 で threshold=6 (= 6 cycle = 6 分連続 timeout で発火)、 1h で 4-5 回観察 = 1h で **2-3 分が SKSCAN 待ち** (= polling sparse の主因の一つ)。

メーターは初回 join 時 pan_channel (= 現在 57) 固定、 再 scan 不要のはず。 BP35CX `SKSCAN 2 <mask> <duration> <side>` の mask 引数で **特定 channel のみ scan 可** (= ch57 のみなら `0x01000000`、 duration 3 で ~4s)。

完成後の見込み: SKSCAN 32s → 4s = **8 倍短縮**、 reconnect 全体 35s → 7s、 1h 内 reconnect 待ち時間 2-3 分 → 30-40 秒に短縮 → polling sparseness 緩和。

## Scope

### A. 新 pure helper `channel_to_mask(ch)` (= BP35CX channel 番号 → bitmap 変換)

```python
def channel_to_mask(ch):
    """spec 034: BP35CX channel 番号 (= ch33-60) を SKSCAN 2 用 32-bit
    channel mask に変換。 ch33 = bit 0, ch60 = bit 27.

    例: channel_to_mask(57) = 0x01000000 (= bit 24)
        channel_to_mask(39) = 0x00000040 (= bit 6)
    """
    return 1 << (int(ch) - 33)
```

### B. `wisun_connect()` に `prefer_known_channel` parameter 追加

- default `False` (= 既存挙動互換、 初回 join は全 scan で安全)
- reconnect path で `True` 渡し:
  - `diag_state.pan_channel` (= 既知 ch) があれば → `channel_to_mask(pan_channel)` で **単 ch mask + duration 3** で `SKSCAN 2 <mask> 3 0` 発行 (= ~4s)
  - SKSCAN 失敗 (= PAN 見つからず) なら **fallback: 全 scan** (`SKSCAN 2 FFFFFFFF 6 0`) で安全 retry
  - `diag_state.pan_channel` が未設定 (= 初回 reconnect 前) なら直接 fallback

### C. config kill switch

- `wisun_reconnect_channel_mask_enabled` (default `True`): false で旧挙動 (= 毎回全 scan、 spec 011 系列の挙動互換)
- `wisun_reconnect_channel_mask_fallback_duration` (default `6`): fallback 全 scan の duration、 spec 010 既存 default 維持

### D. DiagState 拡張

- `wisun_reconnect_short_scan_total` (counter): 単 ch mask scan で成功した件数
- `wisun_reconnect_fallback_full_scan_total` (counter): fallback 全 scan に落ちた件数
- 両 metric で「短縮効果率」 (= short / (short + fallback)) を grafana 観察可

### E. 既存挙動完全保護

- spec 027/032 reconnect threshold (= 6) 不変
- spec 011 C tier rotation / spec 033 batch 不変
- 初回 join (= 起動直後) は **必ず全 scan** (= safety、 init 時は pan_channel 未知)
- spec 017 EVENT 24/29 trigger (= rejoin 自動化) と互換

## Non-Scope

- SKSCAN を完全 skip + 直接 SKJOIN (= 方針 B、 subagent 調査で「安全性 risk」 と判定、 メーター reboot 時 hang)
- BP35CX channel scan の duration 細かい調整 (= spec 010 で 4→6 にした経緯、 spec 034 は mask で短縮、 duration 3 採用)
- メーター chan 変動への対応 (= 副次的に fallback で吸収、 spec 034 主スコープ外)
- spec 028/029/032/033 既存 backfill / batch ロジック変更

## User Scenarios

### Primary User Story

ユーザは spec 034 deploy 後の grafana panel-1 で:
1. **panel-1 sparseness 緩和** (= reconnect 後の復活が 30+ 秒短縮、 polling cluster の空白が縮小)
2. **panel-41 Wi-SUN Reconnects** rate 不変 (= reconnect 頻度は spec 032 と同じ、 ただし各 reconnect 所要時間短縮)
3. **新 panel-83 SKSCAN Cache Hit Rate** (= 後続 spec で追加候補): `wisun_reconnect_short_scan_total / (short + fallback)` の比率で「ch mask cache 効果率」 観察、 通常 95%+ 期待

### Acceptance Scenarios

1. **Given** `diag_state.pan_channel = 57` (= 過去 join 経験あり)、 **When** reconnect 発火、 **Then** `SKSCAN 2 01000000 3 0` (= ch57 only、 ~4s) 発行、 成功で `wisun_reconnect_short_scan_total += 1`
2. **Given** ch57 mask scan で PAN 見つからず (= メーター reboot / chan 変動)、 **When** fallback、 **Then** `SKSCAN 2 FFFFFFFF 6 0` (= 全 scan ~32s) 発行、 `wisun_reconnect_fallback_full_scan_total += 1`
3. **Given** 初回 join (= `diag_state.pan_channel` 未設定)、 **When** `wisun_connect(prefer_known_channel=True)`、 **Then** 全 scan path にそのまま fallback (= safety)
4. **Given** `wisun_reconnect_channel_mask_enabled=False`、 **When** reconnect、 **Then** 旧挙動 (= 毎回全 scan) で動作互換
5. **Given** `channel_to_mask(57)`、 **When** mask 計算、 **Then** `0x01000000` (= 16777216)

## Requirements

### Functional Requirements

- **FR-001**: `channel_to_mask(ch)` pure helper を `skscan` 関連 helper 群の隣 (= 約 line 2636 周辺) に追加
- **FR-002**: `wisun_connect()` (= line ~3998) に `prefer_known_channel=False` 引数追加、 既存 caller は default で互換
- **FR-003**: reconnect path (= line ~4360) で `wisun_connect(prefer_known_channel=True)` 呼出
- **FR-004**: prefer_known_channel=True + diag_state.pan_channel 有りなら短 mask scan、 失敗時 fallback 全 scan
- **FR-005**: `DiagState.wisun_reconnect_short_scan_total` / `wisun_reconnect_fallback_full_scan_total` counter 追加 + `_DIAG_SNAPSHOT_KEYS` + snapshot raw dict + DIAG_SENSOR_DEFS 登録 ([[feedback-diag-sensor-defs-publish]])
- **FR-006**: `apply_defaults` で `wisun_reconnect_channel_mask_enabled=True` + `wisun_reconnect_channel_mask_fallback_duration=6`
- **FR-007**: 既存 spec 020/027/028/029/032/033 完全保護

### Key Entities

- `channel_to_mask` (= 新 pure helper)、 `wisun_connect` (= 既存拡張)、 `DiagState.wisun_reconnect_short_scan_total` / `..._fallback_full_scan_total` (= 新 counter 2 件)

## Success Criteria

- **SC-001**: 単体テスト: `channel_to_mask(33)=0x1` / `channel_to_mask(57)=0x01000000` / `channel_to_mask(60)=0x08000000` 計 3-4 件
- **SC-002**: 単体テスト: `apply_defaults` で新 config 2 keys default 確認
- **SC-003**: 単体テスト: DiagState 新 counter 2 件 baseline + snapshot
- **SC-004**: 既存テスト全件 pass (= 463 → ~470 件)
- **SC-005**: 実機 deploy 後 1h 観察:
  - `wisun_reconnect_short_scan_total > 0` (= 短 mask scan 発火)
  - `wisun_reconnect_short_scan_total / (short + fallback) >= 90%` (= cache hit rate 高い、 メーター chan 安定)
  - reconnect 後 wisun_joined までの所要時間 32s → 5-10s に短縮 (= bridge log で観察)
  - panel-1 sparseness 改善 (= cluster 数 1h で 5 → 7-8 個期待)
- **SC-006**: 1 週間 long-term で fallback 率 5% 以下 (= メーター chan 変動稀)

## Assumptions

- メーター pan_channel は通常 join 後固定 (= 月単位で変動なし、 reboot 時のみ稀に変化)
- BP35CX `SKSCAN 2 <mask> 3 0` (= duration 3 = 4s) で単 ch scan が安定動作 (= reference 仕様確認済)
- fallback (= 全 scan) は失敗時 1 度のみ実行、 fallback も失敗なら既存 SKRESET 再 retry path (= spec 017 EVENT 24/29) に委ねる
- 関連 spec: [[spec-010-eedscan]] (= SCAN_DURATION_BASE=6 元、 spec 034 は duration 3 採用)、 [[spec-017-wisun-rejoin-backoff]] (= EVENT 24/29 rejoin trigger 互換)、 [[spec-027-base-reconnect-threshold]] / [[spec-032-aggressive-polling-defaults]] (= reconnect 頻度制御、 spec 034 は時間短縮)、 [[spec-033-all-cycle-tier1-batch]] (= 並列改善、 batch + 短縮で相乗効果)
- 関連 memory: [[reference-tako-instantaneous-power-architectural-cap]] (= software 改善余地の最終形態として spec 034 が位置付け、 spec 011 F = SKSCAN 固定)
