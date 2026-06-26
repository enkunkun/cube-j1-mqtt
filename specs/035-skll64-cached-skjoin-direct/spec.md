# Feature Specification: SKLL64 Cached + SKJOIN 直行 (= spec 011 G: SKSCAN 完全 skip で reconnect 35s → 5s)

**Feature Branch**: `035-skll64-cached-skjoin-direct`
**Created**: 2026-06-27
**Status**: Draft
**Input**: spec 034 Phase 2 (= 2026-06-27 00:37) で「単 ch active scan ではメーター beacon hit せず」 真因判明。 [[spec-034-skscan-channel-mask-cache]] の dig 過程で「方針 B = SKSCAN skip + SKJOIN 直行」 が subagent 調査で「risk」 評価された経緯あるが、 spec 034 v1 deploy で「短 ch active scan アプローチ自体が機能しない (= cache hit rate 0%)」 確定 → 方針 B を現実的最終手段として再評価。

## Background

cube-j1-mqtt の reconnect path (= spec 027/032 で threshold 6 短縮済):
- SKRESET → SKVER/SKSETPWD/SKSETRBID → **SKSCAN (= 主要 17-32s コスト)** → SKJOIN → 計 35-40s

spec 034 v1 で **SKSCAN を単 ch mask scan で短縮する仮説が失敗** ([[spec-034-skscan-channel-mask-cache]] v1 Phase 2 Finding)。 真因 = メーター beacon timing 不定 → 単 ch では確率低い。

別アプローチ: **メーター IPv6 / MAC / pan_id / channel は join 後固定**、 reboot しない限り変化なし。 既知情報を cache して **SKSCAN 完全 skip + SKJOIN 直行** すれば、 reconnect 全体が 5s 以下になる:

| Step | spec 034 (= disable) | spec 035 (= cached SKJOIN) |
|---|---|---|
| SKRESET | 1s | 1s |
| SKVER/SKSETPWD/SKSETRBID | 3s | 3s |
| SKSREG S2/S3 (= channel/pan_id 再設定) | 含む | 必須 (= cached 値で) |
| SKSCAN | **17-32s** | **0s (= 完全 skip)** |
| SKJOIN | 1s | 1s |
| **合計** | 21-37s | **~5s** |

= **6-7 倍短縮**、 spec 034 想定 (= 8 倍短縮) と同等の改善幅、 ただし仕組みが根本的に違う。

## Risk Analysis (= spec 034 dig 時に subagent が「risk」 評価した内容の再評価)

### Risk 1: メーター reboot 後 IPv6 / pan_id 変化

- **想定頻度**: 月 1 回以下 (= メーター主電源切れる時のみ、 cube-j1 reboot とは独立)
- **検出**: SKJOIN 失敗 (= EVENT 24 / timeout 90s) で確実に detect
- **fallback**: SKJOIN 失敗 → 既存 spec 017 EVENT 24/29 trigger → 次 reconnect で SKSCAN 全 scan path (= spec 034 v1.1 hotfix が必要) で復旧
- **期待損失**: メーター reboot 直後 1 回だけ 90s + SKRESET retry + SKSCAN 17-32s = 約 110-130s overhead、 月 1 回頻度なら年間 20 分以下、 sparseness への影響無視可

### Risk 2: メーター chan 変動 (= ARIB STD-T108 reallocation)

- **想定頻度**: 不明 (= 環境ノイズ依存、 lab メーター実績 = 過去 6 ヶ月 ch57 固定)
- **検出**: SKJOIN 失敗で detect
- **fallback**: 同 Risk 1
- **対策**: cache 値が n 回連続 SKJOIN 失敗で invalidate → 次回必ず SKSCAN fallback

### Risk 3: cache invalidation logic 漏れ

- **対策**: cache 値は **DiagState 経由** (= 既存 pan_channel/pan_id/mac/ipv6 attribute) で in-memory のみ、 bridge restart で消える (= 初回 join 全 scan 必須)。 disk 永続化なし
- bridge restart 時は spec 011 系列の旧挙動 = 初回必ず全 scan、 spec 035 は **同 bridge process 内の reconnect だけ** 短縮 (= 月 1 回の bridge restart 時 1 回だけ 32s 全 scan は許容)

### Risk 4: SKSREG S2 (channel) / S3 (pan_id) 設定漏れ

