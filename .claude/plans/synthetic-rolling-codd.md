# Plan: spec 034 SKSCAN Channel Mask Cache (= spec 011 F: reconnect 32s→4s で sparseness さらに改善)

## Context

spec 033 deploy 後 panel-1 観察で「リカバー一部増えてるが空白多い」 = reconnect の SKSCAN 32s 占有が polling sparseness 主因の一つ。 cube-j1-mqtt reconnect は SKRESET → SKVER/SKSETPWD/SKSETRBID → **SKSCAN 32s** → SKJOIN で計 35-40s、 1h で 4-5 回 (= spec 032 threshold=6) = 1h 内 **2-3 分が SKSCAN 待ち**。

メーター pan_channel (= 現在 57) は join 後固定で、 BP35CX `SKSCAN 2 <mask> <duration> <side>` の mask 引数で単 ch のみ scan 可 (= ch57 なら `0x01000000`、 duration 3 で ~4s)。 reconnect 時に既知 ch のみ短 scan + 失敗時 fallback 全 scan で SKSCAN 32s → 4s = **8 倍短縮** 見込み、 reconnect 全体 35s → 7s、 panel-1 sparseness 緩和。

[[reference-tako-instantaneous-power-architectural-cap]] が「software 改善余地」 として spec 011 F (= SKSCAN 固定) を最終形態に位置付けた spec、 spec 032/033 に続く 3 件目の software 改善。

## Approach

### v1 MVP の構成

1. **新 pure helper `channel_to_mask(ch)`** = `skscan` 直前 (= 約 line 2638 周辺) に追加、 ch33-60 → 32-bit bitmap 変換 (= `1 << (ch - 33)`)
2. **`skscan` 拡張サイン** (= 約 line 2643): `skscan(fd, channel_mask="FFFFFFFF", duration=SCAN_DURATION_BASE, max_retries=None, diag_state=None)` で旧 caller (= keyword なし呼出) 無変更互換。 `max_retries=None` は既存 `SCAN_RETRY_LIMIT` 追従 (= duration 増分 retry)、 `max_retries=1` は 1 回試行で抜け (= 単 ch path 用、 dig round 2 A 決定)
3. **`wisun_connect` 拡張サイン** (= 約 line 2729): `wisun_connect(fd, br_id, br_pwd, prefer_known_channel=False, fallback_duration=SCAN_DURATION_BASE, diag_state=None)` で旧 caller 無変更互換。 `fallback_duration` は main loop で `config.get("wisun_reconnect_channel_mask_fallback_duration", 6)` 取得して渡す (= dig round 2 B 決定)
4. **wisun_connect 内 2 段 scan path** (= dig 決定 = wisun_connect 内 fallback、 caller 1 呼出で完結):
   - `prefer_known_channel=True` かつ `diag_state.pan_channel` が None でなければ → 単 ch mask scan (= `channel_to_mask(pan_channel)` + duration 3) を 1 回試行
   - PAN 見つからず → fallback で `SKSCAN 2 FFFFFFFF {fallback_duration} 0` 実行 (= 旧挙動)
   - `prefer_known_channel=False` or `pan_channel is None` → 直接全 scan path
5. **main loop reconnect path** (= line 4360) で `wisun_connect(..., prefer_known_channel=True, ...)` 渡し、 初回 join (= line 3998) は default `False` で全 scan 維持 (= safety)
6. **DiagState 拡張 2 counter**: `wisun_reconnect_short_scan_total` (= 単 ch 成功) + `wisun_reconnect_fallback_full_scan_total` (= fallback 全 scan)、 「短縮効果率」 grafana 観察可
7. **apply_defaults 2 keys** (= dig 決定 = spec.md 通り escape hatch 完備):
   - `wisun_reconnect_channel_mask_enabled` (default `True`)
   - `wisun_reconnect_channel_mask_fallback_duration` (default `6`、 spec 010 SCAN_DURATION_BASE と同値)

### 設計上の判断 (dig round 1 で確定済)

- **dig A — 単 ch duration**: `3` (= ~4s、 spec.md 明記)。 BP35CX 仕様 192*2^3 = 1536 symbol times ≒ 3-4 秒、 既知 ch (= ノイズ無視) なら検出余裕あり
- **dig B — fallback location**: `wisun_connect` 内 2 段 scan (= 1 呼出で 2 段)。 main loop 上位 except 任せ案 (= 単 ch fail で raise → spec 017 backoff 30s 待ち) は sparseness 改善目的に反する
- **dig C — kill switch**: `enabled` + `fallback_duration` の 2 keys (= spec.md 通り)。 spec 034 は spec 033 と違い reconnect 経路根本変更で risk 高い、 escape hatch 推奨

### Trade-off

