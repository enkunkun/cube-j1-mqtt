# Feature Specification: NextDrive Cloud Daemon Quiesce + tlsdate Repoint

**Feature Branch**: `015-nextdrive-cloud-quiesce`
**Created**: 2026-06-21
**Status**: Draft
**Input**: User description: "hals5412 fork (9477ec3 + 3b97f5e) を取り込み、 終了済み NextDrive クラウドへ撃ち続けている DNS storm を断つ。 副作用として EEDSCAN ノイズへの間接寄与の可能性も検証する"

## Background

Cube J1 は NextDrive 社の販売を終了した IoT デバイスで、 元々クラウド連携 (sessiond / fms / fmssecman / NDCloudDaemon / rds / iijschedule / transman 等) が常駐していた。 当方インストーラ (`production_tool/production_tool`) は `wisund` と `NDEcLiteAgent` だけ stop していて、 これらクラウド連携デーモンは **稼働したまま** になっている。

NextDrive 側のクラウド (`newsignaling.nextdrive.io` 等) は既にサービス終了しているため、 デーモンたちは到達不能な FQDN へ DNS クエリを撃ち続けている。 これがバックグラウンドで動き続けることで:

1. **DNS query storm** → ローカル DNS resolver の負荷、 LAN ノイズ
2. **tlsdate** の同期先 (NextDrive 系ホスト) も到達不能なので時刻同期が失敗、 ログタイムスタンプが古い値で固定 / drift
3. **CPU 負荷の揺らぎ** が BP35CX のシリアル割込み処理タイミングに影響し、 ERXUDP 受信が遅延する可能性
4. **EEDSCAN ノイズ観測との関連** — spec 010 で観測している ch57 のノイズ bimodal 現象 (静か 6-30 / うるさい 188-202) の一部寄与源になっている可能性。 spec 012 noise-adaptive-skip は対症療法だが、 本 spec は **根本原因の除去** を狙う

hals5412/cube-j1-mqtt の `9477ec3` で NextDrive クラウド系 7 デーモンを `disabled` 化 + stop する仕組みが導入されており、 `3b97f5e` で tlsdate の同期先を公開ホスト (`www.google.com` 等) に向け直している。 両者をセットで取り込む。

## Scope

### A. NextDrive クラウドデーモン無効化 (9477ec3)

- `production_tool/cloud_disabled/` ディレクトリを新設、 7 デーモン分の `.rc` を配置:
  - `sessiond.rc` / `fms.rc` / `fmssecman.rc` / `NDCloudDaemon.rc` / `rds.rc` / `iijschedule.rc` / `transman.rc`
  - 各 `.rc` は元の init service 定義を上書きで `disabled` 付与
- `production_tool/production_tool` (インストーラ) を改修:
  - 既存の `wisund_disabled.rc` / `ndeclite_disabled.rc` パターンに揃える
  - `cloud_disabled/*.rc` を `/system/etc/init/` にコピー (バックアップ→上書き)
  - 各デーモンを `stop` で即時停止
  - 失敗時のロールバック (バックアップから復元)
- 環境変数 `DISABLE_CLOUD=0` で skip 可能 (緊急時の escape hatch)

### B. tlsdate 同期先の付け替え (3b97f5e)

- `production_tool/tlsdated_timesync.rc` を追加:
  - tlsdate コマンドの引数を `-H www.google.com` (Google) に変更
  - 同等の public HTTPS ホストを 2-3 個 fallback として候補に
- 環境変数 `REPOINT_TLSDATE=0` で skip 可能

### C. 効果観測

- `/api/diag` または `/api/metrics` に新メトリクス追加候補:
  - `nextdrive_cloud_daemons_stopped` (bool、 install 後の状態)
  - `tlsdate_last_sync_ts` (時刻同期成功時刻)
- 既存 `eedscan_pan_channel_energy` ヒストグラム (spec 010) との相関を Grafana で 1 週間観測

## Non-Scope

- NextDrive クラウドの再開 / 代替クラウド連携 — 完全停止のみが目的
- 全 `/system/etc/init/` 一覧化や監査 — 既知 7 デーモンに限定
- DNS resolver の差し替え (例: dnsmasq → systemd-resolved) — 別問題
- `wpa_supplicant.conf` の P2P AP 永続停止 (`9b327a3`) — spec 008 ap-toggle と方針衝突するため本 spec から除外

## User Scenarios *(mandatory)*

### Primary User Story

