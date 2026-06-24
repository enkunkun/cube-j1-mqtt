"""spec 028: publish_recovery_backfill — 救済 frame の瞬時値を
`<key>_recovered_json` topic に `{"value": V, "ts": "<ISO8601 UTC>"}` で publish.

telegraf JSON parser (`json_time_key="ts"`) が metric.time に変換、
prometheus remote_write が client-supplied timestamp として送信 (= 2h backfill)。
"""
import json

import mqtt_bridge as mb


class FakeMqtt(object):
    def __init__(self):
        self.calls = []  # list of (topic, payload, kwargs)

    def publish(self, topic, payload, **kwargs):
        self.calls.append((topic, payload, kwargs))


class FakeDiagState(object):
    def __init__(self):
        self.power_w_recovered_backfill_total = 0


def test_topic_format_is_recovered_json_suffix():
    mqtt = FakeMqtt()
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": 350}, 1782307200.0)
    assert len(mqtt.calls) == 1
    topic, _, _ = mqtt.calls[0]
    assert topic == "cubej/cubej1/power_w_recovered_json"


def test_payload_includes_value_and_ts_iso8601_seconds():
    """dig A 決定: ts は秒精度 ISO8601 UTC (= telegraf parse format 一致)."""
    mqtt = FakeMqtt()
    # 1782307200.0 = 2026-06-24T13:20:00Z (UTC)
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": 350}, 1782307200.0)
    _, payload, _ = mqtt.calls[0]
    parsed = json.loads(payload)
    assert parsed["value"] == 350
    assert parsed["ts"] == "2026-06-24T13:20:00Z"


def test_diag_state_counter_incremented():
    """diag_state が渡されたら power_w_recovered_backfill_total を +1."""
    mqtt = FakeMqtt()
    diag = FakeDiagState()
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": 350}, 1782307200.0, diag)
    assert diag.power_w_recovered_backfill_total == 1


def test_multiple_keys_each_published():
    """複数 key (= 将来 current_a_* 拡張時) で各々 1 件ずつ publish + counter +1."""
    mqtt = FakeMqtt()
    diag = FakeDiagState()
    mb.publish_recovery_backfill(mqtt, "cubej1",
                                 {"power_w": 350, "current_a_r": 1.2},
                                 1782307200.0, diag)
    assert len(mqtt.calls) == 2
    topics = sorted(c[0] for c in mqtt.calls)
    assert topics == ["cubej/cubej1/current_a_r_recovered_json",
                      "cubej/cubej1/power_w_recovered_json"]
    assert diag.power_w_recovered_backfill_total == 2


def test_none_value_skipped():
    """value=None (= 計測欠落) は publish しない、 counter も増やさない."""
    mqtt = FakeMqtt()
    diag = FakeDiagState()
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": None}, 1782307200.0, diag)
    assert mqtt.calls == []
    assert diag.power_w_recovered_backfill_total == 0


def test_retain_false_qos_0():
    """backfill 用途、 retain=False で broker に滞留させない、 qos=0 で HA 互換."""
    mqtt = FakeMqtt()
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": 350}, 1782307200.0)
    _, _, kwargs = mqtt.calls[0]
    assert kwargs.get("retain") is False
    assert kwargs.get("qos") == 0
