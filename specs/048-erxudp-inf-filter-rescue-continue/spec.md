# Feature Specification: read_erxudp INF filter + rescue 後継続待ち (= H2/H3 解消)

**Feature Branch**: `048-erxudp-inf-filter-rescue-continue`
**Created**: 2026-07-02
**Status**: Draft
**Input**: spec 047 本判定 (= observation-2026-07-02.md、n=66)。H2 (= INF 混入) 59% 支配的 + H3 (= chain 自走) 41% 実在、H1 (= TID=0 即時応答の誤分類) 棄却。read_erxudp の 1 箇所の構造変更で両方を解消する。

## Background (= spec 047 観測で確定した構造)

1. **H2**: メーター自律通知 (= ESV 0x73 INF、TID=0、PANA 再認証 6.7/h 後のインスタンス通知) が read window 内に着信すると、`read_erxudp` の TID check が ESV filter より前段にあるため `lookup_latest` fallback が「遅延応答」と誤認 → INF payload を返して cycle を poll_success 扱いで潰す (= データゼロ、6.5 cycle/h 損失)。本来の応答は待たれずに捨てられる
2. **H3**: 正規 late Get_Res を rescue した瞬間に即 return するため、自 cycle の応答待ちを放棄 → 次 cycle で再び mismatch 救済、の 1 cycle 遅れ chain が自走 (= 4.3 cycle/h)。chain 中の実データは全部 backfill 系列行きで live `power_watts` はゼロ
3. `lookup_latest` fallback (= spec 020 v1.5) の対象だった got_tid=0 frame の正体は 100% INF と判明 → fallback 自体が不要

## User Scenarios & Testing

### Primary User Story

bridge 運用者として、INF に潰されていた cycle と chain に食われていた cycle を「本来の応答を待つ」cycle に戻し、live 瞬時電力取得率を 12.5/h → 19〜22/h 帯に引き上げたい。data 誠実性は維持する (= rescued 実データは従来どおり過去時刻 backfill、live への時刻偽装はしない)。

### Acceptance Scenarios

1. **Given** read window 中に INF frame (= ESV 0x73) が着信、**When** read_erxudp が処理、**Then** rescue せず `erxudp_inf_ignored_total` を inc して読み飛ばし、deadline まで expected TID を待ち続ける
2. **Given** INF の後に expected TID の正規応答が着信、**When** 同一 read window 内、**Then** 正規応答が live として返る (= 現行では INF が cycle を潰していたケース)
3. **Given** ring hit する late Get_Res frame が着信、**When** read_erxudp が処理、**Then** payload + send_ts を `diag_state.pending_rescued_frames` に stash して読み続け、expected TID が来たら live として返る
4. **Given** late frame だけ着信して expected TID は来ない、**When** deadline 到達、**Then** read_erxudp は None (= timeout) を返すが、stash された late frame は main loop で backfill publish される
5. **Given** got_tid=0 の Get_Res frame (= 観測上存在しない population)、**When** ring lookup miss、**Then** `lookup_latest` fallback は撤去済みなので spec 014 discard path に落ちる (= tid_mismatch counter で観測継続)

## Requirements

### Functional Requirements

- **FR-001**: `read_erxudp` で **ESV filter を TID check より前段に移動**する。EL frame (= `1081` prefix) のうち ESV が 0x72/0x52 以外 (= INF 0x73 含む) は `erxudp_inf_ignored_total` (新 counter) を inc して continue (= return しない、rescue もしない)
- **FR-002**: spec 020 v1.5 の **`lookup_latest` fallback を撤去**する (= got_tid=0 は正規 ring lookup miss として spec 014 discard path へ)
- **FR-003**: ring hit した late frame は **即 return せず** `diag_state.pending_rescued_frames` (= bounded deque、maxlen=8) に `(payload, send_ts)` を stash して**読み続ける**。expected TID 一致 frame が来たらそれを返す (= live)。spec 047 の `on_erxudp_rescued` 観測 counter は stash 時に従来どおり inc
- **FR-004**: main loop は read_erxudp の戻りに関わらず **毎 cycle `pending_rescued_frames` を drain** し、各 frame を parse → decode → 既存 backfill 3 系統 (= spec 028/029/046) + late publish (= CUMULATIVE_PUBLISH_KEYS) で publish する。既存の `_late_ts` 分岐 (= 単発 bus) は drain 処理に置換
- **FR-005**: rescue-only cycle (= AC-4) は poll_success ではなく **timeout として計上**する (= data 誠実性: 自 cycle の要求に応答が無かった事実を偽らない)。`erxudp_timeouts_total` は +4/h 程度上振れする見込みを spec に明記 (= 観測解釈の注意点)
- **FR-006**: 新 counter `erxudp_inf_ignored_total` を `DIAG_SENSOR_DEFS` + snapshot (= zero-omit しない) に登録し、compose telegraf topics に追加する
- **FR-007**: `last_recovered_send_ts` bus と main loop の旧 `_late_ts` 分岐は撤去する (= pending list に一本化)

### Non-Functional / 制約

- **NFR-001**: Python 2.7 stdlib のみ。host pytest で TDD
- **NFR-002**: deploy は実装完了後にユーザー確認を挟む (= polling 挙動が変わるため)。spec 047 counter は継続観測し、deploy 後に `esv_inf → 0 / inf_ignored ≈ 6.5/h` への移行で FR-001 を verify
- **NFR-003**: `pending_rescued_frames` は maxlen=8 で bound (= 毎 cycle drain されるので通常 0-1 件、reconnect 直後の burst 対策)

### Success Criteria (= deploy 後 6h 窓)

- **SC-1**: `erxudp_rescued_esv_inf_total` の増分 ≈ 0、`erxudp_inf_ignored_total` ≈ 6/h (= INF が rescue path から排除された)
- **SC-2**: live `count_over_time(power_watts[6h])` が 90 件以上 (= 15/h 以上、baseline 12.5/h から有意増。目標帯 19〜22/h)
- **SC-3**: `power_watts_recovered` の backfill が継続して機能 (= rescued 実データの取りこぼしなし)、rescued lag 60-300s 峰の減衰 (= chain 断ち)

## 関連参照

- spec 047 (= 観測根拠)、observation-2026-07-02.md (= n=66 本判定)
- spec 020 (= rescue 導入元、v1.5 lookup_latest は本 spec で撤去)、spec 014 (= TID discard)
- spec 028/029/046 (= backfill publish 3 系統、drain 処理で再利用)
- memory `feedback-compose-telegraf-pipeline` (= 新 counter の telegraf topic 追加手順)
