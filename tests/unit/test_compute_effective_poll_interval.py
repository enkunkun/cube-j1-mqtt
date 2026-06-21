"""spec 022: compute_effective_poll_interval pure helper tests.

burst mode active なら burst_interval、 expired/off なら base_interval。
state mutation は responsibility 外 (caller の RealtimeModeState.tick が担う)。
"""
import mqtt_bridge as mb


def test_off_mode_returns_base():
    state = {"mode": "off", "expires_at": None, "burst_interval": 5}
    assert mb.compute_effective_poll_interval(1000.0, 60, state) == 60


def test_burst_active_returns_burst_interval():
    state = {"mode": "burst", "expires_at": 1100.0, "burst_interval": 5}
    assert mb.compute_effective_poll_interval(1000.0, 60, state) == 5


def test_burst_expired_returns_base():
    """now >= expires_at: helper は base 返す (mutation は caller)."""
    state = {"mode": "burst", "expires_at": 1000.0, "burst_interval": 5}
    assert mb.compute_effective_poll_interval(1000.0, 60, state) == 60


def test_burst_expires_at_none_returns_base():
    """safety: burst mode でも expires_at=None は base 扱い."""
    state = {"mode": "burst", "expires_at": None, "burst_interval": 5}
    assert mb.compute_effective_poll_interval(1000.0, 60, state) == 60


def test_burst_interval_overrides_base_with_different_value():
    state = {"mode": "burst", "expires_at": 1500.0, "burst_interval": 10}
    assert mb.compute_effective_poll_interval(1200.0, 60, state) == 10
