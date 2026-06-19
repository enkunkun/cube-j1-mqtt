# Tasks: Embedded Admin Web UI

**Feature**: 003-cubej-manager
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Tests**: REQUIRED (Constitution V — TDD non-negotiable)

実装は t_wada スタイルの Red→Green→Refactor。テストは対応する実装の**前**に書いて Red を確認する。

---

## Phase 1: Setup

- [ ] T001 Add `requests` to `tests/requirements.txt` for integration tests
- [ ] T002 Create `tests/integration/` directory with `__init__.py`

---

## Phase 2: Foundational (blocks all user stories)

### Pure helpers (副作用なし、最初に TDD で固める)

- [ ] T003 [P] `tests/unit/test_admin_auth.py`: Basic Auth header parsing (`AdminConfig.match_basic_auth`). 認証成功 / 認証失敗 / 不正ヘッダ / 空 user/password の case
- [ ] T004 [P] `tests/unit/test_admin_atomic_write.py`: `AtomicWriter.write_bytes` / `write_json` の挙動。temp は同 dir に作る / 書き込み失敗時は target 不変 / `os.fsync` 呼び出し
- [ ] T005 [P] `tests/unit/test_admin_validators.py`: `validate_config_patch` / `validate_wifi_patch` の各バリデーション
- [ ] T006 [P] `tests/unit/test_admin_config_io.py`: `config.json` を読んで `admin_password` を `"***"` にマスクするロジック
- [ ] T007 [P] `tests/unit/test_admin_log_tailer.py`: `tail_log(path, n)` の挙動。N クランプ / 改行検索 / ファイル未存在
- [ ] T008 Implement `AdminConfig` in `production_tool/mqtt_bridge.py` per data-model.md
- [ ] T009 Implement `AtomicWriter` in `production_tool/mqtt_bridge.py`
- [ ] T010 Implement `validate_config_patch` / `validate_wifi_patch` in `mqtt_bridge.py`
- [ ] T011 Implement `read_config_masked` (config.json 読み取り + マスク) in `mqtt_bridge.py`
- [ ] T012 Implement `tail_log` in `mqtt_bridge.py`

**Gate**: T003-T012 のテストが全部緑で次フェーズへ。

---

## Phase 3: User Story 1 — config 編集 (Priority: P1) — MVP

**Goal**: ブラウザで `:8080/` を開いて `config.json` を編集できる。

### Tests (Red 先行)

- [ ] T013 [US1] `tests/unit/test_admin_html.py`: 埋め込み HTML が `<title>Cube J1 Admin</title>` を含み、`<form id="config-form">` セクションがある
- [ ] T014 [US1] `tests/integration/test_admin_server.py` の最小骨組み: AdminServer 起動 → 401 (auth なし) → 200 (auth あり) → `/api/config` で JSON が返る
- [ ] T015 [US1] `tests/integration/test_admin_server.py` の続き: `PUT /api/config` で `log_level` を変えて 200、再度 GET で反映確認
- [ ] T016 [US1] `tests/integration/test_admin_server.py` の続き: `admin_password` が `"***"` なら現値維持

### Implementation (Green)

- [ ] T017 [US1] `AdminHandler.do_GET("/")` で `ADMIN_HTML` を返す in `mqtt_bridge.py`
- [ ] T018 [US1] `AdminHandler.do_GET("/api/config")` でマスク済み config を返す
- [ ] T019 [US1] `AdminHandler.do_PUT("/api/config")` で `validate_config_patch` → `AtomicWriter.write_json`
- [ ] T020 [US1] `AdminHandler._authenticate` を all `/api/*` and `/` に適用
- [ ] T021 [US1] `start_admin_server` / `AdminServer` クラスを実装
- [ ] T022 [US1] `main()` に AdminServer 起動を組み込み（`admin_ui_enabled` チェック、エラーは LOGGER に残して main loop は継続）
- [ ] T023 [US1] `apply_defaults` に新規 4 キーを追加（`admin_ui_enabled=False`, `admin_ui_port=8080`, `admin_user=""`, `admin_password=""`）

### Verification

- [ ] T024 [US1] 全 unit + integration テスト緑
- [ ] T025 [US1] ホスト Python 3 で `python -m pytest tests/integration/test_admin_server.py -v` を走らせて全 API パターン緑

**Checkpoint**: US1 MVP 完了。残りの機能なしでもブラウザから config 編集できる。

---

## Phase 4: User Story 2 — mqtt_bridge.py アップデート (Priority: P2)

### Tests

- [ ] T026 [P] [US2] `tests/unit/test_admin_upload_validator.py`: アップロード時の拡張子チェック / size チェックの純粋ロジック
- [ ] T027 [US2] `tests/integration/test_admin_server.py` に: `POST /api/update` を 100KB の `.py` で叩いて 200、syntax error の `.py` で 400、200KB で 413、`.txt` で 415

### Implementation

- [ ] T028 [US2] `AdminHandler.do_POST("/api/update")` で multipart 受信、size limit、`py_compile`、`AtomicWriter` 経由で置換、200ms 遅延 restart in `mqtt_bridge.py`
- [ ] T029 [US2] `AdminHandler.do_POST("/api/restart")` で `stop/start mqtt_ha_bridge` を 200ms 遅延で発火
- [ ] T030 [US2] `restart_bridge()` helper を `subprocess.Popen` で実装

