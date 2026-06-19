# Tasks: Bridge Observability (Diagnostic MQTT + Structured Logging)

**Feature**: 001-bridge-observability
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Tests**: REQUIRED (Constitution V — TDD non-negotiable)

すべての実装タスクは t_wada スタイルの Red→Green→Refactor で進める。テストタスクは対応する実装タスクの**前**に完了し、Red 状態を確認する。

---

## Phase 1: Setup

- [ ] T001 Create `tests/` directory tree and `tests/conftest.py` adding `production_tool` to `sys.path`
- [ ] T002 Create `tests/requirements.txt` listing host-side deps: `pytest`, `pytest-benchmark`
- [ ] T003 [P] Add `BRIDGE_SEMVER = "1.0.0"` and `BRIDGE_GIT_HASH = "unknown"` constants near the top of `production_tool/mqtt_bridge.py` (no behaviour change yet)
- [ ] T004 [P] Create `scripts/embed_git_hash.sh` that rewrites `BRIDGE_GIT_HASH = ...` in `production_tool/mqtt_bridge.py` using `git rev-parse --short HEAD`, fallback `unknown`
- [ ] T005 [P] Update `readme.md` "導入方法" with note "`scripts/embed_git_hash.sh` を USB コピー前に実行する（省略時は version が `<SemVer>+unknown` になる）"

---

## Phase 2: Foundational (blocks all user stories)

Extract pure helpers from `mqtt_bridge.py` into testable sections **without changing existing behaviour**. This phase is the safety net that lets later TDD phases proceed without touching I/O.

- [ ] T006 Write regression unit tests for existing pure helpers in `tests/unit/test_existing_pure.py`: `build_el_get`, `parse_el_response`, `decode_measurements`, `apply_energy_scale`, `_encode_remaining`, `_encode_str`. Use byte fixtures captured from current code. Tests must PASS against unmodified `mqtt_bridge.py`
- [ ] T007 Add `format_iso8601_utc(epoch: float) -> str` helper to `mqtt_bridge.py` and unit test it in `tests/unit/test_format_iso8601.py` (verify `Z` suffix, no microseconds, leap-second safe)
- [ ] T008 Add `bridge_version()` function to `mqtt_bridge.py` returning `"{semver}+{hash}"` and unit test in `tests/unit/test_version_string.py`. Cover: default `unknown`, valid hex hash, invalid semver caught (assert form not validity)

**Gate**: All tests in T006/T007/T008 are green before any later phase begins.

---

## Phase 3: User Story 1 — HA から bridge 健全性が見える (Priority: P1) — MVP

**Goal**: HA のデバイスページに、計測 5 件と並んで診断 10 件のセンサーが表示される（spec User Story 1）。

**Independent Test**: Mosquitto + HA を立てて bridge を実機 (or simulator) で動かし、`http://homeassistant.lab-ub01.home.arpa:8123/config/devices/dashboard` で "Cube J1 Smart Meter" に 15 センサーが現れる。

### Tests (Red 先行)

- [ ] T009 [P] [US1] `tests/unit/test_diag_state.py`: DiagState のすべての `on_*()` メソッドと `snapshot()` が `data-model.md` の不変条件を満たす（カウンター単調非減少 / None attribute は snapshot から除外 / uptime 計算 / snapshot key 順序固定）。fake time 注入で `start_time` を制御
- [ ] T010 [P] [US1] `tests/unit/test_ha_discovery.py`: 計測 5 件 + 診断 10 件の discovery payload が `contracts/ha-discovery.json` のサンプルと完全一致する（device.identifiers 共有確認、entity_category=diagnostic 確認、state_class 一致確認）
- [ ] T011 [P] [US1] `tests/unit/test_diag_publish.py`: MQTT クライアントの fake を使い、`publish_diag(fake_mqtt, device_id, snapshot)` が retain=True で 10 件の `cubej/<id>/diag/<key>` トピックに publish することを検証。None attribute は publish されない

### Implementation (Green)

