# Feature Specification: ERXUDP TID Validation

**Feature Branch**: `014-erxudp-tid-validation`
**Created**: 2026-06-20
**Status**: Draft
**Input**: User description: "nanamitm fork (77fc027) で導入された TID 検証を取り込み、 再接続後の遅延応答が次サイクルの結果に混入する穴を塞ぐ"

## Background

spec 011 で ERXUDP に対する resilience (timeout 30s + intra-cycle retry + 連続 timeout で Wi-SUN 再 join) を整備した結果、 1 サイクル内で同じ TID 系のリクエストが複数回飛ぶケースと、 Wi-SUN 再 join 直後にメーターが過去のリクエストに対する応答を遅延送信してくるケースが構造的に発生する。

現状の `read_erxudp(fd, timeout=15, diag_state=None)` (`production_tool/mqtt_bridge.py:2392`) は ERXUDP の payload 先頭が `1081` (ECHONET Lite EHD) であることだけ確認し、 TID をチェックしない。 送信側 `build_el_get(tid, epcs)` (`production_tool/mqtt_bridge.py:2233`) は TID を `tid & 0xFFFF` で埋めているが、 受信側でその TID と照合していない。

このため次の事象が起こりうる:

1. cycle N で send TID=A、 timeout
2. intra-cycle retry で send TID=B、 メーターから A の遅延応答が先に届く
3. B の応答だと誤認し、 古い (= cycle N より前の状態を反映した) data で publish
4. あるいは Wi-SUN 再 join 直後、 PANA セッション再構築の合間に滞留していたフレームが新しい cycle の先頭で吸われる

ECHONET Lite の TID は 2 バイトで cycle 毎にインクリメントすればユニーク。 受信時に送信時の TID と照合し、 不一致は破棄して次のフレームを読み続けるだけで上記の混入は塞げる。

nanamitm/cube-j1-mqtt の `77fc027 Retry property map detection and validate response TIDs` で同様の対策が実装されており、 production 環境で「メーター再送による次サイクルへのリーク」が確認されている。

## Scope

- `read_erxudp(fd, timeout=..., diag_state=None, expected_tid=None)` のシグネチャに `expected_tid` を追加
  - `expected_tid is None` のときは現状通り (後方互換)
  - 値があるときは ECHONET Lite ペイロードの TID 部 (offset 2-4 byte) と比較し、 不一致なら frame を破棄して待機継続
- 呼び出し側 (main loop の `read_erxudp` 呼び出し 2 箇所、 `production_tool/mqtt_bridge.py:3093, 3105`) は送信に使った `tid` を渡す
- pure helper `extract_el_tid(payload: bytes) -> int` を抽出 (offset 2-4 byte を big-endian で int に)
- 検証で破棄された frame の総数を DiagState の `erxudp_tid_mismatch_total` として publish
- TID 不一致は WARN ログに残す (送信 TID / 受信 TID / cycle 番号)

## Non-Scope

- 全 ECHONET Lite フレームの完全検証 (EHD / SEOJ / DEOJ の照合) — TID だけで十分に cycle 混入は防げる
- TID の生成戦略変更 — 現状の monotonic counter で衝突確率は十分低い
- BP35CX のシリアル層での frame buffering 変更

## User Scenarios *(mandatory)*

### Primary User Story

ユーザは spec 011 の intra-cycle retry が走った直後の cycle で、 Grafana の `cube_j1_smart_meter_power_watts` 値が **直前の cycle と同じ値で擬似的に安定して見える** 現象 (実体は前 cycle の遅延応答を読んでいる) を見なくなる。 値が変化していないように見えていた cycle が、 メーターの実値どおりに動くようになる。

### Acceptance Scenarios

1. **Given** cycle N で send TID=0x0010、 timeout、 **When** intra-cycle retry で send TID=0x0011、 そこにメーターから TID=0x0010 の遅延応答が先着、 **Then** TID 不一致で破棄、 erxudp_tid_mismatch_total +1、 TID=0x0011 を待ち続けて成功
2. **Given** Wi-SUN 再 join 直後、 send TID=0x0050、 滞留フレーム (TID=0x004F) 到着、 **Then** 破棄、 0x0050 の応答を取得
3. **Given** expected_tid を渡さない (既存 caller 互換)、 **When** ERXUDP 受信、 **Then** 現状通り先着 1081 frame を返す
4. **Given** 30s timeout 中ずっと TID 不一致の frame ばかり来る、 **When** deadline 到達、 **Then** None を返す (現状の timeout 動作維持)

### Key Entities

- **`extract_el_tid(payload: bytes) -> int`**: pure 関数、 ECHONET Lite payload 先頭 4 byte 目以降の TID を返す
- **`read_erxudp(fd, timeout, diag_state, expected_tid)`**: TID 一致した frame のみ返却
- **`erxudp_tid_mismatch_total`**: 計上カウンタ (TID 不一致で破棄した frame 数)

## Edge Cases

- `expected_tid=0` を渡された場合: 値 0 は valid な TID として扱う (Python の `is None` 判定で区別)
- payload 長 4 byte 未満: 破棄、 ログ WARN、 frame 待機継続
- 同じ TID の応答が 2 回届く (メーター側の重複送信): 1 つ目を採用、 2 つ目は次の `read_erxudp` 呼び出しが何も期待していないため自然に廃棄される
- spec 010 EEDSCAN 中の `read_erxudp` 呼び出しは EPC リクエストではないため `expected_tid=None` のまま (互換)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `read_erxudp` は `expected_tid` 引数を受け取り、 None でないときは ECHONET Lite TID 不一致 frame を破棄する
- **FR-002**: 破棄時は `diag_state.on_erxudp_tid_mismatch()` を呼び出す (該当ハンドラを DiagState に追加)
- **FR-003**: main loop の通常 EPC poll と intra-cycle retry の両方で送信 TID を `read_erxudp` に渡す
- **FR-004**: `erxudp_tid_mismatch_total` を `/api/metrics` に publish する
- **FR-005**: pure helper `extract_el_tid` は Python 2.7 stdlib のみで動く (struct.unpack 等)
- **FR-006**: 既存呼び出し (`expected_tid` を渡さない) は挙動が変わらない

### Key Entities

- **DiagState.erxudp_tid_mismatch_total**: int カウンタ
- **DiagState.on_erxudp_tid_mismatch()**: インクリメンタメソッド

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 1 週間運用で `erxudp_tid_mismatch_total > 0` が観測される (= 実際に混入が起きていたことの裏付け)。 0 のままなら spec 011 のリトライ実装で混入は起きていなかったことが分かる
- **SC-002**: Grafana の `cube_j1_smart_meter_power_watts` で「直前 cycle と完全一致の値が連続する」現象が発生しなくなる (= 古い遅延応答を読み続けるパターンの消滅)
- **SC-003**: 既存 unit / integration テストが全件 pass する (後方互換性)

## Assumptions

- ECHONET Lite の TID は send 毎に異なる値が割り当てられている (現状の実装で担保済み)
- メーターが TID を改竄せず応答に echo する (ECHONET Lite 仕様準拠の前提)
- BP35CX が ERXUDP frame の payload を改竄しない
- 1 cycle あたりの `read_erxudp` 呼び出しは spec 011 の intra-cycle retry を含めても 3 回以内
