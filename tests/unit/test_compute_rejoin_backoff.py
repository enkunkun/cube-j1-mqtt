"""spec 017: compute_rejoin_backoff pure helper."""
import mqtt_bridge as mb


def test_returns_initial_at_attempt_zero():
    assert mb.compute_rejoin_backoff(0, 30, 2.0, 300) == 30


def test_doubles_each_attempt_with_default_multiplier():
    # 30 → 60 → 120 → 240 → 480-clamped-to-300
    assert mb.compute_rejoin_backoff(1, 30, 2.0, 300) == 60
    assert mb.compute_rejoin_backoff(2, 30, 2.0, 300) == 120
    assert mb.compute_rejoin_backoff(3, 30, 2.0, 300) == 240


def test_clamps_at_max_sec():
    assert mb.compute_rejoin_backoff(4, 30, 2.0, 300) == 300
    assert mb.compute_rejoin_backoff(10, 30, 2.0, 300) == 300


def test_negative_attempt_treated_as_zero():
    assert mb.compute_rejoin_backoff(-1, 30, 2.0, 300) == 30
    assert mb.compute_rejoin_backoff(-5, 30, 2.0, 300) == 30


def test_multiplier_one_returns_initial_for_all_attempts():
    assert mb.compute_rejoin_backoff(0, 30, 1.0, 300) == 30
    assert mb.compute_rejoin_backoff(5, 30, 1.0, 300) == 30


def test_initial_greater_than_max_returns_max():
    assert mb.compute_rejoin_backoff(0, 500, 2.0, 300) == 300
