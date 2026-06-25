"""spec 028: ring `tid_mismatch_history_maxlen` 10 → 240 で深い遅延吸収 +
`power_w_recovery_backfill_enabled` で瞬時電力救済 frame の backfill 経路 ON.
"""
import mqtt_bridge as mb


def test_default_tid_mismatch_history_maxlen_is_240():
    cfg = mb.apply_defaults({})
    assert cfg["tid_mismatch_history_maxlen"] == 240


def test_default_power_w_recovery_backfill_enabled_is_true():
    cfg = mb.apply_defaults({})
    assert cfg["power_w_recovery_backfill_enabled"] is True


def test_explicit_override_is_preserved():
    """setdefault 挙動: ユーザ明示 override (= 旧値 / kill switch off) を尊重."""
    cfg = mb.apply_defaults({
        "tid_mismatch_history_maxlen": 10,
        "power_w_recovery_backfill_enabled": False,
    })
    assert cfg["tid_mismatch_history_maxlen"] == 10
    assert cfg["power_w_recovery_backfill_enabled"] is False


def test_default_cumulative_recovery_backfill_enabled_is_true():
    """spec 029: 累積系 backfill 経路は default ON."""
    cfg = mb.apply_defaults({})
    assert cfg["cumulative_recovery_backfill_enabled"] is True


def test_cumulative_explicit_override_preserved():
    """spec 029: kill switch off を明示 override すれば setdefault が尊重."""
    cfg = mb.apply_defaults({"cumulative_recovery_backfill_enabled": False})
    assert cfg["cumulative_recovery_backfill_enabled"] is False
