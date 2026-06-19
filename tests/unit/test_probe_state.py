"""ProbeState: in-memory toggle + auto-expiry (spec 009)."""
import pytest

import mqtt_bridge as mb


def test_default_state_inactive():
    p = mb.ProbeState()
    assert p.is_active(now=1000.0) is False
    snap = p.snapshot(now=1000.0)
    assert snap["active"] is False
    assert "deadline_ts" in snap


def test_start_makes_state_active():
    p = mb.ProbeState()
    p.start(interval_sec=5, duration_sec=300, now=1000.0)
    assert p.is_active(now=1000.0) is True
    assert p.interval_sec == 5
    assert p.deadline_ts == 1300.0


def test_active_until_deadline():
    p = mb.ProbeState()
    p.start(interval_sec=5, duration_sec=60, now=1000.0)
    assert p.is_active(now=1059.0) is True
    assert p.is_active(now=1060.0) is False
    assert p.is_active(now=1100.0) is False


def test_stop_disables_immediately():
    p = mb.ProbeState()
    p.start(interval_sec=5, duration_sec=600, now=1000.0)
    assert p.is_active(now=1010.0) is True
    p.stop()
    assert p.is_active(now=1010.0) is False


def test_snapshot_includes_remaining_seconds():
    p = mb.ProbeState()
    p.start(interval_sec=5, duration_sec=300, now=1000.0)
    snap = p.snapshot(now=1100.0)
    assert snap["active"] is True
    assert snap["interval_sec"] == 5
    assert snap["remaining_sec"] == 200
    assert snap["deadline_ts"] == 1300.0


def test_snapshot_when_inactive_has_zero_remaining():
    p = mb.ProbeState()
    snap = p.snapshot(now=1000.0)
    assert snap["active"] is False
    assert snap["remaining_sec"] == 0


def test_start_rejects_zero_interval():
    p = mb.ProbeState()
    with pytest.raises(ValueError):
        p.start(interval_sec=0, duration_sec=300, now=1000.0)


def test_start_rejects_zero_duration():
    p = mb.ProbeState()
    with pytest.raises(ValueError):
        p.start(interval_sec=5, duration_sec=0, now=1000.0)


def test_start_overwrites_previous_settings():
    p = mb.ProbeState()
    p.start(interval_sec=5, duration_sec=60, now=1000.0)
    p.start(interval_sec=2, duration_sec=120, now=1010.0)
    assert p.interval_sec == 2
    assert p.deadline_ts == 1130.0


# ---------------------------------------------------------------------------
# decide_cycle_kind: pure helper that mixes probe + normal cycles
# ---------------------------------------------------------------------------

def test_decide_cycle_kind_normal_when_probe_inactive():
    assert mb.decide_cycle_kind(
        probe_active=False, last_normal_start=0.0, now=100.0, poll_interval=60,
    ) == "normal"


def test_decide_cycle_kind_normal_when_first_ever_cycle():
    """Active probe but no normal cycle ever recorded — run normal first."""
    assert mb.decide_cycle_kind(
        probe_active=True, last_normal_start=0.0, now=1000.0, poll_interval=60,
    ) == "normal"


def test_decide_cycle_kind_probe_within_normal_interval():
    """5 秒前に normal poll が走った直後の cycle は probe (まだ 60s 経って
    ないので電力値は十分新しい)."""
    assert mb.decide_cycle_kind(
        probe_active=True, last_normal_start=1000.0, now=1005.0, poll_interval=60,
    ) == "probe"


def test_decide_cycle_kind_normal_after_interval_elapsed():
    """前回 normal から poll_interval 経過したら次は normal で電力値更新."""
    assert mb.decide_cycle_kind(
        probe_active=True, last_normal_start=1000.0, now=1060.0, poll_interval=60,
    ) == "normal"


def test_decide_cycle_kind_normal_when_far_past_interval():
    assert mb.decide_cycle_kind(
        probe_active=True, last_normal_start=1000.0, now=1200.0, poll_interval=60,
    ) == "normal"
