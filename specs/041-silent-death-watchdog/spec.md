# Feature Specification: bridge silent 24h 死亡の検知 + watchdog (= init.svc/process state 乖離対策)

**Feature Branch**: `041-silent-death-watchdog`
**Created**: 2026-06-28
**Status**: Phase 2a Deployed (= 2026-06-28 04:22 JST、 lab-ub01 上 cron `*/5 * * * *` で `~/bin/cube-j1-watchdog.sh` 自動実行、 dry run で `alive pid=21822` 検知成功 + kill -9 simulation で「init 自動 restart 経由で alive 検知」 確認。 Phase 2b (= stderr redirect) + Phase 2c (= heartbeat) は次セッション or 次回 silent death 再発時に対応)

## Phase 2a 実装記録 (= 2026-06-28 04:22 JST)

### root cause 仮説 (= mqtt_ha_bridge.rc 全文確認後)

`/system/etc/init/mqtt_ha_bridge.rc` は 5 行 minimal config:

```
service mqtt_ha_bridge /usr/bin/python /data/local/mqtt_bridge.py
    class late_start
    user root
    group root
    seclabel u:r:shell:s0
```

- `oneshot` flag なし → Android init のデフォルト挙動 = 自動 restart 有効
- `respawn limit` 上書きなし → デフォルト 4 秒以内に 4 回 crash で **一時停止 (= 諦め state)**

24h 沈黙の最有力仮説: 18:44:41 EEDSCAN 直後に Python uncaught exception → process exit → init が短時間に複数回 restart 試行 → respawn limit 到達 → init は state を `running` と保ったまま実際は process 起動を停止。 SELinux audit `denied { read } for /data/local/mqtt_bridge.py` が permissive モードで動いていることが dmesg から確認できる。

### Phase 2a deploy 詳細

- lab-ub01: `~/bin/cube-j1-watchdog.sh` (= 933 bytes、 chmod +x)
- crontab append: `*/5 * * * * ~/bin/cube-j1-watchdog.sh >> ~/.cube-j1-watchdog.log 2>&1 # cube-j1 silent death watchdog (spec 041)`
- 既存 crontab (= yahoo-keepalive) との衝突なし
- script logic: adb connect (idempotent) → pgrep -f mqtt_bridge.py → 0 件なら stop + start mqtt_ha_bridge + 10s 後 verify

### Phase 2a 動作確認

- dry run: `2026-06-28T04:21:58+00:00 alive pid=21822` ✅
- kill -9 simulation: `2026-06-28T04:22:14+00:00 alive pid=22015` = **init が自動 restart して新 pid を取得**、 watchdog 自身は restart 不要だった (= 単発 kill では init で十分)
- 真の zombie state 検知は次回 silent death 再発時に実証 (= 1 件/24h+ の低頻度事象)

### Phase 2a → 2b/2c の繰り越し

- Phase 2b (= stderr redirect): bridge 内 `sys.excepthook` で traceback 記録、 次回 silent death の root cause 究明に必要
- Phase 2c (= heartbeat): bridge main loop で `/data/local/mqtt_bridge.heartbeat` touch + watchdog で `mtime > 180s` 検知、 (D) deadlock 対応
- どちらも当面は Phase 2a の cron + pgrep ベースで「死亡検知 + restart」 までは確保済なので、 緊急性低い


**Input**: 2026-06-28 03:46 (JST) に bridge が **24h 沈黙** (= 2026-06-27 03:44 EEDSCAN OK 直後から process 不在) していたことを発見。 `init.svc.mqtt_ha_bridge = running` だが pgrep 0 件 = silent zombie 状態。 spec 037 deploy 後の独立した死亡で、 traceback / stderr 記録なし、 発見は偶然 (= log grep しようとして気付いた)。 この種の slient death を確実に検知 + 自動 restart する仕組みが必要。

## Background

### 観測された事象 (= 2026-06-28 03:46 JST、 lab-ub01 経由)

1. `tail /data/local/mqtt_bridge.log` の last entry が **2026-06-27 18:44:41 (= JST 03:44:41)** で停止
2. その entry は `EEDSCAN OK: 28 channels, max=E6 min=07` で、 通常運用ログ (= 例外なし、 traceback なし)
3. `pgrep -af mqtt_bridge.py` = 0 件 (= process 不在)
4. `getprop init.svc.mqtt_ha_bridge` = **`running`** (= service state は正しく update されていない)
5. dmesg の `mqtt_ha_bridge killed by signal 9` は別時点 (= 過去 deploy 時の stop/start) で 18:44 タイミング無し
6. 復旧手順: `stop mqtt_ha_bridge; sleep 2; start mqtt_ha_bridge` で SKRESET → SKVER → … → poll_success まで正常起動 (= spec 037 WOPT skip 動作も維持)
7. dmesg の SELinux audit: `avc: denied { getattr/read/ioctl/append } for /data/local/mqtt_bridge.py path=... scontext=u:r:shell:s0` (= permissive 中で警告のみ、 動作影響なし)

### 推測する death scenario (= dig 候補)

- (A) **Python 内部例外** (= serial errno / threading deadlock 等) → stderr に出るが redirect されず捨てられる → init は kill するが state update せず zombie
- (B) **OS-level OOM / signal** → dmesg に痕跡が残るはず、 現状 18:44 タイミング無しのため可能性低
- (C) **deploy script の race** → spec 037 deploy 後 ~24h 後の死亡なので timing 不一致、 可能性低
- (D) **serial /dev/ttyS1 切断 / errno** → 24h の間に何らかの USB-serial 切断、 bridge は close → reopen するはずだが catch されず die

