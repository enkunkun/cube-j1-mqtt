"""spec 023: compute_erxudp_timeout pure helper tests.

mode='burst' なら burst_timeout、 それ以外 base_timeout。
burst_timeout=0 (sentinel) なら base 採用 (kill switch)。
"""
import mqtt_bridge as mb


def test_off_mode_returns_base():
    assert mb.compute_erxudp_timeout("off", 30, 5) == 30


def test_burst_mode_returns_burst_timeout():
    assert mb.compute_erxudp_timeout("burst", 30, 5) == 5


def test_burst_timeout_zero_returns_base_kill_switch():
    """dig 決定: timeout_sec=0 は kill switch sentinel、 base 採用."""
    assert mb.compute_erxudp_timeout("burst", 30, 0) == 30


def test_burst_timeout_negative_returns_base():
    """defensive: 負値は invalid、 kill switch 扱いで base 採用."""
    assert mb.compute_erxudp_timeout("burst", 30, -1) == 30


def test_returns_int_for_float_input():
    """defensive cast: int 戻り値保証."""
    result = mb.compute_erxudp_timeout("burst", 30, 5.7)
    assert isinstance(result, int)
    assert result == 5
