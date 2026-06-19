"""publish_diag flushes DiagState snapshot entries to MQTT with retain=True."""
import mqtt_bridge as mb


class FakeMQTT(object):
    def __init__(self):
        self.calls = []

    def publish(self, topic, payload, retain=False):
        self.calls.append((topic, payload, retain))


def test_publish_diag_emits_each_snapshot_key_to_its_topic():
    mqtt = FakeMQTT()
    snap = {
        "scan_retries_total": 3,
        "mqtt_reconnects_total": 1,
        "uptime_seconds": 100,
        "version": "1.0.0+abc1234",
    }
    mb.publish_diag(mqtt, "cubej1", snap)
    topics = {topic for topic, _payload, _retain in mqtt.calls}
    assert topics == {
        "cubej/cubej1/diag/scan_retries_total",
        "cubej/cubej1/diag/mqtt_reconnects_total",
        "cubej/cubej1/diag/uptime_seconds",
        "cubej/cubej1/diag/version",
    }


def test_publish_diag_uses_retain_true_for_every_publish():
    mqtt = FakeMQTT()
    mb.publish_diag(mqtt, "cubej1", {"uptime_seconds": 10, "version": "1.0.0+x"})
    assert all(retain is True for _topic, _payload, retain in mqtt.calls)


def test_publish_diag_serialises_payloads_as_strings():
    mqtt = FakeMQTT()
    mb.publish_diag(mqtt, "cubej1", {
        "uptime_seconds": 42,
        "lqi": 192,
        "version": "1.0.0+abc1234",
        "last_poll_success_ts": "2026-06-19T12:00:00Z",
    })
    payloads = {topic: payload for topic, payload, _ in mqtt.calls}
    assert payloads["cubej/cubej1/diag/uptime_seconds"] == "42"
    assert payloads["cubej/cubej1/diag/lqi"] == "192"
    assert payloads["cubej/cubej1/diag/version"] == "1.0.0+abc1234"
    assert payloads["cubej/cubej1/diag/last_poll_success_ts"] == "2026-06-19T12:00:00Z"


def test_publish_diag_skips_none_values():
    """If snapshot has trimmed None entries already, but also when called with
    explicit None for safety."""
    mqtt = FakeMQTT()
    mb.publish_diag(mqtt, "cubej1", {
        "lqi": None,
        "uptime_seconds": 10,
        "version": "1.0.0+x",
    })
    topics = {topic for topic, _payload, _retain in mqtt.calls}
    assert "cubej/cubej1/diag/lqi" not in topics
    assert "cubej/cubej1/diag/uptime_seconds" in topics


def test_publish_diag_threads_device_id_into_topic():
    mqtt = FakeMQTT()
    mb.publish_diag(mqtt, "garage", {"uptime_seconds": 1, "version": "0.1.0+x"})
    assert ("cubej/garage/diag/uptime_seconds", "1", True) in mqtt.calls
