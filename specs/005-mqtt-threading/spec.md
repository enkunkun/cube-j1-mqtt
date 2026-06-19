# Feature Specification: Threaded MQTT client (decouple from poll loop)

**Feature Branch**: `005-mqtt-threading`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "MQTT クライアント送受信を独立スレッド化して、Wi-SUN poll が詰まっても broker session を維持する"

## Background

現状の bridge メインループは単一スレッドで動作し、ECHONET Lite の同期 poll（B-route の `SKSENDTO` → `ERXUDP` 待ち、最大 30 秒）と MQTT publish / PINGREQ 送信が直列に動く。Wi-SUN リトライや broker 接続復旧が同時発生すると 60 秒以上ブロックし、broker から `exceeded timeout` で切断される事象が実機で `約 12 分間隔`（実測、 2026-06-19 mosquitto ログ）に発生していた。

004 完了後の hot-fix（spec 外）として MQTT keep-alive を 60s → 300s に引き上げ、切断頻度は 1/5 程度に下がった。ただし `consecutive_erxudp_timeouts >= 5` で `wisun_reconnect` が走るパス（5 分弱）を踏むと 300s も踏み越えうるため、`これは症状緩和であって根本解決ではない`。

本 spec は MQTT クライアントの送信ワーカ / keepalive ワーカ をデーモンスレッドに分離し、メインスレッド（poll ループ）が何秒詰まっても broker session が維持される構造にする。

## Scope

- `MQTTClient` 内部に **送信ワーカスレッド**（publish queue を flush）と **keepalive スレッド**（PINGREQ を定期送信）を追加
- メインスレッド側から見た公開 API は変えない（`mqtt.publish(topic, payload, retain=False)` は **non-blocking enqueue**、戻り値 `None`）
- 既存の `collections.deque` を `Queue.Queue`（Py2）/ `queue.Queue`（Py3）に置き換え、 lock-free な put/get で thread-safe にする
- 再接続ロジック（`_reconnect()` 相当）はワーカスレッド内に閉じ込め、メインスレッドからの publish 呼び出しはネットワーク I/O を**一切しない**
- 観測: `mqtt_queue_depth`（瞬間値）、 `mqtt_publish_dropped_total`（queue 飽和でドロップした件数）、 `mqtt_ping_failures_total`（PINGREQ 送信失敗回数）を `DiagState` に追加
- shutdown: メインスレッド終了時にワーカスレッドへ stop sentinel を送って graceful 停止（5 秒で join、超過したら諦める）

## Non-Scope

- ECHONET Lite poll 側のスレッド化（別 spec）。`SKSENDTO` / `ERXUDP` 周りは依然として同期 I/O のまま
- MQTT QoS 1/2 への対応（現状 QoS 0 publish のまま）。queue が無限に積み上がる場合は drop する（後述）
- TLS / mTLS（broker は LAN 内 mosquitto、 平文 1883 のまま）
- subscribe（bridge は publisher 専用）
- 既存の `MqttClient` を別 class に分割するリファクタ。同じ `MQTTClient` の中で thread を抱える設計にする
- MQTT broker 側の retain / LWT 設定（mosquitto 側の独立タスク）

## User Scenarios *(mandatory)*

### Primary User Story

開発者として、Cube J1 で Wi-SUN poll が一時的にスタックしても、その間 broker session が切れず、回復後に滞留した publish が即時 flush されてほしい。Grafana ダッシュボード `cubej1-smart-meter` で `mqtt_reconnects_total` の傾きが 0/日 に張り付くこと、また `mqtt_queue_depth` が時々スパイクして 0 に戻ることが観測できる。

### Acceptance Scenarios

1. **Given** bridge が起動済みで MQTT 接続 OK、 **When** メインスレッドが `time.sleep(120)` を実行（poll スタックの模擬）、 **Then** その 120 秒間に PINGREQ がデーモンスレッドから少なくとも 1 回送信され、 broker は `exceeded timeout` を起こさない
2. **Given** bridge が起動済み、 **When** メインスレッドから `mqtt.publish(topic, payload)` を呼ぶ、 **Then** 戻り値は `None` で呼び出しは 10ms 以内に返る（実 I/O は worker thread で非同期実行）
3. **Given** broker が一時的に到達不能、 **When** worker が `_reconnect()` を試行中、 **Then** メインスレッドは引き続き publish を queue に積めて停滞しない
4. **Given** queue 上限 1000 件に達した状態、 **When** さらに publish される、 **Then** 古い entry が drop され `mqtt_publish_dropped_total` がインクリメントされる
5. **Given** bridge が SIGTERM を受信、 **When** main が終了処理に入る、 **Then** worker thread に stop sentinel が送られ、 残った queue を 5 秒以内に flush して終了する

