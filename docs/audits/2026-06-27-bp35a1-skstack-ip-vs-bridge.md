# BP35A1 SKSTACK-IP 公式仕様 vs bridge 実装 audit findings

- **調査日**: 2026-06-27
- **bridge**: `production_tool/mqtt_bridge.py` (= 4606 行、 SK ASCII コマンド使用)
- **公式仕様**: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf` (= Ver 1.3.2、 2020.5 改訂、 2024 配布、 66 ページ)
- **実機 firmware**: EVER 1.5.2 (= SKSTACK-IP for BP35A1 / BP35C0 共通)
- **status**: P-NEW-2 / P-NEW-7 resolved by spec 036、 P-NEW-1 resolved by spec 037、 **P-NEW-3 🚫 Reopened (= 2026-06-30、 spec 038 Phase 1 旧結論「EVENT 21 = 0 件」 は誤り、 bridge /api/diag で sk_event_21_total = 67 件 / 24h 計上判明、 root cause = compose/telegraf topics 未 update、 compose commit 0ba5dba1 で fix、 spec 038 Reopened で再観察 + Phase 2 PARAM 区別検討待ち)**、 P-NEW-4 Closed (= spec 039 revert)、 P-NEW-5 Phase 1 SC-002 達成 (= spec 040 24h で wisun_joined 133 件、 720s 仮説 positive、 SC-003 は spec 044 fix 後の再観察待ち)、 spec 041 (= silent death watchdog) Phase 2a Deployed、 spec 042 (= P-NEW-8 SKADDNBR) Deployed + 動作実証 (= bridge /api/diag で skaddnbr_total = 3 確認)、 spec 044 (= bug fix) Deployed + 動作実証 (= sk_event_25_total 0→3 increment)、 残 P-NEW-6/9 open

## 0. 背景

Cube J1 内蔵 Wi-SUN モジュール (品番 BP35C0、 firmware SKSTACK-IP) を制御する bridge コードと、 ROHM 公式仕様書を初めて系統的に突き合わせた audit。

- BP35C0 ハードは 2 系統の firmware (= 「J11 binary」 と 「SKSTACK-IP ASCII」) があり、 Cube J1 内蔵は **SKSTACK-IP** (= BP35A1 と互換) で動作中
- ROHM の datasheet ページから誰でも DL できる PDF はすべて J11 binary 系で、 bridge 検証には使えない
- SKSTACK-IP の公式仕様は ISB-ROHM の認証付き DL ページ (`https://micro.rohm.com/jp/download_support/wi-sun/software/data/other/bp35a1_commandmanual_tr-j.pdf`) から Basic Auth 経由でのみ取得可能 (= 認証 ID/PW は実機 SKINFO 応答の IPv6 link-local prefix `FE80` / MAC OUI 由来 `021D`、 全 BP35x モジュール共通)

## 1. 取得した公式文書

`docs/vendor/bp35a1-skstack-ip/`:

| ファイル | 出自 | サイズ |
|---|---|---|
| `bp35a1_commandmanual_tr-j.pdf` (Ver 1.3.2、 2020.5) | `micro.rohm.com` (= 認証付き、 真の最新版) | 2.0 M |
| `bp35a1_command_reference_se_2014-12-25.pdf` (Ver 1.3.0) | 第三者 host (= 古い SE 版) | 553 K |
| `bp35a1_startupmanual_ug-j.pdf` | `fscdn.rohm.com` (= 公開) | 2.2 M |
| `bp35a1_samplescript_an-j.pdf` | `fscdn.rohm.com` (= 公開) | 1.5 M |
| `BP35A1_Wi-SUN_b-route_script.zip` | `micro.rohm.com` (= 認証付き) | 2.4 K |

## 2. firmware 確定の根拠

