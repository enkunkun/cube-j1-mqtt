# Feature Specification: Embedded Admin Web UI

**Feature Branch**: `003-cubej-manager`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "Web UI で各種設定がいじれる、ついでにアップデートパッケージを Web UI に付けて ADB 不要にする"

## Background

bridge は現在 MQTT publish までは完全に自動化されているが、設定変更（B ルート ID、Wi-Fi、MQTT 接続情報、ログレベル）と bridge コード更新は USB を抜き差ししないとできない。Cube J1 は屋内コンセント奥に置かれることが多く、頻繁な USB 抜き差しは現実的でない。

本 spec は bridge プロセスに**軽量 HTTP サーバを同居**させ、LAN 内のブラウザから:

- 現在の `config.json` を編集できる
- `wpa_supplicant.conf` の Wi-Fi 認証情報を更新できる
- 新しい `mqtt_bridge.py` をアップロードして反映できる
- bridge プロセスや Wi-SUN 接続を手動で再起動できる

Constitution VI に従い、機能は既定で **無効**、有効化は `config.json` のキー切替で行う。LAN 内・Basic Auth 必須。

## Clarifications

### Session 2026-06-19

- Q: アーキ方針（Cube J1 拡張 or lab-ub01 中継）→ A: bridge プロセスに同居（Constitution VI 追加）
- Q: 編集対象 → A: config.json + wpa_supplicant.conf + mqtt_bridge.py アップデート（3 つすべて）
- Q: 認証 → A: 組み込み（Basic Auth、stdlib のみで実装）
- Q: 実装手段 → A: omakase（主 agent 判断 = BaseHTTPServer + 埋め込み HTML）
- Q: 既定で有効/無効 → A: 既定で無効、`admin_ui_enabled` で明示有効化（リスク管理）

## User Scenarios & Testing *(mandatory)*

### User Story 1 - LAN 内のブラウザから設定を見て編集する (Priority: P1)

運用者は Mac のブラウザで `http://<cube_j1_ip>:8080/` を開き、Basic Auth でログインして現在の `config.json` の中身が表示される。値を編集して「Save」を押すと `/data/local/config.json` に書き込まれ、3 秒以内に成功通知が表示される。設定は次回の bridge 再起動から反映される（または「再起動」ボタンで即時反映）。

**Why this priority**: USB 抜き差しを完全に置き換える主目的。これだけ動けば運用上 80% の改善になる。

**Independent Test**: bridge を起動して `config.json` を編集 → ブラウザで値を変える → `/data/local/config.json` の中身が更新されている。

**Acceptance Scenarios**:

1. **Given** `admin_ui_enabled: true` で bridge 起動後, **When** ブラウザで `:8080/` を開く, **Then** Basic Auth promp が表示される
2. **Given** 正しい認証情報を入力, **When** TOP ページが開く, **Then** `config.json` の全フィールド（既存 9 個 + 追加 3 個）が表示される
3. **Given** `log_level` を `debug` に変更して Save, **When** `/data/local/config.json` をローカル確認する, **Then** 新しい値が atomic に書き込まれている
4. **Given** 不正な値（`mqtt_port` に文字列）で Save, **When** API が応答する, **Then** 400 と検証エラーメッセージが返る

### User Story 2 - 新しい bridge コードをアップロード (Priority: P2)

運用者がローカルで `mqtt_bridge.py` を編集して上流の bug fix を反映したくなったら、ブラウザの「Update Bridge」フォームでローカル `mqtt_bridge.py` を選んでアップロードする。サーバ側で Python の `py_compile` で syntax check し、OK なら `/data/local/mqtt_bridge.py` を上書き、`stop/start mqtt_ha_bridge` で再起動する。失敗時はファイル書き換えしない。

**Why this priority**: ADB セットアップ（adb インストール + LAN 上で接続）の手間を省く。ブラウザ 1 つで完結する。

**Independent Test**: わざと syntax error を含む `.py` をアップロード → サーバが 400 + エラー文を返す → 既存の `/data/local/mqtt_bridge.py` は変更されていない。正しい更新版で再試行 → bridge プロセスが再起動して新版で稼働する。

**Acceptance Scenarios**:

