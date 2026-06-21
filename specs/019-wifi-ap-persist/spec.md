# Feature Specification: Wi-Fi AP Toggle State Persistence

**Feature Branch**: `019-wifi-ap-persist`
**Created**: 2026-06-22
**Status**: Draft
**Input**: User description: "デフォルト wifi のオンオフが持続するようにしたい (toggle した状態を保存し、 reboot 後も復元)"

## Background

spec 008 (`ap-toggle`) で admin UI から Wi-Fi AP (P2P-GO mode) を enable / disable できるようになった。 しかし状態は OS 側 (`getprop net.wifi.ap.state`) で持つだけで、 **bridge / Cube J1 を再起動すると OS のデフォルト値に戻ってしまう**。 ユーザの「OFF にした」設定が消える事故が起きる。

hals5412/cube-j1-mqtt fork の `9b327a3` は「install 時に AP を永続 OFF」する方向だが、 当方は spec 008 で動的トグルを既に持っているので、 fork の発想を「**最後にトグルした状態を保存して reboot 後も復元する**」設計に拡張する。

## Scope

### 状態ファイルの新設

- ファイルパス: `/data/local/cube_j1_ap_state` (Cube J1 上、 既存 `/data/local/mqtt_bridge.py` と同じ場所)
- フォーマット: 1 行プレーンテキスト、 値は `"enabled"` または `"disabled"` のみ
- ファイル不在 = 「未設定」 → OS デフォルトに任せる (= 既存挙動互換)

### `ApStateStore` クラス新設

`production_tool/mqtt_bridge.py` に追加:

```python
class ApStateStore(object):
    """Persists desired AP toggle state across reboots.

    Reads/writes a 1-line plain text file with "enabled" or "disabled".
    Missing or malformed file → None (caller leaves OS default alone).
    `opener` is injectable so tests don't touch the real filesystem.
    """
    VALID_STATES = ("enabled", "disabled")

    def __init__(self, path, opener=None):
        self._path = path
        self._opener = opener or open

    def read(self):
        """Return "enabled" / "disabled" / None."""

    def write(self, state):
        """Persist desired state (must be in VALID_STATES)."""
```

### `ApController` を拡張

- `enable()` / `disable()` が成功した後に `ApStateStore.write()` を call
- 既存の `runner` 引数は維持
- 新引数 `state_store` を追加 (`None` 許容、 未指定なら永続化しない = 既存テスト互換)

### bridge 起動時に状態復元

- `main()` の Wi-SUN join 前 (mqtt 接続完了後 / serial open 前あたり) に:
  - `store.read()` → "enabled" なら `ap_controller.enable()`、 "disabled" なら `ap_controller.disable()`、 None なら何もしない
- 失敗してもログ WARN で続行 (起動を止めない)

### config キー

- `ap_state_file_path` (default `/data/local/cube_j1_ap_state`) — テスト時に別 path を渡せるよう
- `ap_state_persist_enabled` (default `true`) — kill switch

### 観測

- ログのみ (`log("AP restored: enabled")` 等)。 起動時 1 回しか動かないので Grafana / DiagState 経由のカウンタは追加しない (dig Round 1 決定)

## Non-Scope

- 状態ファイルの暗号化 / 圧縮 / マルチ entry — 単純な 1 行で十分
- AP の SSID / passphrase などの永続化 — wpa_supplicant.conf がすでに担当
- spec 008 の admin UI 変更 — ApController.enable/disable をそのまま使うので UI は変更不要
- `wpa_supplicant.conf` への `p2p_disabled=1` 追記 (fork 9b327a3 の方針) — 当方は「ユーザ意思で toggle 可能」を維持するため不採用

## User Scenarios *(mandatory)*

### Primary User Story

ユーザが admin UI の /wisun ページから「AP OFF」をクリックすると、 即時 AP が OFF になる。 Cube J1 を電源リセット (or reboot) しても AP が **OFF のまま** で起動する。 「ON にしておきたい」場合も同様で、 toggle で ON にした状態が reboot をまたいで維持される。

