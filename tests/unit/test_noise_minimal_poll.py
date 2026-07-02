"""spec 049: noise skip cycle の 0xE7 単独 minimal poll 降格."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# decide_noise_cycle_action: pure helper (FR-005)
# ---------------------------------------------------------------------------

def test_quiet_channel_is_normal():
    assert mb.decide_noise_cycle_action(
        noisy=False, streak=0, max_consecutive=3,
        minimal_enabled=True) == "normal"


def test_noisy_with_minimal_enabled_is_minimal():
    assert mb.decide_noise_cycle_action(
        noisy=True, streak=0, max_consecutive=3,
        minimal_enabled=True) == "minimal"


def test_noisy_with_minimal_disabled_is_skip():
    """kill switch (= spec 012 の完全 skip 挙動に戻る)."""
    assert mb.decide_noise_cycle_action(
        noisy=True, streak=0, max_consecutive=3,
        minimal_enabled=False) == "skip"


def test_noisy_at_streak_limit_is_normal_failsafe():
    """streak 上限で fail-safe の通常 poll (= 既存挙動不変)."""
    assert mb.decide_noise_cycle_action(
        noisy=True, streak=3, max_consecutive=3,
        minimal_enabled=True) == "normal"
    assert mb.decide_noise_cycle_action(
        noisy=True, streak=3, max_consecutive=3,
        minimal_enabled=False) == "normal"


# ---------------------------------------------------------------------------
# DiagState: minimal poll counter 3 本 (FR-003/004/006)
# ---------------------------------------------------------------------------

def _state():
    return mb.DiagState(start_time=1000.0, version="test")


def test_minimal_poll_counters_increment():
    st = _state()
    st.on_noise_minimal_poll()
    st.on_noise_minimal_poll_success()
    st.on_noise_minimal_poll_timeout()
    snap = st.snapshot(now=1010.0)
    assert snap["noise_minimal_polls_total"] == 1
    assert snap["noise_minimal_poll_success_total"] == 1
    assert snap["noise_minimal_poll_timeout_total"] == 1


def test_minimal_poll_counters_zero_in_snapshot():
    snap = _state().snapshot(now=1010.0)
    for key in ("noise_minimal_polls_total",
                "noise_minimal_poll_success_total",
                "noise_minimal_poll_timeout_total"):
        assert snap[key] == 0, key


def test_diag_sensor_defs_includes_minimal_poll_counters():
    sids = {sid for (sid, *_rest) in mb.DIAG_SENSOR_DEFS}
    for key in ("noise_minimal_polls_total",
                "noise_minimal_poll_success_total",
                "noise_minimal_poll_timeout_total"):
        assert key in sids, key


# ---------------------------------------------------------------------------
# apply_defaults + NOISE_MINIMAL_EPCS (FR-001)
# ---------------------------------------------------------------------------

def test_apply_defaults_minimal_poll_enabled():
    out = mb.apply_defaults({"br_id": "x", "br_pwd": "y",
                             "mqtt_host": "z"})
    assert out["noise_skip_tier1_minimal_poll_enabled"] is True


def test_noise_minimal_epcs_is_e7_only():
    assert list(mb.NOISE_MINIMAL_EPCS) == [0xE7]
