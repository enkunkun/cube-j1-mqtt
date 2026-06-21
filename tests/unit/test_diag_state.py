"""DiagState aggregates bridge diagnostics for MQTT publish.

Invariants from specs/001-bridge-observability/data-model.md:
- Counters are monotonically non-decreasing (no decrement methods).
- `snapshot(now)` returns a dict with None-valued attributes EXCLUDED so HA
  treats them as unknown rather than receiving empty string.
- `uptime_seconds` is non-negative even if `now < start_time`.
- `snapshot` key order is deterministic.
"""
import mqtt_bridge as mb


def make_state(now=1_000_000.0, version="1.0.0+test"):
    return mb.DiagState(start_time=now, version=version)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_snapshot_includes_zero_counters_uptime_and_version():
    """Counters default to 0 and are published with the baseline. None-valued
    timestamps and PAN info are excluded so HA keeps them "unknown" until the
    first real event.
    """
    state = make_state(now=1000.0)
    snap = state.snapshot(now=1042.0)
    assert snap == {
        "scan_retries_total": 0,
        "wisun_reconnects_total": 0,
        "mqtt_reconnects_total": 0,
        "erxudp_timeouts_total": 0,
        # spec 011 retry counters also baseline at 0
        "erxudp_intra_cycle_retries_total": 0,
        "erxudp_recovered_by_retry_total": 0,
        # spec 012 counters also baseline at 0
        "erxudp_tid_mismatch_total": 0,
        "noise_adaptive_skips_total": 0,
        "uptime_seconds": 42,
        "version": "1.0.0+test",
    }


def test_first_scan_retry_increments_counter_from_zero():
    state = make_state()
    state.on_scan_retry()
    snap = state.snapshot(now=state.start_time)
    assert snap["scan_retries_total"] == 1


# ---------------------------------------------------------------------------
# Counter updates
# ---------------------------------------------------------------------------

def test_on_scan_retry_increments_counter():
    state = make_state()
    state.on_scan_retry()
    state.on_scan_retry()
    state.on_scan_retry()
    assert state.snapshot(now=state.start_time)["scan_retries_total"] == 3


def test_on_wisun_reconnect_increments_counter():
    state = make_state()
    state.on_wisun_reconnect()
    assert state.snapshot(now=state.start_time)["wisun_reconnects_total"] == 1


def test_on_mqtt_reconnect_increments_counter():
    state = make_state()
    for _ in range(5):
        state.on_mqtt_reconnect()
    assert state.snapshot(now=state.start_time)["mqtt_reconnects_total"] == 5


def test_on_erxudp_timeout_increments_counter():
    state = make_state()
    state.on_erxudp_timeout()
    assert state.snapshot(now=state.start_time)["erxudp_timeouts_total"] == 1


def test_on_erxudp_tid_mismatch_increments_counter():
    """spec 012: ERXUDP TID 不一致は専用カウンタで観測する。"""
    state = make_state()
    state.on_erxudp_tid_mismatch()
    state.on_erxudp_tid_mismatch()
    snap = state.snapshot(now=state.start_time)
    assert snap["erxudp_tid_mismatch_total"] == 2


def test_counters_are_monotonically_non_decreasing():
    """No method should ever cause a counter to drop."""
    state = make_state()
    state.on_scan_retry()
    state.on_mqtt_reconnect()
    state.on_wisun_reconnect()
    state.on_erxudp_timeout()
    snap1 = state.snapshot(now=state.start_time)
    # Calling snapshot must not modify counters.
    snap2 = state.snapshot(now=state.start_time + 60)
    for key in (
        "scan_retries_total",
        "mqtt_reconnects_total",
        "wisun_reconnects_total",
        "erxudp_timeouts_total",
    ):
        assert snap2[key] >= snap1[key]


# ---------------------------------------------------------------------------
# Timestamp updates
# ---------------------------------------------------------------------------

def test_on_poll_success_records_iso_timestamp():
    state = make_state()
    state.on_poll_success(now=1_700_000_000.0)  # 2023-11-14T22:13:20Z
    assert state.snapshot(now=1_700_000_000.0)["last_poll_success_ts"] == \
        "2023-11-14T22:13:20Z"


