# Plan: spec 035 SKLL64 Cached + SKJOIN 直行 (= spec 011 G: SKSCAN 完全 skip で reconnect 35s → 7s)

## Context

[[spec-034-skscan-channel-mask-cache]] v1 Phase 2 (= 2026-06-27 00:37 真 deploy 1h verify) で「単 ch active scan でメーター beacon hit せず cache hit rate 0%」 確定、 active scan アプローチ自体が機能不能と判明 ([[feedback-spec034-single-ch-scan-fails]])。 別アプローチ = **メーター IPv6 / MAC / pan_id / channel は join 後固定** という性質を利用し、 bridge process 生存中 cache → SKSCAN 完全 skip + SKJOIN 直行で reconnect 35s → 7s = 5 倍短縮見込み。

tako 合議 (= 2026-06-27 01:00) で「software 改善は spec 035 のみ補助、 物理層 RF 改善が本命」 判定済、 ユーザは software 路線維持を選択 = spec 035 を最終 software 改善として実装。

### 期待効果

| Step | spec 011 系列 (= 旧 + spec 034 disable) | spec 035 cached path |
|---|---|---|
| SKRESET | 1s | 1s |
| SKVER/SKSETPWD/SKSETRBID/WOPT 1 | 3-4s | 3-4s |
| SKSCAN | **17-32s** | **0s skip** |
| SKLL64 | 1s | **0s skip** (= cached ipv6) |
| SKSREG S2/S3 | 1s | 1s |
| SKJOIN + EVENT 25 待ち | 1-2s | 1-2s |
| **合計** | 24-41s | **6-8s** |

= 5 倍短縮、 reconnect 5 件/h × 30s 短縮 = 150s/h sparseness 改善。 ただし erxudp_timeouts 30/h (= 30 分/h 主因) には届かず、 補助的施策。

## Approach

### v1 MVP の構成

1. **DiagState 拡張 (= 3 cache attr + 1 counter + 2 metric counter)**:
   - `self.pan_id = None`、 `self.mac = None`、 `self.ipv6 = None` (= 既存 `pan_channel` と並列に追加)
   - `self.consecutive_skjoin_failures = 0` (= cache invalidation 判定用)
   - `self.wisun_reconnect_cached_skjoin_total = 0` / `self.wisun_reconnect_cached_skjoin_fallback_total = 0` (= 効果観察用)
   - `on_wisun_joined(pan)` 拡張: pan_id / mac 記録追加 + ipv6 は別途 SKLL64 結果を set する method 新設 `on_skll64(ipv6)`
   - `on_skjoin_success()` / `on_skjoin_failure()` method 追加 (= counter + cache invalidate gating)

2. **`wisun_connect` 拡張**:
   - サイン: `wisun_connect(fd, br_id, br_pwd, prefer_cached_join=False, cached_invalidate_threshold=2, diag_state=None)`
   - `prefer_cached_join=True` + cache 4 件全揃い (= pan_channel/pan_id/mac/ipv6 全部 non-None) なら → **SKSCAN/SKLL64 skip + SKSREG S2/S3 + SKJOIN cached_ipv6** 直行 path
   - SKJOIN 失敗 (= EVENT 24 / timeout 90s) で `diag_state.on_skjoin_failure()` で counter += 1、 threshold (= 2) 超で cache 全 invalidate (= pan_channel = pan_id = mac = ipv6 = None) → 次回必ず SKSCAN 全 scan
   - SKJOIN 成功で `on_skjoin_success()` で counter リセット
   - cache 未揃 or `prefer_cached_join=False` (= 初回 join) → 既存 SKSCAN 全 scan path 経由 (= [[spec-034]] disable 後の旧挙動互換)
   - **spec 034 引数 (= `prefer_known_channel`/`fallback_duration`) は維持** (= 削除すると既存 caller 壊れる、 spec 034 disable は config 経由なので code 互換維持)

