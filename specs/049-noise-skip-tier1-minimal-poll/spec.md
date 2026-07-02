# Feature Specification: noise skip cycle の 0xE7 単独 minimal poll 降格

**Feature Branch**: `049-noise-skip-tier1-minimal-poll`
**Created**: 2026-07-03
**Status**: Draft
**Input**: spec 048 close 後のユーザー発言「もう少し欠損を減らしたい」。残る software 改善余地 = noise adaptive skip 帯 (= 1.5〜5.9/h、時間帯依存バースト)。tako 合議 2026-07-02 案 3 (= noise skip からの 0xE7 免除) の具体化。

## Background

- spec 012 の noise adaptive skip は「EEDSCAN で PAN ch が noisy (>= threshold 100) なら normal poll を最大 3 連続まで完全 skip」する。目的は **noise 期間中の受信失敗クラスタ → wisun_reconnect storm の防止**
- spec 048 後の欠損収支: 60/h − 26 (live) − 30 (timeout、PANA 由来 touch 不能) − ~4 (noise skip + reconnect downtime) ≈ 0。noise skip 帯が software 最後の一手
- user 方針: 瞬時電力 (0xE7) 最優先、他 EPC の粒度 sacrifice OK ([[user-instantaneous-power-priority]])

## 設計判断 (= AskUserQuestion 2026-07-03 timeout、推奨案で確定)

1. **noisy cycle は完全 skip の代わりに 0xE7 単独 (OPC=1) の minimal poll を送信** — noise 中の airtime / 衝突確率を最小化しつつ最優先軸だけ回収。E8 / 累積系はその cycle 犠牲 (方針と整合)
2. **minimal poll の timeout は force reconnect の連続 timeout counter に数えない** — spec 012 の storm 防止目的を維持。本物のメーター死は spec 041 watchdog が拾う

## User Scenarios & Testing

### Acceptance Scenarios

1. **Given** EEDSCAN noisy + streak < 3、**When** normal cycle 開始、**Then** skip せず 0xE7 単独 frame を送信し、成功したら live `power_watts` を publish する
2. **Given** minimal poll が timeout、**Then** `noise_minimal_poll_timeout_total` のみ inc し、`erxudp_timeouts_total` / `consecutive_erxudp_timeouts` / force reconnect 判定には影響しない
3. **Given** noisy が 3 cycle 続いた、**Then** 4 cycle 目は従来どおり fail-safe の通常 poll (= streak 上限の挙動不変)
4. **Given** `noise_skip_tier1_minimal_poll_enabled=false`、**Then** spec 012 の完全 skip 挙動に戻る (= kill switch)

## Requirements

### Functional Requirements

- **FR-001**: noisy 判定 (= `is_noisy` && streak < max) の normal cycle で、config `noise_skip_tier1_minimal_poll_enabled` (= default true) が有効なら sleep+continue の代わりに **`NOISE_MINIMAL_EPCS = [0xE7]` (OPC=1) で送信**する。streak increment と `noise_adaptive_skips_total` (= 既存 counter、意味は「reduced cycle」に変わる) は従来どおり
- **FR-002**: minimal poll cycle は `normal_cycle_count` を進めない (= tier rotation を乱さない、既存 skip と同じ)
- **FR-003**: minimal poll の成功は通常経路で live publish + `on_poll_success`、加えて `noise_minimal_poll_success_total` を inc。試行は `noise_minimal_polls_total` を inc
- **FR-004**: minimal poll の timeout は `noise_minimal_poll_timeout_total` のみ inc。`on_erxudp_timeout` (= erxudp_timeouts_total / consecutive counter) と force reconnect 判定は呼ばない
- **FR-005**: 分岐判定を pure helper `decide_noise_cycle_action(noisy, streak, max_consecutive, minimal_enabled)` → `"normal" | "skip" | "minimal"` に切り出す (= host テスト可能に)
- **FR-006**: 新 counter 3 本を `DIAG_SENSOR_DEFS` + snapshot (= zero-omit しない) + compose telegraf topics に登録

### Non-Functional / 制約

- **NFR-001**: Python 2.7 stdlib のみ。deploy 前にユーザー確認 (= polling 挙動変更)
- **NFR-002**: ARIB duty 比への影響は無視可能 (= 完全 skip → OPC=1 送信 1 回/分 の追加、burst ではない)

### Success Criteria (= deploy 後 24h 窓、noise はバースト依存なので 6h では不足)

- **SC-1**: `noise_minimal_polls_total` ≈ 従来の skip rate (= 1.5〜6/h)、うち success ≥ 50% (= noise 中でも OPC=1 なら通る仮説の検証)
- **SC-2**: `wisun_reconnects_total` が baseline 6.7/h から悪化しない (= spec 012 目的の維持)
- **SC-3**: live `power_watts` レートが noise バースト時間帯でも 26/h 帯を維持 (= skip 由来の谷が消える)

## 関連参照

- spec 012 (= noise adaptive skip 導入元)、spec 048 (= 直前の取得率改善、observation-2026-07-03.md)
- tako 合議 2026-07-02 案 3、memory `user-instantaneous-power-priority`