### Acceptance Scenarios

1. **Given** AP が ON、 state file 無し、 **When** `ApController.disable()` を call、 **Then** state file に `"disabled"` が書かれ、 AP は OFF
2. **Given** state file = `"disabled"`、 **When** bridge 起動 → restore 経路を通る、 **Then** `ApController.disable()` が呼ばれ、 AP は OFF (OS デフォルトに関わらず)
3. **Given** state file = `"enabled"`、 **When** bridge 起動、 **Then** `ApController.enable()` が呼ばれて AP は ON
4. **Given** state file 不在、 **When** bridge 起動、 **Then** ap_controller を call せず OS デフォルトのまま
5. **Given** state file が壊れている (`"foobar"`)、 **When** read、 **Then** None 返却 + WARN ログ、 起動継続
6. **Given** `ap_state_persist_enabled=false`、 **When** bridge 起動、 **Then** restore 経路を skip (kill switch)
7. **Given** `ApController.enable()` の wpa_cli が失敗、 **When** state file write、 **Then** 既存挙動 (wpa_cli 失敗を上位に伝搬) は維持しつつ、 state file は write しない (= 整合性保持)

### Key Entities

- **`ApStateStore(path, opener)`**: 状態ファイルの read/write、 inject 可能な opener で test 可能
- **`ApController(interface, runner, state_store)`**: 既存に `state_store` 引数追加
- **`apply_defaults`**: `ap_state_file_path`, `ap_state_persist_enabled` を default 設定
- **`main()`**: 起動時の restore 呼び出し

## Edge Cases

- state file が空文字列: None 返却 + WARN「empty state file」
- state file の改行付き ("enabled\n"): `.strip()` で吸収
- state file 読み取り中に IOError (権限等): None 返却 + WARN
- state file write 中に IOError: 例外を上位へ伝搬しない (toggle 自体は成功扱い、 ログ ERROR)
- 同時複数の admin UI から enable/disable: store の write は最後勝ち、 racy だが許容 (シングルユーザ前提)
- bridge 起動時に restore で wpa_cli が失敗: WARN ログ、 起動は継続

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ApStateStore` クラスは read/write 2 メソッドを持つ
- **FR-002**: read は VALID_STATES のいずれか / None を返す
- **FR-003**: write は VALID_STATES 以外を渡されたら ValueError
- **FR-004**: `ApController.__init__` に `state_store` 引数 (default None) を追加
- **FR-005**: `ApController.enable()` / `disable()` 成功後、 state_store 非 None なら write
- **FR-006**: bridge 起動時の restore 経路は store.read() の値に応じて enable() / disable() を call
- **FR-007**: `ap_state_persist_enabled=false` で restore を完全 skip
- **FR-008**: 既存テスト (state_store なしの ApController) は全件 pass で維持

### Key Entities

- `ApStateStore` クラス
- `ApController` の `state_store` 引数
- `ap_state_file_path` / `ap_state_persist_enabled` config キー

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 単体テスト: store.write → store.read で round-trip
- **SC-002**: 単体テスト: 不在ファイル / 不正値 で None + WARN
- **SC-003**: 単体テスト: ApController.enable() で state_store.write("enabled") が呼ばれる
- **SC-004**: 実機テスト: AP OFF → reboot → AP OFF のままで起動 (1 度確認すれば十分)
- **SC-005**: 既存 spec 008 関連テスト全件 pass

## Assumptions

- `/data/local/` は rw マウントされており、 root (bridge プロセスの実効 UID) で書き込み可能
- bridge プロセスは reboot 後も `mqtt_ha_bridge.rc` で自動起動する
- AP toggle は bridge 起動後の早期 (Wi-SUN join 前) で実行しても OS の wpa_supplicant が既に立ち上がっている
- ユーザは 1 拠点 1 ユーザの単一 admin UI 利用が前提
