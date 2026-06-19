# cube-j1-mqtt Constitution

NextDrive Cube J1 を Home Assistant 用スマートメーター MQTT ブリッジとして運用するための原則。
本リポジトリは上流 [tsuyopon123/cube-j1-mqtt](https://github.com/tsuyopon123/cube-j1-mqtt) のフォーク的派生であり、上流互換性を保ちつつ観測性とローカル運用性を強化することを目的とする。

## Core Principles

### I. USB ブート構造を壊さない (NON-NEGOTIABLE)
Cube J1 は USB メモリ直下の `CubeJMTS.txt` と `production_tool/` 一式を検出して自動実行する。この導入経路を維持する。`production_tool/` 配下のファイル名・rc スクリプトの起動契約を勝手に変えない。新規ファイルの追加は許容するが、既存 8 ファイル（`config.json`, `led_effect.sh`, `mqtt_bridge.py`, `mqtt_ha_bridge.rc`, `ndeclite_disabled.rc`, `production_tool`（Android shell launcher）, `wisund_disabled.rc`, `wpa_supplicant.conf`）の役割は据え置く。

### II. Python 2.7 標準ライブラリのみ (NON-NEGOTIABLE)
Cube J1 上のランタイムは Python 2.7 で、pip による追加ライブラリ導入はサポートしない。`mqtt_bridge.py` および同居スクリプトは Python 2.7 stdlib のみで完結させる。MQTT クライアントもシリアル制御も自前実装を継承する。テストコードは Python 3 で書いてよいが、被テストコードは 2.7 互換を維持する。

### III. 観測性は MQTT を第一の出力にする
Cube J1 の `/data/local/` への書き込みは flash 寿命と外部からの可視性の両面で不利。bridge 自身の診断情報（最終成功時刻・scan retry 回数・MQTT 再接続回数・LQI 等）は MQTT で `cubej/<device_id>/diag/*` に publish し、Home Assistant Auto-Discovery でセンサー化する。ローカルログは構造化（JSON Lines）と最小限のローテーションに留める。

### IV. 計測ロジックを観測性追加で劣化させない
ログ・診断 publish の追加は、Wi-SUN ポーリングや ECHONET Lite パース等の計測パスをブロックしてはならない。診断送信失敗は計測値送信を妨げない。ロギング処理は計測パスの最悪レイテンシを実測可能な単位で増やさない。

### V. テスト駆動 (TDD) で改修する
新規追加・修正は t_wada スタイルの Red→Green→Refactor で進める。被テストロジック（ECHONET Lite フレーム組立/パース、診断状態の集約、JSON ログフォーマッタ等）は副作用と分離し、ハードウェア依存のないユニットテストを書く。シリアル/MQTT は I/O 境界で抽象化する。

## Hardware & Runtime Constraints

- ターゲット機器: NextDrive Cube J1（Wi-SUN モジュール BP35C0 内蔵、armhf 系 Linux、Python 2.7）
- 設定ファイル: USB の `production_tool/config.json`（実行時は `/data/local/config.json` に展開される想定）
- シリアル: `/dev/ttyS1` 115200 baud（変更不可前提）
- LED: `/sys/class/leds/{red,green,blue}/brightness`
- ローカルログ: `/data/local/mqtt_bridge.log`（既存パス踏襲、上限サイズと世代管理を導入する）

## Operational Standards

- **MQTT トピック規約**: 計測値は `cubej/<device_id>/<sensor>`、診断は `cubej/<device_id>/diag/<key>`、HA discovery は `homeassistant/sensor/<device_id>/<sensor>/config`。device_id 既定値 `cubej1` を上流踏襲。
- **HA Auto-Discovery**: 全センサー（計測 + 診断）について `device.identifiers` を共有し、HA 上で 1 デバイスにまとまるようにする。
- **設定後方互換**: `config.json` のキーを既存 9 個から減らさない。追加キーは optional とし、欠落時のデフォルトを `mqtt_bridge.py` 側で持つ。
- **障害時挙動**: MQTT 切断中も計測は継続し、queue に保持して再接続時に flush する（既存実装を踏襲）。診断値も同様に扱う。
- **ローカル運用先**: 受信側 (Mosquitto + Home Assistant) は lab-ub01.home.arpa 上の Docker Compose で運用する。クラウド依存は持たない。

## Governance

本 Constitution は本リポジトリ内のすべての spec/plan/実装に優先する。逸脱が必要な場合は spec で明示し、その理由と影響範囲を記録する。原則の追加・変更は SemVer に従って本ファイルのバージョンを上げ、影響を受ける spec / plan / tasks を同 PR で更新する。

開発ワークフローは `/speckit-specify` → `/speckit-clarify`（必要なら）→ `/speckit-plan` → `/speckit-tasks` → `/speckit-implement` の順を踏む。実装は TDD を必須とする（リファクタリングと設定変更を除く）。

**Version**: 1.0.0 | **Ratified**: 2026-06-19 | **Last Amended**: 2026-06-19