1. **Given** 100KB の `mqtt_bridge.py` をアップロード, **When** サーバが受信, **Then** 200 + 「Update succeeded」
2. **Given** syntax error を含む `.py`, **When** アップロード, **Then** 400 + Python の SyntaxError 行番号付き
3. **Given** 200KB のファイル, **When** アップロード, **Then** 413 「File too large」
4. **Given** `.py` 以外の拡張子, **When** アップロード, **Then** 400 「Only .py files are accepted」

### User Story 3 - Wi-Fi 設定の更新 (Priority: P3)

引っ越しや AP 交換で Wi-Fi 認証情報を変える必要がある。ブラウザの「Wi-Fi」タブで SSID と PSK を入力して Save すると、`/data/misc/wifi/wpa_supplicant.conf` が atomic 書き換えされ、`wpa_cli reconfigure` が走って 30 秒以内に新 AP に接続する。

**Why this priority**: 引っ越し等の頻度が低いが、Wi-Fi が切れている状態でこの操作はできない（Web UI に到達できないので）。LAN を維持したまま AP 設定を変える運用に限定される。

**Independent Test**: AP 側で新 SSID を作成 → ブラウザで Wi-Fi 設定を変更 → `wpa_cli status` で新 SSID に associated。

**Acceptance Scenarios**:

1. **Given** 有効な SSID/PSK で Save, **When** wpa_supplicant.conf を確認, **Then** atomic 書き換えされていて `wpa_cli reconfigure` のログに `OK` が含まれる
2. **Given** 空の SSID, **When** Save, **Then** 400 「SSID is required」

### User Story 4 - 手動再起動と診断 (Priority: P3)

「bridge が反応していない」と感じたとき、ブラウザの「Diagnostics」タブから:

- bridge プロセス再起動（`stop/start mqtt_ha_bridge`）
- Wi-SUN 再接続を強制（`SKTERM` → `SKJOIN` 再実行）
- 直近 100 行のローカルログを表示
- 現在の `DiagState` snapshot を JSON で表示

ができる。

**Independent Test**: 再起動ボタンを押すと `pgrep mqtt_bridge.py` の PID が変わる。

**Acceptance Scenarios**:

1. **Given** Diagnostics ページ, **When** `Restart bridge` を押す, **Then** 5 秒以内に bridge プロセスの PID が変わる
2. **Given** ログタブを開く, **When** 「直近 100 行」を要求, **Then** JSON Lines が返される

---

### Edge Cases

- Wi-Fi 設定変更後、Cube J1 が新 AP に繋げず Web UI に再到達不能 → 復旧は USB 経由のみ。spec 上「Wi-Fi 変更はリスク表示する」を要件化
- `admin_ui_enabled: false` の起動 → HTTP サーバを起動しない。`config.json` 編集は USB 経由のみ
- HTTP サーバが起動失敗（port 8080 既に使用中など）→ stderr にエラーを残し、bridge 本体は継続
- 同時に 2 つの PUT /api/config が来た → `threading.Lock` で直列化。後の更新が勝つ
- アップロード中に接続切断 → temp ファイルを cleanup（atexit）
- 認証情報未設定（`admin_user`/`admin_password` 空）→ admin UI を起動しない（フェイルセーフ）

## Requirements *(mandatory)*

### Functional Requirements

#### HTTP サーバ起動

- **FR-001**: bridge 起動時、`config.json` の `admin_ui_enabled` が `true` の場合に限り HTTP サーバを起動する。既定は `false`
- **FR-002**: HTTP サーバは `config.json` の `admin_ui_port`（既定 8080）で listen する
- **FR-003**: HTTP サーバは別 thread（`threading.Thread(daemon=True)`）で動作し、メインループは触らない
- **FR-004**: ポート使用中・thread 起動失敗時は stderr/JsonLogger に error を残し、bridge 本体は通常通り続行する（Constitution IV）

#### 認証

- **FR-005**: すべての `/api/*` と `/` パスは Basic Auth 必須。`admin_user` / `admin_password` を `config.json` で指定する
- **FR-006**: 認証失敗時は 401 + `WWW-Authenticate: Basic realm="cubej"` を返す
- **FR-007**: `admin_user` または `admin_password` が空文字列のときは Web UI を起動しない（FR-001 と独立した安全網）

#### API 仕様

