# Plan: spec 019 Wi-Fi AP Toggle State Persistence

## Context

spec 008 で admin UI から Wi-Fi AP (P2P GO mode) を enable/disable できるが、 状態は `getprop net.wifi.ap.state` のみに乗っていて **bridge / Cube J1 を reboot すると OS デフォルトに戻る**。 ユーザが「AP OFF」にしても電源を抜き差ししたら ON に復帰する。

hals5412 fork `9b327a3` は「常時 OFF 固定」だが当方は spec 008 のトグル機能を維持したいので、 **「toggle した状態を /data/local/cube_j1_ap_state に保存」 + 「bridge 起動時に読んで復元」** の設計で永続化する。

## Approach

- 新クラス `ApStateStore` (path + opener inject 可能) で 1 行プレーンテキスト read/write
- `ApController` に `state_store=None` 引数追加。 enable()/disable() 成功後に store.write
- bridge `main()` の publish_ha_discovery / `mark_initial_discovery_publish` の **直後** に restore 呼び出し (kill switch あり)
- config: `ap_state_file_path` (default `/data/local/cube_j1_ap_state`), `ap_state_persist_enabled` (default `True`)
- main() で ApController を 1 つ作って `start_admin_server(... ap_controller=...)` に渡す → admin handler と同じ ApController を共有し、 toggle/restore が同じ state_store を見る整合性

### Round 1 決定の反映 (重要)

1. **kill switch 完全 OFF**: `ap_state_persist_enabled=false` のとき、 restore はもちろん **write も skip**。 実装: main() で `state_store = ApStateStore(path) if cfg["ap_state_persist_enabled"] else None` とし、 ApController/admin handler に渡る state_store がそもそも None
2. **DiagState counter 不要**: 起動時 1 回しか動かないので Grafana 価値薄、 ログ `AP restored: enabled` で十分。 spec.md からも該当項目 (Key Entities + SC) を削除する
3. **`_FakeOpener` 設計**: dict ベース (`{path: bytes}`、 欠落 → IOError)、 context manager 風の closure ヘルパーで wrap
4. **restore タイミング**: `mark_initial_discovery_publish` 直後 → discovery 発行が安定した状態で AP 復元 → 失敗してもログだけで serial open へ進める

## Files to modify

### `production_tool/mqtt_bridge.py`

1. **`ApStateStore` クラス** (line 1571 直後、 ApController の隣):
   ```python
   class ApStateStore(object):
       """spec 019: persist desired AP toggle state across reboots.

       1-line plain text file containing "enabled" or "disabled".
       Missing / malformed / unreadable → read() returns None; caller
       leaves OS default alone. `opener` is injectable for tests."""
       VALID_STATES = ("enabled", "disabled")

       def __init__(self, path, opener=None):
           self._path = path
           self._opener = opener or open

       def read(self):
           try:
               with self._opener(self._path, "rb") as f:
                   raw = f.read().decode("ascii", errors="replace").strip()
           except IOError:
               return None
           if raw in self.VALID_STATES:
               return raw
           if raw:
               log("WARN: ApStateStore invalid value {!r} in {}".format(
                   raw, self._path))
           return None

       def write(self, state):
           if state not in self.VALID_STATES:
               raise ValueError("state must be one of {}".format(
                   self.VALID_STATES))
           AtomicWriter.write_bytes(self._path, state.encode("ascii"))
   ```
   (Atomic write 経由 で partial-write 事故を防ぐ)

2. **`ApController.__init__` 拡張**:
   ```python
   def __init__(self, interface=None, runner=None, state_store=None):
       self._interface = interface
       self._runner = runner or _default_subprocess_runner
       self._state_store = state_store
   ```

3. **`ApController.enable()` / `disable()` 拡張**:
   ```python
   def disable(self):
       iface = self._resolve_interface()
       self._runner(build_wpa_cli_cmd(iface, "disable"), timeout=5)
       if self._state_store is not None:
           try:
               self._state_store.write("disabled")
           except Exception as e:
               log("ERROR: ApStateStore write failed: {}".format(e))
       return self.get()
   ```
   enable() 側も同様 (write("enabled"))。

4. **`apply_defaults`** (line 153 付近、 spec 013 poll_interval ブロック直後):
   ```python
   # spec 019: persist Wi-Fi AP toggle state across reboots.
   out.setdefault("ap_state_file_path", "/data/local/cube_j1_ap_state")
   out.setdefault("ap_state_persist_enabled", True)
   ```

5. **admin handler の wire-up** (line 1243 `AdminHandler.ap_controller = ap_controller or ApController()`): ApController は class-level singleton で、 main() から `start_admin_server(... ap_controller=...)` 経由で渡せる。 main() で 1 つ生成して渡すよう改修 (admin handler 側のコードは現状維持で OK)。 main() の改修:
   ```python
   _ap_store = (ApStateStore(cfg.get("ap_state_file_path",
                "/data/local/cube_j1_ap_state"))
                if cfg.get("ap_state_persist_enabled", True) else None)
   _ap_controller = ApController(state_store=_ap_store)
   # ...
   start_admin_server(... ap_controller=_ap_controller, ...)
   ```

