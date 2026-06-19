# MQTT Topic Contract

`<device_id>` は `config.json` の `device_id` キーの値（既定 `cubej1`）。
すべての payload は UTF-8 文字列（数値は文字列化されたもの、または JSON オブジェクト）。

## 計測値（既存・変更なし）

| Topic | Payload | retain | QoS | 周期 |
|---|---|---|---|---|
| `cubej/<device_id>/power` | 整数文字列 例 `340` | false | 0 | `poll_interval` 秒（既定 60） |
| `cubej/<device_id>/energy_forward` | 小数文字列 例 `12345.678` | false | 0 | 同上 |
| `cubej/<device_id>/energy_reverse` | 小数文字列 例 `0.000` | false | 0 | 同上 |
| `cubej/<device_id>/current_r` | 小数文字列 例 `1.4` | false | 0 | 同上 |
| `cubej/<device_id>/current_t` | 小数文字列 例 `1.5` | false | 0 | 同上 |

## 診断値（本 spec で追加）

すべて **retain=true** で publish。値が `None` の attribute は publish 自体スキップする（unknown を保つ）。

| Topic | Payload 型 | 例 | 説明 |
|---|---|---|---|
| `cubej/<device_id>/diag/last_poll_success_ts` | ISO 8601 UTC | `2026-06-19T12:34:56Z` | 最終ポーリング成功時刻 |
| `cubej/<device_id>/diag/last_poll_failure_ts` | ISO 8601 UTC | `2026-06-19T12:30:00Z` | 直近のポーリング失敗時刻 |
| `cubej/<device_id>/diag/lqi` | 整数文字列 | `192` | Link Quality Indicator |
| `cubej/<device_id>/diag/pan_channel` | 整数文字列 | `33` | 接続中のチャンネル番号 |
| `cubej/<device_id>/diag/scan_retries_total` | 整数文字列 | `5` | 累計 SKSCAN リトライ回数 |
| `cubej/<device_id>/diag/wisun_reconnects_total` | 整数文字列 | `0` | 累計 Wi-SUN 再 join 回数 |
| `cubej/<device_id>/diag/mqtt_reconnects_total` | 整数文字列 | `2` | 累計 MQTT 再接続回数 |
| `cubej/<device_id>/diag/erxudp_timeouts_total` | 整数文字列 | `1` | 累計 ERXUDP 応答 timeout |
| `cubej/<device_id>/diag/uptime_seconds` | 整数文字列 | `12345` | プロセス起動からの秒数 |
| `cubej/<device_id>/diag/version` | 文字列 | `1.0.0+abc1234` | bridge スクリプトの自己申告バージョン |

### 周期

診断値は計測ポーリングと同じ 60s 周期で送信される（FR-004）。ただし「値が変わっていない」場合でも publish する（broker の retain を最新化するため）。

## Home Assistant Auto-Discovery

`homeassistant/sensor/<device_id>/<key>/config` トピックに retain=true で JSON payload を publish。

bridge 起動直後（MQTT 接続後）に 1 回だけ送信する。計測 5 件 + 診断 10 件 = 15 件の discovery payload。

### 例: 計測（既存踏襲）

```json
{
  "name": "Instantaneous Power",
  "unique_id": "cubej1_power",
  "state_topic": "cubej/cubej1/power",
  "unit_of_measurement": "W",
  "device_class": "power",
  "state_class": "measurement",
  "device": {
    "identifiers": ["cubej1"],
    "name": "Cube J1 Smart Meter",
    "model": "Cube J1",
    "manufacturer": "NextDrive"
  }
}
```

### 例: 診断 timestamp

```json
{
  "name": "Last Poll Success",
  "unique_id": "cubej1_last_poll_success_ts",
  "state_topic": "cubej/cubej1/diag/last_poll_success_ts",
  "device_class": "timestamp",
  "entity_category": "diagnostic",
  "device": {
    "identifiers": ["cubej1"],
    "name": "Cube J1 Smart Meter",
    "model": "Cube J1",
    "manufacturer": "NextDrive"
  }
}
```

### 例: 診断カウンター

```json
{
  "name": "MQTT Reconnects",
  "unique_id": "cubej1_mqtt_reconnects_total",
  "state_topic": "cubej/cubej1/diag/mqtt_reconnects_total",
  "state_class": "total_increasing",
  "entity_category": "diagnostic",
  "device": {
    "identifiers": ["cubej1"],
    "name": "Cube J1 Smart Meter",
    "model": "Cube J1",
    "manufacturer": "NextDrive"
  }
}
```

### 例: 診断 measurement (uptime)

```json
{
  "name": "Uptime",
  "unique_id": "cubej1_uptime_seconds",
  "state_topic": "cubej/cubej1/diag/uptime_seconds",
  "unit_of_measurement": "s",
  "state_class": "measurement",
  "entity_category": "diagnostic",
  "device": {
    "identifiers": ["cubej1"],
    "name": "Cube J1 Smart Meter",
    "model": "Cube J1",
    "manufacturer": "NextDrive"
  }
}
```

## QoS と再接続時の挙動

- 全 publish は QoS 0（既存 MQTT クライアント実装のまま）
- broker 切断中の publish はローカル queue に積まれ、再接続時に flush される（FR Edge Case）
- queue 内の同一 topic は最新のみ保持する（古いものを捨てる）
- `_flush_queue` で全 publish が完了した後、通常の publish 経路に戻る
