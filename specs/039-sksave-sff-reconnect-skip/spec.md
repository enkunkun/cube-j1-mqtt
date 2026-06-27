# Feature Specification: SKSAVE + SFF=1 で reconnect 床値 11s → ~5s 突破

**Feature Branch**: `039-sksave-sff-reconnect-skip`
**Created**: 2026-06-28
**Status**: Phase 1 設計確認待ち
**Input**: 2026-06-27 audit ([[audit-bp35a1-skstack-ip-vs-bridge]]) の P-NEW-4。 BP35A1 公式 Ver 1.3.2 p.10/31/32 で SKSAVE/SKLOAD/SFF レジスタによる FLASH 永続化機構が提供されているが、 bridge は SKSAVE/SKLOAD/SFF/S0A を全て未使用 (= grep 0 件)。 memory [[feedback-bp35cx-reconnect-floor-11s]] で確定した reconnect 床値 11s のうち WOPT/SKSREG S2/S3 分の ~6s を SFF=1 オートロードで省略すれば床値 ~5s 達成。

## Background

### 公式仕様 (BP35A1 Ver 1.3.2)

**p.31 SKSAVE**: 「FLASH メモリ上の不揮発性レジスタにレジスタ値を保存します」 保存可能レジスタは以下:
- **S02**: チャネル
- **S03**: PAN ID
- **S0A**: ペアリング ID (= 同一 PAN 識別子)
- **SFF**: 起動時オートロード (= 1 で起動時に SKLOAD 相当を自動実行)
- **WOPT**: コンソールエコー / メッセージ出力 mode (= spec 037 で対応済)
- **WUART**: UART 設定 (= bridge は触らない)

**重要な制約 (p.10 レジスタ一覧確認)**: **SKSETPWD / SKSETRBID は SKSAVE 対象外**。 これらは bridge 起動毎の発行が必須で、 完全な credential skip は不可能。

**p.31 SKLOAD**: 「FLASH メモリ上に保存されているレジスタ値を読み出します」 SKSAVE と対になる手動 load。

**p.32 SFF**: 「電源投入時のオートロード制御。 1 にすると起動時に SKLOAD 相当を自動実行」 SFF=1 を一度 SKSAVE すれば、 以降は電源 ON 時に S02/S03/S0A/WOPT が自動復元される。

### bridge 現状

`grep -E "SKSAVE|SKLOAD|SFF|S0A" production_tool/mqtt_bridge.py` で **0 件**。 全て未使用。

memory [[feedback-bp35cx-reconnect-floor-11s]] によると現状 reconnect 床値 11s の内訳 (= 推定):

| 工程 | 床値 [s] |
|---|---|
| SKRESET | 1-2 |
| SKVER (= EVER 1.5.2 表示) | 0.5 |
| SKSETPWD C \<pwd\> | 1-2 |
| SKSETRBID \<id\> | 0.5-1 |
| WOPT 1 (= spec 037 で skip 化) | 0-1 |
| SKSREG S2 \<channel\> | 0.5-1 |
| SKSREG S3 \<pan_id\> | 0.5-1 |
| SKLL64 (= spec 035 cache hit) | 0.5-1 |
| SKJOIN \<ipv6\> | 4-5 |
| **計** | **~11s** |

### 修正後の理論床値

SFF=1 で起動時オートロードが効くと S02/S03 + WOPT (= spec 037 で skip 化済) は SKRESET 直後に復元される。 残りは SKRESET + SKVER + SKSETPWD + SKSETRBID + SKLL64 + SKJOIN = ~5s が床値。

| 工程 | 床値 [s] |
|---|---|
| SKRESET | 1-2 |
| SKVER | 0.5 |
| SKSETPWD C | 1-2 |
| SKSETRBID | 0.5-1 |
| (S02/S03/WOPT = SFF=1 で省略) | 0 |
| SKLL64 (cached) | 0.5-1 |
| SKJOIN | 4-5 |
| **計** | **~7s** (= 4s 短縮、 35% 減) |

公式仕様の解釈次第では SKLL64 の MAC アドレス変換も SFF オートロード後の cache から取れて更に短縮可能性あり (= Phase 1 で確認)。

## Phase 1: 設計確認 (= dig)

### 確認事項 (= dig で user 判断)

1. **SKSAVE 発行タイミング**: (a) 初回 deploy 時の手動コマンド、 (b) bridge 起動時に S02/S03 設定後 1 回のみ、 (c) reconnect 毎に呼ぶが SFF=1 確認後は skip
2. **既存 reconnect path との整合**: `_wisun_init_sequence` の SKSREG S2 / S3 発行を SFF=1 検出時に skip する、 失敗時は fallback で発行
3. **SFF 状態確認の手段**: 公式に SFF 読み取り (`RFF` 相当) が無い場合は SKSAVE 副作用ベース判定 (= 試しに SKSREG S2 を読んで現在値と一致するかで判定)
4. **regression risk**: SFF=1 状態で別の bridge instance が別 PAN を発見した場合の挙動 (= S02/S03 が古い値で復元されて SKJOIN 失敗の可能性)