def test_on_poll_failure_records_iso_timestamp_independently():
    state = make_state()
    state.on_poll_failure(now=1_700_000_100.0)
    snap = state.snapshot(now=1_700_000_100.0)
    assert snap["last_poll_failure_ts"] == "2023-11-14T22:15:00Z"
    assert "last_poll_success_ts" not in snap


def test_last_poll_success_and_failure_coexist_independently():
    state = make_state()
    state.on_poll_success(now=1_700_000_000.0)
    state.on_poll_failure(now=1_700_000_500.0)
    snap = state.snapshot(now=1_700_000_500.0)
    assert snap["last_poll_success_ts"] == "2023-11-14T22:13:20Z"
    assert snap["last_poll_failure_ts"] == "2023-11-14T22:21:40Z"


# ---------------------------------------------------------------------------
# PAN info (LQI, channel)
# ---------------------------------------------------------------------------

def test_on_wisun_joined_decodes_hex_lqi_and_channel():
    state = make_state()
    state.on_wisun_joined({"LQI": "C0", "Channel": "21", "Pan ID": "8888", "Addr": "001D"})
    snap = state.snapshot(now=state.start_time)
    assert snap["lqi"] == 0xC0
    assert snap["pan_channel"] == 0x21


def test_on_wisun_joined_overwrites_previous_values_on_re_scan():
    state = make_state()
    state.on_wisun_joined({"LQI": "10", "Channel": "21"})
    state.on_wisun_joined({"LQI": "FF", "Channel": "22"})
    snap = state.snapshot(now=state.start_time)
    assert snap["lqi"] == 0xFF
    assert snap["pan_channel"] == 0x22


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------

def test_uptime_is_truncated_seconds_since_start():
    state = make_state(now=1000.0)
    assert state.snapshot(now=1000.7)["uptime_seconds"] == 0
    assert state.snapshot(now=1042.9)["uptime_seconds"] == 42


def test_uptime_clamps_to_zero_if_clock_jumps_backwards():
    state = make_state(now=2000.0)
    assert state.snapshot(now=1500.0)["uptime_seconds"] == 0


# ---------------------------------------------------------------------------
# Snapshot omits unknown (None) fields
# ---------------------------------------------------------------------------

def test_snapshot_omits_none_timestamps_and_pan_info():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert "last_poll_success_ts" not in snap
    assert "last_poll_failure_ts" not in snap
    assert "lqi" not in snap
    assert "pan_channel" not in snap


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def test_snapshot_always_includes_version_string():
    state = make_state(version="2.5.0+deadbee")
    snap = state.snapshot(now=state.start_time)
    assert snap["version"] == "2.5.0+deadbee"


# ---------------------------------------------------------------------------
# Auto-recovery: consecutive ERXUDP timeouts
# ---------------------------------------------------------------------------

def test_consecutive_erxudp_timeouts_starts_at_zero():
    state = make_state()
    assert state.consecutive_erxudp_timeouts == 0


def test_consecutive_erxudp_timeouts_increments_on_each_timeout():
    state = make_state()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    assert state.consecutive_erxudp_timeouts == 2


def test_consecutive_erxudp_timeouts_resets_on_poll_success():
    state = make_state()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    state.on_poll_success(now=1.0)
    assert state.consecutive_erxudp_timeouts == 0


def test_consecutive_does_not_reset_on_poll_failure_path():
    """`on_poll_failure` updates the failure timestamp but the consecutive
    counter is only reset by a real success — multiple failure events keep
    the counter rising."""
    state = make_state()
    state.on_erxudp_timeout()
    state.on_poll_failure(now=1.0)
    state.on_erxudp_timeout()
    state.on_poll_failure(now=2.0)
    assert state.consecutive_erxudp_timeouts == 2


def test_total_erxudp_counter_continues_to_grow_after_reset():
    state = make_state()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    state.on_poll_success(now=1.0)
    state.on_erxudp_timeout()
    snap = state.snapshot(now=state.start_time)
    assert snap["erxudp_timeouts_total"] == 3
    assert state.consecutive_erxudp_timeouts == 1