3. **main loop reconnect path** (= line 4360 周辺、 spec 034 で既に config gating 配線済):
   - `cfg.get("wisun_reconnect_cached_skjoin_enabled", True)` で gating
   - `cfg.get("wisun_reconnect_cached_skjoin_invalidate_threshold", 2)` で fallback 閾値
   - spec 034 既存 `wisun_reconnect_channel_mask_enabled` は default `True` のままだが、 cube-j1 では explicit `False` override 維持 (= 既に config push 済)
   - 初回 join (= line 4084) は default `prefer_cached_join=False` 維持 = safety

4. **apply_defaults 2 keys 追加**:
   - `wisun_reconnect_cached_skjoin_enabled` (default `True`)
   - `wisun_reconnect_cached_skjoin_invalidate_threshold` (default `2`)

5. **DiagState `_DIAG_SNAPSHOT_KEYS` + raw dict + DIAG_SENSOR_DEFS 拡張** ([[feedback-diag-sensor-defs-publish]]):
   - 新 2 counter (= cached_skjoin_total / cached_skjoin_fallback_total) を MQTT publish
   - 既存 pan_channel と同様の publish パターン (= pan_id/mac/ipv6 は MQTT 不要、 string 値 + 機微性低、 ただし DiagState 内のみ保持で OK)
   - cache 3 件 (= pan_id/mac/ipv6) は admin UI で見えると便利だが、 spec 035 v1 では DiagState 内のみ (= MQTT publish はスコープ外)

### 設計上の判断 (= dig 2 round で確定)

**Round 1**:
- **dig 1A — cache invalidate threshold default = 2** (= 一時ノイズ吸収 + メーター reboot 検出 trade-off の中庸)
- **dig 1B — SKLL64 skip = cached ipv6 直接** (= 1s 短縮、 SKLL64(mac) は決定的計算で再生成意味なし)
- **dig 1C — cache MQTT publish = v1 では DiagState 内のみ** (= 新 2 counter だけ MQTT publish、 pan_id/mac/ipv6 は scope 外)
- **dig 1D — pan_id/mac format = str (hex)** (= SKSCAN 出力そのまま、 SKSREG S3 も str 受け取り = 変換ゼロ)

**Round 2**:
- **dig 2A — `_wait_skjoin_event25` 関数化** (= 既存 wisun_connect L2876-2889 EVENT 25/24 待ち loop を helper 抽出、 cached path + full scan path で再利用 = [[tidy-first]] 構造変更 1 commit 同梱)
- **dig 2B — cached path SKJOIN timeout = 30s** (= 正常 SKJOIN 1-2s に対して余裕 15 倍、 stale cache 時 30s で fallback 進行 = full path 60s+ より 2 倍早諦め)
- **dig 2C — cached fail 後 SKRESET 2 度目入れる** (= EVENT 24 PANA fail 後の BP35CX state 残留 risk 回避、 +1s overhead 容認 = fallback path overhead 60s+ の 1.6% 増)
- **dig 2D — `on_wisun_joined` 拡張 `.get()` で optional**: 既存 test_diag_state L254 が pan dict から `Pan ID`/`Addr` 省略 → `pan_info.get("Pan ID")` / `.get("Addr")` で None default、 既存 test 互換確保

### Trade-off

- **bridge restart 時**: cache 消える = 初回 join で全 scan 必須 (= 月 1 回程度の overhead、 容認可)
- **メーター reboot 時**: cache 古い → SKJOIN 失敗 → 2 fail で invalidate → 全 scan fallback、 1 回だけ 110-130s overhead (= 月 1 回程度、 sparseness 影響軽微)
- **メーター chan 変動 (= ARIB STD-T108 reallocation)**: 同 reboot pattern で fallback、 lab メーター実績 = 6 ヶ月 ch57 固定
- **既存 [[spec-034]] disable 互換**: spec 034 引数残置 + cube-j1 config explicit False で旧挙動、 spec 035 default True で cached path 試行 → 失敗時 spec 034 path (= disable で全 scan) に落ちる、 2 段防御

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **DiagState `__init__`** (= 約 line 2229):
   ```python
   self.pan_id = None              # str (hex)、 既存 pan_channel と並列
   self.mac = None                 # str (= 16 文字 hex)
   self.ipv6 = None                # str (= SKLL64 結果 IPv6)
   self.consecutive_skjoin_failures = 0
   self.wisun_reconnect_cached_skjoin_total = 0
   self.wisun_reconnect_cached_skjoin_fallback_total = 0
   ```

