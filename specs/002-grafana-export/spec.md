# Feature Specification: Grafana Cloud Export via MQTT → Telegraf

**Feature Branch**: `002-grafana-export`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "Cube J1 が publish している MQTT メトリクス（計測 5 件 + 診断 10 件）を Grafana Cloud で時系列として可視化したい。bridge コードは触らない。受信側 (lab-ub01) で MQTT → Prometheus remote_write を行う変換層を入れる。"

## Background

001-bridge-observability で Cube J1 は `cubej/<device_id>/<sensor>` と `cubej/<device_id>/diag/<key>` に計測値と診断値を MQTT publish する設計が確定した。Home Assistant 側ではこれらが Auto-Discovery でセンサーとして見えるが、可視化は HA の組み込みグラフに限られ、長期保存・アラート・PromQL のような集計が貧弱である。

本 spec は受信側 (lab-ub01) に MQTT → Prometheus remote_write の変換層を追加し、Grafana Cloud (`sougen.grafana.net`) のマネージド Prometheus に時系列として送ることで、長期保存・アラート・PromQL・既存ダッシュボードとの統合を実現する。

Constitution III の「観測性は MQTT を第一の出力にする」を踏襲し、bridge 側のコード・設定は一切変更しない。MQTT を“公共の場”として扱い、後段の subscriber を増やす方針である。

## Clarifications

### Session 2026-06-19

- Q: Telegraf を稼働させる場所は？ → A: lab-ub01 上の Docker Compose (`/opt/compose/telegraf/`)、Mosquitto と同じ edge ネットワークに参加
- Q: MQTT subscribe に使う認証ユーザーは？ → A: `homeassistant`（既存 ACL で `cubej/#` 全 subscribe 権を持つ）。専用ユーザーは作らず既存資格情報を再利用する
- Q: Grafana Cloud の送信先は？ → A: マネージド Prometheus (`sougen.grafana.net` 配下、datasource `grafanacloud-sougen-prom`)、remote_write プロトコル
- Q: メトリクス命名規約は？ → A: `cube_j1_smart_meter_<key>_<unit>` パターン。`power` → `cube_j1_smart_meter_power_watts`、`energy_forward` → `cube_j1_smart_meter_energy_forward_kwh`、累積カウンタは `_total` で終わる
- Q: 文字列値（`version`、`last_poll_success_ts` 等）はどう扱う？ → A: `version` は `cube_j1_smart_meter_info{device_id, version} 1` の info メトリクスとして export。タイムスタンプ系は Unix 秒へ変換して `cube_j1_smart_meter_last_poll_success_timestamp_seconds` などのゲージにする（label 化はカーディナリティ過大）
- Q: 設定変更の反映方法は？ → A: `docker compose up -d --force-recreate` または `docker compose restart telegraf`、Watchtower で latest tag は自動更新する

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Grafana Cloud で計測値が時系列として見える (Priority: P1)

運用者は Grafana Cloud (`sougen.grafana.net`) の Explore で `cube_j1_smart_meter_power_watts` を投げると、Cube J1 が publish している瞬時電力が 60 秒粒度で線グラフとして表示される。`cube_j1_smart_meter_energy_forward_kwh` も同様で、`increase()` や `rate()` 等の PromQL を適用できる。

**Why this priority**: 本 feature の主目的そのもの。HA でしか見えなかった計測値が、Grafana の長期保存・アラート・PromQL の世界に乗ることで運用上の価値が一気に増える。

**Independent Test**: lab-ub01 で telegraf コンテナを起動した状態で、Cube J1 が 1 回以上 publish した後、`mcp__grafana-cloud__query_prometheus` で `cube_j1_smart_meter_power_watts` の instant query を実行すると 1 サンプル以上が返る。

**Acceptance Scenarios**:

1. **Given** telegraf コンテナが稼働し Mosquitto に接続済み、Cube J1 が `cubej/cubej1/power` に publish した直後, **When** Grafana Explore で `cube_j1_smart_meter_power_watts{device_id="cubej1"}` を range query する, **Then** publish 値と一致する time series が描画される
2. **Given** 過去 24 時間 telegraf が稼働した, **When** `increase(cube_j1_smart_meter_energy_forward_kwh[24h])` を query する, **Then** 24 時間の積算電力量が単位 kWh で返る
3. **Given** Cube J1 が単相 2 線式接続で `current_t` を publish していない, **When** `cube_j1_smart_meter_current_t_amperes` を query する, **Then** 系列が無い（エラーではなく empty result）

