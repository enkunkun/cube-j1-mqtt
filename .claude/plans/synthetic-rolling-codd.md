# Plan: spec 015 NextDrive Cloud Daemon Quiesce + tlsdate Repoint

## Context

Cube J1 上で NextDrive クラウド連携の 7 デーモン (`NDCloudDaemon` / `fms` / `fmssecman` / `iijschedule` / `rds` / `sessiond` / `transman`) が `running` 状態で稼働し続けている (実機 ps 確認済)。 NextDrive クラウドはサービス終了済 → 到達不能 host への DNS query を継続的に撃っている可能性大、 spec 010 で観測している EEDSCAN ch57 ノイズ bimodal の **間接寄与源** の可能性もある。

加えて `tlsdated.rc` も実機に存在し NextDrive の同期 host (引数なしのデフォルト) を使っている。 hals5412 fork `9477ec3` + `3b97f5e` の発想を取り込み、 **クラウドデーモン 7 つを永続的に disabled 化** + **tlsdate を公開 host に向け直し** をインストーラに追加する。

実装は Python ではなく **shell + Android init `.rc` 中心** (TDD スキップ可、 spec 013/014/016/019 の Python TDD パターンとは性質が違う)。

## Approach

- `production_tool/cloud_disabled/` ディレクトリ新設、 7 デーモン分の `.rc` 配置 (実機 original に `disabled` keyword を 1 行足したもの)
- `production_tool/tlsdated_timesync.rc` 新設 (tlsdate コマンド引数に `-H www.google.com` を渡す形に置換)
- `production_tool/production_tool` インストーラを拡張 (既存 wisund/NDEcLiteAgent 無効化と同じ idiom):
  - 環境変数 `DISABLE_CLOUD=1` (default 1) のとき 7 .rc を `/system/etc/init/` にコピー + stop
  - 環境変数 `REPOINT_TLSDATE=1` (default 1) のとき tlsdated_timesync.rc を `/system/etc/init/tlsdated.rc` にコピー
  - 失敗時の rollback は scope 外 (既存インストーラも rollback なし、 一貫性維持)
- README に新環境変数を 1 節追記

## Files

### `production_tool/cloud_disabled/` (新規ディレクトリ、 7 ファイル)

各ファイルは実機の original .rc に **`    disabled`** を `class late_start` の次行に追加したもの。 例 `cloud_disabled/sessiond.rc`:
```
service sessiond /usr/sbin/sessiond
    class late_start
    disabled
    group root system readproc
    seclabel u:r:shell:s0
```

7 ファイル: `NDCloudDaemon.rc`, `fms.rc`, `fmssecman.rc`, `iijschedule.rc`, `rds.rc`, `sessiond.rc`, `transman.rc`

**dig Round 1 決定 2: `on property:` ハンドラブロックは削除する**。 理由: Android init の `restart <disabled-service>` 挙動が実装依存で disabled を上書きで起動する可能性をゼロにできない、 安全側で完全除去。

実機確認で `on property:` ブロックを持つのは **NDCloudDaemon.rc と fms.rc の 2 ファイルだけ** (dig Round 2 決定 6)。 残り 5 ファイルは service 定義のみで handler 無し、 そのまま `disabled` 追加で済む。
- NDCloudDaemon.rc: 元 `on property:persist.cloud.connection.delay=* → restart NDCloudDaemon` と `on property:persist.cloud.fms.new=* → stop NDCloudDaemon` 両方削除
- fms.rc: 元 `on property:persist.cloud.connection.delay=* → restart fms` 削除

### `production_tool/tlsdated_timesync.rc` (新規)

実機 original を base に、 tlsdate コマンドに `-H www.google.com` を追加:
```
# Init file for starting tlsdated on Android. spec 015: repointed to
# www.google.com since the NextDrive default host is no longer reachable.

on post-fs-data
    mkdir /data/misc/tlsdated 0755 root system

service tlsdated /usr/sbin/tlsdated -v -a 3600 -c /data/misc/tlsdated -G dbus,inet -- /usr/sbin/tlsdate -H www.google.com -C /etc/ssl/certs -l -v
    class late_start
    user root
    group system
    seclabel u:r:shell:s0
```

### `production_tool/production_tool` (拡張)

既存の `# disable wisund` ブロックの下に追加:
```sh
# ── spec 015: disable NextDrive cloud daemons ─────────────────────────────────
if [ "${DISABLE_CLOUD:-1}" = "1" ]; then
    for d in NDCloudDaemon fms fmssecman iijschedule rds sessiond transman; do
        if [ -f "$PT/cloud_disabled/${d}.rc" ]; then
            cp "$PT/cloud_disabled/${d}.rc" "/system/etc/init/${d}.rc"
        fi
    done
fi

# ── spec 015: repoint tlsdate to a public HTTPS host ─────────────────────────
if [ "${REPOINT_TLSDATE:-1}" = "1" ]; then
    if [ -f "$PT/tlsdated_timesync.rc" ]; then
        cp "$PT/tlsdated_timesync.rc" /system/etc/init/tlsdated.rc
    fi
fi
```

既存の `stop wisund` ブロックの下に追加 (即時停止):
```sh
# spec 015: stop cloud daemons immediately (will stay stopped after reboot
# via the disabled keyword in the replaced .rc files).
if [ "${DISABLE_CLOUD:-1}" = "1" ]; then
    for d in NDCloudDaemon fms fmssecman iijschedule rds sessiond transman; do
        stop "$d" 2>/dev/null || true
    done
fi
```

