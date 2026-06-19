# Feature Specification: AP Mode Toggle from Admin UI

**Feature Branch**: `008-ap-toggle`
**Created**: 2026-06-20
**Status**: Draft
**Input**: User description: "AP モードを admin UI から ON/OFF できるようにする (12345678 で立ちっぱなしはセキュリティ上避けたい)"

## Background

Cube J1 はデフォルトで **P2P GO mode (Wi-Fi Direct)** の AP `CubeJ-39f2f2` を `12345678` の PSK で常時起動している (`p2p-wlan0-0` インターフェース、 `net.wifi.ap.state=created`)。

これは持ち運び位置調整時 (spec 007 `/wisun` ページ) には便利だが、 弱い PSK で常時公開しているのは家庭 LAN として望ましくない。 必要な時だけ ON にして用が済んだら OFF にしたい。

実機調査:
- AP 制御は NextDrive 独自の `/usr/sbin/wifimgr` が `wpa_supplicant` 経由で行っている
- バイナリ strings に `P2P_GROUP_REMOVE %s` と `P2P_GROUP_ADD persistent=%d freq=2` を確認
- wpa_supplicant の ctrl_interface は `/data/misc/wifi/sockets/`
- 通常の自宅 Wi-Fi (STA) は `wlan0` で別経路、 AP を落としても影響しない

## Scope

- admin UI に **ON/OFF トグル** を追加 (位置: `/wisun` ページ末尾 + メイン admin top)
- `GET /api/ap_state` で現在状態を取得 (`{"enabled": true|false, "interface": "p2p-wlan0-0"}`)
- `PUT /api/ap_state` `{"enabled": false}` で `wpa_cli` 経由 `P2P_GROUP_REMOVE p2p-wlan0-0` を発行
- `PUT /api/ap_state` `{"enabled": true}` で `wpa_cli` 経由 `P2P_GROUP_ADD persistent=1 freq=2` を発行 (再生成)
- 操作後 1 秒待って `net.wifi.ap.state` の値を読み返し、 結果を返す
- failsafe: AP OFF 中も **自宅 Wi-Fi 経由 (192.168.1.103)** で admin UI に届くので、 OFF から ON に戻せなくなる事故は無い

## Non-Scope

- AP の SSID / PSK 変更 (将来 spec 009 候補)
- 時限 ON (期限付き自動 OFF)
- AP モード状態の MQTT publish (HA 側で不要との判断、 必要なら後付け)
- wifimgr の自動再起動回避 (もし wifimgr が AP を auto re-up したら、 設計を見直す)

## User Scenarios *(mandatory)*

### Primary User Story

ユーザとして家庭 LAN 経由で http://192.168.1.103:8080/wisun を開き、 「AP: ON / OFF」 トグルを操作する。 OFF にすれば `CubeJ-39f2f2` が消え、 ON にすれば再出現する。 位置調整時のみ ON、 普段は OFF で運用する。

### Acceptance Scenarios

1. **Given** AP が立ってる状態、 **When** `GET /api/ap_state`、 **Then** `{"enabled": true, "interface": "p2p-wlan0-0"}` が返る
2. **Given** AP ON 状態、 **When** `PUT /api/ap_state {"enabled": false}`、 **Then** 200 OK で `{"enabled": false}` が返り、 `net.wifi.ap.state` が `disabled`/`removed` に変わる
3. **Given** AP OFF 状態、 **When** `PUT /api/ap_state {"enabled": true}`、 **Then** 200 OK で `{"enabled": true}` が返り、 SSID `CubeJ-39f2f2` が再出現
4. **Given** OFF → ON サイクル後、 **When** スマホで `CubeJ-39f2f2` に接続、 **Then** 同じ PSK (`12345678`) で接続できる (persistent=1 が効いてる)
5. **Given** unauthenticated、 **When** `PUT /api/ap_state`、 **Then** 401

### Key Entities

- **AP state**: Android system property `net.wifi.ap.state` (値: `created` / `disabled` / `unknown`)
- **wpa_cli command**: `wpa_cli -p /data/misc/wifi/sockets -i p2p-wlan0-0 <cmd>`
- **`ApController` (新規)**: `pure get()` (state 取得)、 `enable()` (group add)、 `disable()` (group remove) のラッパー。 サブプロセスを `subprocess.Popen` で起動

## Edge Cases

- **wpa_cli が見つからない / 実行失敗**: `/api/ap_state` で 500 + error JSON、 admin UI はエラー表示
- **wifimgr が auto re-up する**: 実装後の実地テストで発見されたら、 `setprop` 経由のサプレスや wifimgr 一時停止を追加検討
- **interface 名が違う環境**: `net.wifi.ap.interface` propery を実行時取得して動的に決定
- **PUT body が malformed**: 400 + error JSON
- **OFF 中の再 PUT OFF**: idempotent、 200 で `{"enabled": false}` を返す
- **同時多重 PUT**: simple lock は無し (admin UI は単一ユーザ運用前提)

## Success Criteria *(mandatory)*

- **SC-001 [observable change]**: OFF した瞬間に外部 (Mac/スマホ) の Wi-Fi スキャン結果から `CubeJ-39f2f2` が消える
- **SC-002 [reversible]**: OFF → ON で同じ SSID/PSK が復活し、 再接続可能
- **SC-003 [no STA impact]**: AP 操作中・後で自宅 Wi-Fi 経由の admin UI は無中断
- **SC-004 [no measurement impact]**: AP 操作中も ECHONET poll / MQTT publish が継続 (Constitution IV)
- **SC-005 [graceful failure]**: wpa_cli 失敗時に admin UI は 500 でユーザにエラーを表示、 bridge プロセスは死なない

## Assumptions

- `wpa_cli` が `/data/misc/wifi/sockets/p2p-wlan0-0` に接続できる (wifimgr と同じ socket)
- `persistent=1` で grup add すれば同 PSK で復元される (Wi-Fi Direct の永続化)
- wifimgr は AP の auto re-up を行わない (= manual disable が効く)。 この前提は実地テストで確認

## Dependencies

- `production_tool/mqtt_bridge.py` の `AdminHandler` (GET/PUT dispatch)
- `production_tool/mqtt_bridge.py` の `WISUN_HTML` (toggle UI 追加)
- 実機の `/system/bin/wpa_cli` + `/system/bin/getprop`