- [ ] T012 [US1] Implement `DiagState` class in `production_tool/mqtt_bridge.py` per `data-model.md`. Section header comment to make it findable. All counters init to 0, optional fields init to None
- [ ] T013 [US1] Implement `DIAG_SENSOR_DEFS` list and `publish_ha_discovery_diag(mqtt, device_id)` in `mqtt_bridge.py`. Reuse `device` dict from existing `publish_ha_discovery()` to enforce identifier sharing
- [ ] T014 [US1] Extend `publish_ha_discovery(mqtt, device_id)` to also call `publish_ha_discovery_diag(...)` so the existing single entry point publishes both sets
- [ ] T015 [US1] Implement `publish_diag(mqtt, device_id, snapshot)` in `mqtt_bridge.py` that publishes each non-None snapshot entry to `cubej/<id>/diag/<key>` with **retain=True**
- [ ] T016 [US1] Wire `DiagState` updates into existing code paths per `research.md` R-5:
  - `main()`: instantiate `diag_state = DiagState(version=bridge_version(), start_time=time.time())`
  - `skscan()`: call `diag_state.on_scan_retry()` at each retry
  - `wisun_connect()`: call `diag_state.on_wisun_joined(pan)` on success
  - `MQTTClient._reconnect()` (success path): call `diag_state.on_mqtt_reconnect()` — pass DiagState reference via closure or attribute
  - main loop: `on_poll_success(now)` after successful `publish_measurements`, `on_erxudp_timeout()` + `on_poll_failure(now)` on `read_erxudp` returning None, `on_wisun_reconnect()` after the `wisun_connect` retry in `except` block
- [ ] T017 [US1] Add `publish_diag(mqtt, device_id, diag_state.snapshot(time.time()))` call in main loop after `publish_measurements`. Wrap in try/except that logs error and continues (FR-005)

### Verification

- [ ] T018 [US1] Run all unit tests (`pytest tests/unit/`) — must be green
- [ ] T019 [US1] Manual smoke test against Mosquitto + HA on lab-ub01: confirm all 15 sensors appear in HA device page within 90 seconds of bridge start (SC-001)

**Checkpoint**: At this point User Story 1 is fully delivered. The MVP could ship here. Log output remains the legacy text format until Phase 4.

---

## Phase 4: User Story 2 — ローカルログが JSON Lines でローテーション (Priority: P2)

**Goal**: `mqtt_bridge.log` が 1 行 1 JSON でレベル分けされ、1 MiB × 3 世代でローテーションされる。

**Independent Test**: `log_max_bytes=4096` で bridge を 5 分走らせて `mqtt_bridge.log` + `.1`〜`.3` が生成され `mqtt_bridge.log.4` は存在しないこと、各行が `json.loads` で 100% パースできること。

### Tests (Red 先行)

- [ ] T020 [P] [US2] `tests/unit/test_json_logger.py`: `JsonLogger` の各レベルが正しい JSON Lines を出すこと、`level` 閾値未満は出ないこと、`ts` が ISO 8601 UTC `Z` 形式であること、`context` dict が `extra` 経由で組み込まれること、ファイル書き込み失敗時に stderr フォールバックすること
- [ ] T021 [P] [US2] `tests/unit/test_log_rotation.py`: `RotatingFileHandler` を `maxBytes=512, backupCount=3` で構築し、十分書き込んだ後にファイル数とサイズを検証。世代 5 になっても `.4` が存在しないこと
- [ ] T022 [P] [US2] `tests/unit/test_log_event_names.py`: 主要イベント名（`bridge_start`, `mqtt_connected`, `wisun_joined`, `wisun_join_failed`, `mqtt_reconnect`, `poll_success`, `poll_failure`, `scan_retry`）がコード内で発火されることを `JsonLogger` の呼び出し記録から検証（fake logger を注入）

### Implementation (Green)

- [ ] T023 [US2] Implement `JsonLogger` class in `production_tool/mqtt_bridge.py` wrapping `logging.Logger` with a custom `Formatter` that emits JSON Lines. Levels map to `logging.DEBUG/INFO/WARNING/ERROR`. `RotatingFileHandler` configured from `log_max_bytes`, `log_backup_count`. On `IOError`/`OSError` constructing handler, fall back to `StreamHandler(sys.stderr)` and continue
- [ ] T024 [US2] Replace module-level `log(msg)` function with calls into a module-level `LOGGER` (`JsonLogger` instance). Map current call sites: `log("X")` → `LOGGER.info("X")` or appropriate level. Replace state-transition lines with named events (`LOGGER.info(event="poll_success", context={"power_w": ...})`)
- [ ] T025 [US2] Update `main()` to construct `LOGGER` from config keys (`log_level`, `log_max_bytes`, `log_backup_count`) with defaults `info` / `1048576` / `3`. Emit `LOGGER.info(event="bridge_start", context={"device_id": ..., "version": bridge_version()})` as first log line

### Verification

