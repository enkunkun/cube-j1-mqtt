# Feature Specification: `_wait_skjoin_event25` で EVENT 25/24 を on_sk_event に渡す (= spec 006 hook 漏れ修正)

**Feature Branch**: `044-wait-skjoin-event25-metric-hook`
**Created**: 2026-06-30
**Status**: Draft
**Input**: 2026-06-30 spec 038/040 Phase 1 観察で「`sk_event_25_total` = 0 件 vs `wisun_joined` 51 件」 の不一致発見。 root cause = `_wait_skjoin_event25` (= spec 035 で抽出) が EVENT 25/24 受信時に `diag_state.on_sk_event` を呼んでいない。 spec 006 EVENT metric 観測カバレッジに穴。 詳細 = [[feedback-bridge-skjoin-event25-not-counted]]。

## Background

### 発見の経緯

spec 040 Phase 1 観察で `sk_event_25_total` が 0 件 = bridge log の `wisun_joined` event 51 件と完全不一致。 SKJOIN 51 回成功なのに metric が 0 = bridge code 構造の bug。

### bridge code 現状 (`production_tool/mqtt_bridge.py:2941-2951`)

```python
if "EVENT 25" in line:
    if LOGGER is not None:
        emit_wisun_joined(LOGGER, pan=pan, ipv6=ipv6)
    else:
        log("SKJOIN: connected")
    return True  # ← diag_state.on_sk_event("25") を呼んでいない

if "EVENT 24" in line:
    if LOGGER is not None:
        emit_wisun_join_failed(LOGGER, reason="PANA authentication failed (EVENT 24)")
    return False  # ← 同じく on_sk_event("24") も呼んでいない
```

### read_erxudp との対比

`read_erxudp` (= L3460 周辺) では EVENT 受信時に `diag_state.on_sk_event(value)` を呼んでいる:

```python
if value in ("24", "29"):
    diag_state.on_wisun_pana_fail(value)  # → 内部で on_sk_event も呼ぶ
else:
    diag_state.on_sk_event(value)
```

SKJOIN main path (= `_wait_skjoin_event25`) と ERXUDP wait path (= `read_erxudp`) で EVENT 処理 hook が **不整合**。 spec 035 で `_wait_skjoin_event25` を抽出した際に spec 006 hook を踏襲しなかったことが原因。

### 影響

- `sk_event_25_total` = SKJOIN 成功回数を反映しない (= 51 回成功で 0 件)
- `sk_event_24_total` = SKJOIN 時の PANA 失敗を反映しない (= read_erxudp 経由の EVENT 24 のみカウント)
- spec 040 (= PANA 720s 自動再認証周期検証) の前提崩壊

### 修正方針

`_wait_skjoin_event25` の signature に `diag_state=None` を追加、 EVENT 25/24 受信時に `on_sk_event` を呼ぶ。 EVENT 24 は `on_wisun_pana_fail` も呼ぶ判断は dig で確定 (= read_erxudp との挙動整合性)。

## Functional Requirements

### FR-001: `_wait_skjoin_event25` signature に `diag_state` 引数追加

```python
def _wait_skjoin_event25(fd, pan, ipv6, timeout, diag_state=None):
    ...
```

既存挙動互換 = `diag_state=None` のとき何もしない (= test 影響最小)。

### FR-002: EVENT 25 受信時に on_sk_event("25") を呼ぶ

```python
if "EVENT 25" in line:
    if LOGGER is not None:
        emit_wisun_joined(LOGGER, pan=pan, ipv6=ipv6)
    else:
        log("SKJOIN: connected")
    if diag_state is not None:
        try:
            diag_state.on_sk_event("25")
        except Exception as e:
            log("diag on_sk_event(25) error: {}".format(e))
    return True
```

### FR-003: EVENT 24 受信時に on_sk_event("24") を呼ぶ

EVENT 24 = PANA Failed。 read_erxudp の `on_wisun_pana_fail("24")` 経由で sk_event_counts["24"] が increment される設計と同じ意味論を持たせるため、 _wait_skjoin_event25 でも on_sk_event("24") を呼ぶ。

ただし `on_wisun_pana_fail` も呼ぶか判断:
- read_erxudp 経路: 「main loop poll 中に PANA failure 検知 → next iter で SKREJOIN」 → pending_wisun_rejoin = True
- _wait_skjoin_event25 経路: 「SKJOIN 中の PANA failure → 呼出側 wisun_connect が直接 fallback path に進む」 → 既に reconnect 中なので pending_wisun_rejoin = True 不要

→ FR-003 では **`on_sk_event("24")` のみ呼ぶ** (= `on_wisun_pana_fail` は呼ばない、 既存挙動維持)。

```python
if "EVENT 24" in line:
    if LOGGER is not None:
        emit_wisun_join_failed(LOGGER, reason="PANA authentication failed (EVENT 24)")
    if diag_state is not None:
        try:
            diag_state.on_sk_event("24")
        except Exception as e:
            log("diag on_sk_event(24) error: {}".format(e))
    return False
```

### FR-004: wisun_connect 2 呼び出し更新

- cached path L2999: `_wait_skjoin_event25(fd, cached_pan, cached_ipv6, timeout=30, diag_state=diag_state)`
- full scan path L3079: `_wait_skjoin_event25(fd, pan, ipv6, timeout=90, diag_state=diag_state)`

### FR-005: regression test 追加

- `_wait_skjoin_event25` で EVENT 25 受信時に diag_state.on_sk_event("25") が呼ばれる
- 同じく EVENT 24 で on_sk_event("24")
- diag_state=None でも既存挙動互換 (= 例外なく動く)
- threading + LED side effect 対応の test pattern (= 既存 spec 035 test がある場合は踏襲)

## Out of Scope

- EVENT 24 の `on_wisun_pana_fail` 呼出 (= 上記 FR-003 で議論済、 既存挙動維持)
- read_erxudp 内の EVENT 21/27 hook 追加 (= spec 042 で対応済、 本 spec とは独立)
- spec 035 cached path 内の `emit_wisun_joined` 二重呼出 (= 別 spec、 ここでは触らない)

## Success Criteria

- **SC-001**: bridge `_wait_skjoin_event25` で EVENT 25/24 受信時に on_sk_event が呼ばれる
- **SC-002**: 単体 test pass (= 既存 + 新規 ~3 件、 全体 ~490 件)
- **SC-003**: deploy 後 1h で gcx query `cube_j1_smart_meter_sk_event_25_total{device_id="cubej1"}` が 0 件 → 1 件以上に increment 確認
- **SC-004**: 7 日間運用後の `sk_event_25_total` 件数が bridge log の `wisun_joined` 件数と概ね一致 (= ±10% 以内)
- **SC-005**: spec 040 Phase 1 再観察 (= spec 044 deploy 後 24h) で sk_event_25 の出現周期から PANA 720s 仮説の verification 可能化

## Related

- 観測事象: 2026-06-30 spec 038/040 Phase 1 観察で発見
- 関連 memory:
  - [[feedback-bridge-skjoin-event25-not-counted]] (= bug 詳細)
  - [[feedback-phase1-event21-zero-erxudp-rx-dominant]] (= Phase 1 観察結果)
- 関連 spec:
  - spec 006 (= EVENT metric 観測機構、 本 spec で hook 漏れ修正)
  - spec 035 (= SKLL64 cached + SKJOIN 直行、 _wait_skjoin_event25 抽出元)
  - spec 040 (= PANA 720s 観察、 本 spec fix で観察基盤回復、 Phase 1 再観察前提)
- audit findings: 本 spec は audit findings 9 件の外、 bridge 既存 bug 修正
