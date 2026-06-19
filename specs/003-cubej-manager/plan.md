# Implementation Plan: Embedded Admin Web UI

**Branch**: `003-cubej-manager` | **Date**: 2026-06-19 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-cubej-manager/spec.md`

## Summary

`production_tool/mqtt_bridge.py` に LAN 内 Basic Auth 付き HTTP サーバを同居させる。`config.json` / `wpa_supplicant.conf` / `mqtt_bridge.py` の遠隔編集 + bridge 再起動 / 診断スナップショット参照 / ローカルログ tail を 1 つの埋め込み HTML で提供する。HTTP サーバは別 thread で動作し、Constitution IV (計測パスを劣化させない) と VI (組み込み Web UI Optional / LAN only / 認証必須) を満たす。

主要な実装方針:
- `BaseHTTPServer.HTTPServer` + `BaseHTTPRequestHandler` の Python 2.7 stdlib のみ
- ハンドラ実装は I/O 境界（FileSystem / Process / wpa_cli）を薄いラッパーに分離し、純粋ロジックをユニットテストで TDD
- 埋め込み HTML は Python 文字列定数 1 つ。外部 JS/CSS 依存なし（vanilla JS + minimal CSS）
- atomic write は `tempfile.NamedTemporaryFile(dir=...)` + `os.rename` で実装

## Technical Context

**Language/Version**: Python 2.7（Cube J1 ターゲット）、テストは Python 3.11+
**Primary Dependencies**: Python 2.7 stdlib (`BaseHTTPServer`, `SimpleHTTPServer`, `threading`, `base64`, `hashlib`, `cgi`, `tempfile`, `os`, `subprocess`, `py_compile`)。テスト側 `pytest`, `requests` (host 側 HTTP テスト用)
**Storage**: 既存 `/data/local/config.json`, `/data/local/mqtt_bridge.log`, `/data/misc/wifi/wpa_supplicant.conf`。アトミック書き込み（temp → rename）
**Testing**: `tests/unit/test_admin_*.py` (ハンドラ純粋ロジック) + `tests/integration/test_admin_server.py` (`requests` で HTTPServer 起動して全 API を黒箱テスト) + ベンチ (`tests/benchmark/test_admin_overhead.py` で SC-004)
**Target Platform**: NextDrive Cube J1（armhf Linux + Wi-SUN BP35C0 + Android init, Python 2.7）
**Project Type**: シングルスクリプト `production_tool/mqtt_bridge.py` 拡張
**Performance Goals**:
- `PUT /api/config` p95 < 1s (SC-002)
- `POST /api/update` p95 < 10s (SC-003)
- HTTP サーバ起動による計測パスへの影響は ≤ 10% 悪化 (SC-004)
**Constraints**:
- Python 2.7 stdlib only (NON-NEGOTIABLE / Constitution II)
- USB ブート構造を壊さない (Constitution I)
- 計測パスをブロックしない (Constitution IV)
- LAN 内のみ運用、Basic Auth 必須 (Constitution VI)
- HTTP サーバ起動失敗時も bridge 本体は継続 (FR-004 / SC-005)
**Scale/Scope**: 1 デバイス 1 プロセス。Web UI 同時接続は実質 1-2。アップロード size limit 100KB

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | 対応 | Gate |
|---|---|---|
| I. USB ブート構造を壊さない | `production_tool/` のファイル数・名前は不変。Web UI は `mqtt_bridge.py` 内に閉じ込める | ✅ PASS |
| II. Python 2.7 stdlib only | `BaseHTTPServer` / `cgi` / `base64` 等は 2.7 stdlib。テストの `requests` は host 側のみ | ✅ PASS |
| III. 観測性は MQTT 第一 | Web UI は管理用補助。計測値は引き続き MQTT に流す | ✅ PASS |
| IV. 計測パスを劣化させない | HTTP サーバは daemon thread、handler 例外は main loop に伝播させない。SC-004 で micro-benchmark | ✅ PASS |
| V. TDD | ハンドラ → I/O 境界分離 → 純粋ロジックの pytest。HTTP は integration test で `requests` 黒箱 | ✅ PASS |
| VI. 組み込み Web UI Optional / LAN / Auth | `admin_ui_enabled` 既定 false、Basic Auth 必須、port 8080 既定 | ✅ PASS |

すべての原則で違反なし。Complexity Tracking は空のまま。

## Project Structure

### Documentation (this feature)

```text
specs/003-cubej-manager/
├── plan.md              # This file
├── spec.md              # 作成済み
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── http-api.md     # API contract
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
production_tool/
├── mqtt_bridge.py        # MODIFIED: AdminServer / AdminHandler / 埋め込み HTML 追加
├── config.json           # MODIFIED: admin_ui_enabled / admin_ui_port / admin_user / admin_password 追加（既存 12 キー + 4 = 16 キー）
└── (其他のファイルは不変)