### Phase 1 観察

```bash
# 実機で現在の SFF / S02 / S03 を読み取り
# SKSREG SFF (= 読み取りは "SKSREG SFF" で OK)
# SKSREG S02 / S03 (= 現在チャネル / PAN ID)
```

## Phase 2: 実装 (= dig 後)

### FR-001: bridge に SKSAVE / SKLOAD / SFF helper を追加 (= ropt と同じ pattern)

```python
def sksreg_read(fd, reg, timeout=2):
    """Read SKSREG <reg>. Returns int hex value."""
    ...

def sksave(fd, timeout=3):
    """Persist current registers to FLASH (S02/S03/S0A/WOPT/WUART/SFF)."""
    ...
```

### FR-002: bridge 起動時に SFF=1 を SKSAVE (= 初回のみ)

`_wisun_init_sequence` 内で SKSREG SFF を読み、 0 なら 1 に設定 → SKSAVE 発行 → log 出力。 次回起動から SFF=1 が効く。

### FR-003: reconnect 時に SFF=1 を信頼して S02/S03 設定を skip

`_wisun_init_sequence` cached path で SFF=1 状態を検出した場合は S02/S03 の SKSREG 発行を skip。 ただし SKSCAN 後に新 channel/pan_id が発見されたら強制的に SKSREG で上書き + SKSAVE で再保存。

### FR-004: DiagState 拡張

```python
self.sff_autoload_used_count = 0  # SFF=1 で skip した回数
self.sksave_total = 0              # SKSAVE 発行回数
```

```python
("sff_autoload_used_total", "SFF=1 Autoload Used (= S02/S03 skip)", ...),
("sksave_total",            "SKSAVE Issued Count",                   ...),
```

### FR-005: 安全策 - SKSAVE 失敗時 fallback

SKSAVE が FAIL を返した場合は通常 path (= 毎回 SKSREG 発行) に fallback、 fail count をメトリクスで観測。 FLASH 寿命 (= WOPT と同じ 10,000 回制限) も気にする必要があり、 SKSAVE 自体も skip 判定 (= 既存値と一致なら skip) を行う。

### FR-006: regression test 追加 (`tests/unit/test_wisun_health.py` or new file)

- sksreg_read("S02") → 期待値
- sksave() → OK / FAIL の両 path
- SFF=1 検出時の S02/S03 skip / SFF=0 時の通常 path
- SKSCAN 後 channel 変動時の SKSAVE 強制発行

## Out of Scope

- SKSETPWD / SKSETRBID の cache (= 仕様上 SKSAVE 対象外、 別途 BP35A1 firmware 拡張の確認が必要)
- WUART の SKSAVE 統合 (= bridge は UART config 触らない、 memory 遵守)
- SKLL64 cache を SKSAVE に統合する (= 別 spec、 spec 035 cache とは別レイヤ)
- spec 037 WOPT FLASH skip との統合 (= 既に独立で動作、 SKSAVE で同じ FLASH 書込みカウントを消費する点は dig で要検討)

## Success Criteria

### Phase 1 (= 設計)

- **SC-001 (Phase 1)**: dig で SKSAVE タイミング + reconnect skip 条件 + SFF 読み取り方法 + regression risk 対策を確定

### Phase 2 (= 実装)

- **SC-002 (Phase 2)**: bridge `_wisun_init_sequence` で SFF=1 検出時に S02/S03 SKSREG 発行を skip + SFF=0 時に SKSAVE で永続化
- **SC-003 (Phase 2)**: 単体 test pass (= 既存 + 新規 ~10 件)
- **SC-004 (Phase 2)**: deploy 後の admin UI ログで `SFF autoload used` メッセージが reconnect 毎に出現
- **SC-005 (Phase 2)**: deploy 後 24h で reconnect 時間中央値 11s → 7s 以下 (= 35% 短縮達成、 Grafana で reconnect_duration_seconds 観測)
- **SC-006 (Phase 2)**: deploy 後 7 日間で `sksave_total` が 1-2 件 (= 初回 deploy + channel 変動時のみ)

## Related

- audit findings: [[audit-bp35a1-skstack-ip-vs-bridge]] P-NEW-4
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` p.10 (= レジスタ一覧)、 p.31-32 (= SKSAVE/SKLOAD/SFF)
- 関連 memory:
  - [[feedback-bp35cx-reconnect-floor-11s]] (= 床値 11s の根拠、 本 spec が更新可能性)
  - [[feedback-erxudp-timeouts-periodic-pana]] (= 30 件/h baseline、 本 spec は対象外)
- 関連 spec:
  - spec 034 (= 単 ch active scan 不発、 disable 済)
  - spec 035 (= SKLL64 cached、 本 spec と組み合わせて床値突破)
  - spec 037 (= WOPT FLASH skip、 同じ FLASH を本 spec も使用)
- 並行 spec: spec 038 (P-NEW-3 EVENT 0x21)、 spec 040 (P-NEW-5 PANA 720s)
