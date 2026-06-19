"""apply_defaults fills in optional keys without disturbing the legacy 9.

FR-011/012 + SC-005: an upstream-compatible config.json (9 keys only) must
still produce a working bridge with sensible defaults.
"""
import mqtt_bridge as mb


UPSTREAM_KEYS = {
    "br_id", "br_pwd",
    "mqtt_host", "mqtt_port", "mqtt_user", "mqtt_pass",
    "device_id", "serial_port", "poll_interval",
}


def _upstream_config():
    return {
        "br_id":         "0" * 32,
        "br_pwd":        "0" * 12,
        "mqtt_host":     "192.168.1.254",
        "mqtt_port":     1883,
        "mqtt_user":     "user",
        "mqtt_pass":     "passwd",
        "device_id":     "cubej1",
        "serial_port":   "/dev/ttyS1",
        "poll_interval": 60,
    }


# ---------------------------------------------------------------------------
# Backwards compatibility: upstream's 9-key config must work as-is
# ---------------------------------------------------------------------------

def test_apply_defaults_returns_dict_with_new_keys_filled_in():
    cfg = mb.apply_defaults(_upstream_config())
    assert cfg["log_level"] == "info"
    assert cfg["log_max_bytes"] == 1048576
    assert cfg["log_backup_count"] == 3
    assert cfg["mqtt_keepalive"] == 300


def test_apply_defaults_respects_explicit_mqtt_keepalive():
    cfg = mb.apply_defaults({"mqtt_keepalive": 120})
    assert cfg["mqtt_keepalive"] == 120


def test_apply_defaults_enables_mqtt_threading_by_default():
    cfg = mb.apply_defaults(_upstream_config())
    assert cfg["mqtt_threading_enabled"] is True
    assert cfg["mqtt_send_queue_maxsize"] == 1000


def test_apply_defaults_respects_explicit_threading_flag():
    cfg = mb.apply_defaults({"mqtt_threading_enabled": False})
    assert cfg["mqtt_threading_enabled"] is False


def test_apply_defaults_preserves_all_upstream_keys_and_values():
    upstream = _upstream_config()
    cfg = mb.apply_defaults(dict(upstream))
    for key, value in upstream.items():
        assert cfg[key] == value


def test_apply_defaults_does_not_mutate_input_dict():
    upstream = _upstream_config()
    snapshot = dict(upstream)
    mb.apply_defaults(upstream)
    assert upstream == snapshot


# ---------------------------------------------------------------------------
# Explicit values override defaults
# ---------------------------------------------------------------------------

def test_apply_defaults_respects_explicit_log_level():
    cfg = mb.apply_defaults({"log_level": "debug"})
    assert cfg["log_level"] == "debug"


def test_apply_defaults_respects_explicit_max_bytes_and_backup_count():
    cfg = mb.apply_defaults({"log_max_bytes": 4096, "log_backup_count": 1})
    assert cfg["log_max_bytes"] == 4096
    assert cfg["log_backup_count"] == 1


# ---------------------------------------------------------------------------
# Unknown keys are ignored (Edge Case)
# ---------------------------------------------------------------------------

def test_apply_defaults_passes_unknown_keys_through_unchanged():
    cfg = mb.apply_defaults({"future_feature_flag": True, "log_level": "info"})
    assert cfg["future_feature_flag"] is True
    assert cfg["log_level"] == "info"