### Key Entities

- **MQTT send worker thread**: `MQTTClient._sender_loop`。`Queue.get(timeout=1)` で pull → `_send_one` で TCP write → 失敗時は `_reconnect` を呼ぶ
- **MQTT keepalive thread**: `MQTTClient._keepalive_loop`。`time.sleep(keepalive/2)` ごとに PINGREQ を直接 socket write。送信失敗は worker thread が拾う再接続のトリガとして共有 `Event` を立てる
- **`send_queue`**: `Queue.Queue(maxsize=1000)`。FIFO、`put(block=False)` で飽和ハンドル
- **`stop_event`**: `threading.Event`。shutdown の合図
- **`reconnect_event`**: `threading.Event`。worker と keepalive 間の「再接続必要」シグナル

## Edge Cases

- **ワーカ起動失敗**（thread が start できない、 OS リソース不足）: `start_workers()` で raise → main が catch して LOGGER に書いて legacy single-thread モードに fallback（後方互換）。`mqtt_threading_enabled=False` の設定でも明示的に legacy にできる
- **socket write 中に worker が `_reconnect` でブロック**: keepalive thread からの PINGREQ も同じ socket を使うので race する。socket への write は `self._send_lock`（`threading.Lock`）で直列化する
- **publish の payload が dict（json 化必要）**: 既存実装と同じく `_make_pkt` でその場で serialize。worker thread 内で行う（main thread は raw object を put するだけ）
- **queue 飽和直前のメッセージ**: drop ポリシーは「**oldest first**」（FIFO drop）。最新の電力値は失わない（HA dashboard で「現在値」が大事だから）
- **broker が `0xA0` SUBSCRIBE で reset**: bridge は publish 専用なので無視（受信ハンドラを実装しない）
- **gateway は変えない**: ADB hot-reload (spec 004) でデプロイ可能。USB 焼き直し不要

## Success Criteria *(mandatory)*

- **SC-001 [observability]**: 24 時間連続稼働で `mqtt_reconnects_total` が 0（または手動再起動の 1〜2 回のみ）。 現行の `12 分/件` 切断が解消される
- **SC-002 [latency]**: `mqtt.publish()` 呼び出しの 99 パーセンタイル所要時間がメインスレッドから見て 1ms 以下（local bench、 broker disconnected 時を含む）
- **SC-003 [no main-thread network I/O]**: メインスレッドの stack trace 中に `socket.send` / `socket.recv` が出現しない（bench で profile）
- **SC-004 [graceful shutdown]**: SIGTERM 送信から worker thread join 完了まで 5 秒以内、 queue 末尾の publish も含めて 95% 以上が flush される
- **SC-005 [backwards-compat / Constitution VI]**: `mqtt_threading_enabled=False` を config で渡すと従来通り single-thread で動く（fallback パス）
- **SC-006 [test-only injection]**: 受信側に fake socket を差し込んで、 「main が 120 秒 sleep する間に PINGREQ が ≥1 回送られる」 ことを unit test で再現できる

## Assumptions

- Python 2.7.13 / 3.11+ いずれでも `threading` / `Queue.Queue`（Py2）/ `queue.Queue`（Py3）が使える（stdlib のみ、 Constitution II 遵守）
- 1 つの MQTT broker socket への `send` は `_send_lock` で直列化すれば thread-safe（mosquitto は同時 publisher を別 client_id で扱うので、 同 client_id の単一 socket からの concurrent write は OS レベルで avoid する必要あり）
- `keepalive=300` のままで運用、 PINGREQ は `keepalive / 2 = 150 秒`間隔（pre-emptive）
- queue 上限 1000 件は 1 秒に 10 件 publish しても 100 秒分のバッファ、 通常運用には十分（電力値は 30 秒間隔）

## Dependencies

- `production_tool/mqtt_bridge.py` の `MQTTClient` 既存実装（編集対象）
- `DiagState`（メトリクス追加先）
- `apply_defaults`（`mqtt_threading_enabled` キー追加）
- spec 001 の logger（worker thread からの構造化ログ）
- spec 002 の Grafana ダッシュボード（`mqtt_queue_depth` パネル追加 → 別 PR）
