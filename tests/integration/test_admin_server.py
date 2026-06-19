"""Black-box integration tests for the embedded admin HTTP server.

Spins up `AdminServer` on a random local port against a temp config.json,
exercises every public endpoint via `requests`, and tears down.
"""
import base64
import json
import os
import socket
import time

import pytest
import requests

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_creds():
    return {"user": "admin", "password": "secret"}


@pytest.fixture
def config_dir(tmp_path):
    """Temp directory acting as /data/local for the admin server."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "br_id": "0" * 32,
        "br_pwd": "0123456789AB",
        "mqtt_host": "192.168.1.151",
        "mqtt_port": 1883,
        "mqtt_user": "cubej1",
        "mqtt_pass": "secret",
        "device_id": "cubej1",
        "serial_port": "/dev/ttyS1",
        "poll_interval": 60,
        "log_level": "info",
        "log_max_bytes": 1048576,
        "log_backup_count": 3,
        "admin_ui_enabled": True,
        "admin_ui_port": 8080,
        "admin_user": "admin",
        "admin_password": "secret",
    }))
    return tmp_path


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _basic(user, password):
    raw = "{}:{}".format(user, password).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


class _FakeDiag(object):
    def snapshot(self, now):
        return {
            "uptime_seconds": 42,
            "version": "1.0.0+test",
            "scan_retries_total": 0,
            "mqtt_reconnects_total": 0,
            "wisun_reconnects_total": 0,
            "erxudp_timeouts_total": 0,
        }


@pytest.fixture
def admin_server(config_dir, admin_creds):
    port = _free_port()
    server = mb.start_admin_server(
        port=port,
        user=admin_creds["user"],
        password=admin_creds["password"],
        diag_state_provider=lambda: _FakeDiag(),
        config_path=str(config_dir / "config.json"),
        bridge_path=str(config_dir / "mqtt_bridge.py"),
        wpa_supplicant_path=str(config_dir / "wpa_supplicant.conf"),
        log_path=str(config_dir / "bridge.log"),
    )
    # Wait briefly for the bind/serve_forever loop to come up.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    yield "http://127.0.0.1:{}".format(port), port
    server.stop()


# ---------------------------------------------------------------------------
# Auth (T014)
# ---------------------------------------------------------------------------

def test_unauthenticated_request_returns_401(admin_server):
    base, _ = admin_server
    r = requests.get(base + "/api/config", timeout=2)
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_wrong_password_returns_401(admin_server, admin_creds):
    base, _ = admin_server
    r = requests.get(
        base + "/api/config",
        headers={"Authorization": _basic(admin_creds["user"], "wrong")},
        timeout=2,
    )
    assert r.status_code == 401


def _auth(admin_creds):
    return {"Authorization": _basic(admin_creds["user"], admin_creds["password"])}


def test_root_get_returns_html(admin_server, admin_creds):
    base, _ = admin_server
    r = requests.get(
        base + "/",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        timeout=2,
    )
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html")
    assert "<title>Cube J1 Admin</title>" in r.text


def test_unknown_path_returns_404(admin_server, admin_creds):
    base, _ = admin_server
    r = requests.get(
        base + "/api/nope",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        timeout=2,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/config (T014, T016)
# ---------------------------------------------------------------------------

def test_get_config_returns_masked_password(admin_server, admin_creds):
    base, _ = admin_server
    r = requests.get(
        base + "/api/config",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        timeout=2,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["admin_password"] == "***"
    assert body["admin_user"] == "admin"
    assert body["mqtt_host"] == "192.168.1.151"


# ---------------------------------------------------------------------------
# PUT /api/config (T015, T016)
# ---------------------------------------------------------------------------

def test_put_config_updates_value(admin_server, admin_creds, config_dir):
    base, _ = admin_server
    r = requests.put(
        base + "/api/config",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"]),
                 "Content-Type": "application/json"},
        json={"log_level": "debug"},
        timeout=2,
    )
    assert r.status_code == 200
    on_disk = json.loads((config_dir / "config.json").read_text())
    assert on_disk["log_level"] == "debug"
    # Untouched keys preserved.
    assert on_disk["mqtt_host"] == "192.168.1.151"


def test_put_config_rejects_invalid_value(admin_server, admin_creds):
    base, _ = admin_server
    r = requests.put(
        base + "/api/config",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"]),
                 "Content-Type": "application/json"},
        json={"mqtt_port": "not a number"},
        timeout=2,
    )
    assert r.status_code == 400
    assert "mqtt_port" in r.json()["error"].lower()


def test_put_config_with_masked_password_keeps_existing(admin_server,
                                                        admin_creds, config_dir):
    base, _ = admin_server
    r = requests.put(
        base + "/api/config",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"]),
                 "Content-Type": "application/json"},
        json={"admin_password": "***"},
        timeout=2,
    )
    assert r.status_code == 200
    on_disk = json.loads((config_dir / "config.json").read_text())
    assert on_disk["admin_password"] == "secret"


def test_put_config_with_concrete_password_updates(admin_server,
                                                   admin_creds, config_dir):
    base, _ = admin_server
    r = requests.put(
        base + "/api/config",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"]),
                 "Content-Type": "application/json"},
        json={"admin_password": "newpass"},
        timeout=2,
    )
    assert r.status_code == 200
    on_disk = json.loads((config_dir / "config.json").read_text())
    assert on_disk["admin_password"] == "newpass"


# ---------------------------------------------------------------------------
# GET /api/diag (T039 — anticipated for US4 but already covered by helper)
# ---------------------------------------------------------------------------

def test_get_diag_returns_snapshot(admin_server, admin_creds):
    base, _ = admin_server
    r = requests.get(
        base + "/api/diag",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        timeout=2,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["uptime_seconds"] == 42
    assert body["version"] == "1.0.0+test"


# ---------------------------------------------------------------------------
# GET /api/log
# ---------------------------------------------------------------------------

def test_get_log_returns_tail(admin_server, admin_creds, config_dir):
    base, _ = admin_server
    log_path = config_dir / "bridge.log"
    log_path.write_text("\n".join(
        '{{"i":{}}}'.format(i) for i in range(50)) + "\n")
    r = requests.get(
        base + "/api/log?lines=3",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        timeout=2,
    )
    assert r.status_code == 200
    text_lines = [l for l in r.text.split("\n") if l]
    assert len(text_lines) == 3
    assert text_lines[-1] == '{"i":49}'


# ---------------------------------------------------------------------------
# POST /api/update (US2, T027)
# ---------------------------------------------------------------------------

VALID_PY_BODY = b'# valid python\nprint("hello")\n'
SYNTAX_ERR_PY_BODY = b'def bad(:\n  pass\n'


def _no_restart(monkeypatch):
    """Prevent the real restart_bridge_async (would try to exec `stop`)."""
    monkeypatch.setattr(mb, "_restart_bridge_async", lambda: None)


def test_post_update_accepts_valid_python_file(admin_server, admin_creds,
                                                config_dir, monkeypatch):
    _no_restart(monkeypatch)
    base, _ = admin_server
    r = requests.post(
        base + "/api/update",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        files={"update_file": ("mqtt_bridge.py", VALID_PY_BODY, "text/x-python")},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    on_disk = (config_dir / "mqtt_bridge.py").read_bytes()
    assert on_disk == VALID_PY_BODY


def test_post_update_rejects_syntax_error(admin_server, admin_creds,
                                           config_dir, monkeypatch):
    _no_restart(monkeypatch)
    base, _ = admin_server
    # Pre-create the bridge file so we can confirm it isn't replaced.
    (config_dir / "mqtt_bridge.py").write_bytes(b"original\n")
    r = requests.post(
        base + "/api/update",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        files={"update_file": ("mqtt_bridge.py", SYNTAX_ERR_PY_BODY, "text/x-python")},
        timeout=5,
    )
    assert r.status_code == 400
    assert "syntax" in r.json()["error"].lower() or "invalid" in r.json()["error"].lower()
    # Original file untouched.
    assert (config_dir / "mqtt_bridge.py").read_bytes() == b"original\n"


def test_post_update_rejects_oversize_payload(admin_server, admin_creds,
                                               monkeypatch):
    _no_restart(monkeypatch)
    base, _ = admin_server
    big_body = b"# big\n" + b"x" * (100 * 1024 + 100)
    r = requests.post(
        base + "/api/update",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        files={"update_file": ("mqtt_bridge.py", big_body, "text/x-python")},
        timeout=5,
    )
    assert r.status_code == 413


def test_post_update_rejects_non_py_extension(admin_server, admin_creds,
                                                monkeypatch):
    _no_restart(monkeypatch)
    base, _ = admin_server
    r = requests.post(
        base + "/api/update",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        files={"update_file": ("notes.txt", b"hello", "text/plain")},
        timeout=5,
    )
    assert r.status_code == 415


def test_post_restart_returns_200_without_running_bridge_commands(
    admin_server, admin_creds, monkeypatch
):
    _no_restart(monkeypatch)
    base, _ = admin_server
    r = requests.post(
        base + "/api/restart",
        headers={"Authorization": _basic(admin_creds["user"], admin_creds["password"])},
        timeout=2,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "restarting"