2. **DiagState method 追加** (= 約 line 2310 周辺、 既存 `on_wisun_joined` の隣):
   ```python
   def on_skll64(self, ipv6):
       """spec 035: SKLL64 結果を cache。 wisun_connect の SKLL64 成功直後に呼ぶ。"""
       self.ipv6 = ipv6

   def on_skjoin_success(self):
       """spec 035: SKJOIN 成功で連続失敗 counter リセット。"""
       self.consecutive_skjoin_failures = 0

   def on_skjoin_failure(self, invalidate_threshold=2):
       """spec 035: SKJOIN 失敗で counter += 1、 threshold 超えで cache 全 invalidate。"""
       self.consecutive_skjoin_failures += 1
       if self.consecutive_skjoin_failures >= invalidate_threshold:
           self.pan_channel = None
           self.pan_id = None
           self.mac = None
           self.ipv6 = None

   def on_wisun_reconnect_cached_skjoin(self):
       self.wisun_reconnect_cached_skjoin_total += 1

   def on_wisun_reconnect_cached_skjoin_fallback(self):
       self.wisun_reconnect_cached_skjoin_fallback_total += 1
   ```

3. **DiagState `on_wisun_joined` 拡張** (= 約 line 2400 周辺):
   - 既存 pan_channel 記録に加えて pan_id (= 必ず str)、 mac (= str) も記録

4. **`_DIAG_SNAPSHOT_KEYS` + raw dict + DIAG_SENSOR_DEFS** (= 既存 spec 034 と同 pattern):
   - 新 2 counter (= cached_skjoin_total / cached_skjoin_fallback_total) を全 3 箇所追加
   - pan_id/mac/ipv6 は v1 では MQTT publish しない (= dig C で確認)