- **既存 wisun_connect** L2775-2776 で SKSREG S2/S3 設定済 = この path を初回 join と reconnect で **共通化** で確実
- spec 035 は SKSCAN 完全 skip + 既存 SKSREG S2/S3 path に cache 値を渡す = 副作用ゼロ

## Scope

### A. DiagState 拡張: cache attribute 既存利用 + 1 件追加

- `diag_state.pan_channel` (= 既存、 spec 011 系列で確立)
- `diag_state.pan_id` (= 既存)
- `diag_state.mac` (= 既存)
- `diag_state.ipv6` (= 既存)
- `diag_state.consecutive_skjoin_failures` (= **新**、 cache invalidation 判定用、 0 初期化、 SKJOIN 成功で 0 リセット、 失敗で += 1)

### B. `wisun_connect` 拡張 (= spec 034 サインを置換)

- `wisun_connect(fd, br_id, br_pwd, prefer_cached_join=False, cached_invalidate_threshold=2, diag_state=None)`:
  - `prefer_cached_join=True` + `diag_state.pan_channel`/`mac`/`ipv6` 全 cache 揃いなら → **SKSCAN skip + SKSREG S2/S3 + SKJOIN cached_ipv6** 直行
  - SKJOIN 失敗 (= EVENT 24 / timeout) なら `diag_state.consecutive_skjoin_failures += 1`、 threshold (= 2) 超えたら cache invalidate (= pan_channel = None) → 次回必ず全 scan
  - SKJOIN 成功で `consecutive_skjoin_failures = 0` リセット
- 初回 join (= default False) は既存 SKSCAN 全 scan path

### C. main loop reconnect path (= line 4360)

- `cfg.get("wisun_reconnect_cached_skjoin_enabled", True)` で gating
- `cfg.get("wisun_reconnect_cached_skjoin_invalidate_threshold", 2)` で fallback 閾値
- spec 034 (= `wisun_reconnect_channel_mask_enabled`) は disable 推奨 (= 既に explicit override 済)

### D. DiagState counter 2 件追加

- `wisun_reconnect_cached_skjoin_total` (= cache 直行成功)
- `wisun_reconnect_cached_skjoin_fallback_total` (= cache 失敗で SKSCAN fallback)

## Non-Scope

- メーター reboot 検出 (= SKJOIN 失敗で間接的、 直接検出は外)
- channel 変動の予測 (= reactive 対応のみ)
- spec 034 コード除去 (= disable で十分、 削除は後続 cleanup spec で)

## Success Criteria

- **SC-001**: pure helper 単体テスト (= `decide_cached_skjoin_eligible(diag_state)` 等)
- **SC-002**: apply_defaults 新 2 keys default 確認
- **SC-003**: DiagState 拡張 (= counter 2 + consecutive_skjoin_failures)
- **SC-004**: 既存テスト全 pass (= 476 → ~485 件)
- **SC-005**: 実機 deploy 1h 観察:
  - `wisun_reconnect_cached_skjoin_total > 0` (= cache 直行発火)
  - cache 成功率 (= cached / (cached + fallback)) >= 95%
  - reconnect 所要時間 17-32s → 5-7s (= bridge log で SKRESET → EVENT 25 時刻差)
  - panel-1 sparseness 顕著改善 (= cluster 数 1h で 7-8 → 10-12 個期待)
- **SC-006**: 1 週間 long-term で cache invalidate 月 1 回以下

## Assumptions

- メーター IPv6 / pan_id / channel / MAC は bridge process 生存中 cache 可 (= reboot 後は invalidate される自然な protocol)
- SKJOIN 失敗 = メーター reboot 検出の唯一手段 (= timeout 90s で確実、 ただし overhead)
- 関連 spec: [[spec-034-skscan-channel-mask-cache]] (= disable、 同問題への別アプローチ)、 [[spec-017-wisun-rejoin-backoff]] (= EVENT 24/29 trigger 互換)、 [[spec-027-base-reconnect-threshold]] / [[spec-032-aggressive-polling-defaults]] (= reconnect 頻度制御互換)
- 関連 memory: [[reference-tako-instantaneous-power-architectural-cap]] (= software 改善余地、 spec 034 失敗で spec 035 が方針 B として残る唯一手段)