---

### User Story 2 - bridge の診断値も Grafana で見える (Priority: P2)

運用者は `cube_j1_smart_meter_scan_retries_total`、`cube_j1_smart_meter_wisun_reconnects_total`、`cube_j1_smart_meter_erxudp_timeouts_total` を PromQL で集計し、`rate()[1h]` で異常頻度を観察できる。`cube_j1_smart_meter_lqi` の低下を時系列で追える。`cube_j1_smart_meter_info` ラベルで bridge のバージョンが分かる。

**Why this priority**: P1 が動けば計測の主目的は達成。診断系は二次的だが、Grafana 上でアラートを書きたい（例: scan_retries が 1 時間に 10 回以上）ときに必要。

**Independent Test**: telegraf 起動から 5 分以内に、診断系トピック（`cubej/cubej1/diag/*`）を 1 つ以上受信した後、対応する Prometheus メトリクスが Grafana から query 可能。

**Acceptance Scenarios**:

1. **Given** bridge が `cubej/cubej1/diag/lqi` に 200 を publish した, **When** `cube_j1_smart_meter_lqi{device_id="cubej1"}` を instant query する, **Then** 200 が返る
2. **Given** 直近 1 時間で scan retry が 3 回起きた, **When** `increase(cube_j1_smart_meter_scan_retries_total[1h])` を query する, **Then** 3 が返る
3. **Given** bridge が `cubej/cubej1/diag/version` に `1.0.0+09e6f54` を publish した, **When** `cube_j1_smart_meter_info` を query する, **Then** `{device_id="cubej1",version="1.0.0+09e6f54"} 1` が返る

---

### Edge Cases

- Telegraf 起動時に Mosquitto が未起動なら？ → 再接続を試行し、Mosquitto 起動後に subscribe を再確立する
- Grafana Cloud への remote_write が一時的に失敗したら？ → telegraf の内部 buffer に保持し、復旧後に flush する（送信失敗で計測値の subscribe は止めない）
- 同じトピックが頻繁に retain で再送されたら？ → retain メッセージは telegraf 起動時に 1 度だけ受け取り、以降は通常の publish のみが流れる（Mosquitto の挙動）
- `version` 文字列が変わったら？ → 新しい `cube_j1_smart_meter_info` 系列が生成される。古い系列は staleness で消える（カーディナリティ増加は許容範囲、通常リリース頻度では問題にならない）
- MQTT トピックに想定外の値（例: `power` に非数値）が来たら？ → telegraf でパース失敗し当該サンプルを drop、エラーログを残す。他のトピックは継続処理する

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: lab-ub01 上に Telegraf コンテナ (`/opt/compose/telegraf/`) を稼働させる。Docker Compose 管理、Watchtower 自動更新対象、`edge` ネットワーク参加、`restart: unless-stopped`
- **FR-002**: Telegraf は Mosquitto (`tcp://mosquitto:1883`) から `cubej/+/+` と `cubej/+/diag/+` を subscribe する。認証は `homeassistant` ユーザー、パスワードは `/opt/compose-secrets/telegraf/telegraf.env` 経由で環境変数注入
- **FR-003**: 各 MQTT メッセージを Prometheus メトリクスへ変換する。命名は `cube_j1_smart_meter_<key>_<unit>` パターン、tag に `device_id`（トピックの第 2 セグメントから抽出）を付ける
- **FR-004**: 文字列値（`version`, ISO timestamp）は次のように扱う:
  - `diag/version` → `cube_j1_smart_meter_info{device_id, version} 1`
  - `diag/last_poll_success_ts`, `diag/last_poll_failure_ts` → Unix 秒に変換して `cube_j1_smart_meter_last_poll_{success,failure}_timestamp_seconds` (gauge)