5. **`wisun_connect` 拡張サイン + 2 段 path**:
   ```python
   def wisun_connect(fd, br_id, br_pwd, prefer_known_channel=False,
                     fallback_duration=SCAN_DURATION_BASE,
                     prefer_cached_join=False, cached_invalidate_threshold=2,
                     diag_state=None):
       """... 既存 docstring + spec 035 説明追加 ..."""
       # SKRESET / SKVER / SKSETPWD / SKSETRBID / WOPT 1 は不変

       # spec 035: cached SKJOIN 直行 path
       if prefer_cached_join and diag_state is not None:
           cached_ch = diag_state.pan_channel
           cached_pid = diag_state.pan_id
           cached_mac = diag_state.mac
           cached_ipv6 = diag_state.ipv6
           if all(x is not None for x in (cached_ch, cached_pid, cached_mac, cached_ipv6)):
               log("SKJOIN cached direct (ch={} pan_id={} ipv6={})".format(
                   cached_ch, cached_pid, cached_ipv6))
               try:
                   ch_hex = "{:02X}".format(int(cached_ch))
                   skcommand(fd, "SKSREG S2 {}".format(ch_hex))
                   skcommand(fd, "SKSREG S3 {}".format(cached_pid))
                   serial_write(fd, "SKJOIN {}\r\n".format(cached_ipv6))
                   # 以降 EVENT 25/24 待ち path (= 既存 line 2876-2889 の path を関数化 or inline で共通化)
                   if _wait_skjoin_event25(fd, timeout=30):
                       try:
                           diag_state.on_skjoin_success()
                           diag_state.on_wisun_reconnect_cached_skjoin()
                       except Exception as e:
                           log("diag on_skjoin_success error: {}".format(e))
                       return cached_ipv6
                   # EVENT 24 or timeout → fall through to fallback
                   try:
                       diag_state.on_skjoin_failure(cached_invalidate_threshold)
                       diag_state.on_wisun_reconnect_cached_skjoin_fallback()
                   except Exception as e:
                       log("diag on_skjoin_failure error: {}".format(e))
               except Exception as e:
                   log("SKJOIN cached path failed: {} - falling back to full scan".format(e))
                   try:
                       diag_state.on_skjoin_failure(cached_invalidate_threshold)
                       diag_state.on_wisun_reconnect_cached_skjoin_fallback()
                   except Exception as e2:
                       pass
       # 以降 SKSCAN 全 scan path (= 既存挙動、 spec 034 引数も含めて完全保護)
       # ... (既存 line 2755-2895 の path)
   ```
   - **`_wait_skjoin_event25(fd, timeout)` 関数化必須** (= dig 2A): 既存 EVENT 25/24 待ち loop = line 2876-2889 を helper 抽出。 戻り値 = True (= EVENT 25 成功) / False (= EVENT 24 PANA fail or timeout)。 cached path = `timeout=30` (= dig 2B)、 full path = `timeout=90` (= 既存維持)
   - **cached fail 後の re-SKRESET** (= dig 2C): cached path 失敗で fallback に進む直前に `skcommand(fd, "SKRESET", timeout=5)` + `time.sleep(1)` + SKVER/SKSETPWD/SKSETRBID/WOPT 1 再実行 (= 既存 wisun_connect 先頭の冪等な init sequence を関数化 `_wisun_init_sequence(fd, br_id, br_pwd)` に抽出して 2 度目呼出が clean)
   - SKSCAN 成功後の path で **`on_wisun_joined(pan)` 拡張 (= pan_id/mac `.get()` 安全取得、 dig 2D) + `on_skll64(ipv6)` 呼出 + `on_skjoin_success()` 呼出** を追加

6. **apply_defaults** (= line 209 周辺):
   ```python
   # spec 035: SKLL64 cached + SKJOIN 直行 (= SKSCAN skip で reconnect 35s → 7s).
   out.setdefault("wisun_reconnect_cached_skjoin_enabled", True)
   out.setdefault("wisun_reconnect_cached_skjoin_invalidate_threshold", 2)
   ```

7. **main loop reconnect path** (= line 4445 周辺、 spec 034 で既に `wisun_connect(..., prefer_known_channel=..., fallback_duration=..., diag_state=...)` 呼出):
   ```python
   ipv6 = wisun_connect(
       fd, br_id, br_pwd,
       prefer_known_channel=bool(cfg.get("wisun_reconnect_channel_mask_enabled", True)),
       fallback_duration=int(cfg.get("wisun_reconnect_channel_mask_fallback_duration", 6)),
       prefer_cached_join=bool(cfg.get("wisun_reconnect_cached_skjoin_enabled", True)),
       cached_invalidate_threshold=int(cfg.get("wisun_reconnect_cached_skjoin_invalidate_threshold", 2)),
       diag_state=diag_state)
   ```

### `tests/unit/test_apply_defaults_spec_035.py` (= 新規)

- `test_default_cached_skjoin_enabled_true`
- `test_default_invalidate_threshold_2`
- `test_explicit_override_respected`

### `tests/unit/test_diag_state.py` (= 既存拡張)

