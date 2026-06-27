# Plan: spec 037 WOPT FLASH 書込み寿命対策 (= ROPT 確認で WOPT skip)

## Context

2026-06-27 audit [[audit-bp35a1-skstack-ip-vs-bridge]] の P-NEW-1 を解消。 公式 BP35A1 Ver 1.3.2 p.41 で WOPT は「設定時に一度だけ実行するように」 と FLASH 寿命 10,000 回制限の警告があるが、 bridge は `_wisun_init_sequence` で **reconnect 毎に** WOPT 1 を発行していた。 1 日 100 reconnect × 100 日 = 寿命到達 → 物理破損リスク (= モジュール交換が必要)。

修正方針: 公式 ROPT (p.42、 応答 `OK <MODE:2 桁 hex>`) で現在の WOPT 値を読み取り、 既に `01` なら WOPT 1 を **skip**。 これで FLASH 書込みは「初回 deploy 時のみ」 になり寿命実質無制限。

## Phase 1 発見 (= 設計上の重要分岐)

既存 `skcommand` (L2684) は応答 loop の break 条件が `line == "OK"` **完全一致**:
```python
if line in ("OK", ) or line.startswith("FAIL"):
    break
```

ROPT 応答は `OK 01` (= スペース + 値) 形式で完全一致しない → **既存 skcommand では break せず timeout まで待ってしまう** = ROPT パース不可能。

### 採用案: ROPT 専用 helper

`skcommand` 改修 (= `line.startswith("OK")` 拡張) は既存 SK コマンド (= SKRESET / SKVER / SKSETPWD 等 全部) の回帰リスクが大 → 採用しない。 代わりに ROPT 専用 helper `ropt(fd)` を新設して「ROPT は応答形式が特殊」 という性質を helper 内に閉じ込める。

## 修正対象 (= 3 ファイル)

### 1. `production_tool/mqtt_bridge.py` (= helper + 条件分岐 + DIAG_SENSOR_DEFS)

#### a. ROPT 専用 helper 新設 (L2735 周辺、 skcommand の後)

```python
def ropt(fd, timeout=3):
    """Read current WOPT MODE via ROPT. Returns int 0-0xFF. Raises on failure.

    ROPT response format (BP35A1 Ver 1.3.2 p.42): "OK <MODE:2 桁 hex>"
    skcommand cannot parse this because its break condition is exact "OK"
    string match.

    timeout=3s: SKVER (2s) と SKRESET (5s) の中庸、 軽量と余裕の両立
    (dig Round 1 で確定)。
    """
    serial_write(fd, "ROPT\r\n")
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = serial_readline(fd, timeout=max(0.5, deadline - time.time()))
        if line is None:
            continue
        if line.startswith("OK"):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1], 16)
            raise RuntimeError("ROPT malformed response: {!r}".format(line))
        if line.startswith("FAIL"):
            raise RuntimeError("ROPT failed: {}".format(line))
    raise RuntimeError("ROPT timeout")
```

#### b. `_wisun_init_sequence` 改修 (L2832 周辺)

```python
# 旧:
skcommand(fd, "WOPT 1")

# 新:
try:
    current_wopt = ropt(fd)
    if (current_wopt & 1) == 1:  # spec 037: bit mask 判定で bit1-7 予約への将来互換 (= Python の & は == より優先順位低いので括弧必須)
        log("WOPT 1 skip (ROPT=1)")
        if diag_state is not None:
            diag_state.on_wopt_skip()
    else:
        skcommand(fd, "WOPT 1")
        if diag_state is not None:
            diag_state.on_wopt_write()
except Exception as e:
    # ROPT timeout / FAIL → fallback で WOPT 1 発行 (= 安全側、 既存挙動維持)
    log("ROPT failed ({}), fallback to WOPT 1".format(e))
    skcommand(fd, "WOPT 1")
    if diag_state is not None:
        diag_state.on_wopt_write()
```

ただし `_wisun_init_sequence` 現状 (L2832) は `diag_state` を引数で取らない。 引数追加 + 呼出側 (= `wisun_connect`) 修正が必要。 もしくは module-level `LOGGER` のように global `diag_state` を参照する pattern を踏襲 (= 既存コードに合わせる)。

#### c. DiagState 拡張 (L2451 周辺)

```python
def __init__(self, ...):
    ...
    self.wopt_skip_count = 0
    self.wopt_write_count = 0

def on_wopt_skip(self):
    self.wopt_skip_count += 1

def on_wopt_write(self):
    self.wopt_write_count += 1

def snapshot(self, now):
    ...
    snap["wopt_write_skipped_total"] = self.wopt_skip_count
    snap["wopt_write_total"] = self.wopt_write_count
    ...
```

#### d. DIAG_SENSOR_DEFS 追加 (L3925 周辺)

```python
("wopt_write_skipped_total", "WOPT Write Skipped (= FLASH 寿命対策)", None, None, "total_increasing", "diagnostic"),
("wopt_write_total",         "WOPT Write Count",                      None, None, "total_increasing", "diagnostic"),
```

