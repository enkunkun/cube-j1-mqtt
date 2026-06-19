# Feature Specification: Bridge Observability (Diagnostic MQTT + Structured Logging)

**Feature Branch**: `001-bridge-observability`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "Add structured logging and MQTT diagnostic publishing for cube-j1-mqtt bridge"

## Background

上流 `mqtt_bridge.py` は計測値（瞬時電力・積算電力量・電流）を `cubej/<device_id>/<sensor>` に publish するが、bridge 自身の動作状態（Wi-SUN 接続が再確立されたか、ERXUDP timeout が頻発していないか、MQTT 再接続が起きていないか）は `/data/local/mqtt_bridge.log` に平文で残るだけで、外部から観察できない。ローカルログもローテーションされず flash を食う。

本 spec はこの観測性のギャップを埋める。Constitution III/IV に従い、**第一の出力は MQTT**（HA から見える）。ローカルログは JSON Lines 化＋ローテーション＋レベル分けに留める。

## Clarifications

### Session 2026-06-19

- Q: 診断 MQTT トピック (`cubej/<id>/diag/*`) の retain flag をどうする？ → A: retain=true（broker/HA 再起動後も最新値が unknown にならないように永続保持）
- Q: 診断 publish の頻度（計測 60s・診断は別間隔）をどうする？ → A: 60s（計測と同期、毎ポーリングで diag も同時 publish）
- Q: bridge の self-version 文字列 (`diag/version`) はどう決める？ → A: SemVer + git hash（例 `1.0.0+abc1234`、git 部分は欠落時 `unknown`）
- Q: SC-004（計測パスのレイテンシ悪化 10% 以内）の検証方法は？ → A: ユニットテストで計測パスを micro-benchmark（CI 可、診断/ログの追加あり/なしで mean/median を比較）

## User Scenarios & Testing *(mandatory)*

### User Story 1 - HA ダッシュボードから bridge の健全性を一目で見られる (Priority: P1)

運用者は Home Assistant のダッシュボードで Cube J1 デバイスのカードを開けば、計測値だけでなく「最後に成功したポーリング時刻」「Wi-SUN リンク品質 (LQI)」「MQTT 再接続回数」「ERXUDP timeout 回数（直近 1 時間）」が同じデバイスに紐づいたセンサーとして並んで見える。何か変だと感じたとき、SSH も Cube J1 の USB 抜き差しもせずに状況を把握できる。

**Why this priority**: Cube J1 は屋内の電源コンセント奥に置かれることが多く、物理的にアクセスしづらい。外部から見えないことが運用上の最大の痛点なので、これを最初に解消する。

**Independent Test**: bridge を Mosquitto + HA に接続して 1 ポーリング以上回し、HA のデバイスページに `cubej/<device_id>/diag/*` 由来のセンサーが計測センサーと同一デバイス配下に出現することを確認する。

**Acceptance Scenarios**:

1. **Given** bridge が起動完了し最初の計測ポーリングが成功した後, **When** HA を開く, **Then** 「Cube J1 Smart Meter」デバイスに `Last Poll Success` / `LQI` / `Poll Retries` / `MQTT Reconnects` / `Uptime` の診断系センサーが計測系センサーと並んで表示される
2. **Given** Wi-SUN の SKSCAN が 3 回リトライした後成功した状況, **When** ポーリングが完了して MQTT publish が走る, **Then** `cubej/<id>/diag/scan_retries_total` の値が 3 増えており、HA 上でも反映される
3. **Given** ブローカーが瞬断して bridge が再接続した, **When** HA を開く, **Then** `Last Reconnect Time` と `MQTT Reconnects` カウンターが更新されている

### User Story 2 - ローカルログが JSON Lines で構造化されておりサイズ上限がある (Priority: P2)

運用者が Cube J1 を一旦取り出して USB 経由でログを吸い上げたとき、`mqtt_bridge.log` は JSON Lines（1 行 1 イベント）で、`ts` / `level` / `event` / 任意の context フィールドを持つ。`jq` でフィルタできる。flash 寿命を守るため、単一ファイルは設定上限（既定 1 MiB）を超えたら世代ローテーションされ、過去世代は設定数（既定 3）まで保持される。

**Why this priority**: P1 で観測性の主軸は MQTT に移るが、bridge 起動直後（MQTT 未接続）や Wi-SUN join 失敗時の詳細など、MQTT に乗せきれないノイズはローカルログでしか追えない。これを使い物にする。

**Independent Test**: 設定で `log_max_bytes` を小さい値（例 4 KiB）にし、bridge を一定時間走らせて `mqtt_bridge.log` と `mqtt_bridge.log.1`〜`.3` が生成され、世代数上限を超えるとローテーションされることを確認する。各行が `json.loads` でパース可能なことも確認する。

