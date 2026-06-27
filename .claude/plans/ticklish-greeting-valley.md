# Plan: spec 036 BP35A1 SKSTACK-IP 公式仕様整合 (= EVENT メトリクスラベル誤記 + SKSCAN dwell time コメント訂正)

## Context

2026-06-27 audit (= `docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md`) で、 ROHM 認証付き DL で取得した BP35A1 公式コマンドリファレンス Ver 1.3.2 と bridge コードを系統的に突き合わせた結果、 ドキュメントレベル誤記が複数判明。 plan 作成中の Explore agent 調査で **当初想定の P-NEW-2 (= EVENT 32/33 のみ) より広い 5 件のラベル誤記** が確定:

| EVENT | 現ラベル | 公式仕様 (Ver 1.3.2 p.51) | 重大度 |
|---|---|---|---|
| 22 | "PANA OK" | アクティブスキャンが完了した | 🟡 紛らわしい (= 実装は正しく scan 完了として使用) |
| 24 | "PANA Failed" | PANA 接続エラー | ✅ 正しい (= 変更不要) |
| 25 | "PANA Done" | PANA 接続完了 | ✅ 正しい |
| 26 | "Re-auth" | 接続相手からセッション終了要求を受信 | 🔴 完全に違う (= 自動再認証ではない) |
| 28 | "Session End" | セッション終了要求への応答が無く timeout | 🟡 部分的 (= 「End」 だけでは曖昧) |
| 29 | "Session Timeout" | セッションのライフタイム経過 | 🟡 部分的 (= 「Lifetime Expired」 がより正確) |
| 32 | "Scan Done" | ARIB108 送信総和時間制限の発動 | 🔴 完全に逆 |
| 33 | "Scan Started" | 送信総和時間制限の解除 | 🔴 完全に逆 |

加えて SKSCAN dwell time コメント (= P-NEW-7) が公式式 `0.0096 * (2^N + 1)` 秒と 5 倍乖離。 動作影響なしだが将来 spec の根拠誤りを防ぐため訂正対象。

Explore agent が `specs/006-wisun-health/spec.md` にも誤記の伝搬を発見 = spec 006 が公式仕様を確認せず DIAG_SENSOR_DEFS のラベルをそのまま引用していた歴史。 spec 036 で同時訂正する。

## 修正対象 (= 5 ファイル)

### 1. `production_tool/mqtt_bridge.py` (= ラベル 5 件 + コメント 1 件)

| line | 訂正内容 |
|---|---|
| 2714 | SKSCAN dwell time コメントを公式式 `0.0096 * (2^duration + 1)` 秒に訂正 (= BP35A1 Ver 1.3.2 p.20 引用付き) |
| 3926 | "SK EVENT 22 (PANA OK)" → "SK EVENT 22 (Active Scan Done)" |
| 3929 | "SK EVENT 26 (Re-auth)" → "SK EVENT 26 (Session Termination Requested by Peer)" |
| 3930 | "SK EVENT 28 (Session End)" → "SK EVENT 28 (Session Termination Timeout)" |
| 3931 | "SK EVENT 29 (Session Timeout)" → "SK EVENT 29 (Session Lifetime Expired)" |
| 3932 | "SK EVENT 32 (Scan Done)" → "SK EVENT 32 (ARIB Transmit Limit Hit)" |
| 3933 | "SK EVENT 33 (Scan Started)" → "SK EVENT 33 (ARIB Transmit Limit Released)" |

(注: line 3927/3928 = EVENT 24/25 は公式と整合しているため変更しない)

### 2. `specs/006-wisun-health/spec.md` (= ラベル引用訂正)

L20-27 周辺の「sk_event_NN_total (説明)」 表記を bridge 訂正に合わせて更新:

| 該当 | 訂正後 |
|---|---|
| `sk_event_22_total (PANA 成功)` | `sk_event_22_total (Active Scan 完了)` |
| `sk_event_26_total (PANA 再認証要求)` | `sk_event_26_total (相手からセッション終了要求を受信)` |
| `sk_event_32_total (ARIB アクティブスキャン完了)` | `sk_event_32_total (ARIB 送信時間制限の発動)` |
| `sk_event_33_total (ARIB アクティブスキャン開始)` | `sk_event_33_total (ARIB 送信時間制限の解除)` |

### 3. `specs/036-bp35a1-spec-doc-alignment/spec.md` (= 自身を更新)

FR セクションを Phase 1 拡張スコープに更新:
- FR-001 〜 FR-005 = ラベル 5 件訂正 (= EVENT 22, 26, 28, 29, 32, 33)
- FR-006 = SKSCAN コメント訂正
- FR-007 = spec 006 spec.md 同期更新
- FR-008 = audit findings status 更新

### 4. `docs/audits/2026-06-27-bp35a1-skstack-ip-vs-bridge.md`

- ヘッダ `status: open (= 9 件中 0 件着手)` → `status: in progress (= P-NEW-2 / P-NEW-7 を spec 036 で解消中)` に変更
- deploy 完了後に SC-005 satisfied 化として `status: P-NEW-2 / P-NEW-7 resolved by spec 036 commit <hash>` に再更新