- **MQTT publish 件数**: 変化なし (= polling 周辺は spec 033 で確定済、 spec 034 は SKSCAN duration のみ)
- **reconnect 単 ch fail 時**: +4s 余分待ち (= 単 ch 試行 4s) で全 scan 32s に fallback = 全体 36s。 ただし期待 cache hit rate >= 90% で稀
- **メーター chan 変動 (= reboot 時稀)**: 単 ch 必ず fail → fallback で 36s reconnect 1 回発生、 以降 `diag_state.pan_channel` 更新で次回 reconnect は新 ch で 4s 短 scan 戻り
- **既存 `SCAN_DURATION_BASE=6`** 完全保護 (= 初回 join、 fallback 経路で流用)

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **`channel_to_mask(ch)` 新 pure helper** = `SCAN_DURATION_BASE` 定数 (= line 2636) 直後に追加:
   ```python
   def channel_to_mask(ch):
       """spec 034: BP35CX channel 番号 (= ch33-60) を SKSCAN 2 用 32-bit
       channel mask に変換。 ch33 = bit 0, ch60 = bit 27.
       例: channel_to_mask(57) = 0x01000000 (= bit 24)
       """
       return 1 << (int(ch) - 33)
   ```

2. **`skscan` サイン拡張** (= line 2643):
   - L2643: `def skscan(fd, channel_mask="FFFFFFFF", duration=SCAN_DURATION_BASE, max_retries=None, diag_state=None):`
   - L2650: `duration = SCAN_DURATION_BASE` 削除 (= 引数で初期化)
   - L2651 (= `while duration <= SCAN_RETRY_LIMIT` 直前) に `attempt = 0` 初期化 (= ループ外 = 1 skscan 呼出 1 counter) + L2695 `duration += 1` 直前に `attempt += 1; if max_retries is not None and attempt >= max_retries: break` 挿入 (= dig round 3 C: 1 回試行 break、 max_retries=None で旧挙動互換)
   - L2658: `"SKSCAN 2 FFFFFFFF {} 0\r\n".format(duration)` → `"SKSCAN 2 {} {} 0\r\n".format(channel_mask, duration)`
   - 旧 caller (= `skscan(fd, diag_state=diag_state)`) は default `max_retries=None` で旧挙動互換 (= duration 増分 retry)

3. **`wisun_connect` サイン拡張 + 2 段 scan** (= line 2729-2812):
   - サイン: `def wisun_connect(fd, br_id, br_pwd, prefer_known_channel=False, fallback_duration=SCAN_DURATION_BASE, diag_state=None):`
   - L2755-2758 の SKSCAN 呼出 1 箇所を「2 段 path」 で置換:
     - 単 ch 試行 (= `prefer_known_channel=True` + `diag_state.pan_channel` あり) → `skscan(fd, channel_mask="{:08X}".format(channel_to_mask(known_ch)), duration=3, max_retries=1, diag_state=diag_state)` (= dig round 2 A: 1 回試行で抜け)
     - 失敗 (= `pan.get("Channel")` 偽) なら fallback `skscan(fd, channel_mask="FFFFFFFF", duration=fallback_duration, diag_state=diag_state)` (= max_retries=None default = duration 増分 retry で旧挙動)
     - counter 副作用 (= dig round 3 D): 単 ch 成功時 `on_wisun_reconnect_short_scan()` / 単 ch 失敗で fallback path 入った時のみ `on_wisun_reconnect_fallback_full_scan()`。 初回 join (= `prefer_known_channel=False`) は counter 発火しない (= SC cache hit rate 計算の分母から除外) =「単 ch 取り損ね率」 metric 性質保持
     - 既存 try/except 防御 pattern 踏襲 (= diag bug が measurement path 遮らない)
   - 以降 SKJOIN / SKLL64 / SKSREG 等は完全保護

4. **main loop reconnect path** (= line 4360) で `wisun_connect(..., prefer_known_channel=<gated>, fallback_duration=<from-config>, ...)` 渡し:
   - `prefer_known_channel = config.get("wisun_reconnect_channel_mask_enabled", True)` で gating (= False なら旧挙動)
   - `fallback_duration = int(config.get("wisun_reconnect_channel_mask_fallback_duration", 6))` (= dig round 2 B 決定)
   - 初回 join (= line 3998) は引数省略 = default `prefer_known_channel=False` + `fallback_duration=SCAN_DURATION_BASE` 維持 = safety、 pan_channel 未知のため

5. **DiagState 拡張** (= line 2230 周辺 `__init__` + line 1651 `_DIAG_SNAPSHOT_KEYS` + line 3669 DIAG_SENSOR_DEFS):
   - `self.wisun_reconnect_short_scan_total = 0` / `self.wisun_reconnect_fallback_full_scan_total = 0`
   - `on_wisun_reconnect_short_scan(self)` / `on_wisun_reconnect_fallback_full_scan(self)` method
   - `_DIAG_SNAPSHOT_KEYS` + snapshot raw dict + DIAG_SENSOR_DEFS 各 2 件追加 ([[feedback-diag-sensor-defs-publish]] 必須)

