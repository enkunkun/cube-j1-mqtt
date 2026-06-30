# cube-j1-mqtt Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-06-19

## Active Technologies

- Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+ + Python 2.7 stdlib のみ（`logging`, `logging.handlers`, `json`, `socket`, `struct`, `termios`, `select`, `threading`, `time`）。テスト側は `pytest`（host のみ） (001-bridge-observability)

## Project Structure

```text
src/
tests/
```

## Commands

cd src && pytest && ruff check .

## Code Style

Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+: Follow standard conventions

## Recent Changes

- 001-bridge-observability: Added Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+ + Python 2.7 stdlib のみ（`logging`, `logging.handlers`, `json`, `socket`, `struct`, `termios`, `select`, `threading`, `time`）。テスト側は `pytest`（host のみ）

<!-- MANUAL ADDITIONS START -->

## DIAG metric 追加時の必須手順 (= 4 段 pipeline 全段 update)

cube-j1 メトリクス pipeline は **bridge → MQTT broker → telegraf (compose repo) → Prometheus** の 4 段。 bridge 側 (= 本 repo) で `DIAG_SENSOR_DEFS` に entry を追加するだけでは Prometheus / Grafana に届かない。 telegraf の `topics = [...]` 明示列挙への追加が必須。

spec 037/042/044 で連続 3 回この手順を見落とし、 metric が Prometheus に 3 日反映されない事故が発生 (= 2026-06-30 検証で発覚、 compose commit 0ba5dba1 で fix)。 新 metric 追加 spec では必ず以下 checklist を遵守。

### 新 numeric metric 追加 checklist

1. **bridge side** (= 本 repo `production_tool/mqtt_bridge.py`):
   - `DiagState.__init__` で counter 初期化
   - `on_<event>` method 追加
   - `snapshot(now)` で `out["<key>"] = value` (0 でも publish したい時は zero-omit pattern から外す)
   - `DIAG_SENSOR_DEFS` に `(sid, name, unit, dev_class, state_class, "diagnostic")` 追加
2. **compose side** (= `~/git/compose/telegraf/telegraf.conf`):
   - `[[inputs.mqtt_consumer]]` の `topics` list に `"cubej/+/diag/<new_key>"` を追加
   - jj 5 step push (= `~/.claude/rules/jj-workflow.md` 参照)
   - deploy-webhook で telegraf 自動 restart (= 数秒〜数分)
3. **verify** (= 必ず両方):
   - bridge `/api/diag` (= `curl -u admin:<pw> http://cube-j1.home.arpa:8080/api/diag` 経由、 **port 8080**) で値が snapshot に入っているか
   - gcx (= `gcx --context cloud metrics series '{__name__=~"cube_j1_smart_meter_<key>.*"}'`) で metric series 存在 + query 値確認

### 新 string metric 追加時

上記に加え telegraf の `[[processors.starlark]]` namepass = `["mqtt_diag_str"]` の Python source 内で field 変換 (= mode を数値 mapping、 ts を unix epoch 等)。 詳細は `~/.claude/projects/-Users-tendo-git-cube-j1-mqtt/memory/feedback-compose-telegraf-pipeline.md` を参照。

### verify methodology の重要原則

- **gcx で `result: []` (= vector empty) = 「0 件発火」 ではなく「pipeline drop」 を必ず疑う**。 spec 038 Phase 1 で「EVENT 21 = 0 件 / 42h」 と誤判定して spec close した事故 (= 2026-06-30 reopen、 実際は 67 件 / 24h 計上) の root cause がこれ。
- gcx empty → bridge `/api/diag` 直接 snapshot で実値を確認 → 値あれば pipeline drop 確定 → telegraf.conf 確認
- admin UI HTTP port は **8080** (= memory で誤って 8000 と記載していた経緯あり、 8080 が正)

### 関連参照

- memory `feedback-compose-telegraf-pipeline` = pipeline 全 4 段の詳細
- memory `feedback-phase1-event21-zero-erxudp-rx-dominant` = 🚫 INVALIDATED、 反面教師として残置
- audit findings `docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md` = spec 037/042/044 の verify 状況

<!-- MANUAL ADDITIONS END -->
