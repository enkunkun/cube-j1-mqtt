"""spec 048: read_erxudp INF filter + rescue 後継続待ち."""
import collections
import time

import mqtt_bridge as mb


def _state():
    return mb.DiagState(start_time=1000.0, version="test")


# ---------------------------------------------------------------------------
# DiagState: inf_ignored counter + pending_rescued_frames (FR-001/003/006)
# ---------------------------------------------------------------------------

def test_on_erxudp_inf_ignored_increments_counter():
    st = _state()
    st.on_erxudp_inf_ignored()
    st.on_erxudp_inf_ignored()
    assert st.snapshot(now=1010.0)["erxudp_inf_ignored_total"] == 2


def test_snapshot_emits_inf_ignored_zero():
    assert _state().snapshot(now=1010.0)["erxudp_inf_ignored_total"] == 0


def test_pending_rescued_frames_is_bounded_deque():
    st = _state()
    assert isinstance(st.pending_rescued_frames, collections.deque)
    assert st.pending_rescued_frames.maxlen == 8
    assert len(st.pending_rescued_frames) == 0


def test_diag_sensor_defs_includes_inf_ignored():
    sids = {sid for (sid, *_rest) in mb.DIAG_SENSOR_DEFS}
    assert "erxudp_inf_ignored_total" in sids
