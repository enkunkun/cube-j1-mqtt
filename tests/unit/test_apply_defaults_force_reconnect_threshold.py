"""spec 027: erxudp_timeout_force_reconnect_threshold default = 30.

spec 011 で 5 だった default、 実機 grafana 24h データで 78% 欠損 / reconnect
430 回の主因と判明 (spec 024 調査 + 027 spec.md)。 30 に上げて base mode の
保護期間を 150 秒 → 900 秒 (15 分) に。 ユーザ明示 override は維持 (setdefault)。
"""
import mqtt_bridge as mb


def test_default_force_reconnect_threshold_is_30():
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_timeout_force_reconnect_threshold"] == 30


def test_explicit_override_is_preserved():
    """setdefault 挙動: ユーザが明示的に 5 を維持していれば既存挙動."""
    cfg = mb.apply_defaults({"erxudp_timeout_force_reconnect_threshold": 5})
    assert cfg["erxudp_timeout_force_reconnect_threshold"] == 5
