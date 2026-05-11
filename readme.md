# Cube J1 MQTT

2025年3月31日にサービス終了した「NextDrive Cube J1」を、Home Assistant の MQTT 統合にデバイス／センサーとして利用するためのプロジェクトです。

Cube J1 に内蔵された Wi-SUN モジュール（BP35C0）を通じてスマートメーター（Bルート）の計測値を取得し、MQTT 経由で Home Assistant へ送信します。

## 概要

MQTT ブリッジ（`mqtt_bridge.py`）が Cube J1 上で常駐し、スマートメーターから定期的に計測値を取得して Home Assistant へ送信します。
MQTT auto-discovery に対応しており、センサーは自動登録されます。

Home Assistant への MQTT ブローカーの導入と MQTT 統合の設定は事前に完了させてください。  
[MQTT 統合 — Home Assistant ドキュメント](https://www.home-assistant.io/integrations/mqtt/)

## 取得できるセンサー

| センサー名 | ECHONET Lite EPC | 単位 | HA device_class |
|---|---|---|---|
| 瞬時電力 | E7 | W | power |
| 積算電力量（正方向） | E0 | kWh | energy (total_increasing) |
| 積算電力量（逆方向） | E3 | kWh | energy (total_increasing) |
| 瞬時電流 R相 | E8（上位2バイト） | A | current |
| 瞬時電流 T相 | E8（下位2バイト） | A | current |

係数（EPC D3）と単位（EPC E1）も自動取得し、積算電力量の kWh 換算に適用します。

## 導入方法

Cube J1 は USB メモリ内の特定ファイル構成を検出すると自動的にスクリプトを実行する仕組みを持っています。  
リポジトリの内容をそのまま USB メモリ直下へコピーするだけでセットアップが完了します。

### 手順

1. このリポジトリをダウンロードまたは clone する
2. `production_tool/config.json` を編集して認証情報・接続先を設定する（→ [config.json の設定](#configjson-の設定)）
3. `production_tool/wpa_supplicant.conf` を編集して Wi-Fi の SSID とパスワードを設定する
4. FAT32 形式の USB メモリを用意し、`production_tool/` 内のファイルをすべて USB メモリ直下へコピーする
5. Cube J1 に USB メモリを挿入して電源を入れる
6. スクリプトが自動実行され、Home Assistant へのデータ送信が開始される

セットアップ完了後、LED が点滅で完了を通知します。  
設定を変更する場合は、USB メモリのファイルを編集してから再挿入・電源再投入してください。

### config.json の設定

```json
{
    "br_id":          "（スマートメーターBルート認証ID 32文字）",
    "br_pwd":         "（スマートメーターBルートパスワード 12文字）",
    "mqtt_host":      "（Home Assistant の IPアドレス）",
    "mqtt_port":      1883,
    "mqtt_user":      "（MQTTユーザー名）",
    "mqtt_pass":      "（MQTTパスワード）",
    "device_id":      "cubej1",
    "serial_port":    "/dev/ttyS1",
    "poll_interval":  60
}
```

| キー | 説明 |
|---|---|
| `br_id` | スマートメーターの Bルート認証ID（32文字） |
| `br_pwd` | スマートメーターの Bルートパスワード（12文字） |
| `mqtt_host` | Home Assistant が動作しているホストの IP アドレス |
| `mqtt_port` | MQTT ブローカーのポート番号（デフォルト: 1883） |
| `mqtt_user` / `mqtt_pass` | MQTT 認証情報（不要な場合は空文字） |
| `device_id` | HA デバイス識別子。複数台運用時に変更する |
| `serial_port` | Wi-SUN モジュールのシリアルデバイス（通常変更不要） |
| `poll_interval` | スマートメーターへのポーリング間隔（秒） |

## セットアップの内部動作

USB メモリ挿入時に Cube J1 が自動実行する `production_tool` スクリプトが以下の処理を行います。

1. **ADB TCP 有効化** — ポート 5555 で ADB 接続を受け付けるよう設定
2. **Wi-Fi 設定** — `wpa_supplicant.conf` を配置してネットワーク再起動
3. **ブリッジのインストール** — `config.json` と `mqtt_bridge.py` を `/data/local/` へコピー
4. **競合サービス停止** — `/dev/ttyS1` を占有する `wisund` と `NDEcLiteAgent` を停止・無効化
5. **init サービス登録** — `mqtt_ha_bridge.rc` を `/system/etc/init/` へ配置（再起動後も自動起動）
6. **ブリッジ即時起動** — `mqtt_ha_bridge` サービスとして `mqtt_bridge.py` を起動
7. **LED 点滅** — `led_effect.sh` による点滅で完了を通知

## LED の動作

Cube J1 の RGB LED は、動作状態に応じて以下のように点灯します。  

| 状態 | LED の動き |
|---|---|
| セットアップ完了 | 点滅（`led_effect.sh` で制御） |
| Wi-SUN コマンド送信中（SKSTACK） | 緑↔青 を 0.2 秒ごとに交互点滅 |
| PANA 接続待機中（SKJOIN） | 緑↔青 を 0.2 秒ごとに交互点滅 |
| データ取得・MQTT Publish 中 | 青点灯 |

## MQTT トピック構造

| 用途 | トピック |
|---|---|
| HA auto-discovery | `homeassistant/sensor/{device_id}/{sensor_id}/config` |
| 瞬時電力 | `cubej/{device_id}/power` |
| 積算電力量（正方向） | `cubej/{device_id}/energy_forward` |
| 積算電力量（逆方向） | `cubej/{device_id}/energy_reverse` |
| 瞬時電流 R相 | `cubej/{device_id}/current_r` |
| 瞬時電流 T相 | `cubej/{device_id}/current_t` |

## ファイル構成

```
production_tool/
├── production_tool          # メインセットアップスクリプト
├── mqtt_bridge.py           # Wi-SUN → ECHONET Lite → MQTT ブリッジ本体
├── led_effect.sh            # RGB LED 制御スクリプト
├── config.json              # 接続設定（要編集）
├── wpa_supplicant.conf      # Wi-Fi 設定（要編集）
├── mqtt_ha_bridge.rc        # Cube J1 init サービス定義
├── wisund_disabled.rc       # wisund サービス無効化用 RC
└── ndeclite_disabled.rc     # NDEcLiteAgent 無効化用 RC
```

## 技術仕様

- **実行環境**: Cube J1 上の Android 系 Linux（Python 2.7）
- **依存ライブラリ**: Python 2.7 標準ライブラリのみ
  - `termios` `socket` `struct` `select` `json` `threading` 等
  - pyserial・paho-mqtt は**不要**
- **シリアル通信**: `termios` で raw モード設定、115200 bps
- **MQTT**: MQTT 3.1.1 をソケットで直接実装（QoS 0、TCP keepalive 対応、自動再接続）
- **Wi-SUN スキャン**: PAN を探索、LQI 最良の PAN を選択
- **ログ**: `/data/local/mqtt_bridge.log` に追記

## 参考記事

Cube J1 の調査内容や USB メモリ内スクリプトの自動実行の仕組みについては、以下の記事で詳しく解説しています。

[NextDrive Cube J1を分解せずにrootを取りたい！](https://zenn.dev/tsuyopon123/articles/cube-j1-root)

## トラブルシューティング

ブリッジの状態は ADB で確認できます。

```sh
# Cube J1 へ接続
adb connect <Cube-J1のIPアドレス>:5555

# ログ確認
adb shell cat /data/local/mqtt_bridge.log

# プロセス確認
adb shell ps | grep python
```
