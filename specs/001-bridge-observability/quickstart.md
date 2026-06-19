# Quickstart: Bridge Observability

bridge 改修版を実機で動かして HA 上で診断センサーが見えるところまでの最短手順。

## 前提

- lab-ub01.home.arpa にアクセス可能（SSH 鍵設定済み）
- Cube J1 実機 + FAT32 USB メモリ（容量は数 MiB で十分）
- スマートメーター B ルート ID / パスワード（電力会社申請済み）
- 自宅 Wi-Fi の SSID / PSK

## 1. lab-ub01 で Mosquitto + Home Assistant を起動

```bash
ssh lab-ub01.home.arpa
cd /opt/compose/mosquitto && docker compose up -d
cd /opt/compose/home-assistant && docker compose up -d
```

HA は `http://homeassistant.lab-ub01.home.arpa:8123` または Traefik 経由の URL でアクセス。

初回起動で:

1. HA の初回セットアップウィザードを完了
2. MQTT 統合を追加し、ホストを `mqtt.lab-ub01.home.arpa`（または lab-ub01 内 IP）、ポート 1883、ユーザー名 `homeassistant`、パスワードを設定
3. MQTT Auto-Discovery prefix を既定の `homeassistant` のまま

## 2. bridge を USB に焼く

リポジトリのルートで:

```bash
cd ~/git/cube-j1-mqtt

# Bルート ID/PWD と MQTT 接続情報を編集
$EDITOR production_tool/config.json
# {
#   "br_id": "あなたの B ルート ID 32 桁",
#   "br_pwd": "あなたの B ルートパスワード 12 桁",
#   "mqtt_host": "mqtt.lab-ub01.home.arpa",
#   "mqtt_port": 1883,
#   "mqtt_user": "cubej1",
#   "mqtt_pass": "Mosquitto で発行したパスワード",
#   "device_id": "cubej1",
#   "serial_port": "/dev/ttyS1",
#   "poll_interval": 60,
#   "log_level": "info"
# }

# Wi-Fi 設定
$EDITOR production_tool/wpa_supplicant.conf

# git short hash を mqtt_bridge.py に埋め込む
./scripts/embed_git_hash.sh

# USB をマウント（例: /Volumes/CUBEJ1）して CubeJMTS.txt と production_tool/ をコピー
cp CubeJMTS.txt /Volumes/CUBEJ1/
cp -R production_tool /Volumes/CUBEJ1/
diskutil unmount /Volumes/CUBEJ1
```

## 3. Cube J1 に挿して起動

1. Cube J1 の電源を抜く
2. USB メモリを Cube J1 の USB ポートに挿す
3. 電源を入れる
4. **LED 白色 10 回点滅** = セットアップ完了
5. **LED 緑点灯** = Wi-Fi 接続成功
6. その後、Wi-SUN 接続を試行（緑/青交互点滅）し、データ取得開始（青点灯）

## 4. HA で確認

`http://homeassistant.lab-ub01.home.arpa:8123/config/devices/dashboard` を開く。

「Cube J1 Smart Meter」というデバイスが追加されている。中を開くと:

- **コントロール / 計測センサー** (5 件)
  - Instantaneous Power (W)
  - Cumulative Energy Fwd (kWh)
  - Cumulative Energy Rev (kWh)
  - Current R Phase (A)
  - Current T Phase (A)
- **診断（Diagnostic）センサー** (10 件)
  - Last Poll Success
  - Last Poll Failure
  - LQI
  - PAN Channel
  - Scan Retries
  - Wi-SUN Reconnects
  - MQTT Reconnects
  - ERXUDP Timeouts
  - Uptime
  - Bridge Version

## 5. ログを確認したい場合

Cube J1 から USB を抜いて Mac に挿し、`/Volumes/CUBEJ1/data/local/mqtt_bridge.log` を確認...

…ではなく、Cube J1 の内部 flash 上 `/data/local/mqtt_bridge.log` にローカルログがある。USB からは見えない設計（既存挙動踏襲）。SSH で取り出すか、USB ブートスクリプトで `/data/local/mqtt_bridge.log*` を USB にコピーするヘルパーを別途用意することもできる（本 spec 範囲外）。

JSON Lines 形式なので `jq` でフィルタできる:

```bash
ssh root@cubej1.home.arpa "cat /data/local/mqtt_bridge.log" | jq -c 'select(.level=="error")'
```

## トラブルシューティング

| 症状 | 確認 |
|---|---|
| HA に何もセンサーが出ない | mqtt_user/mqtt_pass が config.json と Mosquitto の passwd ファイルで一致しているか。HA の MQTT 統合の Auto-Discovery が有効か |
| 診断センサーが unknown のまま | bridge 起動から最初のポーリング成功までは時間がかかる（SKSCAN 最大 60s + PANA 90s）。LED が青点灯になったか確認 |
| ログが回らない | `log_max_bytes` が十分小さいか、書き込みパスに権限があるか。stderr フォールバックでも書かれる |
| Bridge Version が `1.0.0+unknown` | `scripts/embed_git_hash.sh` を実行せずに USB にコピーした。一旦取り出して再実行する |
