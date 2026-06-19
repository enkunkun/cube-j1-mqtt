# Feature Specification: ADB Hot-Reload for mqtt_bridge.py

**Feature Branch**: `004-adb-update`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "ADB push 1 コマンドで Cube J1 の mqtt_bridge.py を更新し、init service を再起動して開発ループを高速化する"

## Background

Cube J1 上の `/data/local/mqtt_bridge.py` を更新する手段は現在 USB 起動メディアの作り直し（`scripts/prepare_usb.sh` → USB を抜いて Cube J1 に差し戻す）しかない。Wi-SUN ペアリングや MQTT 再接続の挙動を試す iteration コストが大きく、bridge 本体（Python スクリプト）の小さな改修を確かめるたびに USB の抜き差しが発生する。

Cube J1 は LAN 上で ADB port 5555/tcp を有効化しており、現在 `192.168.1.103:5555` に接続できる。bridge は Android init service として `service mqtt_ha_bridge /usr/bin/python /data/local/mqtt_bridge.py`（`production_tool/mqtt_ha_bridge.rc`）で動いており、`start mqtt_ha_bridge` / `stop mqtt_ha_bridge` で制御できる。

本 spec は **mqtt_bridge.py 本体のみ** を ADB push で差し替え、`mqtt_ha_bridge` サービスを再起動するスクリプトを定義する。rc ファイル・config.json・wpa_supplicant.conf 等の更新は対象外で、引き続き USB 経由（`prepare_usb.sh`）を使う。

## Scope

- ローカルの `production_tool/mqtt_bridge.py` を Cube J1 の `/data/local/mqtt_bridge.py` に ADB push する
- `mqtt_ha_bridge` init service を停止→1 秒待機→再起動する
- 再起動後 10 秒以内に bridge プロセスが立ち上がっていることを `pgrep` で簡易確認する
- BRIDGE_GIT_HASH をリポクリーンに保つ（`prepare_usb.sh` と同流儀で push 直前に埋め込み、push 直後に "unknown" に戻す）

## Non-Scope

- `mqtt_ha_bridge.rc` 自体の更新（init service 定義の変更）：これは init の再読み込みが必要で USB 経由が必要
- `config.json` / `wpa_supplicant.conf` / B-route 認証情報の更新：これも USB 経由
- `prepare_usb.sh` との置き換え：初回セットアップは引き続き USB
- ADB 認証ダイアログのハンドリング：Cube J1 はすでに ADB 信頼済みの前提
- adb のインストール：ローカルに `adb` が無い場合はガード（エラー終了）するが、自動インストールはしない

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 開発者が 1 コマンドで bridge コードを差し替えて挙動を確認できる (Priority: P1)

開発者は `production_tool/mqtt_bridge.py` を編集した直後に `scripts/adb_push_update.sh` を実行するだけで、Cube J1 上の bridge が新コードで動き出す。USB を取り外して PC に挿し、`prepare_usb.sh` を回して、USB を抜いて Cube J1 に挿し戻す、という物理的な手順が要らなくなる。

**Why this priority**: 観測されている開発の最大ボトルネック。bridge 改修 1 回あたり数分の物理操作が消える。

**Independent Test**: ローカルで `production_tool/mqtt_bridge.py` のログメッセージを 1 行変え、`scripts/adb_push_update.sh` を実行し、Cube J1 の `/data/local/mqtt_bridge.log` または diag MQTT に変更後の文字列が現れることを確認する。

**Acceptance Scenarios**:

1. **Given** Cube J1 が LAN 上で reachable で ADB port 5555 が開いている, **When** 開発者が `scripts/adb_push_update.sh` を実行する, **Then** スクリプトは 30 秒以内に正常終了し、Cube J1 上の `/data/local/mqtt_bridge.py` がローカルファイルと一致し、`mqtt_ha_bridge` プロセスが PID を持って動いている
2. **Given** ローカルの `production_tool/mqtt_bridge.py` が変更されていない作業コピー, **When** push を実行する, **Then** push 完了後に `BRIDGE_GIT_HASH` は `"unknown"` に戻り、`git diff production_tool/mqtt_bridge.py` は何も出さない
3. **Given** Cube J1 の IP がデフォルトの `192.168.1.103` と異なる, **When** `scripts/adb_push_update.sh 192.168.1.42` を実行する, **Then** 指定 IP に対して push と再起動が走る

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: スクリプトは Cube J1 の IP を位置引数 1 個で受け取る。引数省略時は `192.168.1.103` をデフォルトとする
- **FR-002**: `command -v adb` で adb の存在を確認し、無ければ終了コード 2 で `adb not found` を標準エラーに出して終了する
- **FR-003**: `adb connect <ip>:5555` で接続を試み、接続失敗（出力に `unable to connect` / `failed to connect` を含む、または接続後の `adb -s <ip>:5555 get-state` が `device` を返さない）なら終了コード 1 でエラー終了する
- **FR-004**: push の直前に `scripts/embed_git_hash.sh` を実行し、`BRIDGE_GIT_HASH` を現在の git short hash に埋め込む。push の直後（再起動より前）に `sed` で `BRIDGE_GIT_HASH` を `"unknown"` に戻し、作業コピーをクリーンに保つ（`prepare_usb.sh` の流儀と一致）
- **FR-005**: ローカルの `production_tool/mqtt_bridge.py` を `adb -s <ip>:5555 push` で Cube J1 の `/data/local/mqtt_bridge.py` に上書きコピーする
- **FR-006**: push 後、`adb -s <ip>:5555 shell stop mqtt_ha_bridge` → `sleep 1` → `adb -s <ip>:5555 shell start mqtt_ha_bridge` の順で init service を再起動する
- **FR-007**: 再起動の 10 秒後に `adb -s <ip>:5555 shell pgrep -f mqtt_bridge.py` を実行し、PID（数字 1 行以上）が返らなければ終了コード 1 でエラー終了する
- **FR-008**: 正常終了時に `Updated bridge at <ip>:5555 (version <semver>+<hash>)` を標準出力に 1 行表示する。`<semver>` は `BRIDGE_SEMVER`、`<hash>` は push に使った git short hash
- **FR-009**: 終了時（成功・失敗どちらでも、`trap` で）`adb disconnect <ip>:5555` を呼んで切断する