### Verification

- [ ] T031 [US2] 全テスト緑
- [ ] T032 [US2] 実機 smoke: ローカルの `mqtt_bridge.py` をわずかに変更（例えば `BRIDGE_SEMVER` を `1.0.1` に） → ブラウザからアップロード → Uptime が 0 にリセット & Bridge Version が `1.0.1+...` に変わるのを HA で確認

**Checkpoint**: US2 完了。USB/ADB なしで bridge アップデート可能。

---

## Phase 5: User Story 3 — Wi-Fi 設定 (Priority: P3)

### Tests

- [ ] T033 [P] [US3] `tests/unit/test_admin_wifi_writer.py`: `wpa_supplicant.conf` のテンプレートを SSID/PSK で書き換える純粋ロジック。テンプレートを破壊しない (`freq_list`, `scan_ssid` 等は保持)
- [ ] T034 [US3] `tests/integration/test_admin_server.py` に: `PUT /api/wifi` で SSID/PSK 変更後、テスト用ファイルが atomic に書き換えられる

### Implementation

- [ ] T035 [US3] `write_wpa_supplicant_conf(path, current_content, ssid, psk)` を pure function で実装
- [ ] T036 [US3] `AdminHandler.do_PUT("/api/wifi")`: validate → write → `subprocess.Popen(["wpa_cli", ...])` でリロード
- [ ] T037 [US3] HTML の Wi-Fi セクションに「⚠️ AP の入力ミスで Cube J1 が見えなくなります」警告

### Verification

- [ ] T038 [US3] 全テスト緑

**Checkpoint**: US3 完了。

---

## Phase 6: User Story 4 — 診断 & ログ (Priority: P3)

### Tests

- [ ] T039 [P] [US4] `tests/integration/test_admin_server.py` に: `GET /api/diag` で `DiagState.snapshot()` が返る
- [ ] T040 [P] [US4] `tests/integration/test_admin_server.py` に: `GET /api/log?lines=10` で 10 行返る、`lines=2000` は 1000 にクランプ

### Implementation

- [ ] T041 [US4] `AdminHandler.do_GET("/api/diag")` で `diag_state_provider().snapshot(time.time())` を返す
- [ ] T042 [US4] `AdminHandler.do_GET("/api/log")` で `tail_log` を呼ぶ
- [ ] T043 [US4] HTML の Diagnostics セクションに JS で fetch + 表示

### Verification

- [ ] T044 [US4] 全テスト緑

**Checkpoint**: US4 完了。spec のスコープ完成。

---

## Phase 7: Polish & Cross-Cutting

- [ ] T045 [P] `tests/benchmark/test_admin_overhead.py`: AdminServer ON/OFF で計測パスのレイテンシ中央値を 1000 iter で比較。+10% 以内を assert (SC-004)
- [ ] T046 [P] `readme.md` に「組み込み Web UI」セクション追加。`admin_ui_enabled` の説明と quickstart リンク
- [ ] T047 [P] `secrets/config.json` の template に admin 4 キーを追加（local 開発用）
- [ ] T048 Verify `production_tool/` は依然 8 ファイル（Constitution I gate）
- [ ] T049 全 unit + integration + benchmark テストで `pytest tests/ -q` 緑
- [ ] T050 実機 smoke: prepare_usb.sh で焼き直し → Cube J1 起動 → ブラウザで `:8080/` → config 編集 + アップロード + 診断パネル全部触る

---

## Dependencies

```
Phase 1 Setup ──► Phase 2 Foundational ──► Phase 3 US1 (MVP)
                                        └► Phase 4 US2
                                        └► Phase 5 US3
                                        └► Phase 6 US4
Phase 7 Polish ──► after all user stories
```

US1〜US4 は実装単位で互いに独立（spec で User Story を分けた狙い）だが、すべて同じ `AdminHandler` クラスを編集するので**直列**実装が現実的。並列の余地はテストだけ。

## MVP Scope

US1 (Phase 1+2+3, T001-T025) で MVP。これだけで「ブラウザから設定を見て編集」が動く。USB 抜き差し頻度を 1/10 程度に減らせる。

## Parallel Execution Examples

- **Phase 2 のテスト**: T003〜T007 は別ファイル、 [P] 並列可
- **Phase 3-6 の Implementation**: 同 `mqtt_bridge.py` を触るので直列
- **Phase 3-6 の Tests**: 別ファイル / 別 fixture なら [P] 並列可
- **Phase 7**: T045 (benchmark) と T046 (readme) と T047 (secrets template) は別ファイル、 [P]

## Validation Checklist

- [x] 全タスクに ID, file path, story label が付与されている
- [x] US1 がスタンドアロン MVP として機能する
- [x] テストタスクが対応する実装タスクの前に並んでいる
- [x] Constitution V (TDD) に従いテスト先行
- [x] Constitution I (USB ブート構造) gate を T048 に設定
- [x] Constitution IV (計測パスを劣化させない) gate を T045 (benchmark) に設定
- [x] Constitution VI (Optional / 認証必須) は T020 + T022 + T023 で担保
