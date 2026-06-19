# Phase 0 Research: Bridge Observability

実装前に確認しておくべき技術項目をまとめる。spec の Clarifications で要求事項は確定済みなので、ここでは「stdlib API の挙動」「既存コードへの差し込み箇所」を整理する。

## R-1. `logging.handlers.RotatingFileHandler` (Python 2.7) の挙動

**結論**: 採用可能。`maxBytes` 到達時、現行ファイルを `.1`, `.1` を `.2` ... と shift し、`backupCount + 1` 個まで保持する。`backupCount = 3` なら `mqtt_bridge.log` + `.1` + `.2` + `.3` の最大 4 ファイル。これは spec の SC-002（合計 ≈ 4 MiB）と一致する。

**確認事項**:
- ローテーション中に `os.rename` が失敗した場合は次回書き込み時にリトライされる（Cube J1 の flash が満杯のときに無限ループしないか）
- マルチスレッドで書き込み中の rotate は `Lock` で直列化される（本 bridge はシングルスレッド main loop なので問題なし）
- `delay=False`（既定）で起動時にファイルを open する。書き込み権限不在時の例外は `IOError` / `OSError`

**実装方針**:
- `JsonLogger` 内部で `RotatingFileHandler(LOG_PATH, maxBytes=cfg.log_max_bytes, backupCount=cfg.log_backup_count, delay=False)` を生成
- handler 構築失敗時は stderr フォールバック（既存挙動踏襲、FR-009）
- `Formatter` は使わず、`emit` 時点で自前で JSON Lines 文字列を組み立てて `handler.stream.write()` する方が依存が薄い → 採用しない。標準の `logging.Logger.<level>(extra={...})` インターフェースを使い、`Formatter` を `json.dumps` ベースのカスタム class にする方が pytest からテストしやすい

## R-2. HA Auto-Discovery payload の最小フィールドセット

**結論**: 既存 `publish_ha_discovery()` の payload と同じ形が診断系にも使える。差分は次の通り。

- 時刻系（`last_poll_success_ts`, `last_poll_failure_ts`）:
  - `device_class: "timestamp"`
  - `state_class` は **無し**（timestamp は state_class を持たない）
  - state 形式: ISO 8601 UTC。HA は `+00:00` も `Z` も両方パースするが、`Z` 形式の方がスマートメーター実装界隈で多く `YYYY-MM-DDTHH:MM:SSZ` を採用
  - 未確定時の publish 値: `last_poll_failure_ts` がまだ無いとき、payload は空文字ではなく **publish しない**（unknown を保つ）。HA Auto-Discovery 仕様では discovery config に `payload_not_available` 等の指定もできるが、本実装では「値が無い間は state を publish しない」方針で十分
- カウンター系（`scan_retries_total`, `wisun_reconnects_total`, `mqtt_reconnects_total`, `erxudp_timeouts_total`）:
  - `state_class: "total_increasing"`
  - `unit_of_measurement` は省略（HA は unit なしの total_increasing を許容）
  - `entity_category: "diagnostic"`
- 計装値（`lqi`, `pan_channel`, `uptime_seconds`）:
  - `state_class: "measurement"`
  - `entity_category: "diagnostic"`
  - `unit_of_measurement`: `uptime_seconds` は `"s"`、`lqi` と `pan_channel` は単位なし
- メタ（`version`）:
  - `state_class` なし、`device_class` なし、`entity_category: "diagnostic"`

**device 統合**: `device.identifiers = [device_id]` を計測センサーと完全一致させれば HA 上で 1 デバイスにまとまる。

## R-3. MQTT retain=true publish

**結論**: 既存 `MQTTClient._make_pkt(topic, payload, retain=False)` は fixed header bit に retain (0x01) を OR する実装になっており、`mqtt.publish(topic, payload, retain=True)` を呼ぶだけで対応完了。改修不要。

**注意**: 既存 `_flush_queue` 経由のリプレイ時も retain flag を保持できるよう、queue tuple は `(topic, payload, retain)` のままで OK（すでにそうなっている）。診断 publish はこの queue に乗せても OK で、broker offline 時も queue 内で「同一 topic は最新のみ」を保つ最適化を入れる（FR Edge Case）。

## R-4. Cube J1 上の時計同期と ISO 8601 タイムスタンプ

**結論**: Cube J1 の Wi-Fi 接続後（`wpa_supplicant` 起動後）に systemd-timesyncd 相当の NTP 同期が走る前提で、bridge 起動時の最初のタイムスタンプは NTP 同期前の epoch（1970 や 2000）の可能性がある。