- `test_pan_id_starts_none` (= baseline)
- `test_mac_starts_none`
- `test_ipv6_starts_none`
- `test_consecutive_skjoin_failures_starts_zero`
- `test_on_wisun_joined_sets_pan_id_and_mac` (= pan dict から記録確認)
- `test_on_skll64_sets_ipv6`
- `test_on_skjoin_success_resets_failures` (= 一度 fail 後 success で 0 リセット)
- `test_on_skjoin_failure_increments` (= 1 fail で counter 1)
- `test_on_skjoin_failure_invalidates_cache_at_threshold` (= threshold 2 で cache 全 None)
- `test_on_skjoin_failure_keeps_cache_below_threshold` (= 1 fail では cache 残る)
- `test_wisun_reconnect_cached_skjoin_total_baseline_zero`
- `test_on_wisun_reconnect_cached_skjoin_increments`
- `test_wisun_reconnect_cached_skjoin_fallback_total_baseline_zero`
- `test_on_wisun_reconnect_cached_skjoin_fallback_increments`
- `test_snapshot_includes_cached_skjoin_totals` (= MQTT publish 経路保護、 2 件)
- (既存 baseline test に新 counter 2 件追加が必要 = spec 034 で同 pattern 確立済)

## Test list (TDD 順)

1. **apply_defaults 3 件** (= 1 cycle、 spec 028/29/032/034 で同 pattern)
2. **DiagState cache attr 4 件** (= pan_id/mac/ipv6/consecutive_skjoin_failures 全部 baseline None/0)
3. **DiagState method 6 件** (= on_skll64 / on_skjoin_success / on_skjoin_failure 3 cases / on_wisun_reconnect_cached_skjoin × 2 件)
4. **DiagState on_wisun_joined 拡張 1 件** (= pan dict から pan_id/mac 記録、 既存 test 互換性破らない)
5. **DiagState snapshot 2 件** (= 新 2 counter 経路保護、 既存 baseline test 1 件修正)
6. main loop / wisun_connect 拡張 = 実機検証 (= SC-005 で「cached_skjoin_total > 0」 + cache hit rate >= 95%)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で既存 476 + 新規 ~18 件 pass
2. cube-j1-mqtt 1 commit (= 私が直接、 subagent rate limit 回避) + jj push --remote fork (= forward only)
3. lab-ub01 経由 deploy + **deploy 反映 grep 確認** (= [[feedback-lab-ub01-deploy-stale-git]] 必須): `ssh lab-ub01 'adb shell "grep -c on_skll64 /data/local/mqtt_bridge.py"'` で実機ファイル直接確認
4. cube-j1 `/data/local/config.json` で spec 034 disable override 維持確認 (= spec 035 の前提)
5. **Phase 1 sanity** (= deploy 後 5-8 分、 `ScheduleWakeup` or `CronCreate`):
   - bridge alive (`pgrep -f mqtt_bridge`)
   - `/api/diag` で `wisun_reconnect_cached_skjoin_total` / `_fallback_total` snapshot 経路生存
   - 新 cache attr (= pan_id/mac/ipv6) は v1 では MQTT publish しないが、 DiagState 内に保持されてるか実機 verify (= bridge log で「SKJOIN cached direct」 が初回 join 後の reconnect で出るか)
   - reconnect 0-1 件想定範囲
6. **Phase 2 SC-005** (= 1h 累積):
   - `wisun_reconnect_cached_skjoin_total > 0` (= cache 直行発火)
   - cache 成功率 `cached / (cached + fallback) >= 95%`
   - reconnect 所要時間 17-32s → 6-8s 短縮 (= bridge log で SKRESET → EVENT 25 時刻差)
   - panel-1 sparseness cluster 数増加 (= grafana lab dashboard で前後比較)

## Commit 戦略

- compose 不要 (= telegraf 変更なし、 既存 DIAG_SENSOR_DEFS publish に乗る)
- cube-j1-mqtt 1 commit + push
- `~/.claude/hooks/redact-plans.sh` 適用、 plan file 同梱
- jj 5 step (= fetch → log → rebase → set → push)

## Commit message

`feat(bridge): SKLL64 cached + SKJOIN 直行で SKSCAN 完全 skip、 reconnect 35s → 7s 短縮 (spec 035 = spec 011 G、 spec 034 失敗の代替)`
