"""Noise-adaptive poll skip (spec 012)."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# apply_defaults: new keys
# ---------------------------------------------------------------------------

def test_apply_defaults_noise_adaptive_skip_enabled_default_true():
    cfg = mb.apply_defaults({})
    assert cfg["noise_adaptive_skip_enabled"] is True


def test_apply_defaults_noise_skip_threshold_default_100():
    cfg = mb.apply_defaults({})
    assert cfg["noise_skip_threshold"] == 100


def test_apply_defaults_noise_skip_max_consecutive_default_3():
    cfg = mb.apply_defaults({})
    assert cfg["noise_skip_max_consecutive"] == 3


def test_apply_defaults_eedscan_interval_default_shortened_to_120():
    """spec 012: EEDSCAN を 5 分 → 2 分に短縮、 ノイズ判定をフレッシュに."""
    cfg = mb.apply_defaults({})
    assert cfg["eedscan_interval_sec"] == 120


# ---------------------------------------------------------------------------
# EedScanState.is_noisy
# ---------------------------------------------------------------------------

def _make_eed():
    return mb.EedScanState(interval_sec=120)


def test_is_noisy_returns_false_when_no_data():
    """起動直後 (sample なし) は False (通常 poll を spawn する)."""
    e = _make_eed()
    assert e.is_noisy(threshold=100, pan_channel=0x39) is False


def test_is_noisy_returns_true_above_threshold():
    e = _make_eed()
    e.record({0x39: 200, 0x3A: 5}, ts=1000.0)
    assert e.is_noisy(threshold=100, pan_channel=0x39) is True


def test_is_noisy_returns_false_below_threshold():
    e = _make_eed()
    e.record({0x39: 50, 0x3A: 5}, ts=1000.0)
    assert e.is_noisy(threshold=100, pan_channel=0x39) is False


def test_is_noisy_returns_false_when_pan_channel_missing():
    e = _make_eed()
    # last_result に pan_channel が無い (= mask が pan_channel を含まなかった etc)
    e.record({0x40: 200}, ts=1000.0)
    assert e.is_noisy(threshold=100, pan_channel=0x39) is False


def test_is_noisy_threshold_inclusive_at_boundary():
    e = _make_eed()
    e.record({0x39: 100}, ts=1000.0)
    # 100 ちょうどでも noisy 扱い (>= threshold)
    assert e.is_noisy(threshold=100, pan_channel=0x39) is True


# ---------------------------------------------------------------------------
# DiagState: noise_adaptive_skips_total counter
# ---------------------------------------------------------------------------

def _make_diag():
    return mb.DiagState(start_time=1000.0, version="1.0.0+test")


def test_on_noise_adaptive_skip_increments():
    d = _make_diag()
    d.on_noise_adaptive_skip()
    d.on_noise_adaptive_skip()
    assert d.noise_adaptive_skips_total == 2


def test_snapshot_exposes_noise_skip_counter():
    d = _make_diag()
    d.on_noise_adaptive_skip()
    snap = d.snapshot(now=1234.0)
    assert snap["noise_adaptive_skips_total"] == 1
