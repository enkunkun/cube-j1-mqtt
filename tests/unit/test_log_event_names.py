"""Bridge fires named log events for major state transitions (FR-010).

Rather than running the whole main loop, we exercise the small helpers that
the loop delegates to and inject a fake logger into the module under test.
"""
import mqtt_bridge as mb


class FakeLogger(object):
    def __init__(self):
        self.events = []  # list of (level, event, context-or-None)

    def _record(self, level, event, msg=None, context=None):
        self.events.append((level, event, context))

    def debug(self, event="log", msg=None, context=None):
        self._record("debug", event, msg=msg, context=context)

    def info(self, event="log", msg=None, context=None):
        self._record("info", event, msg=msg, context=context)

    def warn(self, event="log", msg=None, context=None):
        self._record("warn", event, msg=msg, context=context)

    def error(self, event="log", msg=None, context=None):
        self._record("error", event, msg=msg, context=context)


def names_of(fake):
    return [name for _level, name, _ctx in fake.events]


def test_log_emits_bridge_start_event_when_called_via_helper():
    """`emit_bridge_start(logger, device_id, version)` is what main() calls
    on startup. It must produce a `bridge_start` event with the device_id
    and version in context."""
    fake = FakeLogger()
    mb.emit_bridge_start(fake, device_id="cubej1", version="1.0.0+abc1234")
    assert "bridge_start" in names_of(fake)
    [(_level, _event, context)] = [
        e for e in fake.events if e[1] == "bridge_start"
    ]
    assert context == {"device_id": "cubej1", "version": "1.0.0+abc1234"}


def test_emit_mqtt_connected_records_host_and_port():
    fake = FakeLogger()
    mb.emit_mqtt_connected(fake, host="mqtt.example", port=1883)
    [(_l, ev, ctx)] = [e for e in fake.events if e[1] == "mqtt_connected"]
    assert ctx == {"host": "mqtt.example", "port": 1883}


def test_emit_mqtt_reconnect_uses_warn_level():
    fake = FakeLogger()
    mb.emit_mqtt_reconnect(fake)
    [(level, ev, _ctx)] = [e for e in fake.events if e[1] == "mqtt_reconnect"]
    assert level == "warn"


def test_emit_wisun_joined_records_pan_info():
    fake = FakeLogger()
    mb.emit_wisun_joined(fake, pan={
        "Channel": "21", "Pan ID": "8888",
        "Addr": "001D12", "LQI": "C0",
    }, ipv6="fe80::1")
    [(_l, ev, ctx)] = [e for e in fake.events if e[1] == "wisun_joined"]
    assert ctx["channel"] == "21"
    assert ctx["ipv6"] == "fe80::1"


def test_emit_wisun_join_failed_uses_error_level():
    fake = FakeLogger()
    mb.emit_wisun_join_failed(fake, reason="PANA timeout")
    [(level, ev, ctx)] = [e for e in fake.events if e[1] == "wisun_join_failed"]
    assert level == "error"
    assert ctx == {"reason": "PANA timeout"}


def test_emit_scan_retry_carries_duration():
    fake = FakeLogger()
    mb.emit_scan_retry(fake, duration=6)
    [(level, ev, ctx)] = [e for e in fake.events if e[1] == "scan_retry"]
    assert level == "warn"
    assert ctx == {"duration": 6}


def test_emit_poll_success_includes_measurement_summary():
    fake = FakeLogger()
    mb.emit_poll_success(fake, measurements={
        "power_w": 340,
        "energy_forward_kwh": 12345.678,
        "current_r_a": 1.4,
        "current_t_a": 1.5,
    })
    [(_l, ev, ctx)] = [e for e in fake.events if e[1] == "poll_success"]
    assert ctx["power_w"] == 340


def test_emit_poll_failure_uses_warn_level():
    fake = FakeLogger()
    mb.emit_poll_failure(fake, reason="erxudp_timeout")
    [(level, ev, ctx)] = [e for e in fake.events if e[1] == "poll_failure"]
    assert level == "warn"
    assert ctx == {"reason": "erxudp_timeout"}
