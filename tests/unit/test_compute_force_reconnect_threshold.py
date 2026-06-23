"""spec 025: compute_force_reconnect_threshold pure helper tests.

mode='burst' なら burst_threshold (ただし 0 sentinel は kill switch)、
それ以外 base_threshold。 spec 023 compute_erxudp_timeout と同じ pattern。
"""
import mqtt_bridge as mb


def test_off_mode_returns_base():
    assert mb.compute_force_reconnect_threshold("off", 5, 30) == 5


def test_burst_mode_returns_burst_threshold():
    assert mb.compute_force_reconnect_threshold("burst", 5, 30) == 30


def test_burst_threshold_zero_returns_base_kill_switch():
    """dig 決定 (sentinel pattern): burst=0 で kill switch、 base 採用."""
    assert mb.compute_force_reconnect_threshold("burst", 5, 0) == 5


def test_burst_threshold_negative_returns_base():
    """defensive: 負値は invalid、 kill switch 扱いで base 採用."""
    assert mb.compute_force_reconnect_threshold("burst", 5, -1) == 5


def test_returns_int_for_float_input():
    """defensive cast: int 戻り値保証."""
    result = mb.compute_force_reconnect_threshold("burst", 5, 30.7)
    assert isinstance(result, int)
    assert result == 30
