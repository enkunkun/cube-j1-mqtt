"""spec 034: apply_defaults SKSCAN channel mask cache defaults.

新 keys:
- wisun_reconnect_channel_mask_enabled (default True)
- wisun_reconnect_channel_mask_fallback_duration (default 6 = SCAN_DURATION_BASE)
"""

from mqtt_bridge import apply_defaults


def test_default_channel_mask_enabled_true():
    cfg = apply_defaults({})
    assert cfg["wisun_reconnect_channel_mask_enabled"] is True


def test_default_fallback_duration_6():
    cfg = apply_defaults({})
    assert cfg["wisun_reconnect_channel_mask_fallback_duration"] == 6


def test_explicit_override_respected():
    cfg = apply_defaults({
        "wisun_reconnect_channel_mask_enabled": False,
        "wisun_reconnect_channel_mask_fallback_duration": 4,
    })
    assert cfg["wisun_reconnect_channel_mask_enabled"] is False
    assert cfg["wisun_reconnect_channel_mask_fallback_duration"] == 4
