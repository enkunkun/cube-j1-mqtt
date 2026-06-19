# Implementation Plan: Bridge Observability (Diagnostic MQTT + Structured Logging)

**Branch**: `001-bridge-observability` | **Date**: 2026-06-19 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-bridge-observability/spec.md`

## Summary

`production_tool/mqtt_bridge.py` を改修し、bridge 自身の診断情報（成功/失敗 timestamp・LQI・カウンター・uptime・self-version）を `cubej/<device_id>/diag/<key>` に **retain=true** で publish する。HA Auto-Discovery で計測センサーと同一デバイスに統合する。ローカルログは `logging.handlers.RotatingFileHandler` で JSON Lines 形式 + 1 MiB × 3 世代 + レベル分けに置き換える。診断・ログの追加は計測パスを劣化させない（best-effort、SC-004 で micro-benchmark）。

技術的アプローチ:
- 既存 `mqtt_bridge.py` の I/O 抽象化を最小限に進め、純粋関数（DiagState・JSON ログフォーマッタ・HA discovery payload ビルダー）を切り出す
- 純粋関数は Python 3 で書いたユニットテストで TDD する（被テストコードは 2.7 互換）
- I/O 境界（MQTTClient, SerialPort, 時計, ファイル）は薄いラッパーに分離して、テストでは fake を差し込む
- ビルドスクリプトを `production_tool/` のコピー前に走らせ、`BRIDGE_GIT_HASH` を mqtt_bridge.py に埋め込む

## Technical Context

**Language/Version**: Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+
**Primary Dependencies**: Python 2.7 stdlib のみ（`logging`, `logging.handlers`, `json`, `socket`, `struct`, `termios`, `select`, `threading`, `time`）。テスト側は `pytest`（host のみ）
**Storage**: Cube J1 上の `/data/local/`（config.json, mqtt_bridge.log + ローテーション世代）
**Testing**: `pytest` ユニットテスト + micro-benchmark（`tests/unit/`, `tests/benchmark/`）。HW 依存箇所は fake で代替、シリアル/MQTT/LED は接触させない
**Target Platform**: NextDrive Cube J1（armhf Linux + Wi-SUN BP35C0、USB ブート）
**Project Type**: シングルスクリプト（`production_tool/mqtt_bridge.py`）+ サポートビルドスクリプト
**Performance Goals**: 計測ポーリング 1 周あたりの「ポスト計測処理」（診断集計 + ログ書き出し + MQTT publish）の median が、診断/ログ無効化ベースラインの +10% 以内（SC-004）
**Constraints**:
- Python 2.7 stdlib only（NON-NEGOTIABLE / Constitution II）
- USB ブート構造を壊さない（NON-NEGOTIABLE / Constitution I）。`production_tool/` 既存 6 ファイル名は維持
- 計測パスをブロックしない（Constitution IV、FR-005）
- ローカルログ合計サイズ上限 ≈ 4 MiB（既定、SC-002）
**Scale/Scope**: 改修対象は `production_tool/mqtt_bridge.py`（〜780 行）+ 新規 build スクリプト 1 本 + テスト群。1 デバイス 1 プロセス、ポーリング周期 60s、MQTT publish の頻度は計測 + 診断あわせて 1 ポーリングあたり 11 トピック程度

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | 対応 | Gate |
|---|---|---|
| I. USB ブート構造を壊さない | `production_tool/` 既存 6 ファイル名・rc 契約は維持。追加するのは `mqtt_bridge.py` 内ロジックと、リポジトリ直下の build スクリプト（USB にコピーされない）と tests/ のみ | ✅ PASS |
| II. Python 2.7 stdlib only | `logging` / `logging.handlers.RotatingFileHandler` は 2.7 stdlib。`json`, `socket`, `struct` も既出。新規依存ゼロ | ✅ PASS |
| III. 観測性は MQTT 第一 | FR-001〜005 が diag publish。ローカルログは補助 | ✅ PASS |
| IV. 計測パスを劣化させない | 診断/ログ追加は try/except でラップし計測パスへ伝播させない。SC-004 micro-benchmark で測定 | ✅ PASS |
| V. TDD | 純粋関数（DiagState, ログフォーマッタ, HA discovery builder, version 文字列組み立て）を抽出して Red→Green→Refactor。I/O 境界は fake 注入 | ✅ PASS |

すべての原則で違反なし。Complexity Tracking は空のまま。

## Project Structure

### Documentation (this feature)

```text
specs/001-bridge-observability/
├── plan.md              # This file
├── spec.md              # Already created
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (MQTT topic & HA discovery schemas)
│   ├── mqtt-topics.md
│   └── ha-discovery.json
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
production_tool/
├── mqtt_bridge.py        # MODIFIED: diag + JSON logger を内包。BRIDGE_SEMVER 定数追加
├── config.json           # MODIFIED: log_level, log_max_bytes, log_backup_count を任意追加（既存キーは不変）
├── led_effect.sh         # unchanged
├── mqtt_ha_bridge.rc     # unchanged
├── ndeclite_disabled.rc  # unchanged
├── wisund_disabled.rc    # unchanged
└── wpa_supplicant.conf   # unchanged