- bridge log の `SKVER` 応答 = `EVER 1.5.2` (= ASCII 形式 = SKSTACK-IP)
- 公式 BP35A1 マニュアル冒頭: 「BP35A1 は SKSTACK-IP 用 SK コマンドが使用されます。 コマンドは ASCII 文字で指定し、 コマンド引数の区切りにはスペースを使います」
- J11 binary firmware (= `0x6019` 起動通知、 ユニークコード + チェックサムフレーム) とは別系統 = ROHM が同じ datasheet ページに 2 系統の文書を並列配置していたため最初の audit で 4 PDF が無関係と判明していた

## 3. 真の問題点 (9 件)

### 🔴 重大度高 = 即修正候補

#### P-NEW-1: WOPT を毎接続で発行 = FLASH 書込み 10,000 回制限違反のリスク

- 公式 p.41: 「WOPT は実行する度に内部 FLASH メモリに書込み保存」 「FLASH 書込み回数 10,000 回以下」 「**設定時に一度だけ実行するように**」
- bridge `mqtt_bridge.py:2857`: `_wisun_init_sequence` の中で **bridge 起動毎・reconnect 毎に** `skcommand(fd, "WOPT 1")` を発行
- 影響: reconnect 1 日 100 回 × 100 日 = 10,000 回到達でモジュール FLASH 領域劣化、 最悪保存値破損
- 対策: ROPT で現在値を読んで既に WOPT 1 なら skip。 ROPT helper 追加 + 条件分岐 ~10 行
- 不確定要素: SKSTACK-IP firmware 側に「同値書き込みなら skip」 最適化があるか不明。 仕様書記述に従えば修正必須

#### P-NEW-2: EVENT 32 / 33 のラベルが完全に逆 (= ARIB 送信制限を 「Scan Done/Started」 と誤記)

- 公式 p.51:
  - EVENT 0x32 = ARIB108 送信総和時間制限が**発動** (= 以後あらゆるデータ送信が内部キャンセル)
  - EVENT 0x33 = 送信総和時間制限が**解除**
- bridge `mqtt_bridge.py:3932-3933`:
  - `("sk_event_32_total", "SK EVENT 32 (Scan Done)", ...)`
  - `("sk_event_33_total", "SK EVENT 33 (Scan Started)", ...)`
- 公式の Scan 完了系: EVENT 0x22 (active scan 完了) / EVENT 0x1F (ED scan 完了)
- 影響: Grafana ダッシュボード上で「scan 頻発で接続不安定」 と読んでいたものが「ARIB 上限到達でデータ送信全停止」 に意味が変わる。 これまでの運用観察が根本的に間違っていた可能性
- 対策: ラベル変更 = 文字列 2 行 + 関連 spec.md 修正
- spec 011 系の関連 spec も併せて確認: 「sk_event_32_total」 を 「scan 関連メトリクス」 として参照している箇所がないか grep

### 🟡 重大度中 = 検証 + 実装

#### P-NEW-3: EVENT 0x21 (UDP 送信結果通知) を完全 ignore

- 公式 p.51: EVENT 0x21 PARAM=0/1/2 で UDP 送信結果通知
  - 0 = 成功 (= キャリアセンス OK、 ユニキャストは Ack 確認)
  - 1 = 失敗 (= キャリアセンスビジー / 送信時間制限 / Ack 未受信)
  - 2 = アドレス要請後の自動再送 (= 待つだけで送信される)
- bridge: `grep "EVENT.*21\|0x21"` で 0 件
- 影響: SKSENDTO 後の ERXUDP 待ち timeout (= 30s 待ち) の一部は実は 1-2s で EVENT 0x21 PARAM=1 が通知されている。 これを拾えば即 retry できる
- memory「BP35CX reconnect 床値 11s = erxudp timeout 主因」 と関連: ERXUDP timeout の一部は「メーター応答遅延」 ではなく「送信失敗」 だった可能性

#### P-NEW-4: SKSAVE / SKLOAD / SFF レジスタ完全未使用 = spec 036 候補 (= reconnect 床値突破) の正規ルート

