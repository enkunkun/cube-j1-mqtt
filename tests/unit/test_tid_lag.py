"""ERXUDP TID mismatch lag observability (spec 011 follow-up 2)."""
import pytest

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# compute_tid_lag: pure helper
# ---------------------------------------------------------------------------

def test_compute_tid_lag_basic():
    """expected=0029 got=0001 → 古い request 40 frame 分の応答が遅延着信."""
    assert mb.compute_tid_lag(expected=0x0029, got=0x0001) == 40


def test_compute_tid_lag_handles_wrap_around():
    """expected=0001 got=FFFE → 3 frame 遅延 (16-bit wrap)."""
    assert mb.compute_tid_lag(expected=0x0001, got=0xFFFE) == 3


def test_compute_tid_lag_zero_when_equal():
    assert mb.compute_tid_lag(expected=0x0010, got=0x0010) == 0


def test_compute_tid_lag_none_when_missing():
    assert mb.compute_tid_lag(expected=None, got=None) is None
    assert mb.compute_tid_lag(expected=0x0010, got=None) is None
    assert mb.compute_tid_lag(expected=None, got=0x0010) is None


# ---------------------------------------------------------------------------
# DiagState.on_erxudp_tid_mismatch keeps a lag deque
# ---------------------------------------------------------------------------

def _make_state():
    return mb.DiagState(start_time=1000.0, version="1.0.0+test")


def test_on_erxudp_tid_mismatch_with_lag_records_history():
    s = _make_state()
    s.on_erxudp_tid_mismatch(expected=0x0029, got=0x0001)
    s.on_erxudp_tid_mismatch(expected=0x002B, got=0x0029)
    assert list(s.erxudp_tid_mismatch_lags_recent) == [40, 2]


def test_on_erxudp_tid_mismatch_without_lag_still_increments_counter():
    """既存呼び出し (引数なし) で counter は増える、 lag history 無関係."""
    s = _make_state()
    s.on_erxudp_tid_mismatch()
    s.on_erxudp_tid_mismatch()
    assert s.erxudp_tid_mismatch_total == 2
    assert len(s.erxudp_tid_mismatch_lags_recent) == 0


def test_on_erxudp_tid_mismatch_skips_lag_when_none():
    s = _make_state()
    s.on_erxudp_tid_mismatch(expected=None, got=0x0010)
    assert len(s.erxudp_tid_mismatch_lags_recent) == 0


def test_lag_deque_caps_at_100():
    s = _make_state()
    for i in range(150):
        s.on_erxudp_tid_mismatch(expected=i + 1, got=0)
    assert len(s.erxudp_tid_mismatch_lags_recent) == 100
    # 古い 50 件は drop、 直近 100 件が残る
    assert list(s.erxudp_tid_mismatch_lags_recent)[0] == 51
    assert list(s.erxudp_tid_mismatch_lags_recent)[-1] == 150


# ---------------------------------------------------------------------------
# Snapshot exposes lag percentiles
# ---------------------------------------------------------------------------

def test_snapshot_emits_lag_p50_p95_max():
    s = _make_state()
    # lag 1, 5, 10, 20, 40, 80 を投入 → max=80, p50 ≈ 15, p95 ≈ 80
    for lag in (1, 5, 10, 20, 40, 80):
        s.on_erxudp_tid_mismatch(expected=lag, got=0)
    snap = s.snapshot(now=1234.0)
    assert snap["erxudp_tid_mismatch_lag_max"] == 80
    assert 10 <= snap["erxudp_tid_mismatch_lag_p50"] <= 25
    assert 70 <= snap["erxudp_tid_mismatch_lag_p95"] <= 80


def test_snapshot_omits_lag_keys_when_no_history():
    s = _make_state()
    snap = s.snapshot(now=1234.0)
    assert "erxudp_tid_mismatch_lag_max" not in snap
    assert "erxudp_tid_mismatch_lag_p50" not in snap
    assert "erxudp_tid_mismatch_lag_p95" not in snap


# ---------------------------------------------------------------------------
# apply_defaults: retry default cut to 0
# (spec 011 follow-up 2 当時の default=0 test は spec 032 で 0→3 に巻き戻し、
#  test/unit/test_apply_defaults_spec_032.py に移管済。 spec 011 spec.md は
#  歴史記録として残し、 ここは挙動本体の test に集中。)
# ---------------------------------------------------------------------------
