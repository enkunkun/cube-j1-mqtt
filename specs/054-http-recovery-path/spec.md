# Feature Specification: HTTP からの復旧経路（adbd の立ち上げ直しと更新の上限）

**Feature Branch**: `054-http-recovery-path`
**Created**: 2026-10-05
**Status**: Draft
**Input**: 2026-10-05、実機の adb が `device offline` になり、5555/tcp が閉じていた（実機側の adbd が止まっていた）。管理 Web UI（8080）は応答していたが、HTTP から adbd を戻す手段も、HTTP で bridge を更新する手段もなく、実機の電源を抜き差しして復旧した。

## 背景

- 実機の bridge は 7 月 4 日の版（`1005c73`）で止まっていた。spec 041 の記録にもあるとおり、8 月以降は adb が使えず、更新を入れられなかった
- `POST /api/update`（spec 003 FR-012）は、受け付けるファイルを 100 KB までに制限している。`mqtt_bridge.py` は 7 月の時点で 237 KB、現在 241 KB あり、HTTP での更新は以前から使えない状態だった
- 起動スクリプト（`production_tool/production_tool`）は `setprop service.adb.tcp.port 5555` → `stop adbd` → `start adbd` で adb を TCP で有効にしている。電源の抜き差しで adb が戻ったのは、`persist.adb.tcp.port 5555` が残っていたため

## Requirements

- **FR-001**: `POST /api/adb/restart` を追加する。Basic 認証（既存の `_authenticate`）を通ったときだけ、応答 `{"status": "restarting adbd"}` を返してから、0.2 秒後に `setprop service.adb.tcp.port 5555` → `stop adbd` → `start adbd` を順に実行する（`_restart_bridge_async` と同じく timer thread から `subprocess.Popen(...).wait()` で呼ぶ）。コマンドの失敗は log に残し、bridge は止めない
- **FR-002**: `POST /api/update` の上限を 100 KB から **512 KB** に上げ、413 の文言も合わせる。現在の 241 KB に対して 2 倍以上の余裕を持たせる
- **FR-003**: 管理 Web UI の画面にボタンは足さない（API だけ）。復旧は手元から `curl -u ... -X POST .../api/adb/restart` で行う
- **NFR-001**: Python 2.7 stdlib のみ（Constitution II）。管理 API は認証必須・LAN 内のみ（Constitution VI）の原則を変えない
- **NFR-002**: 計測パス（Wi-SUN の poll と MQTT publish）に影響を与えない（Constitution IV）。adbd の立ち上げ直しは別 thread で行う

## Success Criteria

- **SC-001**: 単体テストで、`/api/adb/restart` が認証なしでは 401、認証ありでは 200 を返し、`setprop` → `stop adbd` → `start adbd` の順にコマンドが呼ばれる（コマンド実行は差し替えて確かめる）
- **SC-002**: 単体テストで、`/api/update` が 512 KB 以下を受け付け、512 KB を超えると 413 を返す
- **SC-003**: 実機で `POST /api/adb/restart` を呼んだあと、lab-ub01 から `adb connect 192.168.1.103:5555` が `device` になる
- **SC-004**: 実機で、`POST /api/update` に現在の `mqtt_bridge.py`（241 KB）を送って更新できる

## Out of Scope

- 実機そのものの再起動 API（`reboot`）。adbd と bridge の立ち上げ直しで足りる範囲に留める
- adbd の死活を bridge が監視して自動で立ち上げ直す仕組み