**Acceptance Scenarios**:

1. **Given** bridge が起動して数イベント記録した, **When** `mqtt_bridge.log` を 1 行ずつ JSON パースする, **Then** すべての行が `{"ts","level","event"}` 必須キーを持つ valid JSON である
2. **Given** ログ世代上限が 3, **When** ローテーションが 4 回起きた, **Then** `mqtt_bridge.log.4` は存在せず、最古世代から順に消えている
3. **Given** `log_level: "info"` が設定済み, **When** `debug` レベルのイベントが発生した, **Then** ファイルに記録されない

### User Story 3 - ログレベルとローテーション挙動を設定で調整できる (Priority: P3)

運用者は `config.json` の追加キーで、ログレベル（`debug`/`info`/`warn`/`error`）、ログ世代数・サイズ上限を変えられる。デフォルト値は健全な選択で、キーが欠落しても挙動が変わらない（後方互換）。

**Why this priority**: 静かに運用したい場合と詳細調査したい場合で要件が違う。ただしまず動くことが先で、これは P1/P2 ができてからチューニング目的で必要。

**Independent Test**: `config.json` から該当キーを削除しても起動しデフォルト値で動く。`log_level: "debug"` に変えるとローカルログに debug 行が出る。

**Acceptance Scenarios**:

1. **Given** `config.json` に新規キーが 1 つも無い, **When** bridge を起動する, **Then** 既定値（`log_level=info`, `log_max_bytes=1048576`, `log_backup_count=3`）で起動して計測・診断が動く
2. **Given** `log_level: "debug"` を指定, **When** bridge を起動して 1 ポーリング回す, **Then** ローカルログに `level: "debug"` のイベントが含まれる

---

### Edge Cases

- MQTT broker に接続できないまま起動した場合、diag publish も計測 publish と同じローカル queue に積まれ、再接続時に flush される。queue 内の同一 topic は古い方を捨てて最新だけ残す（broker 側の retain=true と整合）
- 診断値の計算中に例外が起きても、計測ポーリングは止まらない（diag は best-effort）
- ローカルログファイルが Read-only など書き込めない場合、stderr フォールバックを維持する（既存挙動踏襲）
- `config.json` に未知のキーがあっても起動する（無視する）
- 診断 publish の組み立て中に例外が起きても計測 publish 自体は実施される（FR-005 と一致）

## Requirements *(mandatory)*

### Functional Requirements

#### 診断 MQTT publish

- **FR-001**: bridge は以下の診断値を `cubej/<device_id>/diag/<key>` トピックに **retain=true** で publish しなければならない（broker / HA 再起動後も最新値が復元されるため）:
  - `last_poll_success_ts` (ISO 8601 文字列): 最新の ECHONET Lite 計測が成功した UTC 時刻
  - `last_poll_failure_ts` (ISO 8601 文字列 or 空): 直近の計測失敗時刻
  - `lqi` (整数): 最新の SKSCAN で選択された PAN の LQI（接続済み PAN を保持し、再 scan 時に更新）
  - `pan_channel` (整数): 接続中のチャンネル
  - `scan_retries_total` (整数): 起動以降の累計 SKSCAN リトライ回数
  - `wisun_reconnects_total` (整数): 起動以降の累計 Wi-SUN 再 join 回数
  - `mqtt_reconnects_total` (整数): 起動以降の累計 MQTT 再接続回数
  - `erxudp_timeouts_total` (整数): 起動以降の累計 ERXUDP 応答 timeout 回数
  - `uptime_seconds` (整数): プロセス起動からの秒数
  - `version` (文字列): bridge スクリプトの自己申告バージョン。形式は `<SemVer>+<git_short_hash>`（例 `1.0.0+abc1234`）。git hash が取得できない環境（直接 USB 配布など）では `<SemVer>+unknown` にフォールバックする

- **FR-002**: bridge は Home Assistant Auto-Discovery の config を `homeassistant/sensor/<device_id>/<diag_key>/config` に publish し、`device.identifiers` を計測センサーと同じ `[device_id]` で揃えて、HA 上で 1 デバイスに統合されなければならない

- **FR-003**: 各診断 HA discovery payload は以下を満たす:
  - 時刻系は `device_class: timestamp`、ISO 8601 UTC
  - カウンター系は `state_class: total_increasing`
  - 計装値（LQI, channel）は `state_class: measurement`、適切な `entity_category: diagnostic`

- **FR-004**: 診断 publish は計測ポーリングと**同周期**で行う。すなわち各ポーリング成功（または失敗）の直後に診断値スナップショットを `cubej/<device_id>/diag/<key>` へ retain=true で送る。`diag_publish_interval` 設定キーは廃止し、`poll_interval` に従う

