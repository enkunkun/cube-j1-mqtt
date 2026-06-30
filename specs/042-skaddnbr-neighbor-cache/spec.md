# Feature Specification: SKJOIN 後 SKADDNBR で IP 層ネイバーキャッシュ登録 = 初回 SKSENDTO 1-2s 短縮

**Feature Branch**: `042-skaddnbr-neighbor-cache`
**Created**: 2026-06-28
**Status**: Phase 2 Implemented (= 2026-06-30 JST、 commit 完了、 cube-j1 adb 接続不可 (= 物理 power cycle 待ち) で deploy 保留中、 user 復旧確認後に spec 044 と同梱 deploy 予定)
**Input**: 2026-06-27 audit ([[audit-bp35a1-skstack-ip-vs-bridge]]) の P-NEW-8。 BP35A1 公式 Ver 1.3.2 p.29 で SKADDNBR は「IP 層のネイバーキャッシュに Reachable 状態で登録、 アドレス要請を省略して直接 IP パケットを出力」 と明示されているが、 bridge は未使用 (= grep 0 件)。 初回 SKSENDTO で Neighbor Solicitation が発生し 1-2s 追加。

## Background

### 公式仕様 (BP35A1 Ver 1.3.2 p.29)

> 「指定した IPv6 アドレスと MAC アドレス (IEEE 64bit) 情報を、 IP 層のネイバーキャッシュに Reachable 状態で登録します。 これによってアドレス要請を省略して直接 IP パケットを出力することができます」

入力形式 (= 公式仕様):
```
SKADDNBR <IPADDR> <MACADDR>
```

response = OK / FAIL ER<code>。

### bridge 現状

- `grep -E "SKADDNBR" production_tool/mqtt_bridge.py` → **0 件** (= 未使用)
- 初回 SKSENDTO 時に IP 層が Neighbor Solicitation を送出 → メーター応答待ち = **1-2 秒追加**
- 通常運用では 60s poll cycle なので 1-2s は許容、 ただし reconnect 直後の **初回 cycle** で観測される

### 修正方針

`wisun_connect` の SKJOIN 成功 path 2 か所 (= cached path + full scan path) で、 EVENT 25 受信後に `SKADDNBR <ipv6> <mac>` を 1 回打つ。 これで以降の SKSENDTO は **ネイバー解決 skip + 直接 IP パケット送出** で完了。

### spec 039 失敗との比較 (= 構造的問題なしの確認)

spec 039 (= SKSAVE + SFF=1) は **永続化機構を SKRESET 経由で再利用しようとした** ため、 SKRESET が「内部変数初期化」 で SFF をクリアして失敗。 spec 042 (= SKADDNBR) は **SKJOIN 成功直後に毎回 1 行打つ揮発前提** なので、 SKRESET でネイバーキャッシュがクリアされても次回 SKJOIN で再登録される自然な設計。 構造的問題なし。

## Functional Requirements

### FR-001: SKADDNBR 発行 helper or skcommand 直接呼び出し

SKADDNBR は OK / FAIL の単純 response、 既存 `skcommand` で対応可能 (= 完全一致 "OK" break)。 ただし dig で「skcommand の timeout 値 / 失敗時挙動」 を確定。

### FR-002: wisun_connect 2 か所で SKJOIN 成功後 SKADDNBR

- cached path (= spec 035 L2994 周辺): `_wait_skjoin_event25` 成功直後に `skcommand(fd, "SKADDNBR {} {}".format(cached_ipv6, cached_mac))`
- full scan path (= L3076 周辺): `_wait_skjoin_event25` 成功直後に `skcommand(fd, "SKADDNBR {} {}".format(ipv6, mac))`

### FR-003: SKADDNBR 失敗時の fallback (= 安全側)

SKADDNBR が FAIL を返した場合は **continue** (= 次の SKSENDTO は通常通り Neighbor Solicitation で解決)。 throw せず、 log で記録のみ。 既存 wisun_connect の return path を変えない。

### FR-004: DiagState / DIAG_SENSOR_DEFS 拡張 (= 観測点)

- `skaddnbr_total` (= 発行成功回数、 0 でも常時 publish)
- `skaddnbr_fail_total` (= 発行失敗回数、 観測点)

これらで「SKADDNBR が機能しているか」 を Grafana で観測可能化。

### FR-005: regression test

- `test_wisun_health.py` に SKADDNBR helper / DiagState 拡張 / DIAG_SENSOR_DEFS 登録 の test
- integration test は `_wisun_init_sequence` 同様 deploy verify に委ねる

## Out of Scope

- SKDEL (= ネイバーキャッシュ削除コマンド、 仕様確認後別 spec 候補)
- SKTABLE E でネイバーキャッシュ確認 (= debug 用、 別 spec)
- 複数 PAN 環境 / 複数 meter の SKADDNBR 同時管理

## Success Criteria

- **SC-001**: bridge `wisun_connect` の SKJOIN 成功 path 2 か所で SKADDNBR が発行される
- **SC-002**: 単体 test pass (= 既存 + 新規 ~4 件)
- **SC-003**: deploy 後の admin UI ログで SKJOIN 直後に `SKADDNBR OK` ログが出現することを確認
- **SC-004**: deploy 後 24h で `skaddnbr_total` が reconnect 件数 (= 30 件/h × 24h) と概ね一致、 `skaddnbr_fail_total` が 0 件であることを Grafana で確認
- **SC-005**: deploy 後 reconnect 直後の初回 cycle 時間が 1-2s 短縮されることを log 解析 (= EVENT 25 → poll_success までの時間差) で確認

## Related

- audit findings: [[audit-bp35a1-skstack-ip-vs-bridge]] P-NEW-8
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` p.29 (SKADDNBR)
- 関連 memory:
  - [[feedback-bp35a1-skreset-clears-sksreg-non-product]] (= spec 039 失敗教訓、 SKADDNBR は揮発前提なので影響なし)
  - [[feedback-bp35cx-reconnect-floor-11s]] (= 床値、 本 spec の効果は床値以外 = 初回 cycle 時間)
- 関連 spec:
  - spec 035 (= SKLL64 cached + SKJOIN 直行、 本 spec を組み込む path)
  - spec 037 (= WOPT FLASH skip、 本 spec と独立)
  - spec 039 (= SKSAVE + SFF Closed、 本 spec とは別アプローチ)