scripts/
└── embed_git_hash.sh     # NEW: USB 配布前に mqtt_bridge.py の BRIDGE_GIT_HASH を git short hash で書き換える

tests/
├── unit/
│   ├── test_diag_state.py        # NEW: DiagState の集計・snapshot
│   ├── test_json_logger.py       # NEW: JSON Lines フォーマット & ローテーション
│   ├── test_ha_discovery.py      # NEW: 計測・診断 discovery payload の構造
│   ├── test_version_string.py    # NEW: SemVer + git hash 組み立て
│   └── test_existing_pure.py     # NEW: 既存純粋関数 (build_el_get / parse_el_response / decode_measurements / apply_energy_scale) のリグレッション
├── benchmark/
│   └── test_post_poll_latency.py # NEW: SC-004 検証（ベースライン比 +10% 以内）
└── conftest.py                   # NEW: 共通 fixture
```

**Structure Decision**: シングルスクリプト構造を維持する。`mqtt_bridge.py` 内で `DiagState`, `JsonLogger`, `ha_discovery_for_diag()`, `bridge_version()` といった「副作用を持たない関数/クラス」を明確にセクション化して切り出す（モジュール分割しない理由: USB ブートで自動展開される `production_tool/` 配下にファイルを増やしたくない / Cube J1 上で import path を維持するため）。テストはホスト側で `sys.path.insert(0, "production_tool")` してロードする。

## Phase 0: Outline & Research

### 未解決事項（NEEDS CLARIFICATION）

spec の Clarifications で 4 件すべて解消済み。`Technical Context` 上にも追加の NEEDS CLARIFICATION なし。

ただし以下は実装時に確認が必要な技術メモを `research.md` にまとめる:

1. **Python 2.7 `logging.handlers.RotatingFileHandler` の挙動**: maxBytes 到達時のローテーション順序、ファイル名命名規則（`.log.1` `.log.2` ...）、close 後の rotate 失敗時の挙動
2. **HA Auto-Discovery payload の最小フィールドセット**: 診断系センサーで `entity_category: diagnostic` を有効にする場合の必要キー、`device_class: timestamp` 採用時の state 形式（ISO 8601 with `Z` か `+00:00` か）
3. **MQTT 3.1.1 retain flag の publish 形式**: 既存 `MQTTClient._make_pkt` の fixed header bit 配置を確認、retain bit (0x01) は既に対応している
4. **Cube J1 上の `time.time()` / 時計同期**: Wi-SUN/MQTT 接続より前に NTP 同期が走るか。同期前タイムスタンプの扱い
5. **既存 main loop の責務分割の最小単位**: try/except スコープを既存どおりに保ちつつ、診断更新とログ書き出しを差し込む位置

Phase 0 では `research.md` を作成して上記を整理する（コード変更は行わない）。

## Phase 1: Design Artifacts

1. **data-model.md** に `DiagState` / `LogEvent` / `HADiscoveryPayload` のフィールドと不変条件を記載
2. **contracts/mqtt-topics.md** に publish topic と payload 仕様（計測 + 診断 すべて）を記載
3. **contracts/ha-discovery.json** に診断系 5+ センサーの discovery payload サンプルを記載（HA 側の MQTT 統合に手動で投げて UI 上の見え方を確認できる形式）
4. **quickstart.md** に「lab-ub01 の Mosquitto を立てる手順 → Cube J1 への USB コピー → HA 上の見え方確認」までの動線を記載
5. agent context update スクリプト（`.specify/scripts/bash/update-agent-context.sh`）を実行して CLAUDE.md 等を更新

### Post-Design Constitution Re-check

Phase 1 完了後、設計成果物が以下を満たしているか再確認:
- Constitution I: `production_tool/` 配下にファイル追加なし（既存 6 ファイルの中身改変のみ）
- Constitution II: 設計上必要な機能がすべて 2.7 stdlib で実装可能であること
- Constitution V: data-model.md のエンティティが I/O 副作用を含まないこと

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| なし | — | — |
