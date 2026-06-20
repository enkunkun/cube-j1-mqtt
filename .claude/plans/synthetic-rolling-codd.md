# Plan: spec 012 + 013 並行実装（fork 取り込み）

## Context

GitHub Insights で取り込み可能な fork (nanamitm/cube-j1-mqtt) を調べた結果、当方の spec 011 までの実装ですでに大半カバー済みだが、 以下 2 件は取り込み価値ありと判定した:

- **spec 012 (ERXUDP TID 検証)**: spec 011 の intra-cycle retry / 再 join 直後にメーター遅延応答が次サイクルの結果に混入する穴を塞ぐ。 nanamitm `77fc027` の発想を採用
- **spec 013 (poll_interval 30s 下限)**: ARIB STD-T108 920MHz 帯 duty cycle (360s/hour) 違反を未然に防ぐ config validation。 nanamitm `c2c7c0a` の発想を採用

両者は独立で互いに干渉しない。 spec 013 を先に入れて運用設定を保護してから、 spec 012 で本丸の Wi-SUN 信頼性を強化する順序。

## Affected files

- `production_tool/mqtt_bridge.py` (両 spec とも)
- `tests/unit/test_diag_state.py` (spec 012 用拡張)
- `tests/unit/test_read_erxudp.py` (spec 012 新規 or 既存があれば追記)
- `tests/unit/test_config_defaults.py` または新規 `test_config_validation.py` (spec 013)
- 新規 `tests/unit/test_extract_el_tid.py` (spec 012 pure helper 用)
- `specs/012-erxudp-tid-validation/spec.md` / `specs/013-poll-interval-floor/spec.md` (作成済み、 実装コミットに同梱)

## spec 013 実装 (先に着手、 risk 極小)

### 設計修正点（plan 中の発見）

spec.md FR-003 は「`load_config` で floor 適用」と書いたが、 実装の `load_config` (96-98 行) は `json.load` 1 発で defaults は別経路 (`apply_defaults`、 ~110 行台) で当たる。 floor は `apply_defaults` 側で当てるのが正しい。

spec.md FR-004 の HTML `min="30"` 属性は、 admin UI フィールドが JS で動的生成されている (個別属性なし、 571 行付近で regex validation) ため適用面が小さい。 サーバ側検証 (FR-001) で十分担保できるので、 FR-004 は **削除** する (spec.md も update する)。

### 変更内容

`production_tool/mqtt_bridge.py`:

1. モジュール冒頭 (定数定義領域) に追加:
   ```python
   MIN_POLL_INTERVAL_SEC = 30
   ```

2. `apply_defaults()` (101 行〜) で setdefault + floor clamp を **両方** 追加 (Round 2 決定 5)。 現状 `poll_interval` の setdefault は無く、 default 60 は read 側 (line 2972 `cfg.get("poll_interval", 60)`) で当たっている。 default と floor を 1 箇所に集約する:
   ```python
   out.setdefault("poll_interval", 60)
   if int(out["poll_interval"]) < MIN_POLL_INTERVAL_SEC:
       log("WARN: poll_interval={} below floor, clamping to {}".format(
           out["poll_interval"], MIN_POLL_INTERVAL_SEC))
       out["poll_interval"] = MIN_POLL_INTERVAL_SEC
   ```
   read 側の `cfg.get("poll_interval", 60)` は互換のため残す (実質 dead default になるが防御の意味で温存)

3. `validate_config_patch()` (388 行) に `poll_interval` 専用ブランチ追加:
   ```python
   elif key == "poll_interval":
       if (not isinstance(value, int) or isinstance(value, bool)
               or value < MIN_POLL_INTERVAL_SEC):
           return None, ("poll_interval must be an integer >= {} seconds "
                         "(ARIB STD-T108 920MHz duty cycle)"
                         .format(MIN_POLL_INTERVAL_SEC))
   ```
   `_POSITIVE_INT_KEYS` から `poll_interval` を除外して二重判定回避

### テスト (TDD Red → Green → Refactor)

新規 `tests/unit/test_config_validation.py`:

