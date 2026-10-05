"""単位が分かるまで積算電力量を送らない."""
import mqtt_bridge as mb

ENERGY_KEYS = ("energy_forward_kwh", "energy_reverse_kwh",
               "energy_forward_fixed_kwh", "energy_reverse_fixed_kwh")


def _measurements():
    m = {"power_w": 712, "current_r_a": 7.0}
    for k in ENERGY_KEYS:
        m[k] = 734395.0
    return m


def test_drop_unscaled_energy_removes_energy_while_scale_is_unknown():
    """単位を知らないまま換算した積算電力量は単位倍に化けているので送らない。"""
    out = mb.drop_unscaled_energy(_measurements(), scale_known=False)
    assert out == {"power_w": 712, "current_r_a": 7.0}


def test_drop_unscaled_energy_keeps_energy_once_scale_is_known():
    m = _measurements()
    assert mb.drop_unscaled_energy(m, scale_known=True) == m
