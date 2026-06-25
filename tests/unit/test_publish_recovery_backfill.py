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


def test_default_counter_attr_is_power_w_for_spec028_compat():
    """spec 029: helper generic 化後も default は spec 028 互換 attribute へ +1."""
    mqtt = FakeMqtt()
    diag = FakeDiagState()
    # 明示しない (= spec 028 既存 caller pattern)
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": 100}, 1782307200.0, diag)
    assert diag.power_w_recovered_backfill_total == 1
    # cumulative attribute は触らず default 0 のまま
    assert getattr(diag, "cumulative_recovered_backfill_total", 0) == 0


def test_counter_attr_argument_increments_specified_attribute():
    """spec 029: counter_attr 指定で別 attribute を increment。"""
    mqtt = FakeMqtt()
    diag = FakeDiagState()
    diag.cumulative_recovered_backfill_total = 0  # init
    mb.publish_recovery_backfill(
        mqtt, "cubej1", {"energy_forward_kwh": 12.345}, 1782307200.0, diag,
        counter_attr="cumulative_recovered_backfill_total")
    assert diag.cumulative_recovered_backfill_total == 1
    # spec 028 既存 attribute は触らず
    assert diag.power_w_recovered_backfill_total == 0


def test_counter_attr_missing_attribute_initializes_to_1():
    """spec 029: 未定義 attribute でも setattr/getattr 経由で 0 → +1 で安全."""
    mqtt = FakeMqtt()
    diag = FakeDiagState()  # cumulative_* attr 未定義
    mb.publish_recovery_backfill(
        mqtt, "cubej1", {"energy_forward_kwh": 5}, 1782307200.0, diag,
        counter_attr="cumulative_recovered_backfill_total")
    assert diag.cumulative_recovered_backfill_total == 1


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


def test_retain_false():
    """backfill 用途、 retain=False で broker に滞留させない (= 救済 frame は
    publish 時点で意味、 次以降の subscriber に再配送不要).

    spec 028 v1.1 (= 2026-06-25 hotfix): 当初 `qos=0` も assert していたが
    実機 MQTTClient.publish() は `qos` 引数を受け付けない signature で
    TypeError → main loop catch → reconnect 強制誘発という 2 重バグ。
    実機 publish sig (= `publish(topic, payload, retain=False)`) に合わせ
    `qos` 渡しを削除、 test も retain のみ assert に縮小。
    """
    mqtt = FakeMqtt()
    mb.publish_recovery_backfill(mqtt, "cubej1", {"power_w": 350}, 1782307200.0)
    _, _, kwargs = mqtt.calls[0]
    assert kwargs.get("retain") is False
    assert "qos" not in kwargs  # 実機 sig に qos なし、 渡してはいけない