- **FR-005**: 診断値の集約・publish 中に例外が発生しても、計測パスはブロックされない。診断機能は計測パスから見て best-effort であり、診断失敗時もログにエラーを残して処理を継続する

#### ローカルログ構造化とローテーション

- **FR-006**: ローカルログは 1 行 1 JSON オブジェクトの JSON Lines 形式で出力されなければならない。必須キーは `ts` (ISO 8601 UTC), `level`, `event`。任意で `context` (dict) を持てる

- **FR-007**: ログレベルは `debug` / `info` / `warn` / `error` の 4 段階。設定 `log_level` の閾値未満のイベントは出力されない

- **FR-008**: ログファイルは `log_max_bytes`（既定 1048576 = 1 MiB）を超えたらローテーションされ、`mqtt_bridge.log.1` ... `mqtt_bridge.log.<log_backup_count>`（既定 3）まで保持される

- **FR-009**: ログファイルへ書き込めない場合、bridge は起動を続行し stderr にフォールバックする（既存挙動踏襲）

- **FR-010**: 主要な状態遷移（`mqtt_connected`, `wisun_joined`, `wisun_join_failed`, `mqtt_reconnect`, `poll_success`, `poll_failure`, `scan_retry`, `bridge_start`）はイベント名としてログに出る

#### 設定後方互換

- **FR-011**: `config.json` の新規キー `log_level`, `log_max_bytes`, `log_backup_count` は **すべて optional**。欠落時は既定値が使われる

- **FR-012**: 既存の 9 キー（`br_id`, `br_pwd`, `mqtt_host`, `mqtt_port`, `mqtt_user`, `mqtt_pass`, `device_id`, `serial_port`, `poll_interval`）の意味とデフォルトは一切変えない

### Key Entities

- **DiagState**: bridge 内部で保持する診断カウンター・最新値の集合。スレッドセーフではない（単一メインループ前提）。`update_*()` メソッドで更新し、`snapshot()` で MQTT publish 用 dict を返す
- **StructuredLogger**: `logging` モジュールベースで JSON Lines を出力するラッパー。`RotatingFileHandler` を内部で使う。レベル + イベント名 + 任意 context を受ける
- **HA Diag Sensor**: 既存 `SENSOR_DEFS` と同様、診断用センサー定義のリスト。`(key, name, unit, device_class, state_class, entity_category)`

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cube J1 をリブートしてから 90 秒以内に、HA 上のデバイスページに少なくとも `last_poll_success_ts` と `mqtt_reconnects_total` を含む診断センサー群が現れる
- **SC-002**: bridge が連続稼働 7 日間で、`mqtt_bridge.log` 系ファイル群の合計サイズは `log_max_bytes * (log_backup_count + 1)` バイトを超えない（既定値で約 4 MiB）
- **SC-003**: ログファイルの 100 サンプル行を `json.loads` でパースしたとき、100% が成功する
- **SC-004**: ユニットテストの micro-benchmark で、診断スナップショット生成 + JSON Lines ロガー呼び出しを含めた「ポスト計測処理」1 周あたりの実行時間（中央値・1000 回試行）が、診断/ログ機能を無効化したベースライン比で 10 % 以内に収まる。テスト環境は CI で再現可能（host Python 3 で実装、被テストコードは 2.7 互換）
- **SC-005**: `config.json` に新規キーを 1 つも追加しない上流互換の設定ファイルで bridge を起動でき、既存の計測 publish の挙動が変わらない

## Assumptions

- 受信側 MQTT broker は lab-ub01.home.arpa 上の Mosquitto を使用する（別 spec/タスクで構築する）
- Home Assistant も同 lab-ub01 上で稼働し、MQTT 統合と Auto-Discovery が有効化されている
- bridge を動作させる Cube J1 上の Python は 2.7 系で、`logging.handlers.RotatingFileHandler` は使用可能
- ISO 8601 タイムスタンプは UTC で `YYYY-MM-DDTHH:MM:SSZ` 形式（マイクロ秒は省略）
- 診断値のうち `version` は `production_tool/mqtt_bridge.py` 冒頭の `BRIDGE_SEMVER` 定数（SemVer、手動更新）と、ビルド時スクリプトが同ファイルに埋め込む `BRIDGE_GIT_HASH` 定数（git の short hash、欠落時は `"unknown"`）を `"{semver}+{hash}"` 形式に結合する。git hash 埋め込みスクリプトは `production_tool/` のコピー前に走る前処理として用意し、未実行でも bridge は起動できる
- Cube J1 の wall clock は NTP 同期されている前提（同期前のタイムスタンプはベストエフォート）