- 公式 p.10, 31, 32:
  - SKSAVE: 保存可能レジスタ (= S02 channel / S03 PAN ID / S0A Pairing ID / SFF オートロード) と WOPT/WUART を FLASH 保存
  - SFF=1 で電源投入時に SKLOAD 相当が自動実行
- bridge: SKSAVE / SKLOAD / SFF / S0A 全て grep 0 件
- 影響: bridge 起動毎の 11s 床値 (= memory「BP35CX reconnect 床値 11s」) のうち WOPT / SKSREG S2 / SKSREG S3 分の ~6s は SFF=1 オートロードで省略可能
- 制約: SKSETPWD / SKSETRBID は保存可能レジスタに含まれていない (= 公式仕様 p.10 のレジスタ一覧で確認) → bridge 起動毎の発行必須。 完全な credential skip は不可能、 spec 036 の理論床値は **~5s** (= SKRESET + SKSETPWD + SKSETRBID 各 1-2s)
- 対策: 初回 deploy 時に SKSAVE + SFF=1、 reconnect 時は WOPT/SKSREG S2/S3 を skip して SKSETPWD → SKSETRBID → SKJOIN

#### P-NEW-5: PANA セッションライフタイム 900s + 80% 自動再認証 = 720s 周期 が main loop と衝突

- 公式 p.9: S16 (= PANA セッションライフタイム) デフォルト 900 秒
- 公式 p.14: 80% 経過時に PaC が SKREJOIN を**自動実行**
- → 接続後 **720 秒ごと** (= 12 分) に自動再認証
- bridge poll_interval = 60s default なので、 12 分ごとに 1-2 cycle の SKSENDTO が「送信禁止期間 (= EVENT 29 / SKJOIN 発行 → EVENT 25)」 と衝突する可能性
- 影響: 12 分ごとの ERXUDP timeout が観測される可能性。 memory「ERXUDP timeout 主因」 の一部
- 不確定: 仕様書には自動再認証成功時の EVENT 通知有無が明示されていない → 実機 log の 720s 周期挙動を grep して確認
- 対策候補 A: S16 を最大値 (0xFFFFFFFF) に設定して自動再認証を実質無効化
- 対策候補 B: SKREJOIN を bridge が能動的に発行する (= 720s tick で自分でタイミング制御)

#### P-NEW-6: SKSCAN の 4 引数目 "0" は公式仕様未定義

- 公式 p.19-20: `SKSCAN+<MODE>+<CHANNEL_MASK>+<DURATION>` の **3 引数**
- bridge `mqtt_bridge.py:2758`: `"SKSCAN 2 {} {} 0\r\n"` で **4 引数**
- bridge コメント line 2757: `# BP35C0 style scan command: <mode> <channel_mask> <duration> <side>`
- 解釈: ROHM が BP35C0 系 firmware で `<side>` パラメータを追加したが、 BP35A1 公式 Ver 1.3.2 にはまだ未反映 (= 未公開拡張)
- 実機 EVER 1.5.2 では 4 引数を受け入れている (= 動作実績あり)
- 対策: 「未公開拡張」 とコメント追記 + 4 引数が ER05/ER06 で reject された場合の 3 引数 fallback の検討

### 🟢 重大度低 = 改善余地

#### P-NEW-7: bridge コメント `192 * 2^N + 1 symbol` の数値根拠が公式と乖離

- 公式 p.20: SKSCAN duration N の 1 ch あたり時間 = `0.0096 sec * (2^N + 1)` (= 960 symbols at 100 kbps)
- bridge コメント `mqtt_bridge.py:2714`: `# scan dwell time = (192 * 2^duration + 1) symbol times.`
- 100 kbps 換算で公式 = 960 symbols * (2^N + 1)、 bridge コメント = 192 * 2^N + 1 symbols → **5 倍乖離**
- 動作影響なし (= duration 値の整数だけ stack に渡している)、 ドキュメント根拠としては誤り
- 対策: コメント 1 行修正

