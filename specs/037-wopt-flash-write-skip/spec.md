# Feature Specification: WOPT 毎回発行を ROPT 確認で skip (= FLASH 書込み寿命 10,000 回制限のハード破損リスク回避)

**Feature Branch**: `037-wopt-flash-write-skip`
**Created**: 2026-06-27
**Status**: In Progress (= TDD Red→Green 完了、 510 件 pytest pass、 deploy + SC verify 待ち)
**Input**: 2026-06-27 audit ([[audit-bp35a1-skstack-ip-vs-bridge]]) の P-NEW-1。 BP35A1 公式 Ver 1.3.2 p.41 で WOPT は「設定時に一度だけ実行するように」 と明示されているが、 bridge は `_wisun_init_sequence` で **reconnect 毎に** WOPT 1 を発行していた。

## Background

### 公式仕様 (BP35A1 Ver 1.3.2 p.41)

> 本コマンドは、 実行する度に設定が内部 FLASH メモリに書込み保存され、 電源を再起動しても設定は保存されています。 **FLASH メモリへの書込み回数には制限 (10,000 回以下) があります**ので、 制限回数には注意し、 **設定時に一度だけ本コマンドを実行するように**してください。

### bridge 現状 (`production_tool/mqtt_bridge.py:2857`)

```python
def _wisun_init_sequence(fd, br_id, br_pwd):
    skcommand(fd, "SKRESET", timeout=5)
    ...
    skcommand(fd, "SKSETPWD C {}".format(br_pwd))
    skcommand(fd, "SKSETRBID {}".format(br_id))
    skcommand(fd, "WOPT 1")  # ← bridge 起動 + reconnect 毎に発行
```

`_wisun_init_sequence` は bridge process 起動時 + reconnect 時 (= `wisun_connect` fallback path、 cached SKJOIN failure 後の re-init 等) で呼ばれる。

### 影響試算

- reconnect 1 日 100 回 × 100 日 = **10,000 回到達** → FLASH 書込み制限超過
- memory [[feedback-erxudp-timeouts-periodic-pana]] によると現在 erxudp_timeout 30 件/h baseline = reconnect 頻発系 → 寿命到達リスク高
- 物理破損 = モジュール交換が必要、 software では復旧不可能

### 修正方針

公式 ROPT (`bp35a1_commandmanual_tr-j.pdf` p.42) で現在の WOPT 設定を読み取る:

```
ROPT
OK 01    ← 現在 WOPT bit0=1 (= ASCII hex 表示) を示す
```

bridge 起動 / reconnect 時に ROPT を発行 → 既に `01` ならば WOPT 1 を **skip** する。 これで WOPT FLASH 書込みは「初回 deploy 時のみ」 になり、 寿命 10,000 回は実質無制限 (= deploy ペース次第)。

## Functional Requirements

- **FR-001**: bridge に `ropt(fd)` helper を追加。 `ROPT` を発行して応答 `OK <hex>` をパースし、 MODE 値 (= 0/1) を int で返す。 失敗時は例外 (= 既存 skcommand 形式に合わせる)
- **FR-002**: `_wisun_init_sequence` の `WOPT 1` 発行直前で `ropt(fd)` を呼び、 既に 1 ならば `WOPT 1` を skip する条件分岐を追加
- **FR-003**: skip 時の log 出力: `log("WOPT 1 skip (= already set per ROPT)")` で「skip した」 ことを明示 (= FLASH 寿命対策が機能していることの観測ポイント)
- **FR-004**: DIAG_SENSOR_DEFS に `wopt_write_skipped_total` メトリクス追加。 ROPT 確認で skip した回数をカウント。 これにより「WOPT 書込みが実際に何回起きているか」 が Grafana で観測可能になり、 寿命残り回数の試算根拠になる
- **FR-005**: DiagState に `on_wopt_skip()` / `on_wopt_write()` を追加。 reconnect 時の WOPT 発行 / skip を区別
- **FR-006**: regression test 追加 (`tests/unit/test_wisun_health.py` または新 `test_wopt_flash_skip.py`):
  - ROPT 応答 "OK 01" → skip = `on_wopt_skip` が呼ばれる、 WOPT 1 が serial に流れない
  - ROPT 応答 "OK 00" → 書込み = `on_wopt_write` が呼ばれる、 WOPT 1 が serial に流れる
  - ROPT が timeout / FAIL → fallback で WOPT 1 を発行 (= 安全側、 既存挙動維持)

## Out of Scope

- WUART の同様処理 (= memory「bridge は UART config 触らない」、 別 spec 候補)
- SKSAVE / SKLOAD の活用 (= P-NEW-4、 spec 040 で別途)
- ROPT 応答の MODE bit1-7 (= 予約) のハンドリング (= 仕様上 bit0 のみ有効)

## Success Criteria

- **SC-001**: bridge `_wisun_init_sequence` 内で WOPT 1 発行前に ROPT 確認の条件分岐がある
- **SC-002**: 単体 test で 3 ケース (= skip / write / fallback) が pass
- **SC-003**: deploy 後の admin UI `/api/log` で `WOPT 1 skip (= already set per ROPT)` ログが reconnect 毎に出現することを 1 件以上確認
- **SC-004**: deploy 後 7 日間で `wopt_write_skipped_total` メトリクスが increment し、 同期間で実際の WOPT 書込み件数が 1-2 件 (= bridge 起動時の初回のみ) であることを Grafana で確認

## Related

- audit findings: [[audit-bp35a1-skstack-ip-vs-bridge]] P-NEW-1
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` p.41 (WOPT)、 p.42 (ROPT)
- 関連 memory: [[reference-bp35a1-skstack-ip-vs-j11-firmware]] (= 公式仕様の所在)
- 後続 spec 候補: spec 040 (= P-NEW-4 SKSAVE/SFF 活用) で WOPT FLASH 書込みを SKSAVE 統合する選択肢もある (= ただし WOPT は仕様上独立 FLASH 領域なので影響なし)