6. **bridge `main()` 起動時の restore** (line 3173 `mark_initial_discovery_publish` の **直後**、 serial open の前):
   ```python
   # spec 019: restore Wi-Fi AP toggle state from last session.
   # _ap_store is None when persist is disabled → apply_ap_state_restore
   # naturally short-circuits via stored_state=None.
   if _ap_store is not None:
       try:
           result = apply_ap_state_restore(_ap_store.read(), _ap_controller)
           if result is not None:
               log("AP restored: {}".format(result))
       except Exception as e:
           log("WARN: AP state restore failed: {}".format(e))
   ```
   注: `_ap_store` と `_ap_controller` は admin handler 用に既に生成済 (上の #5)、 同じインスタンスを再利用。

### `tests/unit/test_ap_state_store.py` (新規)

`_FakeRunner` パターンを踏襲、 fixture 不使用。 (dig Round 2 決定 7)

- **read 系テスト** (`_FakeOpener` で in-memory file 模倣、 dict ベース `{path: bytes}`、 欠落 → IOError):
  - `test_read_returns_none_when_file_missing`
  - `test_read_returns_none_when_file_empty`
  - `test_read_returns_none_when_file_invalid_value`
  - `test_read_strips_whitespace_and_newline`
- **write 系テスト** (`tempfile.mkstemp` で実 file system に書ける path を作って AtomicWriter 経由の write を実行、 既存 `test_admin_atomic_write.py` の先例を踏襲):
  - `test_write_then_read_round_trip_enabled`
  - `test_write_then_read_round_trip_disabled`
  - `test_write_rejects_invalid_value` (ValueError、 file system 触れず raise 確認のみ)

### `tests/unit/test_ap_controller.py` (拡張)

既存 15 tests に追加:

- `test_enable_writes_enabled_to_state_store_when_provided`
- `test_disable_writes_disabled_to_state_store_when_provided`
- `test_state_store_write_failure_does_not_break_toggle` (defensive)
- `test_ap_controller_without_state_store_still_works` (既存挙動互換)

### 起動時 restore のテスト

main() の restore ロジックは pure に出しづらい (config + ApController + ApStateStore の wire-up)。 unit test には pure helper `apply_ap_state_restore(stored, ap_controller)` を抽出して、 そちらをテスト:
- `test_apply_ap_state_restore_calls_enable_when_stored_enabled`
- `test_apply_ap_state_restore_calls_disable_when_stored_disabled`
- `test_apply_ap_state_restore_noop_when_none`

main() 内では `apply_ap_state_restore(store.read(), ApController(state_store=store))` を call。

## Pure helper

```python
def apply_ap_state_restore(stored_state, ap_controller):
    """spec 019: dispatch restore based on previously stored state."""
    if stored_state == "enabled":
        ap_controller.enable()
        return "enabled"
    if stored_state == "disabled":
        ap_controller.disable()
        return "disabled"
    return None  # leave OS default alone
```

## Test list (TDD 順)

1. **Red**: `test_write_then_read_round_trip_enabled` → ApStateStore 未実装で AttributeError
2. (stub: ApStateStore class with empty body)
3. **Red**: `test_write_rejects_invalid_value` (ValueError)
4. **Red**: `test_read_returns_none_when_file_missing`
5. **Red**: `test_read_returns_none_when_file_empty`
6. **Red**: `test_read_returns_none_when_file_invalid_value`
7. **Red**: `test_read_strips_whitespace_and_newline`
8. **Red**: `test_write_then_read_round_trip_disabled`
9. **Red**: `test_apply_ap_state_restore_calls_enable_when_stored_enabled` (pure helper)
10. **Red**: `test_apply_ap_state_restore_calls_disable_when_stored_disabled`
11. **Red**: `test_apply_ap_state_restore_noop_when_none`
12. **Red**: `test_enable_writes_enabled_to_state_store_when_provided` (ApController)
13. **Red**: `test_disable_writes_disabled_to_state_store_when_provided`
14. **Red**: `test_state_store_write_failure_does_not_break_toggle`
15. **Red**: `test_ap_controller_without_state_store_still_works` (互換)
16. main() restore integration はテストせず (config + wire-up、 実機検証)

## Verification

1. `.venv/bin/pytest -q --ignore=tests/benchmark` で 既存 (334) + 新規 ~14 = ~348 件 pass
2. ruff check 新規エラー無し
3. lab-ub01 経由 deploy で `/data/local/mqtt_bridge.py` 更新
4. 実機で admin UI から AP OFF クリック → `/data/local/cube_j1_ap_state` に "disabled" が書かれている (adb shell cat で確認)
5. Cube J1 reboot → bridge 自動起動 → 起動直後 `getprop net.wifi.ap.state` が disabled (OS default に上書きで restore 効いている)
6. admin UI で再度 ON クリック → state file が "enabled" に → reboot → ON のまま起動

## Commit

`feat(bridge,admin): Wi-Fi AP toggle 状態を /data/local に永続化 (spec 019)`

spec.md は impl と同 commit に同梱 (spec 013/014/016 と一貫)。 plan ファイル (本ファイル) も同梱。
