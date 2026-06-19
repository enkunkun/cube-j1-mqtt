"""Basic Auth header parsing for the embedded admin UI.

AdminConfig.match_basic_auth(header_value) compares an `Authorization: Basic
<b64>` header against the configured user/password using hmac.compare_digest
so the check is constant-time.
"""
import base64

import mqtt_bridge as mb


def _auth_header(user, pwd):
    raw = "{}:{}".format(user, pwd).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _cfg(user="admin", pwd="secret", enabled=True, port=8080):
    return mb.AdminConfig(enabled=enabled, port=port, user=user, password=pwd)


def test_match_basic_auth_accepts_correct_credentials():
    cfg = _cfg()
    assert cfg.match_basic_auth(_auth_header("admin", "secret")) is True


def test_match_basic_auth_rejects_wrong_password():
    cfg = _cfg()
    assert cfg.match_basic_auth(_auth_header("admin", "wrong")) is False


def test_match_basic_auth_rejects_wrong_user():
    cfg = _cfg()
    assert cfg.match_basic_auth(_auth_header("intruder", "secret")) is False


def test_match_basic_auth_rejects_missing_header():
    cfg = _cfg()
    assert cfg.match_basic_auth(None) is False
    assert cfg.match_basic_auth("") is False


def test_match_basic_auth_rejects_non_basic_scheme():
    cfg = _cfg()
    assert cfg.match_basic_auth("Bearer xxx") is False
    assert cfg.match_basic_auth("Digest user=admin") is False


def test_match_basic_auth_rejects_malformed_base64():
    cfg = _cfg()
    assert cfg.match_basic_auth("Basic !!!notb64!!!") is False


def test_is_active_true_when_enabled_and_creds_present():
    assert _cfg(enabled=True, user="admin", pwd="secret").is_active() is True


def test_is_active_false_when_disabled():
    assert _cfg(enabled=False, user="admin", pwd="secret").is_active() is False


def test_is_active_false_when_user_empty():
    assert _cfg(enabled=True, user="", pwd="secret").is_active() is False


def test_is_active_false_when_password_empty():
    assert _cfg(enabled=True, user="admin", pwd="").is_active() is False
