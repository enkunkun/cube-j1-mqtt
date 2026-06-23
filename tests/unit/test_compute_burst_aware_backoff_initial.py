"""spec 026: compute_burst_aware_backoff_initial pure helper tests.

mode='burst' なら burst_initial (ただし 0 sentinel は kill switch)、
それ以外 base_initial。 spec 023 / 025 と同じ mode 依存 pattern。
"""
import mqtt_bridge as mb


def test_off_mode_returns_base():
    assert mb.compute_burst_aware_backoff_initial("off", 30, 5) == 30


def test_burst_mode_returns_burst_initial():
    assert mb.compute_burst_aware_backoff_initial("burst", 30, 5) == 5


def test_burst_initial_zero_returns_base_kill_switch():
    """dig 決定: burst_initial=0 で kill switch sentinel、 base 採用."""
    assert mb.compute_burst_aware_backoff_initial("burst", 30, 0) == 30


def test_burst_initial_negative_returns_base():
    """defensive: 負値は invalid、 kill switch 扱いで base 採用."""
    assert mb.compute_burst_aware_backoff_initial("burst", 30, -1) == 30


def test_returns_int_for_float_input():
    """defensive cast: int 戻り値保証."""
    result = mb.compute_burst_aware_backoff_initial("burst", 30, 5.7)
    assert isinstance(result, int)
    assert result == 5
