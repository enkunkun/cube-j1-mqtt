# Phase 1 Data Model: Embedded Admin Web UI

副作用を分離した内部エンティティ。すべて Python 2.7 stdlib のみで実装し、ユニットテストで TDD する。

## AdminConfig

bridge 起動時に `config.json` から組み立てる Web UI 関連設定の immutable な値オブジェクト。

### Attributes

| 名前 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `enabled` | `bool` | `False` | Web UI を起動するか |
| `port` | `int` | `8080` | listen ポート |
| `user` | `str` | `""` | Basic Auth user |
| `password` | `str` | `""` | Basic Auth password (平文保持、API レスポンスではマスク) |

### Methods

- `is_active() -> bool`: `enabled` かつ `user` と `password` が両方非空のとき True。`config.json` の typo 防止フェイルセーフ
- `match_basic_auth(header_value: str) -> bool`: `Authorization: Basic <b64>` 形式のヘッダ値を解析し、`user`/`password` と一致するか判定。比較はタイミング攻撃を避けるため `hmac.compare_digest` を使う

## AtomicWriter

ファイルに atomic に書き込む staticmethod ユーティリティ。

### API

- `AtomicWriter.write_bytes(path: str, data: bytes) -> None`: temp ファイルに書き、`os.fsync` の後 `os.rename` で atomic 置換
- `AtomicWriter.write_json(path: str, obj: dict) -> None`: `json.dumps` してから `write_bytes`

### 不変条件

- 書き込み失敗時、temp ファイルは削除され例外を再 raise する
- 同一 FS 内（同一ディレクトリ）で temp 作成 + rename を行う
- ターゲットファイルは置換中も既存の content を持つ（renameの atomic 性により）

## AdminHandler

`BaseHTTPRequestHandler` の subclass。すべての HTTP メソッドを実装。各メソッドの開始時に `_authenticate()` を呼び、認証失敗なら 401 を即返す。

### Class attributes (server から注入)

- `admin_config: AdminConfig`
- `diag_state_provider: callable() -> DiagState` (late binding 用 lambda)
- `config_path: str` (= `/data/local/config.json`)
- `bridge_path: str` (= `/data/local/mqtt_bridge.py`)
- `wpa_supplicant_path: str` (= `/data/misc/wifi/wpa_supplicant.conf`)
- `log_path: str` (= `/data/local/mqtt_bridge.log`)
- `lock: threading.Lock` (書き込み API の直列化)

### Methods

| メソッド | パス | 動作 |
|---|---|---|
| `do_GET` | `/` | 埋め込み HTML を 200 で返す |
| `do_GET` | `/api/config` | `config.json` を読み、`admin_password` を `"***"` にマスクして返す |
| `do_GET` | `/api/diag` | `diag_state_provider().snapshot(time.time())` を返す |
| `do_GET` | `/api/log?lines=N` | `log_path` の末尾 N 行を JSON Lines で返す |
| `do_PUT` | `/api/config` | body JSON を type 検証、`admin_password` が `"***"` なら現値維持、それ以外は受領 → atomic write |
| `do_PUT` | `/api/wifi` | `{"ssid","psk"}` を受け取り、`wpa_supplicant.conf` を atomic write → `wpa_cli reconfigure` 実行 |
| `do_POST` | `/api/update` | multipart で `.py` 受信、size limit 100KB、`py_compile` で syntax check → atomic write → 200ms 遅延 restart |
| `do_POST` | `/api/restart` | 200ms 遅延で `stop/start mqtt_ha_bridge` |

### Helpers

- `_authenticate() -> bool`: `Authorization` ヘッダから `admin_config.match_basic_auth` で検証
- `_send_json(status: int, payload: dict)`: ヘッダ + body の JSON 出力
- `_send_text(status: int, text: str)`: text/plain 出力
- `_read_json_body() -> dict | None`: `Content-Length` を見て body を読み、`json.loads` 失敗時に None
- `log_message(format, *args)`: BaseHTTPRequestHandler 既定の stderr 出力を抑止し、`LOGGER` に渡す（ログ統合）

### 不変条件

- 全 `/api/*` ハンドラは認証必須
- すべての書き込み API は `lock` で直列化される
- ハンドラ内で発生した未捕捉例外は `do_*` の wrapper で catch して 500 を返し、`LOGGER.error(event="admin_unhandled_error", ...)` を発火

## AdminServer

`HTTPServer` + `Thread` のラッパー。

### Attributes

- `httpd: HTTPServer`
- `thread: threading.Thread` (daemon=True, name="cubej-admin-http")
- `port: int`

### Methods

- `start()`: thread を `httpd.serve_forever` で起動
- `stop()`: `httpd.shutdown()` を呼ぶ（テストでのみ使う）

### Construction helper

`start_admin_server(port, user, password, diag_state_provider, config_path, bridge_path, wpa_supplicant_path, log_path) -> AdminServer`:

1. `AdminConfig` を構築
2. `AdminHandler` の class attr に各 path / config / lock / diag provider を注入
3. `ReusingHTTPServer(("", port), AdminHandler)` を構築
4. `AdminServer` ラッパーを返す
5. `start()` を呼ぶ

## ConfigValidator

`PUT /api/config` の body 検証。pure function 群。

### Functions

- `validate_config_patch(patch: dict, current: dict) -> tuple[dict | None, str | None]`:
  - patch は部分更新（未指定キーは current から保持）
  - 返り値は (merged, None) もしくは (None, error_message)
  - 検証ルール:
    - `mqtt_port`, `poll_interval`, `log_max_bytes`, `log_backup_count`, `admin_ui_port`: positive int
    - `log_level`: `"debug"`, `"info"`, `"warn"`, `"error"` のいずれか
    - `admin_ui_enabled`: bool
    - `admin_password` が `"***"` なら current の値で置き換え
    - 未知キーは通過させる (FR 後方互換)

## WiFiValidator

`PUT /api/wifi` の body 検証。

### Functions

- `validate_wifi_patch(payload: dict) -> tuple[dict | None, str | None]`:
  - `ssid` は非空文字列
  - `psk` は 8〜63 文字 (WPA2-PSK 規格)
  - 返り値は (normalized, None) もしくは (None, error_message)

## LogTailer

`/data/local/mqtt_bridge.log` の末尾 N 行を返す pure function。

### Functions

- `tail_log(path: str, n: int) -> list[str]`:
  - `n` を 1..1000 にクランプ
  - ファイル末尾から逆向きに改行を探索（メモリ効率重視）
  - ファイル未存在時は空リストを返す
  - ローテーション中の race は許容（ベストエフォート）