### 2. `tests/unit/test_wisun_health.py` (= regression test 追加)

`_FakeFd` pattern (= L195 周辺の既存) を流用:

- `test_ropt_parses_mode_01`: serial_readline mock → "OK 01" → `ropt(fd) == 1`
- `test_ropt_parses_mode_00`: → "OK 00" → `ropt(fd) == 0`
- `test_ropt_raises_on_fail`: → "FAIL ER10" → RuntimeError
- `test_ropt_raises_on_timeout`: → None → RuntimeError
- `test_on_wopt_skip_increments`: DiagState の counter test
- `test_on_wopt_write_increments`: 同上
- `test_diag_label_wopt_skipped_exists`: DIAG_SENSOR_DEFS 登録確認

`_wisun_init_sequence` の skip 判定はやや integration 寄りで、 unit test だけでは bridge 全体 startup を mock する必要がある (= 既存テストに init_sequence 単体テストは無い) → integration 部分は **deploy 後の admin UI ログ確認 (= SC-003)** で verify する設計。

### 3. `docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md`

P-NEW-1 status を `open` → `in progress (= spec 037 で対処中)` に遷移、 deploy 完了後に `resolved by spec 037 commit <hash>` 化。

## TDD 順序

1. **Red**: test_wisun_health.py に上記 7 件を追加 → 既存 helper 不在のため fail
2. **Green-1**: `ropt(fd)` helper を mqtt_bridge.py に追加 → ropt 系 test pass
3. **Green-2**: DiagState 拡張 (= on_wopt_skip / on_wopt_write + snapshot) → counter test pass
4. **Green-3**: DIAG_SENSOR_DEFS に 2 件追加 → label test pass
5. **Green-4**: `_wisun_init_sequence` に ROPT 判定 + diag_state 引数追加 (= 呼出側 wisun_connect も同時修正)
6. **Refactor**: ROPT が今後他用途で必要になれば skcommand 改修も検討 (= 本 spec では skip)

## Decisions (dig Round 1, 2026-06-27)

事前判断 (= 自明な決定、 dig 質問不要):

| 項目 | 決定 |
|---|---|
| skcommand 改修 vs 専用 helper | **専用 helper** (= 既存 SK 全体への回帰リスク回避) |
| ROPT 失敗時の fallback | **WOPT 1 を発行** (= 安全側、 既存挙動維持) |
| metric 名 | `wopt_write_skipped_total` + `wopt_write_total` (= snake_case + total suffix で既存 pattern 踏襲) |
| commit 粒度 | spec 036 と同じ 1 commit (= bridge + tests + spec.md + audit) |
| deploy 経路 | lab-ub01 経由 (= memory 遵守、 spec 036 と同じ) |
| jj push | main に 5 step push (= spec 036 と同じ) |

dig で残った不確実点を質問: `_wisun_init_sequence` への diag_state 引数追加方式 (= 引数追加 vs global 参照)。

## Verification

1. **host test**: `uv run --with pytest pytest tests/unit/test_wisun_health.py -v` で新 7 件 pass + 既存 29 件 pass = 計 36 件
2. **lint**: `uvx ruff check production_tool/mqtt_bridge.py tests/unit/test_wisun_health.py` で新規エラーなし
3. **commit**: 1 commit 集約、 Conventional Commits 形式 (= `fix(bridge): WOPT 毎回発行を ROPT 確認で skip (spec 037、 FLASH 寿命 10,000 回制限対策)`)
4. **deploy**: `ssh lab-ub01 'cd /tmp/cube-j1-mqtt && git checkout -B main fork/main && bash scripts/adb_push_update.sh cube-j1.home.arpa'` (= spec 036 で確立した手順)
5. **SC-001 確認**: bridge コード grep で WOPT 1 発行が ROPT 判定の条件分岐内にある
6. **SC-002 確認**: pytest pass
7. **SC-003 確認**: deploy 後の admin UI `/api/log` で `WOPT 1 skip (= already set per ROPT)` が出ること (= reconnect 1 回トリガで確認)
8. **SC-004 確認**: 7 日間運用後に `wopt_write_skipped_total` が大きく増加、 `wopt_write_total` が 1-2 件に留まることを Grafana で確認 (= 別途観察)

## 留意事項

- `_wisun_init_sequence` に diag_state を渡す方式: 引数追加が clean。 呼出側 `wisun_connect` も diag_state を受け取っているはずなので素直に転送可能 (= 要 grep 確認)
- 既存 `cached SKJOIN failure 後の re-init` path でも `_wisun_init_sequence` が呼ばれる (= L2960) → そこにも diag_state を渡す必要
- spec 036 で追加した EVENT ラベル regression test pattern と同じ流儀で DIAG_SENSOR_DEFS の新 metric label test を追加 (= regression 防止)
- ROPT 応答の hex parse は `int(parts[1], 16)` で 8-bit unsigned (= 0x00〜0xFF)、 spec 037 では bit0 のみ判定 (= `current_wopt & 1 == 1`) でも良いが、 公式仕様で bit1-7 は予約なので 0/1 比較で十分
