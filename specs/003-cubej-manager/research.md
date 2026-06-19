# Phase 0 Research: Embedded Admin Web UI

実装前の技術メモ。spec/plan で要件は確定済みなので、ここは「stdlib API の挙動」「Cube J1 上のシステムコマンドとの作法」「埋め込み HTML の運用」を整理する。

## R-1. `BaseHTTPServer.HTTPServer` (Python 2.7)

**結論**: 採用可能。`HTTPServer((host, port), handler_class)` で listening socket を作り、`serve_forever()` で blocking ループ。

**確認事項**:
- 別 thread から `httpd.shutdown()` を呼べばループ抜け
- `SO_REUSEADDR` は `HTTPServer` のサブクラスで `allow_reuse_address = True` クラス変数で有効化
- handler は要求ごとに `BaseHTTPRequestHandler` インスタンスを 1 つ作る。Server スレッドプール拡張は別途必要だが本機能ではシングルスレッドで十分

**実装方針**:
```python
class ReusingHTTPServer(BaseHTTPServer.HTTPServer):
    allow_reuse_address = True

httpd = ReusingHTTPServer(("", 8080), AdminHandler)
t = threading.Thread(target=httpd.serve_forever, name="cubej-admin-http")
t.daemon = True
t.start()
```

shutdown は bridge 終了時に呼ぶ。bridge は SIGKILL されることが多い前提なので shutdown handler の有無は性能に響かない。

## R-2. `cgi.FieldStorage` の multipart 解析 (Python 2.7)

**結論**: `cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD":"POST", "CONTENT_TYPE": ctype})` で multipart を解析できる。アップロードされたファイルは `form["update_file"].file` で読める。

**注意点**:
- `Content-Length` を先に確認し、100KB 超ならその時点で 413 を返して `FieldStorage` 構築をスキップ（DoS 対策）
- `form["update_file"].filename` で拡張子チェック (`.py` のみ受け付け)
- 解析後の temp ファイルは `cgi.FieldStorage` が `tempfile` を内部使用するので明示的 cleanup 不要だが、念のため atexit 登録

```python
length = int(self.headers.get("Content-Length", 0))
if length > 100 * 1024:
    self._send_json(413, {"error": "File too large"})
    return
form = cgi.FieldStorage(
    fp=self.rfile,
    headers=self.headers,
    environ={"REQUEST_METHOD": "POST",
             "CONTENT_TYPE": self.headers.get("Content-Type")},
)
upload = form["update_file"]
if not upload.filename.endswith(".py"):
    self._send_json(400, {"error": "Only .py files are accepted"})
    return
data = upload.file.read()
```

## R-3. `py_compile.compile()` で syntax check

**結論**: `py_compile.compile(file, doraise=True)` で syntax check。`PyCompileError` を catch して エラーメッセージを返す。

**注意点**:
- ファイル全体をディスクに書く前に syntax check したいので、temp 経由で:

```python
import py_compile, tempfile
tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, dir="/data/local")
try:
    tmp.write(data)
    tmp.close()
    py_compile.compile(tmp.name, doraise=True)
    os.rename(tmp.name, "/data/local/mqtt_bridge.py")
finally:
    if os.path.exists(tmp.name):
        os.unlink(tmp.name)
```

`PyCompileError.exc_value` でエラー詳細（行番号付き）を取得して返す。

**重要**: 実行はしない。`compile()` のみ。アップロードされた `.py` を `exec` してはならない（任意コード実行になるため）。本機能の意義は「アップロード → ファイル置換 → init による restart で **OS プロセス境界経由で実行**」。

## R-4. `subprocess` で `wpa_cli` / `stop/start mqtt_ha_bridge`

**結論**: Android shell で:
- `wpa_cli -p /data/misc/wifi/sockets -i wlan0 reconfigure` で再読込
- `stop mqtt_ha_bridge` / `start mqtt_ha_bridge` で init サービス再起動

これらはすでに `production_tool` スクリプトが呼んでおり、Cube J1 上で動作確認済み。Python の `subprocess.Popen` で呼ぶ。

```python
import subprocess
def restart_bridge():
    subprocess.Popen(["stop", "mqtt_ha_bridge"], stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE).communicate(timeout=5)
    time.sleep(1)
    subprocess.Popen(["start", "mqtt_ha_bridge"], stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE).communicate(timeout=5)
```

ただし bridge プロセス自身が自分を `stop` するとその場で死ぬので、`subprocess.Popen(...)` 起動した後に HTTP レスポンスを返す前にプロセスが終わる。レスポンスを先に書いてから restart する順序が重要:

```python
self._send_json(200, {"status": "restarting"})
self.wfile.flush()
threading.Timer(0.2, restart_bridge).start()  # 200ms 遅延して restart
```

これで client にレスポンスを返した後に restart が走る。

## R-5. `tempfile.NamedTemporaryFile` + `os.rename` atomic 性

**結論**: 同一 FS 内であれば `os.rename` は POSIX atomic。`/data/local/` 内に temp を作って `/data/local/<target>` に rename すれば atomic。

```python
class AtomicWriter:
    @staticmethod
    def write(path, data):
        dir_ = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=dir_)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
```

- `os.fsync` で kernel buffer も flush
- 失敗時は temp 削除して例外を再 raise

wpa_supplicant.conf は `/data/misc/wifi/` にあり、`/data/local/` とは別ディレクトリ。`/data/misc/wifi/` 内で temp 作成して同じ FS の前提でrename する（Android の `/data` 全体は通常同一 partition）。

## R-6. 埋め込み HTML

**結論**: Python 文字列定数 1 つで static HTML を持つ。テンプレート言語不使用。CSS は `<style>` インライン、JS は `<script>` インライン。外部ネットワーク依存ゼロ。

**XSS 対策**: HTML 出力時に値を埋め込む箇所は最小限。設定値は API 経由で JSON 取得し、クライアント側 JS で textContent で挿入（innerHTML 不使用）。サーバ側で HTML エスケープすべき箇所は `cgi.escape()` (Python 2.7) を使う。

**サイズ**: 1 ファイルで 5〜10KB を目安にする。複雑な UI が必要なら spec を分割するか、別仕様で SPA 化。

```python
ADMIN_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Cube J1 Admin</title>
<style>
body { font-family: -apple-system, sans-serif; margin: 2rem; max-width: 720px; }
fieldset { border: 1px solid #ccc; padding: 1rem; margin-bottom: 1rem; }
label { display: block; margin: 0.5rem 0; }
input, select { width: 100%; padding: 0.4rem; }
button { padding: 0.6rem 1.2rem; }
.ok { color: green; } .err { color: red; }
</style>
</head>
<body>
<h1>Cube J1 Admin</h1>
<div id="status"></div>
<fieldset><legend>Config</legend>
  <form id="config-form">
    <!-- fields injected by JS -->
    <button type="submit">Save Config</button>
  </form>
</fieldset>
<fieldset><legend>Wi-Fi</legend>
  <form id="wifi-form">
    <label>SSID <input name="ssid" required></label>
    <label>PSK  <input name="psk" type="password" required></label>
    <button type="submit">Save Wi-Fi (危険)</button>
  </form>
</fieldset>
<fieldset><legend>Bridge Update</legend>
  <form id="update-form" enctype="multipart/form-data">
    <input type="file" name="update_file" accept=".py" required>
    <button type="submit">Upload &amp; Restart</button>
  </form>
</fieldset>
<fieldset><legend>Diagnostics</legend>
  <button id="btn-diag">Refresh Diag</button>
  <button id="btn-restart">Restart Bridge</button>
  <pre id="diag-output"></pre>
</fieldset>
<script>/* minimal vanilla JS — fetch + DOM */</script>
</body>
</html>"""
```

## R-7. main loop への組み込み箇所

`main()` の冒頭、`LOGGER` 構築直後に AdminServer の起動を試みる。`config.json` の `admin_ui_enabled` を読んで判定:

```python
def main():
    cfg = apply_defaults(load_config())
    # ... LOGGER 構築 ...
    if cfg.get("admin_ui_enabled") and cfg.get("admin_user") and cfg.get("admin_password"):
        try:
            admin_server = start_admin_server(
                port=int(cfg.get("admin_ui_port", 8080)),
                user=cfg["admin_user"],
                password=cfg["admin_password"],
                diag_state_ref=lambda: diag_state,  # late binding
            )
            LOGGER.info(event="admin_ui_started", context={"port": ...})
        except Exception as e:
            LOGGER.error(event="admin_ui_start_failed", context={"error": str(e)})
    # ... 以下 mqtt.connect, wisun_connect, main loop ...
```

`start_admin_server` は `HTTPServer` 構築 + thread 起動を返り値で返す。`diag_state` はまだ構築されていないので closure で参照する設計。

main loop 終了時の cleanup は気にしない（Cube J1 は signal で kill される運用）。