6. **apply_defaults** (= line 101-210、 末尾追加):
   ```python
   # spec 034: SKSCAN channel mask cache for faster reconnect (32s → 4s).
   out.setdefault("wisun_reconnect_channel_mask_enabled", True)
   out.setdefault("wisun_reconnect_channel_mask_fallback_duration", 6)
   ```

### `tests/unit/test_channel_to_mask.py` (= 新規)

- `test_channel_33_returns_bit_0` (= `channel_to_mask(33) == 0x1`)
- `test_channel_57_returns_known_mask` (= `channel_to_mask(57) == 0x01000000`、 lab メーター現 ch)
- `test_channel_60_returns_max_bit` (= `channel_to_mask(60) == 0x08000000`)
- `test_int_string_arg_coerced` (= `channel_to_mask("57") == 0x01000000`、 diag_state.pan_channel が str fallback case の安全弁)

### `tests/unit/test_apply_defaults_spec_034.py` (= 新規)

- `test_default_channel_mask_enabled_true`
- `test_default_fallback_duration_6`
- `test_explicit_override_respected` (= user config の `False` / `4` が保持)

### `tests/unit/test_diag_state.py` (= 既存拡張)

- `test_wisun_reconnect_short_scan_total_baseline_zero`
- `test_on_wisun_reconnect_short_scan_increments`
- `test_wisun_reconnect_fallback_full_scan_total_baseline_zero`
- `test_on_wisun_reconnect_fallback_full_scan_increments`
- `test_snapshot_includes_short_scan_total` (= MQTT publish 経路保護、 [[feedback-diag-sensor-defs-publish]])
- `test_snapshot_includes_fallback_full_scan_total`

## Test list (TDD 順)

1. `test_channel_33_returns_bit_0` (= pure helper baseline)
2. `test_channel_57_returns_known_mask` (= 主要 use case)
3. `test_channel_60_returns_max_bit` (= 境界)
4. `test_int_string_arg_coerced` (= robust 性)
5. `apply_defaults` 3 件 (= 1 cycle で書ける)
6. DiagState 6 件 (= counter 2 件 × 3 cases、 spec 028/029 で同 pattern 確立済)
7. main loop / wisun_connect 拡張 = 実機検証 (= SC-005 で「reconnect 後 short scan total > 0」 と「panel-1 sparseness 改善」 確認)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で既存 463 + 新規 ~13 件 pass
2. `ruff check production_tool/mqtt_bridge.py tests/unit/test_channel_to_mask.py tests/unit/test_apply_defaults_spec_034.py` 新規エラー無し
3. cube-j1-mqtt 1 commit (= 私が直接、 subagent rate limit risk 回避) + jj push --remote fork (= forward only、 5 step)
4. lab-ub01 経由 deploy (= `~/cube-j1-mqtt/install/adb_push_update.sh cube-j1.home.arpa`)
5. **Phase 1 sanity check** (= deploy 直後 5-8 分後、 `ScheduleWakeup delaySeconds=480`、 [[feedback-phased-deploy-observation]]):
   - bridge alive (`pgrep -f mqtt_bridge`)
   - polling success 継続 (= last_poll_success_ts 5 分以内更新)
   - reconnect 0-1 件想定範囲内、 bridge log で「SKSCAN single-ch try (ch=57 mask=01000000)」 出現確認
   - `/api/diag` で `wisun_reconnect_short_scan_total` / `..._fallback_full_scan_total` snapshot 経路生存確認
6. **Phase 2 SC-005 達成判定** (= 残 50 分後、 `ScheduleWakeup delaySeconds=3000`):
   - `wisun_reconnect_short_scan_total > 0`
   - cache hit rate `short / (short + fallback) >= 90%`
   - reconnect 所要時間 32s → 5-10s 短縮 (= bridge log で SKRESET から EVENT 25 までの時刻差で観察)
   - panel-1 sparseness 改善 (= cluster 1h 5 個 → 7-8 個期待)
7. SC-006 (= 1 週間 long-term で fallback 率 5% 以下) は別セッション継続

## Commit 戦略

- compose 不要 (= telegraf 変更なし、 既存 DIAG_SENSOR_DEFS publish に乗る)
- cube-j1-mqtt 1 commit + push (= 私が直接、 subagent rate limit risk 回避)
- `~/.claude/hooks/redact-plans.sh` 適用、 plan file 同梱
- `jj git push --remote fork --bookmark main` で forward only

## Commit message

`feat(bridge): reconnect 時 SKSCAN を既知 channel mask 単 ch scan + 失敗時 fallback で 32s → 4s 短縮 (spec 034 = spec 011 F)`