- [ ] T026 [US2] Run unit tests (`pytest tests/unit/test_json_logger.py tests/unit/test_log_rotation.py tests/unit/test_log_event_names.py`)
- [ ] T027 [US2] Manual: set `log_max_bytes=4096` in `production_tool/config.json`, deploy to Cube J1, let run for 5 minutes, verify rotation file count and JSON parseability (SC-002, SC-003)

**Checkpoint**: US2 delivered. Together with US1 the spec's primary observability goals are fulfilled.

---

## Phase 5: User Story 3 — 設定で挙動を調整 (Priority: P3)

**Goal**: `config.json` の追加キーが optional で、すべて存在しない場合でも US1+US2 の挙動が既定値で得られる。

**Independent Test**: 上流の `config.json`（9 キー）でそのまま起動でき、計測 publish と diag publish が動く。次に `log_level: "debug"` を足して `LOGGER.debug(...)` 行が出ることを確認。

### Tests

- [ ] T028 [P] [US3] `tests/unit/test_config_defaults.py`: `load_config()` の上にラップする `apply_defaults(cfg)` を導入し、欠落キー全パターン (none / partial / all present) で正しい値が返ることを検証。既存 9 キーのデフォルトは変えない (FR-012)
- [ ] T029 [P] [US3] `tests/unit/test_config_unknown_keys.py`: 未知キーを持つ config が起動エラーを出さない (Edge Case)

### Implementation

- [ ] T030 [US3] Add `apply_defaults(cfg)` helper in `mqtt_bridge.py` that fills `log_level=info`, `log_max_bytes=1048576`, `log_backup_count=3` when absent. Unknown keys are passed through (ignored)
- [ ] T031 [US3] Update `main()` to call `apply_defaults(load_config())` and pass `cfg` to `JsonLogger` and `DiagState` constructors

### Verification

- [ ] T032 [US3] Run config tests, then manually delete new keys from `config.json` and confirm bridge still starts with sensible defaults (SC-005)

**Checkpoint**: US3 delivered. Spec scope complete.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T033 [P] `tests/benchmark/test_post_poll_latency.py`: micro-benchmark for the "post-poll processing block" (DiagState.snapshot + publish_diag + LOGGER.info call). Baseline = same block with diag/logger disabled (no-op). Run 1000 iterations, assert median(observability_on) <= 1.10 * median(baseline) (SC-004)
- [ ] T034 [P] Update `readme.md` to document new `config.json` keys, link to `specs/001-bridge-observability/quickstart.md`
- [ ] T035 Verify `production_tool/` still contains exactly the original 6 files (Constitution I) — diff the directory listing
- [ ] T036 Smoke test: full pipeline from `scripts/embed_git_hash.sh` → USB copy → Cube J1 boot → HA shows all 15 sensors (User Story 1+2 acceptance)
- [ ] T037 If git hash is currently `unknown` (developer skipped embed step), bridge still starts cleanly with `Bridge Version = <SemVer>+unknown` (FR-001 fallback)

---

## Dependencies

```
Phase 1 (Setup) ──► Phase 2 (Foundational) ──► Phase 3 (US1)
                                            └► Phase 4 (US2)
                                            └► Phase 5 (US3 depends on US2 for LOGGER)
                                            
Phase 6 (Polish) ──► after all user stories
```

User Story 1 と User Story 2 は **互いに独立**（US1 は既存 `log()` のままで動く）。
User Story 3 は LOGGER の設定キーに触れるので User Story 2 の後にやる方が無駄なテストの書き直しを避けられる。

## MVP Scope

User Story 1 (Phase 1 + 2 + 3) のみを実装すれば spec の主目的（HA からの可視化）は達成される。US2 (JSON ログ) と US3 (設定) は段階的に追加可能。

## Parallel Execution Examples

### Phase 2 内
- T006, T007, T008 は別ファイルのため [P]

### Phase 3 内のテスト
- T009 (DiagState), T010 (HA discovery), T011 (publish_diag) は別ファイルのため [P]
- ただし T012〜T017 の実装は同一 `mqtt_bridge.py` を編集するため**直列**

### Phase 4 内のテスト
- T020, T021, T022 は別ファイルのため [P]

### Phase 6
- T033 (benchmark), T034 (readme) は別ファイルのため [P]

## Validation Checklist

- [x] 全タスクに ID, file path, story label が付与されている
- [x] User Story 1 がスタンドアロンで MVP として機能する
- [x] テストタスクが対応する実装タスクの前に並んでいる
- [x] Constitution V (TDD) に従いテスト先行で組まれている
- [x] Constitution I (USB ブート構造) に T035 で gate を設けている