- **FR-005**: Telegraf は Grafana Cloud のマネージド Prometheus に remote_write する。エンドポイント・basic auth は `/opt/compose-secrets/telegraf/telegraf.env` 経由で環境変数注入、設定ファイルにベタ書きしない
- **FR-006**: 累積カウンタ（`scan_retries_total`, `wisun_reconnects_total`, `mqtt_reconnects_total`, `erxudp_timeouts_total`）は Prometheus 命名規約に従い `_total` サフィックスを保持する。型は counter として export する
- **FR-007**: 設定変更には `docker compose restart telegraf` で反映できる。コンテナイメージの更新は Watchtower 任せ
- **FR-008**: Telegraf の設定ファイル (`telegraf.conf`) と `compose.yaml` は `enkunkun/compose` リポジトリ管理下に置く。シークレットは `/opt/compose-secrets/telegraf/` に分離し、リポジトリに混入させない
- **FR-009**: remote_write 失敗時も MQTT subscribe を継続し、内部バッファに保持して復旧後に送信する（計測値の取りこぼしを最小化）

### Non-Functional Requirements

- **NFR-001**: Telegraf コンテナの常時メモリ使用量は 100 MiB 以下を目標とする（lab-ub01 全体のリソース配分のため）
- **NFR-002**: Grafana Cloud free tier のメトリクスカーディナリティ上限を圧迫しない命名（`device_id` 以外のラベルを足さない、`version` のみ別系列）

### Out of Scope

- Cube J1 上の `mqtt_bridge.py` の変更（Constitution I/II/III に従い不可侵）
- Mosquitto コンテナ設定の変更（ACL 等は既存の `homeassistant` ユーザー権限を再利用）
- Home Assistant 側の設定変更
- 自宅 Lab Grafana (`grafana.lab-ub01.home.arpa`) への二重送信（必要になれば別 feature で）
- Grafana Cloud 上のダッシュボード・アラートルール作成（本 feature はメトリクス導管のみ。ダッシュボード/アラートは後続作業）

### Key Entities

- **MQTT topic → Prometheus metric mapping**: 1 トピック ↔ 1 メトリクス系列の対応表。`device_id` はトピックパスから抽出。詳細は contracts/topic-metric-map.md（後続作成）
- **Telegraf secrets**: Mosquitto パスワード、Grafana Cloud Prometheus tenant ID、Grafana Cloud API key。3 つ。`/opt/compose-secrets/telegraf/telegraf.env` に root:root 600 で保存

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: telegraf コンテナを起動してから 5 分以内に、Grafana Cloud Prometheus で `cube_j1_smart_meter_power_watts{device_id="cubej1"}` の instant query が値を返す
- **SC-002**: 24 時間連続稼働後、`count_over_time(cube_j1_smart_meter_power_watts[24h])` が期待サンプル数の 95% 以上（60 秒間隔の publish 想定で 24h × 60 = 1440 のうち 1368 以上）
- **SC-003**: `docker logs telegraf --tail 100` に MQTT 接続エラー・remote_write 認証エラーが連続して出ていない（起動直後の一時的なエラーは除く）
- **SC-004**: Grafana Cloud のアクティブシリーズが 30 系列以下（計測 5 + 診断 10 + info 1 ≒ 16 程度、device_id ラベル展開込みでも 30 以下を目標）
- **SC-005**: Telegraf プロセスのメモリ使用量が常時 100 MiB 以下（`docker stats telegraf`）

## Assumptions

- Grafana Cloud (`sougen.grafana.net`) アカウントが存在し、マネージド Prometheus への remote_write 用 basic auth（tenant ID + API key with `metrics:write`）を取得できる
- Mosquitto は lab-ub01 上の `/opt/compose/mosquitto/` で稼働中、`edge` Docker ネットワークに参加済み、`homeassistant` ユーザーで `cubej/#` の subscribe 権限がある
- Cube J1 (bridge) が 60 秒間隔で計測値を publish している (001-bridge-observability spec 準拠)
- lab-ub01 上で Docker Compose が動作し、`/opt/compose/` 配下は `enkunkun/compose` git リポジトリで管理されている
- `/opt/compose-secrets/<svc>/` 配下は root:root 700 のパーミッションで secrets を保存する慣習が確立済み
- Watchtower が `com.centurylinklabs.watchtower.enable: "true"` ラベル付きコンテナの自動更新を担当する
- 本 feature は受信側 (lab-ub01) の構成変更のみで、Cube J1 にデプロイされた bridge コード・設定には一切触れない（Constitution I/II/III）