1. **Red**: `test_validate_config_patch_rejects_poll_interval_below_floor` — `{"poll_interval": 10}` を patch すると error_message に "30" を含むエラー返却
2. **Red**: `test_validate_config_patch_accepts_poll_interval_at_floor` — `{"poll_interval": 30}` で成功
3. **Red**: `test_validate_config_patch_accepts_poll_interval_above_floor` — `{"poll_interval": 300}` で成功
4. **Red**: `test_apply_defaults_clamps_low_poll_interval` — `{"poll_interval": 5}` を渡すと結果が 30
5. **Red**: `test_apply_defaults_preserves_normal_poll_interval` — `{"poll_interval": 60}` は 60 のまま
6. **Red**: `test_validate_config_patch_other_keys_unaffected` — poll_interval を含まない patch は影響なし (既存ふるまい保護)

順に **Green** にしながら実装。

## spec 012 実装

### 変更内容

`production_tool/mqtt_bridge.py`:

1. ECHONET Lite helper 領域 (`build_el_get` の隣、 2245 行付近) に pure helper:
   ```python
   def extract_el_tid(payload):
       """Return ECHONET Lite TID from a Get-response payload.

       payload[2:4] is the big-endian TID. Returns None for payloads
       shorter than 4 bytes (caller treats as 'cannot validate, accept').
       """
       if len(payload) < 4:
           return None
       return struct.unpack(">H", bytes(payload[2:4]))[0]
   ```

2. `DiagState.__init__` (1758-) で counter 追加:
   ```python
   self.erxudp_tid_mismatch_total = 0
   ```
   `on_erxudp_intra_cycle_retry` (1808) の隣に method 追加:
   ```python
   def on_erxudp_tid_mismatch(self):
       self.erxudp_tid_mismatch_total += 1
   ```
   metrics 公開には **2 箇所** 編集が必要 (whitelist パターン):
   - `_DIAG_SNAPSHOT_KEYS` tuple (1412-1425 行) に `"erxudp_tid_mismatch_total",` を追加 (順序は erxudp 関連カウンタの隣、 1422 行直後が自然)
   - DiagState の snapshot 関数内 `raw` dict (1855-1874 行) に `"erxudp_tid_mismatch_total": self.erxudp_tid_mismatch_total,` を追加

3. `read_erxudp` (2392 行) シグネチャ拡張 + テスタビリティ向上の refactor:
   ```python
   def read_erxudp(fd, timeout=15, diag_state=None, expected_tid=None,
                   readline=None):
       """`readline` defaults to module-level `serial_readline`; tests inject
       a fake. Both old (no-kw) and new call sites work unchanged."""
       if readline is None:
           readline = serial_readline
   ```
   内部の `serial_readline(fd, timeout=...)` 呼び出しを `readline(fd, timeout=...)` に置換。 payload を返す前の `if not value.startswith("1081"):` 判定の **直後** に (Round 2 決定 7、 既存の try/except 保護を維持):
   ```python
   try:
       payload = bytearray(binascii.unhexlify(value))
   except Exception as e:
       log("ERXUDP hex decode error: {}".format(e))
       continue
   if expected_tid is not None:
       got_tid = extract_el_tid(payload)
       if got_tid != expected_tid:
           if diag_state is not None:
               diag_state.on_erxudp_tid_mismatch()
           if got_tid is None:
               log("WARN: ERXUDP payload too short ({} bytes), "
                   "discarding".format(len(payload)))
           else:
               log("WARN: ERXUDP TID mismatch expected={:04X} got={:04X}, "
                   "discarding".format(expected_tid, got_tid))
           continue
   return payload
   ```
   既存の hex decode 二重走行を排除、 short payload と TID mismatch を別ログに分岐 (Round 1 決定 1)。

