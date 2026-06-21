# Feature Specification: HA Discovery Auto-Republish

**Feature Branch**: `016-ha-discovery-republish`
**Created**: 2026-06-21
**Status**: Draft
**Input**: User description: "hals5412 fork (a3c3637) の発想を取り込み、 MQTT broker 再構築や retained 喪失時に HA discovery が消える事故を防ぐ"

## Background

Home Assistant の MQTT discovery は broker の retained message に依存する。 当方の bridge は起動時に 1 回だけ `publish_ha_discovery()` (`production_tool/mqtt_bridge.py:3123` 付近) で discovery topic に retained publish する。 broker (Mosquitto 等) が retained を保持し続ける限り HA からセンサーが見える。

しかし以下のケースで retained が消える / HA から見えなくなる:

1. **MQTT broker の再構築**: Mosquitto コンテナを再作成、 volume を clear、 イメージアップグレード等で retained store が初期化される
2. **broker 側の retained TTL** 設定でメッセージが期限切れになる
3. **broker → bridge の再接続中に discovery タイミングがズレる** ケース (頻度は低い)
4. **HA 側 cache の不整合**

復旧には **bridge プロセス自体の再起動** が必要 (publish_discovery を再走させる手段が無いため)。 これは運用上の事故ポイント。

hals5412/cube-j1-mqtt の `a3c3637` で:
- `DISCOVERY_REPUBLISH_INTERVAL = 24h` の周期再 publish
- **MQTT 再接続時** に追加で再 publish

の対策が入っている。 fork は周期と reconnect の 2 トリガでカバーしており、 当方も同じパターンで採用する。

## Scope

### 周期再 publish

- `config.json` に `discovery_republish_interval_sec` (default `86400` = 24h) を追加
- main loop または背景スレッドで前回 publish 時刻からの経過を判定、 経過したら `publish_ha_discovery()` を再実行
- 0 を指定すると周期再 publish を無効化 (escape hatch)

### MQTT reconnect 時の再 publish

- 当方既存の `diag_state.on_mqtt_reconnect()` (`production_tool/mqtt_bridge.py:1843` 付近) が呼ばれた **直後** に discovery 再 publish をトリガ
- ただし reconnect 直後すぐ publish すると同種の MQTT 再接続ループに巻き込まれる可能性があるため、 短い debounce (例: 5 秒) を入れて 1 度だけ再 publish
- 周期再 publish のタイマーもリセット (24h を再起算)

### 観測メトリクス

- `discovery_republish_total` カウンタを DiagState に追加 (= 周期 + reconnect の合計回数)
- `last_discovery_publish_ts` を snapshot に追加 (最終 publish 時刻、 ISO8601)

## Non-Scope

- discovery の **削除** (sensor 撤去時の retained クリア) — 別途 spec (017以降の候補) で議論
- HA 側 cache の制御 — bridge 側からは触れない
- broker 切り替え時の自動 fallback — 単一 broker 前提
- topic prefix / discovery payload schema の変更 — 現状の `homeassistant/...` 形式を維持

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは Mosquitto コンテナを `docker compose down -v && docker compose up -d` で再構築した。 従来は HA からセンサーが消え、 復旧のために bridge を `adb shell stop/start mqtt_ha_bridge` する必要があった。 本 spec 後は、 bridge が broker への再接続を検知して自動で discovery を再 publish し、 HA からセンサーが **数秒〜数十秒以内** に復活する。 ユーザの介入不要。

### Acceptance Scenarios

1. **Given** bridge 稼働中、 **When** MQTT broker を停止 → 再起動、 **Then** bridge が再接続を検知して 5 秒以内に discovery を再 publish、 `discovery_republish_total` が +1
2. **Given** bridge を 24h 以上連続稼働、 **When** `discovery_republish_interval_sec=86400` に到達、 **Then** 周期再 publish が走り、 `discovery_republish_total` が +1
3. **Given** `discovery_republish_interval_sec=0`、 **When** 24h 連続稼働、 **Then** 周期再 publish は走らない (MQTT reconnect 時のみ)
4. **Given** MQTT 切断→再接続が 5 秒以内に複数回、 **When** 再接続が安定、 **Then** discovery 再 publish は debounce で 1 回だけ
5. **Given** `discovery_republish_interval_sec` 未設定、 **When** bridge 起動、 **Then** default 86400 が適用される

### Key Entities

- **`discovery_republish_interval_sec`** (config キー、 default 86400)
- **`DiagState.discovery_republish_total`** (counter)
- **`DiagState.last_discovery_publish_ts`** (ISO8601 timestamp)
- **`publish_ha_discovery()`** (既存関数を pure 化 / 再利用)
- **debounce ロジック** (5 秒の windowed dedupe)

## Edge Cases

- `publish_ha_discovery()` 実行中に再 publish トリガが入る: 排他 (Lock) で skip
- MQTT publish 失敗 (queue full 等): エラーログ + 次回 reconnect 時の再 publish に期待
- 24h interval の起点: 「最後に成功した publish 時刻」基準。 起動時は startup publish を起点
- bridge が長期スリープから復帰 (sleep > interval): 復帰直後に 1 度だけ再 publish (リセットして次の interval から)
- config 変更で interval を縮めた / 伸ばした: 次回 publish 時点から新値で動作 (既存タイマーは再起算)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `apply_defaults` で `discovery_republish_interval_sec` の default を 86400 にする
- **FR-002**: main loop または背景スレッドで前回 publish 時刻からの経過を判定し、 interval 超過で `publish_ha_discovery()` を再走させる
- **FR-003**: `diag_state.on_mqtt_reconnect()` の発火後、 5 秒 debounce で discovery を再 publish する
- **FR-004**: `discovery_republish_interval_sec=0` のとき周期再 publish を無効化 (FR-003 の reconnect 再 publish は維持)
- **FR-005**: `discovery_republish_total` を `/api/metrics` に publish する
- **FR-006**: `last_discovery_publish_ts` を `/api/diag` snapshot に追加する
- **FR-007**: 既存 `publish_ha_discovery()` は副作用なしで複数回 call 可能 (idempotent)

### Key Entities

- `DiagState.discovery_republish_total`: int counter
- `DiagState.last_discovery_publish_ts`: float or None
- `DiagState.on_discovery_republish(now)`: counter inc + ts update

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: MQTT broker を停止→再起動した実機テストで、 30 秒以内に HA 側のセンサーが復活する
- **SC-002**: 24h 連続稼働で `discovery_republish_total >= 1` が観測される (周期再 publish の有効性)
- **SC-003**: `last_discovery_publish_ts` が常に過去 25 時間以内 (interval + ε) になる (周期動作の証跡)
- **SC-004**: 単体テスト: pure 化された republish ロジック (前回 ts と now から republish 要否を返す関数) のテストが成立
- **SC-005**: 既存テスト全件 pass + 既存 discovery publish の挙動は startup 直後に変化なし

## Assumptions

- MQTT publish が retained=True で動作している (既存仕様、 変更なし)
- HA 側は新規 retained discovery を受信したら適切に再構成する
- bridge プロセスの内部 clock (`time.time()`) は monotonic enough (= 24h スパンで NTP 補正される程度の精度で十分)
- `publish_ha_discovery()` が 1 回の呼び出しで 数十 topic 程度 (現状の DIAG_SENSOR_DEFS + 既存センサー定義の総数) を 1 秒以内に publish できる