オペレータが `bash production_tool/production_tool` を実機で実行すると、 NextDrive クラウド系 7 デーモンが停止 + 永続無効化され、 tlsdate が Google など到達可能なホストに向くようになる。 再起動後も状態が保たれる。 1 週間運用後、 Grafana の `eedscan_pan_channel_energy` ヒストグラムを比較すると bimodal のうるさい側 (188-202) の発生頻度が減少する。

### Acceptance Scenarios

1. **Given** インストーラ未実行の Cube J1、 **When** `production_tool/production_tool` を実行、 **Then** 7 クラウドデーモンの state が `stopped`、 `/system/etc/init/<daemon>.rc` に `disabled` 付与
2. **Given** インストーラ後の再起動、 **When** `getprop init.svc.sessiond` (等) 確認、 **Then** `disabled` で起動しない
3. **Given** インストーラ未実行 + `DISABLE_CLOUD=0`、 **When** インストーラ実行、 **Then** クラウドデーモン処理を skip、 ログに「DISABLE_CLOUD=0, skipping cloud daemon quiesce」
4. **Given** tlsdate が NextDrive ホストを引けない、 **When** `REPOINT_TLSDATE=1` (default) でインストーラ実行、 **Then** `tlsdated_timesync.rc` 配置後、 次回起動で `www.google.com` への同期成功
5. **Given** インストーラ失敗 (途中で `cp` エラー等)、 **When** rollback パスが走る、 **Then** バックアップから元の `.rc` を復元、 デーモン状態を元に戻す

### Key Entities

- **`cloud_disabled/<daemon>.rc`**: 7 ファイル、 各デーモンの init 定義に `disabled` を追加した上書き版
- **`tlsdated_timesync.rc`**: 公開ホスト向け tlsdate 起動定義
- **`DISABLE_CLOUD`** / **`REPOINT_TLSDATE`**: インストーラの環境変数 escape hatch (default both `1`)
- **`production_tool/production_tool`** : インストーラ本体 (改修)

## Edge Cases

- 既に `disabled` 状態のデーモン: skip、 ログ INFO で「already disabled」
- バックアップディレクトリが既に存在: 上書きせず timestamp 付きで別名保存
- tlsdate コマンド自体が存在しない: skip、 ログ WARN「tlsdate not installed」
- インストーラ実行中に SIGTERM: ロールバックを走らせて clean に終わる (set -e + trap)
- 公開ホスト (`www.google.com`) も到達不能 (本体 LAN が完全オフライン): tlsdate は失敗するが bridge は時刻なしでも動作継続 (spec 011 timeout は monotonic clock 利用)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `production_tool/cloud_disabled/` に 7 デーモン分の `.rc` を配置する
- **FR-002**: インストーラは `DISABLE_CLOUD=1` (default) のとき 7 デーモンを stop + `disabled` 化する
- **FR-003**: インストーラは `DISABLE_CLOUD=0` のときクラウド処理を完全 skip する (既存挙動互換)
- **FR-004**: インストーラは tlsdate を `REPOINT_TLSDATE=1` (default) のとき公開ホストに向け直す
- **FR-005**: 全変更はバックアップを取り、 任意のステップでの失敗時にロールバック可能とする
- **FR-006**: インストーラのログは構造化 (`[15/N] Disabling sessiond ... ok`) で運用が追跡できる
- **FR-007**: README に新環境変数と効果を 1 節追記する

### Key Entities

- 上記 Scope A/B 参照

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: インストーラ実行後、 `ps | grep -E "sessiond|fms|NDCloud|rds|iijschedule|transman"` で 0 件
- **SC-002**: 再起動後も同上で 0 件 (永続無効化の検証)
- **SC-003**: 1 週間運用で `eedscan_pan_channel_energy` ヒストグラムの bimodal うるさい側 (190 以上) の出現割合が baseline (spec 010 観測値) から **30% 以上減少** する。 減少しない場合は DNS storm はノイズ寄与源ではなかったと結論できる (=spec 012 noise-adaptive-skip が正しい対策)
- **SC-004**: tlsdate のログ `/data/log/tlsdate.log` (或いは equivalent) に `success` エントリが日次以上で残る
- **SC-005**: 既存テスト (pytest + 整合性 test) が全件 pass する

## Assumptions

- Cube J1 の `init` システムは Android init で、 `.rc` ファイル上書きで service 定義を変えられる
- `/system/etc/init/` が rw マウントされている or remount-rw できる
- 7 デーモン以外のクラウド連携 (将来追加されたもの) は存在しない
- 公開ホスト (`www.google.com`) への HTTPS は当該 LAN から到達可能