### Key Entities

- **Cube J1 host**: ADB over TCP で接続される対象。IP はデフォルト `192.168.1.103`、port は 5555 固定
- **mqtt_ha_bridge service**: Android init が管理する Python プロセス。`/system/etc/init/mqtt_ha_bridge.rc` で定義済み（このスクリプトは触らない）
- **BRIDGE_GIT_HASH 定数**: `production_tool/mqtt_bridge.py` 内の文字列定数。`prepare_usb.sh` と同様に push 前後で埋め込み・復元する

## Edge Cases

- **Cube J1 が ping できない / ADB ポートが閉じている**: `adb connect` が `unable to connect` を返す → FR-003 によりエラー終了
- **adb がローカルに無い**: `command -v adb` で検出 → FR-002 によりエラー終了
- **push 後の Python スクリプトが syntax error を含む**: `start mqtt_ha_bridge` 直後はプロセスが起動失敗、`pgrep` が 0 件 → FR-007 によりエラー終了。開発者は `adb shell cat /data/local/mqtt_bridge.log` で詳細確認する想定（スクリプトは詳細解析しない）
- **複数 ADB device が接続中**: `-s <ip>:5555` 指定で曖昧性回避（FR-005/006/007 すべて `-s` 付き）
- **push 中の接続切断**: `adb push` 自体が non-zero を返す → `set -euo pipefail` により即終了
- **再起動後の pgrep が長時間応答しない**: スクリプトは `adb shell` のデフォルトタイムアウトに依存（adb 5 系で 5s 程度）。これ以上の細かい制御は本 spec のスコープ外

## Success Criteria *(mandatory)*

- **SC-001**: USB を物理的に触らずに `編集 → スクリプト実行 → bridge 再起動完了` までの所要時間が 30 秒以内（push の転送時間 + `sleep 1` + 再起動確認の 10 秒待ち + adb のオーバーヘッド）
- **SC-002**: 失敗時はゼロ以外の終了コードと、原因が 1 行で読める標準エラー出力を返す（例: `adb not found` / `failed to connect to 192.168.1.103:5555` / `mqtt_ha_bridge did not start within 10s`）
- **SC-003**: スクリプト実行後に `git diff production_tool/mqtt_bridge.py` が空（BRIDGE_GIT_HASH の入れ替えがリポを汚さない）
- **SC-004**: スクリプトは `set -euo pipefail` 下で動き、任意の中間ステップ失敗時に `trap` で `adb disconnect` が走る

## Assumptions

- Cube J1 が LAN 上で reachable で、ADB port 5555 がすでに開いていて、ホスト PC が ADB 信頼済み（初回の `adb connect` で `Allow USB debugging?` ダイアログを通している）
- ローカルマシン（Mac もしくは lab-ub01）に `adb` がインストールされている。無ければ Mac は `brew install android-platform-tools`、Linux は `sudo apt install adb` で導入する
- `production_tool/mqtt_bridge.py` は単独で `/data/local/` に置けば動く（`config.json` 等の他ファイルは更新せず、既存のものを使い回す）
- `mqtt_ha_bridge.rc` は既に `/system/etc/init/` に配置済みで、`start mqtt_ha_bridge` / `stop mqtt_ha_bridge` が効く状態

## Dependencies

- `scripts/embed_git_hash.sh`（既存）
- `production_tool/mqtt_bridge.py`（既存、本 spec では編集しない）
- ホスト側 `adb`（Android Platform Tools 1.0.41 以降、`adb connect <host>:<port>` をサポートするバージョン）
