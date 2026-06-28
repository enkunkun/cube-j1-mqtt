# Feature Specification: PANA セッションライフタイム 720s 自動再認証の挙動観察 + 対策評価

**Feature Branch**: `040-pana-720s-reauth-observation`
**Created**: 2026-06-28
**Status**: **Phase 1 判定保留 (= 観察基盤の既存 bug で判定不可、 spec 044 fix 後に再評価) / 2026-06-30 JST**

deploy から 42h 経過で `sk_event_27_total` = 0 件、 `sk_event_25_total` = 0 件。 ただし bridge log で `wisun_joined` event 51 件 = SKJOIN 51 回成功なのに sk_event_25_total = 0 = **bridge 既存 bug** (= `_wait_skjoin_event25` で on_sk_event 未呼出、 [[feedback-bridge-skjoin-event25-not-counted]] に詳細) で SKJOIN 文脈の EVENT が metric 計上されない設計欠落判明。

= 「PANA 720s 自動再認証周期 vs 12 分周期 erxudp_timeout の相関」 を sk_event_25_total ベースで判定する **観察基盤の前提崩壊**。 spec 044 (= bug 修正) deploy 後に Phase 1 再観察 = 24h 経過後に再評価。

audit findings P-NEW-5 = **未確定 (= spec 044 待ち)**、 spec 040 自体は draft 継続。
**Input**: 2026-06-27 audit ([[audit-bp35a1-skstack-ip-vs-bridge]]) の P-NEW-5。 BP35A1 公式 Ver 1.3.2 p.9 で S16 (= PANA セッションライフタイム) のデフォルトは 900 秒、 p.14 で 80% 経過時に PaC が SKREJOIN を自動実行、 つまり接続後 **720 秒 (= 12 分) ごと**に自動再認証が走る。 memory [[feedback-erxudp-timeouts-periodic-pana]] で「erxudp_timeouts 30 件/h baseline + 10-11 分周期」 と観測された周期は、 この自動再認証と一致する仮説あり。

## Background

### 公式仕様 (BP35A1 Ver 1.3.2)

**p.9 S16 (= PANA セッションライフタイム)**:
- 単位: 秒
- 範囲: 60 - 0xFFFFFFFF
- デフォルト: **0x384 (= 900 秒、 15 分)**

**p.14 自動再認証**:
> 「セッションライフタイムの 80% が経過した時点で、 PaC が SKREJOIN を自動的に実行します」

→ 接続成立から **720 秒** (= 900 * 0.8) ごとに自動 SKREJOIN が走る。

**p.17 送信禁止期間**:
> 「SKJOIN 発行から EVENT 25 発生まで無線送信をしないでください」

SKREJOIN も同様の送信禁止期間が発生する可能性 (= 仕様未明示) → SKSENDTO と衝突したら ERXUDP timeout 発生。

### bridge 現状

- S16 を SKSREG で設定する code は **0 件** (= デフォルト 900s のまま)
- SKREJOIN 自動実行を捕捉する EVENT 通知の handling は **未定義**
- main loop の poll_interval = 60s (= デフォルト) → 12 分ごとに 1-2 cycle と衝突する可能性

### 既存観測 (memory [[feedback-erxudp-timeouts-periodic-pana]])

- erxudp_timeouts baseline = 30 件/h
- 周期: 10-11 分 (= 720s = 12 分と近い)
- ユーザ証言で「self-induced 仮説 D 否定」、 真の解は hardware (= spec 031 CT クランプ)、 software 改善は 4 spec で限界と結論

本 spec はこの結論の **観測ベース更新**。 EVENT 0x21 (= spec 038) と並んで、 「観測されていない事象を観測する」 ことで限界判断の根拠を取り直す。

## Phase 1: 観察 (= 周期実証)

### 確認手順

```bash
# 24h 以上の実機 log 取得
curl -s -u admin:<pw> http://cube-j1.home.arpa:8000/api/log?lines=100000 > /tmp/bridge-log-24h.txt

# SKJOIN 発行タイミング (= 自動 SKREJOIN を含む)
grep -nE "SKJOIN|SKREJOIN" /tmp/bridge-log-24h.txt | head -50

# EVENT 25 (= 接続成功) のタイミング
grep -nE "EVENT 25" /tmp/bridge-log-24h.txt | head -50

# 接続成功からの経過時間で 720s ピークを確認
# (= awk で timestamp 差分集計、 scratchpad に script 用意)

# ERXUDP timeout 発生時刻と SKREJOIN タイミングの相関
grep -nE "erxudp_timeout|SKREJOIN|EVENT 25" /tmp/bridge-log-24h.txt
```

### 仮説検証マトリクス

