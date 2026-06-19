"""validate_config_patch / validate_wifi_patch."""
import mqtt_bridge as mb


CURRENT = {
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
    "admin_password": "oldpass",
}


# ---------------------------------------------------------------------------
# validate_config_patch
# ---------------------------------------------------------------------------

def test_partial_update_merges_into_current():
    merged, err = mb.validate_config_patch({"log_level": "debug"}, CURRENT)
    assert err is None
    assert merged["log_level"] == "debug"
    assert merged["mqtt_host"] == "192.168.1.151"  # untouched


def test_config_patch_rejects_non_integer_mqtt_port():
    merged, err = mb.validate_config_patch({"mqtt_port": "not a number"}, CURRENT)
    assert merged is None
    assert "mqtt_port" in err.lower()


def test_config_patch_rejects_non_positive_poll_interval():
    merged, err = mb.validate_config_patch({"poll_interval": 0}, CURRENT)
    assert merged is None
    assert "poll_interval" in err.lower()


def test_config_patch_rejects_unknown_log_level():
    merged, err = mb.validate_config_patch({"log_level": "verbose"}, CURRENT)
    assert merged is None
    assert "log_level" in err.lower()


def test_config_patch_accepts_all_valid_log_levels():
    for level in ("debug", "info", "warn", "error"):
        merged, err = mb.validate_config_patch({"log_level": level}, CURRENT)
        assert err is None, level
        assert merged["log_level"] == level


def test_admin_password_kept_when_patch_uses_mask():
    merged, err = mb.validate_config_patch({"admin_password": "***"}, CURRENT)
    assert err is None
    assert merged["admin_password"] == "oldpass"


def test_admin_password_replaced_when_patch_provides_concrete_value():
    merged, err = mb.validate_config_patch({"admin_password": "newpass"}, CURRENT)
    assert err is None
    assert merged["admin_password"] == "newpass"


def test_admin_ui_enabled_must_be_bool():
    merged, err = mb.validate_config_patch({"admin_ui_enabled": "yes"}, CURRENT)
    assert merged is None
    assert "admin_ui_enabled" in err.lower()


def test_unknown_keys_pass_through():
    """FR-018 / backwards compatibility."""
    merged, err = mb.validate_config_patch({"future_flag": True}, CURRENT)
    assert err is None
    assert merged["future_flag"] is True


# ---------------------------------------------------------------------------
# validate_wifi_patch
# ---------------------------------------------------------------------------

def test_wifi_patch_accepts_valid_ssid_and_psk():
    out, err = mb.validate_wifi_patch({"ssid": "MyNet", "psk": "password123"})
    assert err is None
    assert out["ssid"] == "MyNet"
    assert out["psk"] == "password123"


def test_wifi_patch_rejects_empty_ssid():
    out, err = mb.validate_wifi_patch({"ssid": "", "psk": "password123"})
    assert out is None
    assert "ssid" in err.lower()


def test_wifi_patch_rejects_missing_ssid():
    out, err = mb.validate_wifi_patch({"psk": "password123"})
    assert out is None
    assert "ssid" in err.lower()


def test_wifi_patch_rejects_too_short_psk():
    out, err = mb.validate_wifi_patch({"ssid": "MyNet", "psk": "short"})
    assert out is None
    assert "psk" in err.lower()


def test_wifi_patch_rejects_too_long_psk():
    out, err = mb.validate_wifi_patch({"ssid": "MyNet", "psk": "x" * 64})
    assert out is None
    assert "psk" in err.lower()


def test_wifi_patch_accepts_psk_at_boundaries():
    for length in (8, 63):
        out, err = mb.validate_wifi_patch({"ssid": "X", "psk": "p" * length})
        assert err is None, length
