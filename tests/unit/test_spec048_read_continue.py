"""spec 048: read_erxudp INF filter + rescue 後継続待ち."""
import collections

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


# ---------------------------------------------------------------------------
# publish_late_frame: drain された rescued frame の publish (FR-004)
# 旧 _late_ts 分岐 (spec 020/028/029/046/047) の関数抽出
# ---------------------------------------------------------------------------

class FakeMqtt(object):
    def __init__(self):
        self.calls = []  # list of (topic, payload, kwargs)

    def publish(self, topic, payload, **kwargs):
        self.calls.append((topic, payload, kwargs))


def _topics(mqtt):
    return [t for (t, _p, _k) in mqtt.calls]


def test_late_frame_power_w_goes_to_backfill_topic():
    mqtt = FakeMqtt()
    st = _state()
    mb.publish_late_frame(mqtt, "cubej1", {"power_w": 700}, 1782307200.0,
                          {}, st)
    assert "cubej/cubej1/power_w_recovered_json" in _topics(mqtt)
    assert st.power_w_recovered_backfill_total == 1


def test_late_frame_cumulative_goes_to_late_publish_and_backfill():
    mqtt = FakeMqtt()
    st = _state()
    mb.publish_late_frame(mqtt, "cubej1", {"energy_forward_kwh": 123.4},
                          1782307200.0, {}, st)
    topics = _topics(mqtt)
    # spec 020 late publish (過去 timestamp) + spec 029 backfill JSON の両方
    assert "cubej/cubej1/energy_forward_kwh_recovered_json" in topics
    assert st.cumulative_recovered_backfill_total == 1


def test_late_frame_current_r_a_goes_to_backfill_topic():
    mqtt = FakeMqtt()
    st = _state()
    mb.publish_late_frame(mqtt, "cubej1", {"current_r_a": 7.5},
                          1782307200.0, {}, st)
    assert "cubej/cubej1/current_r_a_recovered_json" in _topics(mqtt)
    assert st.current_r_a_recovered_backfill_count == 1


def test_late_frame_empty_measurement_increments_counter():
    mqtt = FakeMqtt()
    st = _state()
    mb.publish_late_frame(mqtt, "cubej1", {}, 1782307200.0, {}, st)
    assert mqtt.calls == []
    assert st.erxudp_rescued_empty_measurement_total == 1


def test_late_frame_backfill_disabled_by_config():
    mqtt = FakeMqtt()
    st = _state()
    mb.publish_late_frame(mqtt, "cubej1", {"power_w": 700}, 1782307200.0,
                          {"power_w_recovery_backfill_enabled": False}, st)
    assert "cubej/cubej1/power_w_recovered_json" not in _topics(mqtt)
