# Phase 1 Data Model: Bridge Observability

副作用を持たない内部エンティティの定義。これらは Python 2.7 stdlib のみで実装し、ユニットテストで TDD する。

## DiagState

bridge 起動中の診断値を保持する単純なデータ構造。シングルスレッド main loop からのみ更新される（並行性は考慮しない）。

### Attributes

| 名前 | 型 | 初期値 | 説明 |
|---|---|---|---|
| `start_time` | `float` (epoch) | `time.time()` at construction | プロセス起動時刻。`uptime_seconds` 計算に使う |
| `last_poll_success_ts` | `Optional[float]` | `None` | 最終ポーリング成功時刻（epoch）。`None` の間は publish しない |
| `last_poll_failure_ts` | `Optional[float]` | `None` | 直近のポーリング失敗時刻（epoch） |
| `lqi` | `Optional[int]` | `None` | 直近 SKSCAN で選択された PAN の LQI（10進整数。SKSCAN は hex 文字列で返すので構築時にデコード） |
| `pan_channel` | `Optional[int]` | `None` | 接続中のチャンネル（hex 文字列を 10 進整数化） |
| `scan_retries_total` | `int` | 0 | 起動以降の累計 SKSCAN リトライ回数（`SKSCAN no PAN found, retrying with longer duration` 発生回数） |
| `wisun_reconnects_total` | `int` | 0 | main loop 例外後の `wisun_connect()` 再呼び出し成功回数 |
| `mqtt_reconnects_total` | `int` | 0 | `MQTTClient._reconnect()` の成功回数 |
| `erxudp_timeouts_total` | `int` | 0 | `read_erxudp()` が `None` を返した回数 |
| `version` | `str` | `bridge_version()` 関数の戻り値 | `<SemVer>+<git_hash>` 形式 |

### Methods

すべて副作用は内部 attribute の更新のみ。例外は投げない。

- `on_poll_success(now: float)`: `last_poll_success_ts = now`
- `on_poll_failure(now: float)`: `last_poll_failure_ts = now`
- `on_erxudp_timeout()`: `erxudp_timeouts_total += 1`
- `on_scan_retry()`: `scan_retries_total += 1`
- `on_wisun_joined(pan_info: dict)`: `lqi = int(pan_info["LQI"], 16)`, `pan_channel = int(pan_info["Channel"], 16)`
- `on_wisun_reconnect()`: `wisun_reconnects_total += 1`
- `on_mqtt_reconnect()`: `mqtt_reconnects_total += 1`
- `snapshot(now: float) -> dict`: 現在の attribute を MQTT publish 用 dict に変換して返す。`None` attribute は dict から除外（publish しない）

### snapshot() 出力例

```python
{
    "last_poll_success_ts": "2026-06-19T12:34:56Z",
    "last_poll_failure_ts": "2026-06-19T12:30:00Z",
    "lqi": 192,
    "pan_channel": 33,
    "scan_retries_total": 5,
    "wisun_reconnects_total": 0,
    "mqtt_reconnects_total": 2,
    "erxudp_timeouts_total": 1,
    "uptime_seconds": 12345,
    "version": "1.0.0+abc1234",
}
```

### 不変条件

- カウンター系は単調非減少（`on_*()` メソッドはデクリメントしない）
- `uptime_seconds = int(now - start_time)`、負値にならない（now が start_time より前に設定された場合は 0）
- `snapshot()` は呼び出しごとに同じ key の組を返す（dict 順序は固定）

## LogEvent

JSON Lines として出力される 1 イベント。`JsonLogger` が `logging.Formatter` 経由で生成する。

### Fields

| 名前 | 型 | 必須 | 説明 |
|---|---|---|---|
| `ts` | `str` (ISO 8601 UTC) | YES | イベント発生時刻 |
| `level` | `str` (`debug`/`info`/`warn`/`error`) | YES | ログレベル |
| `event` | `str` | YES | イベント名（FR-010 のホワイトリスト + 任意） |
| `msg` | `str` | NO | 補足説明（既存ログメッセージとの互換維持用） |
| `context` | `dict` | NO | 任意の追加 context フィールド |

### Examples

