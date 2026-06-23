"""spec 023: compute_intra_cycle_retries pure helper tests.

mode='burst' なら burst_retries、 それ以外 base_retries。
"""
import mqtt_bridge as mb


def test_off_mode_returns_base_retries():
    assert mb.compute_intra_cycle_retries("off", 1, 0) == 1


def test_burst_mode_returns_burst_retries():
    assert mb.compute_intra_cycle_retries("burst", 1, 0) == 0


def test_burst_retries_one_explicit():
    """default は 0 だが、 config で 1 にした場合の挙動確認."""
    assert mb.compute_intra_cycle_retries("burst", 1, 1) == 1