注: 既存スクリプトに rollback はない。 `cp` 失敗 = init service が見つからない致命的状態だが、 USB media 経由 install なので運用上で発生しない前提 (既存 wisund/NDEcLiteAgent と同等)。

### `readme.md` (1 節追記)

「インストール環境変数」セクションに 2 行追加:
- `DISABLE_CLOUD=0`: NextDrive クラウドデーモン無効化を skip (default `1`)
- `REPOINT_TLSDATE=0`: tlsdate 同期先付け替えを skip (default `1`)

## Pure helper / tests

なし。 shell script + .rc 静的ファイルなので Python unit test 対象外 (CLAUDE.md `/tdd` skill: 「リファクタリング・設定/CI/CD はスキップ可」)。

## Verification

1. 実装後 `pytest -q --ignore=tests/benchmark` で **既存 361 件 pass** (回帰なし)
2. lab-ub01 経由で 8 .rc ファイル + 改修済 `production_tool` インストーラを Cube J1 にコピー (運用上は USB 経由 install だが、 deploy 簡略化のため adb push で対応)
3. 実機で確認 (lab-ub01 経由 adb shell):
   - `getprop | grep init.svc.sessiond` → `stopped` (or `disabled`)
   - 同様に `fms` / `NDCloudDaemon` / etc. すべて `stopped`
   - `ps | grep -E "(sessiond|fms|NDCloud|rds|iijschedule|transman)"` → 0 件
   - tlsdate のログで Google 同期成功 (`/data/log/...` あるいは logcat)
4. **reboot 後** に同様確認 (永続化検証):
   - `getprop init.svc.<name>` → `stopped` のまま (`disabled` キーワードで起動しない)
5. 長期効果 (1 週間程度):
   - Grafana の `eedscan_pan_channel_energy` ヒストグラムを baseline と比較
   - bimodal うるさい側 (>= 190) の出現割合が baseline から **30% 以上減少** すれば SC-003 達成
   - 減少しない場合は DNS storm はノイズ寄与源ではなかったと結論、 spec 012 noise-adaptive-skip が正しい対策の証拠

## Deploy 戦略

**dig Round 1 決定 1: 直接 push + syntax check** (フルインストーラ実行は副作用が大きいので不採用)。

具体的手順:

1. **syntax check** (script の最低限担保):
   ```sh
   sh -n production_tool/production_tool
   ```
   ローカルで実行、 エラー無いことを commit 前に確認。

2. **scp で lab-ub01 に転送**:
   ```sh
   ssh lab-ub01 'mkdir -p /tmp/spec015-deploy/cloud_disabled'
   scp -q production_tool/cloud_disabled/*.rc lab-ub01:/tmp/spec015-deploy/cloud_disabled/
   scp -q production_tool/tlsdated_timesync.rc lab-ub01:/tmp/spec015-deploy/
   ```

3. **lab-ub01 から adb で実機の /system/etc/init/ に書き込み** (一部 stop 失敗でも他は続ける、 `|| true` で contain — dig Round 2 決定 5):
   ```sh
   ssh lab-ub01 'set -e
     adb kill-server >/dev/null 2>&1; sleep 1; adb start-server >/dev/null 2>&1
     adb connect cube-j1.home.arpa:5555 >/dev/null 2>&1; sleep 1
     adb shell "mount -o rw,remount /"
     for d in NDCloudDaemon fms fmssecman iijschedule rds sessiond transman; do
       adb push /tmp/spec015-deploy/cloud_disabled/${d}.rc /system/etc/init/${d}.rc
       adb shell "stop $d" || true
     done
     adb push /tmp/spec015-deploy/tlsdated_timesync.rc /system/etc/init/tlsdated.rc
     adb shell "stop tlsdated" || true
     adb shell "start tlsdated" || true
     adb disconnect cube-j1.home.arpa:5555 >/dev/null 2>&1'
   ```

4. **即時確認**:
   - `getprop init.svc.sessiond` 等 → `stopped`
   - `ps | grep -E "(sessiond|fms|NDCloud|rds|iijschedule|transman)"` → 0 件

5. **reboot 検証 (永続化確認)**: ユーザ判断で `adb reboot` 実施。 復活待機後 (約 1-2 分)、 同じ確認コマンドで `stopped` が維持されていることを確認。 cube-j1.home.arpa への adb 経路は reboot 後自動復活 (前 spec 014 等で実証済み)。

   reboot は本セッションでは ユーザに伺ってから実施。 mqtt_bridge.py の deploy と違って /system 変更の検証が reboot を要求するため、 これは spec 015 の必須ステップ。

## Commit 戦略

前 3 回踏襲 (spec 014/016/019):
- 017/018 spec.md を /tmp に stash
- spec 015 関連 + spec.md + plan + redact-plans 適用で commit
- 017/018 spec.md を restore して @ に戻す
- 並行セッションが私の作業を巻き込んでいた場合は `jj edit` で cleanup

## Commit

`feat(install): NextDrive クラウドデーモン無効化 + tlsdate 公開 host へ向け直し (spec 015)`

8 .rc ファイル + production_tool 改修 + spec.md + plan を 1 commit。
