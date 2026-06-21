"""Pure predicate that decides whether the main loop should force a
Wi-SUN reconnect after a streak of ERXUDP timeouts.

Mirrors the safety net for the situation observed live: Wi-SUN session
appears healthy at the SKSTACK layer, but the smart meter stops replying
to ECHONET Lite Get requests. `erxudp_timeouts_total` keeps growing while
`wisun_reconnects_total` stays at 0. The bridge needs to give up on the
current PANA session and try a full re-join.
"""
import mqtt_bridge as mb


def test_returns_false_when_consecutive_count_below_threshold():
    assert mb.should_force_wisun_reconnect(consecutive=0, threshold=5) is False
    assert mb.should_force_wisun_reconnect(consecutive=4, threshold=5) is False


def test_returns_true_when_consecutive_count_at_threshold():
    assert mb.should_force_wisun_reconnect(consecutive=5, threshold=5) is True


def test_returns_true_when_consecutive_count_above_threshold():
    assert mb.should_force_wisun_reconnect(consecutive=12, threshold=5) is True


def test_threshold_zero_disables_force_reconnect():
    """`erxudp_timeout_force_reconnect_threshold = 0` opts out (FR-018-style
    backwards compatibility)."""
    assert mb.should_force_wisun_reconnect(consecutive=100, threshold=0) is False


def test_negative_threshold_treated_as_disabled():
    assert mb.should_force_wisun_reconnect(consecutive=10, threshold=-1) is False


# ---------------------------------------------------------------------------
# spec 017: EVENT 24/29 pending signal overrides threshold
# ---------------------------------------------------------------------------


def test_pending_overrides_threshold_check():
    """spec 017: PANA fail (EVENT 24/29) raised pending=True → immediate
    rejoin even if consecutive timeouts haven't hit the threshold yet."""
    assert mb.should_force_wisun_reconnect(
        consecutive=0, threshold=5, pending=True) is True
    assert mb.should_force_wisun_reconnect(
        consecutive=100, threshold=0, pending=True) is True


def test_pending_false_keeps_existing_behaviour():
    """Default pending=False preserves spec 011 thresholding."""
    assert mb.should_force_wisun_reconnect(
        consecutive=4, threshold=5, pending=False) is False
    assert mb.should_force_wisun_reconnect(
        consecutive=5, threshold=5, pending=False) is True
