# HTTP API Contract

すべての `/api/*` と `/` は Basic Auth 必須。`Authorization` ヘッダがない、不正、認証失敗の場合は:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="cubej"
Content-Type: application/json

{"error": "Authentication required"}
```

エラーレスポンスは原則 `{"error": "<reason>"}` 形式の JSON。

---

## GET /

埋め込み HTML を返す。LAN 内 Basic Auth でアクセス制限される運用想定。

| 項目 | 値 |
|---|---|
| Method | GET |
| Auth | Basic Auth |
| Response | `200 OK` + `Content-Type: text/html; charset=utf-8` + 埋め込み HTML body |

---

## GET /api/config

現在の `config.json` を返す。`admin_password` はマスク。

### Response 200

```json
{
  "br_id": "00000099021800000000000001CB485E",
  "br_pwd": "XCKA98UY1018",
  "mqtt_host": "192.168.1.151",
  "mqtt_port": 1883,
  "mqtt_user": "cubej1",
  "mqtt_pass": "WtGP6gKwTvXmpaCIHASONbCVqMg7weC",
  "device_id": "cubej1",
  "serial_port": "/dev/ttyS1",
  "poll_interval": 60,
  "log_level": "info",
  "log_max_bytes": 1048576,
  "log_backup_count": 3,
  "admin_ui_enabled": true,
  "admin_ui_port": 8080,
  "admin_user": "admin",
  "admin_password": "***"
}
```

---

## PUT /api/config

`config.json` の部分更新。送信したキーのみ更新、未指定は維持。`admin_password` を `"***"` のままにすると既存値を維持。

### Request body

```json
{
  "log_level": "debug",
  "poll_interval": 30
}
```

### Response 200

```json
{"status": "ok"}
```

### Response 400 (validation error)

```json
{"error": "mqtt_port must be a positive integer"}
```

### 副作用

- `/data/local/config.json` を atomic 書き換え
- 既存 bridge プロセスは引き続き旧値で動く
- 反映には `POST /api/restart` または bridge プロセス再起動が必要

---

## PUT /api/wifi

Wi-Fi 認証情報を更新。

### Request body

```json
{"ssid": "AC9120-hanpen1", "psk": "EnRoomEdition"}
```

### Response 200

```json
{"status": "ok", "wpa_cli_output": "OK"}
```

### Response 400 (validation error)

- `ssid` 空 → `{"error": "SSID is required"}`
- `psk` 文字数違反 → `{"error": "PSK must be 8-63 characters"}`

### 副作用

- `/data/misc/wifi/wpa_supplicant.conf` を atomic 書き換え（既存の構造維持、SSID/PSK のみ書き換え）
- `wpa_cli -p /data/misc/wifi/sockets -i wlan0 reconfigure` 実行
- 失敗すると Cube J1 が LAN から見えなくなる可能性 → UI 側でリスク警告

---

## POST /api/update

新しい `mqtt_bridge.py` をアップロードして反映。

### Request

```http
POST /api/update HTTP/1.1
Content-Type: multipart/form-data; boundary=----xxx

------xxx
Content-Disposition: form-data; name="update_file"; filename="mqtt_bridge.py"
Content-Type: text/x-python

<file content>
------xxx--
```

### Response 200

```json
{"status": "ok", "restarting": true}
```

レスポンス送信後、200ms 遅延で `stop/start mqtt_ha_bridge` が走る。

### Response 400 (syntax error)

```json
{"error": "SyntaxError: invalid syntax (line 42, col 5)"}
```

### Response 413 (size limit)

```json
{"error": "File too large (max 100KB)"}
```

### Response 415 (unsupported file type)

```json
{"error": "Only .py files are accepted"}
```

### 副作用

- syntax check 成功時のみ `/data/local/mqtt_bridge.py` を atomic 置換
- 200ms 遅延で bridge プロセス restart

---

## POST /api/restart

bridge プロセスを `stop/start` する。

### Response 200

```json
{"status": "restarting"}
```

レスポンス送信後、200ms 遅延で `stop mqtt_ha_bridge` → `start mqtt_ha_bridge` が走る。

---

## GET /api/diag

現在の `DiagState.snapshot()` を返す。

### Response 200

```json
{
  "last_poll_success_ts": "2026-06-19T15:12:34Z",
  "last_poll_failure_ts": "",
  "lqi": 168,
  "pan_channel": 57,
  "scan_retries_total": 15,
  "wisun_reconnects_total": 0,
  "mqtt_reconnects_total": 0,
  "erxudp_timeouts_total": 0,
  "uptime_seconds": 12345,
  "version": "1.0.0+09e6f54"
}
```

`None` 値は省略（不明）。

---

## GET /api/log?lines=N

`/data/local/mqtt_bridge.log` の末尾 `N` 行を JSON Lines として返す。`N` 既定 100、上限 1000。

### Response 200

```http
HTTP/1.1 200 OK
Content-Type: application/x-ndjson; charset=utf-8

{"ts":"2026-06-19T14:15:00Z","level":"info","event":"poll_success","context":{...}}
{"ts":"2026-06-19T14:14:00Z","level":"info","event":"poll_success","context":{...}}
...
```

---

## エラーステータスコード一覧

| Code | 意味 |
|---|---|
| 200 | 成功 |
| 400 | validation error / syntax error / 不正な body |
| 401 | 認証失敗 |
| 404 | 未定義パス |
| 405 | サポートしないメソッド |
| 413 | アップロードサイズ超過 |
| 415 | サポートしないファイル種類 |
| 500 | サーバ内部エラー（ハンドラ例外） |

## CORS

ローカル運用のみのためサポートしない。`Access-Control-Allow-Origin` ヘッダは返さない。

## CSRF

Basic Auth 認証のため、CSRF token は不要（Basic Auth は credentials が毎リクエスト送られるため自動的に保護）。ただし副作用 API (PUT/POST) は `Content-Type` が `application/json` または `multipart/form-data` で送られることが期待される（XSS 防御の補強）。
