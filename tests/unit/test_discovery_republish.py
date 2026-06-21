"""spec 016: should_republish_discovery pure helper."""
import mqtt_bridge as mb


def test_returns_false_when_interval_not_elapsed_and_no_pending():
    # last published 100s ago, interval=86400, no pending → no republish
    assert mb.should_republish_discovery(
        now=1000.0, last_publish_ts=900.0,
        pending=False, interval_sec=86400) is False


def test_returns_true_when_pending_flag_set():
    # MQTT reconnect set pending=True → republish regardless of interval
    assert mb.should_republish_discovery(
        now=1000.0, last_publish_ts=999.0,
        pending=True, interval_sec=86400) is True


def test_returns_true_when_interval_elapsed():
    # last_ts 86400s ago, interval=86400 → at boundary, republish
    assert mb.should_republish_discovery(
        now=1_086_400.0, last_publish_ts=1000.0,
        pending=False, interval_sec=86400) is True


def test_returns_false_when_interval_zero_disables_periodic():
    # interval=0 → periodic disabled, pending=False → False
    assert mb.should_republish_discovery(
        now=1_000_000.0, last_publish_ts=0.0,
        pending=False, interval_sec=0) is False


def test_pending_overrides_zero_interval_disable():
    # interval=0 (disabled) but pending=True → still republish
    assert mb.should_republish_discovery(
        now=1_000_000.0, last_publish_ts=0.0,
        pending=True, interval_sec=0) is True


def test_none_last_ts_returns_true_as_first_publish():
    # Defensive: helper called before any publish — treat as overdue
    assert mb.should_republish_discovery(
        now=1000.0, last_publish_ts=None,
        pending=False, interval_sec=86400) is True


def test_pending_overrides_none_last_ts():
    # Both True conditions hold — order doesn't matter, result is True
    assert mb.should_republish_discovery(
        now=1000.0, last_publish_ts=None,
        pending=True, interval_sec=86400) is True
