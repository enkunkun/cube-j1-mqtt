"""ERXUDP resilience: timeout config + intra-cycle retry (spec 011)."""
import pytest

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# apply_defaults: new keys
# (spec 011 当時の `_timeout_sec=30 / _intra_cycle_retries=0` default test は
#  spec 032 で巻き戻し済 = `_timeout_sec=6 / _intra_cycle_retries=3` に変更、
#  該当 default test は tests/unit/test_apply_defaults_spec_032.py に移管。
#  spec 011 spec.md は歴史記録として残し、 retry_backoff_sec test と
#  override 維持 test のみここに保持。)
# ---------------------------------------------------------------------------

def test_apply_defaults_retry_backoff_sec_default_2():
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_retry_backoff_sec"] == 2


def test_apply_defaults_respects_overrides():
    cfg = mb.apply_defaults({
        "erxudp_timeout_sec": 60,
        "erxudp_intra_cycle_retries": 5,
        "erxudp_retry_backoff_sec": 1,
    })
    assert cfg["erxudp_timeout_sec"] == 60
    assert cfg["erxudp_intra_cycle_retries"] == 5
    assert cfg["erxudp_retry_backoff_sec"] == 1


# ---------------------------------------------------------------------------
# should_retry_in_cycle: pure helper
# ---------------------------------------------------------------------------

def test_should_retry_in_cycle_first_attempt():
    """attempt=0 (1 回目失敗) で max=2 ならリトライする."""
    assert mb.should_retry_in_cycle(attempt=0, max_retries=2) is True


def test_should_retry_in_cycle_second_attempt():
    """attempt=1 (2 回目失敗) で max=2 ならまだリトライする."""
    assert mb.should_retry_in_cycle(attempt=1, max_retries=2) is True


def test_should_retry_in_cycle_third_attempt_stops():
    """attempt=2 (3 回目失敗) は max=2 で諦める."""
    assert mb.should_retry_in_cycle(attempt=2, max_retries=2) is False


def test_should_retry_in_cycle_zero_max_never_retries():
    """max_retries=0 はリトライしない."""
    assert mb.should_retry_in_cycle(attempt=0, max_retries=0) is False


def test_should_retry_in_cycle_negative_attempt_safe():
    """edge: attempt<0 でも True を返す (defensive)."""
    assert mb.should_retry_in_cycle(attempt=-1, max_retries=2) is True


# ---------------------------------------------------------------------------
# DiagState: new counters
# ---------------------------------------------------------------------------

def _make_state():
    return mb.DiagState(start_time=1000.0, version="1.0.0+test")


def test_on_erxudp_intra_cycle_retry_increments():
    s = _make_state()
    s.on_erxudp_intra_cycle_retry()
    s.on_erxudp_intra_cycle_retry()
    assert s.erxudp_intra_cycle_retries_total == 2


def test_on_erxudp_recovered_by_retry_increments():
    s = _make_state()
    s.on_erxudp_recovered_by_retry()
    assert s.erxudp_recovered_by_retry_total == 1


def test_snapshot_exposes_new_counters():
    s = _make_state()
    s.on_erxudp_intra_cycle_retry()
    s.on_erxudp_intra_cycle_retry()
    s.on_erxudp_intra_cycle_retry()
    s.on_erxudp_recovered_by_retry()
    snap = s.snapshot(now=1234.0)
    assert snap["erxudp_intra_cycle_retries_total"] == 3
    assert snap["erxudp_recovered_by_retry_total"] == 1