### 既存 (= 不十分な検出)

- init service: state を `running` と報告するが pgrep 0 件 = **検知失敗**
- admin UI: bridge が die すると `/api/log` (= bridge 内 HTTP server) も停止、 mac 側からは接続失敗 (curl exit 7) でしか分からない
- bridge metrics: Prometheus が stale dataの increment 停止で気付けるが alert 未設定
- bridge log: stderr / traceback 記録なし、 死亡時の手がかり消失

## Functional Requirements

### FR-001: 外部 process 監視 cron + 自動 restart (= 最優先)

- **deploy 先**: lab-ub01 から adb 経由で cube-j1 に bash script を配置 (= `/data/local/watchdog_mqtt_bridge.sh` 等)
- **周期**: 5 分ごと cron (= cube-j1 内に cron 無いなら lab-ub01 側 cron + adb で remote 実行)
- **logic**:
  ```bash
  if ! pgrep -f mqtt_bridge.py >/dev/null; then
    stop mqtt_ha_bridge
    sleep 2
    start mqtt_ha_bridge
    # 通知 (= MQTT / Loki / journal)
  fi
  ```
- **alert**: restart イベントを MQTT publish or Loki log に流して Grafana で観測 + Discord/Slack 通知 (= dig で確認)

### FR-002: stderr / traceback の記録

- deploy script の起動コマンドに `2>&1` を追加 (= `/data/local/mqtt_bridge.py` 起動時の stderr を mqtt_bridge.log にマージ)
- もしくは `2> /data/local/mqtt_bridge.stderr.log` で別 file
- Python の `sys.excepthook` で未捕捉例外を log に書く実装

### FR-003: bridge 内 heartbeat (= 自前 watchdog)

- main loop 毎回 (= 60s 周期) に `/data/local/mqtt_bridge.heartbeat` ファイルを touch
- watchdog が `mtime > 180s` (= 3 min stale) で restart trigger
- 利点: process 生存だけでなく main loop 進行も検知できる、 (D) serial 切断 deadlock 対応

### FR-004: silent death 検知メトリクス

- DiagState に `bridge_restart_total` (= watchdog 経由 restart 回数) 追加
- DIAG_SENSOR_DEFS で Prometheus 観測可能化
- Grafana で「直近 1 週間で restart 1 件以上」 の alert を spec 042 で組む (= 別 spec)

### FR-005: SELinux audit log の点検 (= 副次)

- 18:44 周辺の audit denial が死亡と無関係であることを確認 (= permissive で warn のみ、 動作影響 0)
- 確認後 audit ノイズを削減する context 修正は別 spec 候補

## Phase 1: 観察 (= 死因究明)

- watchdog deploy までの数日間で **bridge log + dmesg** を 1 日 1 回 grep し、 再死亡が起きた場合の traceback / dmesg signal を集める
- 並行で `journalctl --since="2026-06-27 18:00" --until="2026-06-27 19:00"` 相当を cube-j1 で取得 (= Android では別 mechanism、 logcat -d 等)

## Phase 2: 実装 (= watchdog deploy)

dig で確定後、 上記 FR-001 〜 003 を実装。 順序:
1. FR-002 stderr redirect (= 次回死亡で traceback 取得)
2. FR-001 cron watchdog (= 自動 restart)
3. FR-003 heartbeat (= deadlock 対応、 dig 必要)
4. FR-004 メトリクス

## Out of Scope

- 死因 root cause の修正 (= 別 spec、 traceback 取得後)
- SELinux context 正規化 (= 別 spec 候補)
- Watchtower 系 container deployment (= bridge は init service 直接管理、 container 化は別議論)

## Success Criteria

### Phase 1 (= 観察)

- **SC-001 (Phase 1)**: 1 週間で再死亡 0 件 or 1 件、 traceback / dmesg 記録の有無を整理
- **SC-002 (Phase 1)**: dig で watchdog 実装手段 (= cube-j1 内 cron vs lab-ub01 cron + adb remote) を確定

### Phase 2 (= 実装)

- **SC-003 (Phase 2)**: stderr redirect 後の log で次回死亡時の traceback 取得実証
- **SC-004 (Phase 2)**: cron watchdog deploy 後、 手動で `kill -9 <pid>` → 5 分以内に自動 restart 観測
- **SC-005 (Phase 2)**: heartbeat 機構 deploy 後、 deadlock simulation (= main loop に sleep 600 注入) で restart trigger 動作
- **SC-006 (Phase 2)**: 7 日間 watchdog 稼働で `bridge_restart_total` メトリクスの increment 数 = 自動 recovery 回数

## Related

- 観測事象: 2026-06-28 03:46 JST の bridge 24h 沈黙発見
- 関連 memory:
  - [[feedback-android-ps-pgrep]] (= pgrep -f は正、 ps -A は無効)
  - [[project-deployment-topology]] (= lab-ub01 経由が必須)
  - [[feedback-lab-ub01-deploy-stale-git]] (= deploy verify で実機 file 直接確認)
- 関連 spec:
  - spec 037 (= WOPT skip、 本 spec の発見契機 = 24h 後の log 確認で気付いた)
  - spec 038/039/040 (= 並行進行、 観察前提として bridge 稼働継続が必要 = 本 spec で防衛線)
- audit findings: 直接の対応無し (= 本 spec は audit findings 9 件の外、 別カテゴリの operational issue)