### 5. `tests/unit/test_wisun_health.py` (= テスト追加)

既存 pattern (= L119-121 の `assert snap["sk_event_22_total"] == 1`) を踏襲。 Explore agent 発見の通り EVENT 26 / 32 / 33 の increment テスト未存在 → 追加:

1. `classify_sk_line("EVENT 26 ...")` が `("event", "26")` を返すこと
2. 同 EVENT 32 / 33 で類似
3. `on_sk_event` が呼ばれて snap で `sk_event_26_total` / `_32_` / `_33_` が 1 になること
4. DIAG_SENSOR_DEFS のラベル assertion 新規:
   - `sk_event_32_total` のラベルが "ARIB Transmit Limit" を含むこと
   - `sk_event_33_total` のラベルが "ARIB Transmit Limit" を含むこと
   - regression 防止: ラベル一覧に "Scan Done" / "Scan Started" / "PANA OK" / "Re-auth" が **含まれないこと**

## TDD 順序

CLAUDE.md global rule (= [[feedback-tdd-spec-template.md]]) に従い Red → Green → Refactor:

1. **Red**: tests/unit/test_wisun_health.py に上記 4 種のテスト追加 → 既存ラベル文字列のため fail
2. **Green-1**: mqtt_bridge.py L3926-3933 のラベル 5 件 + L2714 コメントを訂正 → test pass
3. **Green-2**: specs/006-wisun-health/spec.md 4 行訂正 (= テスト対象外、 整合性のためのドキュメント同期)
4. **Green-3**: specs/036-.../spec.md の FR セクション拡張 (= 自身の文書整合)
5. **Green-4**: docs/audits/.../bp35a1-skstack-ip-vs-bridge.md status 行更新
6. **Refactor**: なし (= 文字列訂正のみで構造変化なし)

## Decisions (dig Round 1, 2026-06-27)

### ラベル英語表現 (= Item 1, 採用案: 公式意味重視)

確定の英語ラベル (= HA UI / Prometheus HELP text):

| EVENT | 旧 | 新 |
|---|---|---|
| 22 | "SK EVENT 22 (PANA OK)" | **"SK EVENT 22 (Active Scan Done)"** |
| 26 | "SK EVENT 26 (Re-auth)" | **"SK EVENT 26 (Session Termination Requested by Peer)"** |
| 28 | "SK EVENT 28 (Session End)" | **"SK EVENT 28 (Session Termination Timeout)"** |
| 29 | "SK EVENT 29 (Session Timeout)" | **"SK EVENT 29 (Session Lifetime Expired)"** |
| 32 | "SK EVENT 32 (Scan Done)" | **"SK EVENT 32 (ARIB Transmit Limit Hit)"** |
| 33 | "SK EVENT 33 (Scan Started)" | **"SK EVENT 33 (ARIB Transmit Limit Released)"** |

理由: HA UI 上の誤認ゼロ、 audit findings との整合性、 公式 Ver 1.3.2 p.51 の意味を可能な限り英訳保持。 長さよりも意味の正確性を優先 (= 既存「PANA OK」 のような短さで誤読を生むより、 「Session Termination Requested by Peer」 のように長くても明確な方がベター)。

### deploy 経路 (= Item 2, 採用案: lab-ub01 経由)

`ssh lab-ub01 'cd /tmp/cube-j1-mqtt && git pull && bash scripts/adb_push_update.sh cube-j1.home.arpa'`

理由: memory `project-deployment-topology.md` 遵守 (= Mac 直接 adb は Tailscale subnet router 越しに失敗、 lab-ub01 を bastion 必須、 hostname 引数必須)。 過去 deploy 実績 (= 2026-06-25 追記の routine) を踏襲。 Mac 直接実行は **disallowed**。

### commit 粒度 (= Item 3, 採用案: 1 commit 集約)

bridge コード + tests + spec 006 spec.md + spec 036 spec.md + audit findings を **1 つの commit** に。 commit message は global rule (= jj-workflow.md) に従い:

- jj subagent (= Bash `run_in_background: true`) で `jj diff` を渡して Conventional Commits 形式メッセージを生成
- 形式例: `docs(spec036): BP35A1 SKSTACK-IP 公式仕様整合 (= EVENT 22/26/28/29/32/33 ラベル訂正 + SKSCAN dwell time コメント訂正)`
- type 候補: `docs` (= 文字列訂正中心) or `fix` (= メトリクス意味の bug 修正)、 subagent に判断委ねる

### jj push 戦略 (= Item 11, 採用案: main 5 step push)

memory `jj-workflow.md` の「共有 bookmark への push 手順 5 step」 に厳密に従う:

1. `jj git fetch` (= リモート最新化)
2. `jj log -r 'main..@-' -n 5` (= 自分のコミットチェーンと main の関係確認)
3. (必要時のみ) `jj rebase -s <my-root> -d main` で main の上に rebase。 `<my-root>` = 自分が作った全コミットのうち、 リモート main の祖先になっていない最古
4. `jj bookmark set main -r @-` (= bookmark forward 移動)
5. `jj git push --bookmark main`