#### P-NEW-8: SKADDNBR でネイバー解決省略可能

- 公式 p.29: 「IP 層のネイバーキャッシュに Reachable 状態で登録。 これによってアドレス要請を省略して直接 IP パケットを出力」
- bridge は未使用 (= grep 0 件)
- 影響: 初回 SKSENDTO で Neighbor Solicitation が発生し 1-2s 追加
- 対策: spec 035 cached path で SKJOIN 後に `SKADDNBR {ipv6} {mac}` を 1 回打つ → main loop の初回 SKSENDTO 高速化

#### P-NEW-9: 送信禁止期間で in-flight SKSENDTO の invalidate hook がない

- 公式 p.17: 「EVENT 29 発生時点または SKJOIN 発行時から EVENT 25 発生時まで無線送信をしないでください」
- bridge: SKJOIN → `_wait_skjoin_event25` 中は SKSENDTO を出さない設計 → 順序的に守れている
- 残課題: EVENT 29 を検出した cycle の SKSENDTO はすでに発行済みの場合があり、 応答 (ERXUDP) は来ない / retry も意味なし
- 対策: EVENT 29 検出時に in-flight SKSENDTO を invalidate するフック追加

## 4. 推奨 spec 化順序

| spec 番号案 | 内容 | 規模 | 期待効果 |
|---|---|---|---|
| **spec 036** | P-NEW-2 (EVENT 32/33 ラベル) + P-NEW-7 (SKSCAN コメント) | 文字列 ~3 行 + spec.md | メトリクス意味の正常化 (= 過去観測の再解釈) |
| **spec 037** | P-NEW-1 (WOPT 毎回発行 → ROPT 確認で skip) | helper + 条件分岐 ~10 行 | FLASH 寿命延命 |
| **spec 038** | P-NEW-3 (EVENT 0x21 PARAM=1 捕捉 + 即 retry) | serial parser 拡張 + retry hook | ERXUDP timeout 短縮 |
| **spec 039** | P-NEW-5 検証 (= 720s 周期自動再認証の影響観察) | 実機 log grep + 解析 | 12 分周期の ERXUDP timeout の有無確認 |
| **spec 040** | P-NEW-4 (SKSAVE + SFF=1 で reconnect 床値突破) | 初回 deploy hook + reconnect path 分岐 | reconnect 11s → ~5s |
| **spec 041** | P-NEW-8 (SKADDNBR で UDP 送信高速化) | spec 035 path に 1 行追加 | 初回 SKSENDTO 1-2s 短縮 |
| **spec 042** | P-NEW-9 (EVENT 29 検出時 in-flight SKSENDTO invalidate) | flag + hook | reconnect 後の無意味 retry 排除 |
| (将来) | P-NEW-6 (SKSCAN 4 引数 fallback) | 1 行 + 例外捕捉 | firmware 更新耐性 |

## 5. 関連参考ファイル

- bridge 本体: `production_tool/mqtt_bridge.py`
  - `_wisun_init_sequence` (L2832-2858)
  - `_wait_skjoin_event25` (L2860)
  - `wisun_connect` (L2898)
  - `skscan` (L2735)
  - `skll64` (L2802)
  - `skcommand` (L2684)
  - EVENT メトリクス定義 (L3932-3933)
- 公式仕様: `docs/vendor/bp35a1-skstack-ip/bp35a1_commandmanual_tr-j.pdf`
- スタートアップ: `docs/vendor/bp35a1-skstack-ip/bp35a1_startupmanual_ug-j.pdf` (= SKINFO 取得手順 §2.5)
- 抽出 text 全文: `/private/tmp/claude-501/-Users-tendo-git-cube-j1-mqtt/8ba89e99-5eed-44f1-b090-4292e9ca9591/scratchpad/bp35a1_full.txt`
- 関連 memory: `feedback-bp35cx-reconnect-floor-11s.md` (= 11s 床値の根拠)、 `feedback-tdd-spec-template.md` (= spec 実装テンプレ)
