"""spec 013: poll_interval must be >= 30s (ARIB STD-T108 duty cycle)."""
import mqtt_bridge as mb


CURRENT = {
    "br_id": "0" * 32,
    "br_pwd": "0" * 12,
    "mqtt_host": "192.168.1.151",
    "mqtt_port": 1883,
    "mqtt_user": "u",
    "mqtt_pass": "p",
    "device_id": "cubej1",
    "serial_port": "/dev/ttyS1",
    "poll_interval": 60,
}


def test_validate_config_patch_rejects_poll_interval_below_floor():
    merged, err = mb.validate_config_patch({"poll_interval": 10}, CURRENT)
    assert merged is None
    assert "poll_interval" in err.lower()
    assert "30" in err


def test_validate_config_patch_accepts_poll_interval_at_floor():
    merged, err = mb.validate_config_patch({"poll_interval": 30}, CURRENT)
    assert err is None
    assert merged["poll_interval"] == 30


def test_validate_config_patch_accepts_poll_interval_above_floor():
    merged, err = mb.validate_config_patch({"poll_interval": 300}, CURRENT)
    assert err is None
    assert merged["poll_interval"] == 300


def test_apply_defaults_clamps_low_poll_interval():
    cfg = mb.apply_defaults({"poll_interval": 5})
    assert cfg["poll_interval"] == mb.MIN_POLL_INTERVAL_SEC


def test_apply_defaults_preserves_normal_poll_interval():
    cfg = mb.apply_defaults({"poll_interval": 60})
    assert cfg["poll_interval"] == 60


def test_apply_defaults_uses_default_when_poll_interval_missing():
    cfg = mb.apply_defaults({})
    assert cfg["poll_interval"] == 60


def test_validate_config_patch_other_keys_unaffected():
    """poll_interval が patch に含まれない場合は新検証ロジックを通らない."""
    merged, err = mb.validate_config_patch({"log_level": "debug"}, CURRENT)
    assert err is None
    assert merged["log_level"] == "debug"
    assert merged["poll_interval"] == 60