| 観測パターン | 仮説の検証 | 次手 |
|---|---|---|
| SKREJOIN log が 720s 周期で出る | 自動再認証発火確定 → ERXUDP timeout 相関を集計 | Phase 2 へ |
| SKREJOIN log は無いが EVENT 25 が 720s 周期で出る | 自動再認証は発火しているが SKREJOIN log は内部処理のみ | Phase 2 へ |
| 720s 周期の log 兆候が一切無い | 自動再認証は実機 firmware で発火していない / 別 mechanism | spec close (= ERXUDP timeout は別 root cause) |
| ERXUDP timeout と SKREJOIN/EVENT 25 の時刻相関が高い | 衝突仮説確定 | Phase 2 で対策 A or B |
| ERXUDP timeout は周期的だが SKREJOIN/EVENT 25 と無相関 | 別 root cause (= メーター側ファーム / 電波環境) | spec close、 memory 更新 |

### Phase 1 観察ツール (= 軽量実装)

`production_tool/mqtt_bridge.py` の classify_sk_line に SKREJOIN 関連の event を追加して、 Phase 2 実装前に **観測のみ** を deploy:

```python
("event_25_pana_success", r"^EVENT 25 \S+$"),     # 既存
("event_24_pana_failure", r"^EVENT 24 \S+$"),     # 既存
# 新規 (= 観測のみ):
("event_27_session_end",      r"^EVENT 27 \S+$"),  # PANA セッション終了 (= reauth 失敗等)
```

DiagState + DIAG_SENSOR_DEFS に sk_event_27_total を追加して、 EVENT 25 / 27 の 720s 周期出現を Grafana で観測。

## Phase 2: 対策実装 (= 衝突確定時)

### 対策候補 A: S16 を最大値に設定して自動再認証を実質無効化

```python
# bridge 起動時:
skcommand(fd, "SKSREG S16 FFFFFFFF")  # = 約 136 年
```

- 利点: 単純、 衝突回避確実
- 欠点: PANA セッションが理論上無期限に → メーター側でセッション切れ時の動作不明、 仕様逸脱の可能性

### 対策候補 B: SKREJOIN を bridge が能動制御 (= 720s tick で自分のタイミングで)

- bridge main loop に「最後の EVENT 25 から経過時間が 600s 超 + 次の SKSENDTO 前」 で能動 SKREJOIN
- 利点: poll 周期との衝突を avoid、 制御可能
- 欠点: 実装複雑、 SKREJOIN 失敗時の fallback 設計が必要

### 対策候補 C: 何もしない + 観測のみ + adaptive polling との連携

- Phase 1 観察で「ERXUDP timeout 主因 < 50%」 と判明したら spec close
- 残余対策は spec 032 系列の adaptive polling、 spec 031 CT (= memory 結論通り)

## Out of Scope

- メーター側 (= 経済産業省 B-route 仕様) の PANA セッション挙動推測
- SKREJOIN 失敗時の再認証 cascading 制御 (= 別 spec)
- ECHONET アプリケーション層の retry (= bridge より上位)

## Success Criteria

### Phase 1 (= 観察)

- **SC-001 (Phase 1)**: classify_sk_line + DiagState + DIAG_SENSOR_DEFS に EVENT 27 観測を追加して deploy
- **SC-002 (Phase 1)**: 24h 以上の log で SKREJOIN / EVENT 25 / EVENT 27 の出現周期を集計、 720s 仮説を実証 or 反証
- **SC-003 (Phase 1)**: erxudp_timeout と SKREJOIN/EVENT 25 の時刻相関を集計、 「衝突」 仮説の真偽を確定

### Phase 2 (= 対策、 衝突確定時のみ)

- **SC-004 (Phase 2)**: dig で対策 A/B/C を選択
- **SC-005 (Phase 2)**: 選択した対策実装 + 単体 test pass
- **SC-006 (Phase 2)**: deploy 後 7 日間で erxudp_timeouts baseline が低下、 12 分周期ピーク消失を Grafana で確認

## Related

- audit findings: [[audit-bp35a1-skstack-ip-vs-bridge]] P-NEW-5
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` p.9 (S16)、 p.14 (= 自動再認証)、 p.17 (= 送信禁止期間)
- 関連 memory:
  - [[feedback-erxudp-timeouts-periodic-pana]] (= 既存結論、 本 spec が更新可能性)
  - [[reference-tako-instantaneous-power-architectural-cap]] (= software 限界の根本根拠、 hardware が真の解)
  - [[reference-tako-spec027-v2-design]] (= 関連設計案)
- 関連 spec:
  - spec 027 (= base reconnect threshold、 erxudp timeout 連動 reconnect)
  - spec 028 (= 瞬時電力 recovery)
  - spec 031 (= CT クランプ、 真の解)
  - spec 032 (= aggressive polling、 software 改善 4 件の 1 つ)
- 並行 spec: spec 038 (P-NEW-3 EVENT 0x21)、 spec 039 (P-NEW-4 SKSAVE/SFF)