理由: 過去 spec (= 001-035) はすべて main 直 push の前例。 spec 036 も同様。 別 bookmark (= feature/spec-036) は採用しない (= 過去履歴と一貫しない)。 scp 経由の直接 deploy も採用しない (= memory 遵守と jj history との一貫性確保)。

lab-ub01 側の `git pull` は jj git push 完了後に origin main 経由で新 commit を取得する。

### HA UI 上の sensor 表示名変更 (= Item 5, 採用案: entity_id 維持 + name 変更許容)

deploy 後、 HA dashboard 上で sensor の表示名が以下のように変わる:
- 「SK EVENT 22 (PANA OK)」 → 「SK EVENT 22 (Active Scan Done)」
- 「SK EVENT 32 (Scan Done)」 → 「SK EVENT 32 (ARIB Transmit Limit Hit)」 等

ただし HA entity_id (= `sensor.cubej1_sk_event_22_total` 等) と Prometheus metric name (= `sk_event_22_total`) は **不変**。 user dashboard を entity_id ベース / metric name ベースで記述している場合は影響なし (= クエリ層は無傷)。

採用方針: user に事前確認せずに deploy を進める。 deploy 後に user が「sensor 名が変わった」 と気づいた時点で、 意図通りの訂正であることを説明する。 もし user が override したい場合は post-hoc rollback ではなく override mapping (= 「旧名を維持するエイリアス」 等) を別 spec で検討。

理由: spec 036 の本質はメトリクスラベルの意味の正常化であり、 表示名変更は意図通りの帰結。 過去の表示名「PANA OK」「Scan Done」 等は **誤読を生む誤記**なので、 維持する価値がない。

### SC-004 期間拡張ルール (= Item 4)

過去観測の ARIB 上限到達 sample が **過去 7 日で 0 件**だった場合:
1. 過去 **30 日**に期間拡張して `gcx --context cloud query` で再 query
2. 30 日でも 0 件なら **90 日**に再拡張
3. 90 日でも 0 件なら「観測未確認、 ただしメトリクス意味の訂正は実装済」 として audit findings に記録、 SC-004 を partial satisfied 化
4. retention 上限 (= Prometheus / Grafana Cloud の保持期間) を超える場合はそこで打ち切り

## 既存 helper 再利用 (= 新規 helper 不要)

- メトリクスラベル定義: `DIAG_SENSOR_DEFS` (= production_tool/mqtt_bridge.py L3920 周辺)
- EVENT parser: `classify_sk_line` (= 同 ファイル内)
- DiagState 増分処理: `on_sk_event` (= L2451-2453)
- テスト pattern: `tests/unit/test_wisun_health.py` (= L21, L25, L119-121)

## Verification

1. **host test**: `cd src && pytest tests/unit/test_wisun_health.py -v` で新 4 テスト pass
2. **lint**: `ruff check .` で既存 + 新コードに問題なし (= 既存 6 件の無関係 ruff エラーは todo.md 通り別途)
3. **commit**: bridge + tests + 関連 spec.md / audit を 1 commit に
4. **deploy**: `ssh lab-ub01 'cd /tmp/cube-j1-mqtt && git pull && bash scripts/adb_push_update.sh cube-j1.home.arpa'` 経由 (= memory「Mac 直接 adb は失敗、 lab-ub01 を bastion」 + 「scripts/adb_push_update.sh の引数は hostname 必須」 + 「lab-ub01 内 deployment は /tmp/cube-j1-mqtt に git clone + bash scripts/adb_push_update.sh cube-j1.home.arpa」)
5. **SC-001 / SC-002 確認**: bridge コード grep で新ラベルが全て公式語と整合
6. **SC-003 確認**: deploy 後 30s 待機 → admin UI `curl -u admin:... http://cube-j1.home.arpa:8080/api/log?lines=20` で 起動 log + 新メトリクス名が登場
7. **SC-004 確認**: Grafana で過去 7 日の `sk_event_32_total` の rate を query。 0 でない期間があれば SKSENDTO timeout / 失敗パターンとの相関を audit findings に追記 (= 「過去 ARIB 上限到達観測あり」 の sample 1 件確保)
8. **SC-005 確認**: audit findings status を `resolved by commit <hash>` 化

## 留意事項

- メトリクス名 (`sk_event_XX_total`) は変更しない → Prometheus 履歴の連続性確保 ([[reference-prometheus-remote-write-backfill]])
- ラベル文字列 (= HELP text) のみ変更 → Prometheus 自動反映、 compose/telegraf pipeline 変更不要 ([[feedback-compose-telegraf-pipeline]] 影響なし)
- HA discovery の name 表示が DIAG_SENSOR_DEFS ラベルを使うため、 HA UI 上のセンサー表示名が変わる (= user 視認可、 意図通り、 「ARIB 送信制限」 系の表示は意味の正常化)
- spec 036 は user memory [[user-instantaneous-power-priority]] と整合: polling 周期は変更しない、 tier 構造変更も無し、 メトリクス意味の訂正のみで瞬時電力品質に影響なし
