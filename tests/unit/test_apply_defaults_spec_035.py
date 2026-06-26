"""spec 035: apply_defaults SKLL64 cached + SKJOIN 直行 defaults.

新 keys:
- wisun_reconnect_cached_skjoin_enabled (default True)
- wisun_reconnect_cached_skjoin_invalidate_threshold (default 2)
"""

from mqtt_bridge import apply_defaults


def test_default_cached_skjoin_enabled_true():
    cfg = apply_defaults({})
    assert cfg["wisun_reconnect_cached_skjoin_enabled"] is True


def test_default_invalidate_threshold_2():
    cfg = apply_defaults({})
    assert cfg["wisun_reconnect_cached_skjoin_invalidate_threshold"] == 2


def test_explicit_override_respected():
    cfg = apply_defaults({
        "wisun_reconnect_cached_skjoin_enabled": False,
        "wisun_reconnect_cached_skjoin_invalidate_threshold": 3,
    })
    assert cfg["wisun_reconnect_cached_skjoin_enabled"] is False
    assert cfg["wisun_reconnect_cached_skjoin_invalidate_threshold"] == 3