- **FR-008**: `GET /` は埋め込み HTML を返す（単一 HTML、JS 軽量）
- **FR-009**: `GET /api/config` は `/data/local/config.json` を読んで JSON で返す。`admin_password` は `"***"` でマスク
- **FR-010**: `PUT /api/config` は JSON body を受け取り、type 検証してから atomic 書き換えする
  - `mqtt_port` は integer
  - `poll_interval`, `log_max_bytes`, `log_backup_count`, `admin_ui_port` は positive integer
  - `log_level` は `debug`/`info`/`warn`/`error` のいずれか
  - `admin_password` フィールドが `"***"` のままなら既存値を維持
- **FR-011**: `PUT /api/wifi` は `{"ssid": "...", "psk": "..."}` を受け取り、`/data/misc/wifi/wpa_supplicant.conf` を atomic 書き換えして `wpa_cli -p /data/misc/wifi/sockets -i wlan0 reconfigure` を実行する
- **FR-012**: `POST /api/update` は `multipart/form-data` で `.py` ファイルを受け取り、`py_compile.compile()` で syntax check の後、`/data/local/mqtt_bridge.py` を atomic 書き換え + `stop/start mqtt_ha_bridge` で再起動する。size limit 100KB
- **FR-013**: `POST /api/restart` は引数なしで `stop/start mqtt_ha_bridge` を発火する
- **FR-014**: `GET /api/diag` は現在の `DiagState.snapshot()` を JSON で返す
- **FR-015**: `GET /api/log?lines=N` は直近 N 行（既定 100、上限 1000）の `/data/local/mqtt_bridge.log` を JSON Lines として返す

#### Atomic 書き込み

- **FR-016**: 設定ファイル（`config.json`, `wpa_supplicant.conf`, `mqtt_bridge.py`）の書き込みはすべて temp ファイル経由（`/data/local/.tmp.XXXX`）→ `os.rename` で atomic に行う。書き込み失敗時に既存ファイルは温存される

#### 排他制御

- **FR-017**: 同時 2 件以上の書き込み要求は `threading.Lock` で直列化される。後勝ち

#### `config.json` 拡張

- **FR-018**: 新規キー `admin_ui_enabled` (bool, default false), `admin_ui_port` (int, default 8080), `admin_user` (str, default ""), `admin_password` (str, default "") は **すべて optional**（FR-018 → 既存 12 キー＋新規 4 キー = 16 キーの構成）

### Key Entities

- **AdminConfig**: Web UI 関連の設定。`enabled`, `port`, `user`, `password` を持つ。`admin_user`/`admin_password` が空なら `enabled=False` 扱い
- **AdminHandler**: `BaseHTTPRequestHandler` の subclass。`do_GET` / `do_PUT` / `do_POST` を実装。`_authenticate()` メソッドで Basic Auth 検証
- **AdminServer**: `HTTPServer` のラッパー。`threading.Thread` で `serve_forever()` を起動し、shutdown handler も持つ
- **AtomicWriter**: `write(path, data)` を temp → rename で実行するユーティリティ

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cube J1 起動から 90 秒以内に `http://<cube_j1_ip>:8080/` が HTTP 200 を返す（admin_ui_enabled=true の場合）
- **SC-002**: `PUT /api/config` の処理レイテンシ p95 は 1 秒以内（atomic write 含む）
- **SC-003**: `POST /api/update` の処理レイテンシ p95 は 10 秒以内（100KB アップロード + syntax check + 再起動含む）
- **SC-004**: HTTP サーバ起動による計測パスの median レイテンシ悪化は SC-004 (001 spec) の 10% 以内に収まる（既存ベンチを再実行）
- **SC-005**: HTTP サーバが落ちても bridge 本体は継続稼働する（fault injection で確認）
- **SC-006**: 認証なしリクエストは 401 を返し、`/api/*` の処理を呼び出さない
- **SC-007**: 100 サンプルの `PUT /api/config` で 100% atomic（一度も `config.json` が空 or 破損状態にならない）

## Assumptions

- Cube J1 は LAN 内（192.168.1.0/24）で `cube_j1_ip` で到達可能
- LAN は信頼できる前提（Basic Auth のみで HTTPS なし）
- ブラウザは Chrome / Safari / Firefox の最新版を想定（JS / Fetch API 利用可能）
- HTTP サーバは port 8080 を使用、他サービスと衝突しない
- `wpa_cli` バイナリは Cube J1 の `/data/misc/wifi/sockets` 経由でアクセス可能（production_tool スクリプトと同じ機構）
- `init` 経由のサービス管理 (`start mqtt_ha_bridge` / `stop mqtt_ha_bridge`) は Android 標準コマンドで利用可能