```json
{"ts":"2026-06-19T12:00:00Z","level":"info","event":"bridge_start","context":{"device_id":"cubej1","version":"1.0.0+abc1234"}}
{"ts":"2026-06-19T12:00:05Z","level":"info","event":"mqtt_connected","context":{"host":"mqtt.lab-ub01.home.arpa","port":1883}}
{"ts":"2026-06-19T12:00:30Z","level":"warn","event":"scan_retry","context":{"duration":5}}
{"ts":"2026-06-19T12:01:00Z","level":"info","event":"wisun_joined","context":{"channel":"21","pan_id":"8888","mac":"001D129012345678","ipv6":"fe80::21d:1290:1234:5678"}}
{"ts":"2026-06-19T12:01:30Z","level":"info","event":"poll_success","context":{"power_w":340,"energy_forward_kwh":12345.678,"current_r_a":1.4,"current_t_a":1.5}}
{"ts":"2026-06-19T12:02:00Z","level":"error","event":"erxudp_timeout","msg":"No ERXUDP response (timeout)"}
```

### 不変条件

- `ts` は UTC、`Z` サフィックス、マイクロ秒なし
- `level` は 4 値のみ
- `event` は ASCII の英小文字 + `_`
- 各行は valid JSON で改行 1 つで終わる

## HADiscoveryPayload

HA Auto-Discovery `config` トピックに retain=true で publish する dict。計測センサーと診断センサーで構造は共通。

### Schema

```python
{
    "name": str,                          # 例: "Last Poll Success"
    "unique_id": str,                     # 例: "cubej1_last_poll_success_ts"
    "state_topic": str,                   # 例: "cubej/cubej1/diag/last_poll_success_ts"
    "device_class": str,                  # optional, e.g. "timestamp"|"power"|"current"|"energy"
    "state_class": str,                   # optional, e.g. "measurement"|"total_increasing"
    "unit_of_measurement": str,           # optional
    "entity_category": str,               # optional, "diagnostic" for diag sensors
    "device": {                           # 必須、計測センサーと完全一致
        "identifiers": [device_id],
        "name": "Cube J1 Smart Meter",
        "model": "Cube J1",
        "manufacturer": "NextDrive",
    },
}
```

### DIAG_SENSOR_DEFS

`mqtt_bridge.py` 内に既存 `SENSOR_DEFS` と並べて定義。

```python
# (key, name, unit, device_class, state_class, entity_category)
DIAG_SENSOR_DEFS = [
    ("last_poll_success_ts",   "Last Poll Success",   None, "timestamp", None,                "diagnostic"),
    ("last_poll_failure_ts",   "Last Poll Failure",   None, "timestamp", None,                "diagnostic"),
    ("lqi",                    "LQI",                 None, None,        "measurement",       "diagnostic"),
    ("pan_channel",            "PAN Channel",         None, None,        "measurement",       "diagnostic"),
    ("scan_retries_total",     "Scan Retries",        None, None,        "total_increasing",  "diagnostic"),
    ("wisun_reconnects_total", "Wi-SUN Reconnects",   None, None,        "total_increasing",  "diagnostic"),
    ("mqtt_reconnects_total",  "MQTT Reconnects",     None, None,        "total_increasing",  "diagnostic"),
    ("erxudp_timeouts_total",  "ERXUDP Timeouts",     None, None,        "total_increasing",  "diagnostic"),
    ("uptime_seconds",         "Uptime",              "s",  None,        "measurement",       "diagnostic"),
    ("version",                "Bridge Version",      None, None,        None,                "diagnostic"),
]
```

### 不変条件

- `device.identifiers` は計測・診断すべてのセンサーで完全一致
- `unique_id` は device_id + sensor key の組で衝突しない
- `state_topic` は spec の topic 規約に従う（`cubej/<id>/<key>` or `cubej/<id>/diag/<key>`）

## BridgeVersion

`bridge_version() -> str` 関数で組み立てる。純粋関数（モジュール定数 2 個に依存）。

```python
BRIDGE_SEMVER = "1.0.0"      # 手動更新
BRIDGE_GIT_HASH = "unknown"  # scripts/embed_git_hash.sh が書き換える

def bridge_version():
    return "{}+{}".format(BRIDGE_SEMVER, BRIDGE_GIT_HASH)
```

### 不変条件

- `BRIDGE_SEMVER` は SemVer 文字列（regex `^\d+\.\d+\.\d+$`）
- `BRIDGE_GIT_HASH` は `[a-f0-9]{4,40}` または `unknown`