**対処**:
- `format_iso8601_utc(t)` ユーティリティを `mqtt_bridge.py` 内に置き、`time.gmtime(t)` + `time.strftime("%Y-%m-%dT%H:%M:%SZ", ...)` で組み立てる（マイクロ秒省略）
- bridge 起動直後の `last_poll_success_ts` は最初の成功ポーリングまで未定義のまま（publish しない → R-2 と整合）
- bridge 自身は NTP 同期を待たない（Wi-SUN/MQTT 接続の妨げにならないため）。診断値の絶対時刻が初期に正しくない可能性は spec の Assumption で受け入れ済み

## R-5. 既存 main loop への差し込み箇所

既存 `main()` の構造:

1. `load_config()`
2. `MQTTClient.connect()`（無限リトライ）
3. `publish_ha_discovery(mqtt, device_id)`
4. `open_serial()`
5. `wisun_connect(fd, br_id, br_pwd)`（無限リトライ）
6. main while ループ: `send_el_get` → `read_erxudp` → `parse_el_response` → `decode_measurements` → `apply_energy_scale` → `publish_measurements`

**差し込み計画**:

| 既存箇所 | 追加処理 | 影響 |
|---|---|---|
| `main()` 冒頭 | `BRIDGE_SEMVER`, `BRIDGE_GIT_HASH` 読み込み・`JsonLogger` 初期化・`DiagState` 構築 | `log()` 関数を JsonLogger に置き換える |
| `MQTTClient.connect()` の `_reconnect` 呼び出し時 | `DiagState.on_mqtt_reconnect()` 呼び出し（既存実装をフック） | カウンター更新 |
| `publish_ha_discovery()` | 診断センサー用の payload を追加 publish（既存 SENSOR_DEFS の後に DIAG_SENSOR_DEFS） | retain=true |
| `wisun_connect()` 内 `skscan()` で `duration += 1` する箇所 | `DiagState.on_scan_retry()` | カウンター更新 |
| `wisun_connect()` 成功時 | `DiagState.on_wisun_joined(pan_info)` で LQI と channel を保持 | 値更新 |
| main loop の `send_el_get` 成功時 | `DiagState.on_poll_success()` で last_poll_success_ts 更新 | 値更新 |
| main loop の `read_erxudp` timeout 時 | `DiagState.on_erxudp_timeout()` + `on_poll_failure()` | カウンター更新 |
| main loop の `publish_measurements` 直後 | `publish_diag(mqtt, device_id, diag_state.snapshot())` | retain=true で送信 |
| main loop の `except Exception as e:` 内 | `DiagState.on_wisun_reconnect()`（reconnect 成功時） | カウンター更新 |

**重要**: すべての `DiagState` 更新は try/except で囲み、`publish_diag` 失敗時も計測パスを止めない（Constitution IV / FR-005）。

## R-6. `BRIDGE_GIT_HASH` 埋め込み

**結論**: `scripts/embed_git_hash.sh` を作成し、USB コピー前に手動 or Makefile 等から呼び出す。

```bash
#!/usr/bin/env bash
set -euo pipefail
HASH="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
sed -i.bak "s|^BRIDGE_GIT_HASH = .*|BRIDGE_GIT_HASH = \"${HASH}\"|" production_tool/mqtt_bridge.py
rm -f production_tool/mqtt_bridge.py.bak
```

- 既定値は `BRIDGE_GIT_HASH = "unknown"` を `mqtt_bridge.py` 冒頭に書いておく
- スクリプト未実行でも bridge は起動する（unknown のまま）
- USB 配布前の README に「`scripts/embed_git_hash.sh` を実行してから USB にコピーすること」を追記する

## R-7. Python 2.7 と Python 3 の両対応コーディング規約

被テストコード（`mqtt_bridge.py`）は 2.7 互換を保つ。テストは Python 3 で書く。差分:

- `from __future__ import print_function, absolute_import, unicode_literals`
- 整数除算は `//`
- `str` / `bytes` の扱いは既存コードと同じスタイル（`isinstance(x, int)` で分岐済み）
- 例外チェーンは使わない
- f-string は使わない（`"{}".format(x)` または `%` フォーマット）
- type hints は使わない（コメントで補う）
- `dict.items()` の戻り値が view か list かに依存する処理を書かない

テスト側は普通の Python 3 で書く。`pytest`、`pytest-mock`（fake 注入）、`pytest-benchmark`（SC-004）を host venv に入れる。