tests/
├── unit/
│   ├── test_admin_auth.py         # NEW: Basic Auth header parser
│   ├── test_admin_config_io.py    # NEW: config.json 読み書きとマスキング
│   ├── test_admin_atomic_write.py # NEW: temp → rename の挙動
│   ├── test_admin_validators.py   # NEW: PUT /api/config の type 検証
│   └── test_admin_html.py         # NEW: 埋め込み HTML の最小 sanity (<title>, <form> 存在)
├── integration/
│   └── test_admin_server.py       # NEW: requests で HTTPServer 起動、全 API 黒箱テスト
├── benchmark/
│   └── test_admin_overhead.py     # NEW: HTTP サーバ ON/OFF で計測パスのレイテンシ比較
└── (其他のテストは既存)
```

**Structure Decision**: シングルスクリプト構造を維持。`mqtt_bridge.py` 内で `AdminConfig` / `AtomicWriter` / `AdminHandler` / `AdminServer` をセクション分離。埋め込み HTML は同ファイル末尾に近い場所に文字列定数。

## Phase 0: Outline & Research

spec の Clarifications で主要決定は確定済み。実装時に確認が必要な技術メモを `research.md` にまとめる:

1. **`BaseHTTPServer.HTTPServer` の挙動 (Python 2.7)**: `serve_forever()` の停止方法、`shutdown()` を別 thread からの呼び出し、`SO_REUSEADDR` の扱い
2. **`cgi.FieldStorage` の multipart 解析**: Python 2.7 で `POST /api/update` の `multipart/form-data` を扱う。memory 上限制御 (`environ["CONTENT_LENGTH"]` 検査)
3. **`py_compile.compile()` の安全性**: アップロードされた `.py` の syntax check だけ行い実行はしない。失敗時の例外ハンドリング
4. **`subprocess` で `wpa_cli` と `stop/start mqtt_ha_bridge` を呼ぶ**: Cube J1 の Android shell 経由でこれらが動くことを確認、stdout/stderr のキャプチャ
5. **`tempfile.NamedTemporaryFile(dir=...)` と `os.rename` の atomic 性**: 同 FS 内で rename を行う。`/data/local/` 内に temp を作成
6. **埋め込み HTML を Python 文字列で持つ運用**: テンプレートエンジン不使用、`%` フォーマットで動的部分を埋め込み。XSS 対策は出力 escape 関数を共通化

Phase 0 では `research.md` でこれらを整理（コード変更なし）。

## Phase 1: Design Artifacts

1. **data-model.md** に `AdminConfig` / `AdminHandler` / `AdminServer` / `AtomicWriter` のフィールドと不変条件を記載
2. **contracts/http-api.md** に全 API（GET / / GET /api/config / PUT /api/config / PUT /api/wifi / POST /api/update / POST /api/restart / GET /api/diag / GET /api/log）の リクエスト / レスポンス / status code / payload schema を記載
3. **quickstart.md** に「ローカル開発時のテストサーバ起動」「Cube J1 への deploy」「ブラウザでの実機確認」の動線を記載
4. agent context 更新

### Post-Design Constitution Re-check

Phase 1 完了後:
- Constitution I: `production_tool/` 配下のファイル数は不変
- Constitution II: 設計上必要な機能がすべて 2.7 stdlib で実装可能
- Constitution IV: HTTP サーバを daemon thread で動かすので main loop ブロックなし
- Constitution VI: 既定 false、認証必須、LAN only 想定

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| なし | — | — |
