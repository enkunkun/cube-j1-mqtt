# Feature Specification: poll_interval Lower Bound (ARIB STD-T108)

**Feature Branch**: `013-poll-interval-floor`
**Created**: 2026-06-20
**Status**: Draft
**Input**: User description: "nanamitm fork (c2c7c0a) の poll_interval 30 秒下限を取り込み、 Wi-SUN duty cycle 違反を未然に防ぐ"

## Background

`config.json` の `poll_interval` は現状 `validate_config_patch` (`production_tool/mqtt_bridge.py:388-409`) で「正の整数」しか検査されていない。 `_POSITIVE_INT_KEYS` に含まれているため `poll_interval=1` のような値も通る。

ARIB STD-T108 (920MHz 帯特定小電力無線局の標準規格) は 1 時間あたりの送信時間を 360 秒に制限している (3600s 中 10% duty cycle)。 spec 011 の通常 polling で 1 サイクル送信は約 500ms。 retry / 再 join / EEDSCAN を含めると 1 サイクルで 1-2 秒消費する想定。 `poll_interval=10` で動かすと 1 時間 360 サイクル × 1.5s = 540 秒となり制限超過。

実機運用で「もっと頻繁に値が見たい」と config を書き換えてしまうと、 規格違反のうえメーター側の duty cycle 制限で結果的に PANA セッション切断や ECHONET 無応答に陥り、 spec 011 の force-reconnect ラダーが連鎖発火する負のスパイラルになる。

nanamitm/cube-j1-mqtt の `c2c7c0a Enforce 30s minimum on poll_interval to stay within Wi-SUN duty cycle` で `poll_interval < 30` を config 検証ではじく対策が入っている。 当方も同じ floor を採用する。

30 秒が妥当な根拠: spec 011 C の tier rotation で 1 サイクル最大 2 EPC、 送信 + 受信 + ackで実測 200-700ms。 30 秒間隔なら 1 時間で 120 サイクル × 1s = 120 秒、 retry / EEDSCAN 込みでも 200 秒程度に収まり、 360 秒上限に対し 1.8 倍の余裕がある。

## Scope

- `validate_config_patch` で `poll_interval` を `_POSITIVE_INT_KEYS` の汎用検査から外し、 専用ブランチで `value >= MIN_POLL_INTERVAL_SEC` を要求
- `MIN_POLL_INTERVAL_SEC = 30` をモジュール定数として定義 (将来の調整余地、 単体テストから参照)
- 違反時のエラー文は ARIB STD-T108 の根拠を含める (オペレータが「なぜ 30 秒」を分かるように)
- 起動時 (`apply_defaults`) も同じ floor を当てる。 既存 config.json が 1 秒で書かれていた場合は WARN ログ + 30 秒に補正して継続 (起動を止めない)。 注: `load_config` 自体は `json.load` の薄いラッパなので floor 適用は `apply_defaults` 側で行う

admin UI HTML 側の `min="30"` 属性付与はスコープ外: フィールドが JS で動的生成されており個別属性付与の適用面が小さい。 サーバ側検証で十分担保できる。

## Non-Scope

- ARIB STD-T108 の duty cycle 計測機能 (実送信時間の累計トラッキング) — 過剰
- poll_interval の動的調整 (LQI 悪化時に間隔延長等) — 別 spec 候補
- `erxudp_timeout_sec` や `erxudp_intra_cycle_retries` の上下限 — 現状でも合理的な値を default にしていて事故事例なし

## User Scenarios *(mandatory)*

### Primary User Story

オペレータが「もっと細かい粒度で電力を見たい」と admin UI から `poll_interval=10` を保存しようとすると、 「poll_interval must be >= 30 seconds (ARIB STD-T108 duty cycle)」のエラーで弾かれる。 30 以上で保存すれば従来通り通る。

### Acceptance Scenarios

1. **Given** admin UI に `poll_interval=10` を入力、 **When** POST /api/config、 **Then** 400 エラー + 「>= 30」メッセージ、 config 不変
2. **Given** `poll_interval=30` を入力、 **When** POST /api/config、 **Then** 保存成功、 200 返却
3. **Given** 既存 config.json が `"poll_interval": 5` で書かれている、 **When** bridge 起動、 **Then** WARN ログ「poll_interval=5 below floor, using 30」、 30 で運用継続
4. **Given** `poll_interval=60` (現 default)、 **When** 起動、 **Then** ログなし、 60 でそのまま運用
5. **Given** `poll_interval` を patch に含めない (他キーだけ更新)、 **When** POST /api/config、 **Then** poll_interval 検査はスキップ、 他キーは保存

### Key Entities

- **`MIN_POLL_INTERVAL_SEC = 30`**: モジュール定数
- **`validate_config_patch`**: poll_interval 専用検査を追加
- **`load_config`**: 起動時の floor clamp を追加

## Edge Cases

- `poll_interval=30.0` (float): 既存検証で `isinstance(value, int)` を要求しているので reject、 これは現状維持
- `poll_interval` を JSON で `"30"` 文字列で渡される: 既存検証で reject、 現状維持
- 起動時 config.json が壊れている: 既存挙動 (default 60 にフォールバック) を維持、 floor 検査は default 値には適用しない
- 30 ちょうど: 通す (>= で比較)
- 負値 / 0: 既存の「正の整数」検査がまず弾く、 floor 検査に到達しない

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `validate_config_patch` で `poll_interval` を _POSITIVE_INT_KEYS の汎用検査から外し、 (`isinstance(value, int)` かつ `not isinstance(value, bool)` かつ `value >= MIN_POLL_INTERVAL_SEC`) を要求する
- **FR-002**: 違反時のエラーメッセージは「poll_interval must be >= 30 seconds (ARIB STD-T108 920MHz duty cycle)」とする
- **FR-003**: `apply_defaults` で読み込んだ `poll_interval` が floor 未満なら WARN ログを出して 30 に補正する
- **FR-004**: `MIN_POLL_INTERVAL_SEC` はモジュール定数として export し、 unit test から参照可能にする

### Key Entities

- **`MIN_POLL_INTERVAL_SEC`**: int 定数、 値 30

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `poll_interval=1` を POST すると 400 で reject される (unit test)
- **SC-002**: `poll_interval=30` を POST すると 200 で accept される (unit test)
- **SC-003**: `load_config` に `{"poll_interval": 5}` を渡すと WARN ログが 1 件出て返却値は 30 になる (unit test)
- **SC-004**: 既存の bridge unit / integration テストが全件 pass する (後方互換)

## Assumptions

- ARIB STD-T108 の 360s/hour 制約は 920MHz 帯 Wi-SUN 通信全般に適用される (BP35CX の動作前提)
- メーター側も同じ規格に従うため、 こちらが守れば bidirectional に duty cycle 内で収まる
- 1 サイクルあたりの送信時間が 2 秒を大幅に超える事象は spec 011 + 012 の resilience でほぼ発生しない
- オペレータが意図的に floor 未満を設定する強い動機は無い (短くしてもメーターは秒オーダーでしか更新しない)
