# Quickstart: Embedded Admin Web UI

## 1. 有効化

USB 経由か `scripts/prepare_usb.sh` 経由で、`secrets/config.json` に admin UI 用 4 キーを追加:

```json
{
  "br_id": "...",
  "br_pwd": "...",
  "mqtt_host": "192.168.1.151",
  ...

  "admin_ui_enabled": true,
  "admin_ui_port": 8080,
  "admin_user": "admin",
  "admin_password": "<秘密のパスワード>"
}
```

その config.json を Cube J1 に配って bridge 再起動。

## 2. ブラウザでアクセス

```
http://<cube_j1_ip>:8080/
```

Basic Auth ダイアログで `admin` / `<パスワード>` を入れる。TOP ページが開く。

## 3. config を編集する

「Config」セクションで任意の値を変更 → `Save Config` をクリック → 「Updated」表示。

設定の反映には `Restart Bridge` ボタンを押す（または bridge プロセスを次回起動するまで待つ）。

## 4. bridge コードを更新する

「Bridge Update」セクションで新しい `mqtt_bridge.py` を選ぶ → `Upload & Restart`。

syntax check が走り、エラーなら現行のファイルは変わらず、ブラウザに「SyntaxError: line N」のように表示される。

成功時は約 1 秒後に bridge が新コードで再起動する。診断パネルで `Uptime` が 0 にリセットされたことを見て確認する。

## 5. ローカル開発時のテストサーバ

ホスト Python 3 でも一応起動して挙動を確認できる（ただし `subprocess` 呼び出し系は no-op or stub に差し替え）:

```bash
cd ~/git/cube-j1-mqtt
# pytest で integration テストを起動して全 API を叩く
.venv/bin/pytest tests/integration/test_admin_server.py -v
```

実機での挙動確認は `scripts/prepare_usb.sh` → USB → Cube J1 か `scripts/adb_push_update.sh` で。

## 6. リスクと注意

- **Wi-Fi 設定変更は退避手段なし**: 入力ミスで Cube J1 が AP から見えなくなったら、USB 経由で `wpa_supplicant.conf` を直すしかない。UI 側でリスク警告を出す
- **admin_password を弱くしない**: LAN 内とはいえ他の家族端末 / IoT 機器からも 8080 にアクセスできる。8 文字以上を推奨
- **port 8080 が他サービスと被ったら起動失敗**: stderr / mqtt_bridge.log に `admin_ui_start_failed` イベントが出る。`admin_ui_port` を別の番号に変える

## 7. 無効化

`admin_ui_enabled: false` に戻して USB or ADB 経由で配り、bridge を再起動。HTTP サーバは立ち上がらない。