4. main loop の `read_erxudp` 呼び出し 2 箇所 (3093, 3105 行) に `expected_tid=sent_tid` 引数追加。

   **重要な実装注意**: 既存コードは `send_el_get(fd, ipv6, tid, ...)` の **直後** (line 3091, 3103) で `tid = (tid + 1) & 0xFFFF` を実行しており、 `read_erxudp` 到達時点では tid がすでに 1 増えた値になっている。 そのまま `expected_tid=tid` を渡すと検証先がズレる。

   修正パターン: `send_el_get` の **直前** に `sent_tid = tid` でキャプチャし、 `expected_tid=sent_tid` を read_erxudp に渡す。 2 箇所 (通常 send と retry send) 両方で同じ対応が必要:
   ```python
   sent_tid = tid
   send_el_get(fd, ipv6, sent_tid, epc_list=cycle_epcs)
   tid = (tid + 1) & 0xFFFF
   t_send = time.time()
   data = read_erxudp(fd, timeout=_erxudp_timeout,
                      diag_state=diag_state, expected_tid=sent_tid)
   ```
   tier rotation の `epc_list` も sent_tid と対応する EPC リストで渡す (既存挙動維持)。

### テスト (TDD Red → Green → Refactor)

新規 `tests/unit/test_extract_el_tid.py`:
1. **Red**: `test_extract_el_tid_normal` — `bytearray(b"\x10\x81\x12\x34\x05\xff...")` → `0x1234`
2. **Red**: `test_extract_el_tid_short_payload` — 3 byte → `None`
3. **Red**: `test_extract_el_tid_zero_tid` — `bytearray(b"\x10\x81\x00\x00\x05\xff...")` → `0`

新規 `tests/unit/test_read_erxudp.py` (既存があれば追記):
4. **Red**: `test_read_erxudp_no_expected_tid_accepts_any` — 後方互換
5. **Red**: `test_read_erxudp_expected_tid_match_returns_payload`
6. **Red**: `test_read_erxudp_expected_tid_mismatch_discards_then_waits` — 不一致 frame の後に一致 frame が来たら一致のほうを返す。 fake `serial_readline` を渡せる構造に
7. **Red**: `test_read_erxudp_expected_tid_mismatch_increments_diag_counter`

既存 `tests/unit/test_diag_state.py` に追記:
8. **Red**: `test_diag_state_on_erxudp_tid_mismatch_increments_counter`
9. **Red**: `test_diag_state_to_metrics_dict_includes_tid_mismatch_total`

fake `readline` は `read_erxudp(... , readline=fake)` で注入する (Round 1 決定 3)。 fake は `list.pop(0)` 形式の closure で 1 行ずつ返す。 既存テストの patterns を踏襲 (Python 2.7 stdlib 縛り、 pytest fixture 不使用、 直接テスト関数の方針)。

## 既存テストの非回帰確認

`cd src && pytest && ruff check .` (CLAUDE.md の指定コマンド) を全件 pass で確認。 spec 011 関連の `test_should_force_wisun_reconnect` 等が壊れないことを最終チェック。

## コミット戦略

CLAUDE.md ルール「プランファイルは実装コミットに同梱、 単独の更新コミットを作らない」「spec 011 と同様 spec ごとに分割」に従う:

1. **コミット 1**: `feat(admin): poll_interval >= 30s floor (spec 013)` — spec 013 spec.md + 実装 + tests + 本プランファイル の半分相当を同梱
2. **コミット 2**: `feat(bridge): ERXUDP TID validation (spec 012)` — spec 012 spec.md + 実装 + tests を同梱

プランファイルは 1 つで両 spec をカバーするため、 コミット 1 に同梱（コミット 2 では削除しない、 spec 012 にも有効）。

## Verification

実装後の確認手順:

1. `cd src && pytest -xvs tests/unit/test_config_validation.py tests/unit/test_extract_el_tid.py tests/unit/test_read_erxudp.py tests/unit/test_diag_state.py` — 新規テスト個別 pass
2. `pytest && ruff check .` — 既存テスト全件 pass、 lint クリーン
3. 実機 (Cube J1) にデプロイ後、 `/api/metrics` に `erxudp_tid_mismatch_total` が出ることを確認
4. admin UI から `poll_interval=10` 保存試行 → 400 エラーが返ることを実測
5. 1 週間の Grafana 観測で `erxudp_tid_mismatch_total > 0` が記録されるか (= 実際に混入が起きていたかの evidence)
