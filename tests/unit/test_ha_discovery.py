"""Home Assistant Auto-Discovery payloads for measurements + diagnostics.

Verifies the structure matches contracts/ha-discovery.json.
"""
import json

import mqtt_bridge as mb


class FakeMQTT(object):
    """Capture every publish call for inspection."""
    def __init__(self):
        self.calls = []

    def publish(self, topic, payload, retain=False):
        self.calls.append((topic, payload, retain))


def _payloads_by_topic(mqtt):
    return {topic: payload for topic, payload, _ in mqtt.calls}


def _device_dict(device_id):
    return {
        "identifiers": [device_id],
        "name": "Cube J1 Smart Meter",
        "model": "Cube J1",
        "manufacturer": "NextDrive",
    }


# ---------------------------------------------------------------------------
# Existing measurement discovery (regression: must not change)
# ---------------------------------------------------------------------------

def test_measurement_discovery_publishes_five_sensors_with_shared_device():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery(mqtt, "cubej1")
    payloads = _payloads_by_topic(mqtt)
    expected_topics = {
        "homeassistant/sensor/cubej1/power/config",
        "homeassistant/sensor/cubej1/energy_forward/config",
        "homeassistant/sensor/cubej1/energy_reverse/config",
        "homeassistant/sensor/cubej1/current_r/config",
        "homeassistant/sensor/cubej1/current_t/config",
    }
    assert expected_topics.issubset(payloads.keys())
    for topic in expected_topics:
        cfg = payloads[topic]
        assert cfg["device"] == _device_dict("cubej1")


def test_measurement_discovery_payloads_are_published_with_retain():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery(mqtt, "cubej1")
    for topic, _payload, retain in mqtt.calls:
        if "/diag/" in topic or "homeassistant" in topic:
            assert retain is True, topic


# ---------------------------------------------------------------------------
# Diagnostic discovery
# ---------------------------------------------------------------------------

def test_diag_discovery_publishes_all_defined_sensors():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery_diag(mqtt, "cubej1")
    payloads = _payloads_by_topic(mqtt)
    # Core 10 are baseline; spec 006 added Wi-SUN health rows. Use the
    # SOURCE OF TRUTH (DIAG_SENSOR_DEFS) so this test doesn't churn every
    # time we add an observability row.
    expected_topics = {
        "homeassistant/sensor/cubej1/{}/config".format(sid)
        for (sid, *_rest) in mb.DIAG_SENSOR_DEFS
    }
    assert expected_topics == set(payloads.keys())
    # Sanity: at least the original 10 must still be present.
    core10 = {
        "last_poll_success_ts", "last_poll_failure_ts",
        "lqi", "pan_channel",
        "scan_retries_total", "wisun_reconnects_total",
        "mqtt_reconnects_total", "erxudp_timeouts_total",
        "uptime_seconds", "version",
    }
    assert core10.issubset({sid for (sid, *_r) in mb.DIAG_SENSOR_DEFS})


def test_diag_discovery_payload_for_timestamp_sensor():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery_diag(mqtt, "cubej1")
    payloads = _payloads_by_topic(mqtt)
    cfg = payloads["homeassistant/sensor/cubej1/last_poll_success_ts/config"]
    assert cfg["name"] == "Last Poll Success"
    assert cfg["unique_id"] == "cubej1_last_poll_success_ts"
    assert cfg["state_topic"] == "cubej/cubej1/diag/last_poll_success_ts"
    assert cfg["device_class"] == "timestamp"
    assert cfg["entity_category"] == "diagnostic"
    assert "state_class" not in cfg
    assert cfg["device"] == _device_dict("cubej1")


def test_diag_discovery_payload_for_counter_sensor():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery_diag(mqtt, "cubej1")
    payloads = _payloads_by_topic(mqtt)
    cfg = payloads["homeassistant/sensor/cubej1/mqtt_reconnects_total/config"]
    assert cfg["name"] == "MQTT Reconnects"
    assert cfg["state_class"] == "total_increasing"
    assert cfg["entity_category"] == "diagnostic"
    assert "device_class" not in cfg
    assert "unit_of_measurement" not in cfg


def test_diag_discovery_payload_for_uptime_includes_seconds_unit():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery_diag(mqtt, "cubej1")
    cfg = _payloads_by_topic(mqtt)["homeassistant/sensor/cubej1/uptime_seconds/config"]
    assert cfg["state_class"] == "measurement"
    assert cfg["unit_of_measurement"] == "s"
    assert cfg["entity_category"] == "diagnostic"


def test_diag_discovery_payload_for_version_is_minimal():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery_diag(mqtt, "cubej1")
    cfg = _payloads_by_topic(mqtt)["homeassistant/sensor/cubej1/version/config"]
    assert cfg["name"] == "Bridge Version"
    assert cfg["entity_category"] == "diagnostic"
    assert "state_class" not in cfg
    assert "device_class" not in cfg


# ---------------------------------------------------------------------------
# Shared device identifier across all 15 sensors
# ---------------------------------------------------------------------------

def test_measurement_and_diag_share_identical_device_block():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery(mqtt, "cubej1")       # 5 measurement configs
    mb.publish_ha_discovery_diag(mqtt, "cubej1")  # diag configs (count varies)
    devices = set()
    for _topic, payload, _retain in mqtt.calls:
        if isinstance(payload, dict) and "device" in payload:
            devices.add(json.dumps(payload["device"], sort_keys=True))
    assert len(devices) == 1
    assert json.loads(next(iter(devices))) == _device_dict("cubej1")


def test_publish_ha_discovery_includes_diag_when_called_once():
    """After integration the single entry point publish_ha_discovery should
    publish both measurement and diagnostic configs (count = 5 measurement
    + len(DIAG_SENSOR_DEFS) diagnostic)."""
    mqtt = FakeMQTT()
    mb.publish_ha_discovery(mqtt, "cubej1")
    payloads = _payloads_by_topic(mqtt)
    assert "homeassistant/sensor/cubej1/power/config" in payloads
    assert "homeassistant/sensor/cubej1/mqtt_reconnects_total/config" in payloads
    assert len(payloads) == 5 + len(mb.DIAG_SENSOR_DEFS)


def test_device_id_threads_through_topic_and_unique_id():
    mqtt = FakeMQTT()
    mb.publish_ha_discovery_diag(mqtt, "kitchen_meter")
    payloads = _payloads_by_topic(mqtt)
    topic = "homeassistant/sensor/kitchen_meter/lqi/config"
    assert topic in payloads
    assert payloads[topic]["unique_id"] == "kitchen_meter_lqi"
    assert payloads[topic]["state_topic"] == "cubej/kitchen_meter/diag/lqi"
    assert payloads[topic]["device"]["identifiers"] == ["kitchen_meter"]
