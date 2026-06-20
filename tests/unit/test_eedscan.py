"""EEDSCAN parser + EedScanState (spec 010)."""
import pytest

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# parse_eedscan: pure helper
# ---------------------------------------------------------------------------

def test_parse_eedscan_real_payload():
    """実機 dump: `EEDSCAN` ヘッダ + 1 line of <ch> <energy> pairs."""
    payload = "0 21 1A 22 19 23 13 24 0E 25 0E 26 0B 27 0D 28 17 29 14 2A 10 2B 16 2C 10 2D 0E 2E 21 2F 0F 30 0B 31 0C 32 0A 33 0C 34 0B 35 14 36 0E 37 0E 38 0D 39 0B 3A 0E 3B 0A 3C 13"
    result = mb.parse_eedscan(payload)
    assert result[0x21] == 0x1A
    assert result[0x39] == 0x0B  # pan_channel 57 = 0x39
    assert result[0x3C] == 0x13
    assert len(result) == 28  # ch33-60


def test_parse_eedscan_skips_status_byte():
    """先頭の 0 は status code、 channel-energy pair の前にある."""
    payload = "0 21 1A"
    result = mb.parse_eedscan(payload)
    assert result == {0x21: 0x1A}


def test_parse_eedscan_returns_empty_for_empty():
    assert mb.parse_eedscan("") == {}
    assert mb.parse_eedscan("0") == {}


def test_parse_eedscan_handles_lowercase_hex():
    payload = "0 21 1a 22 1b"
    result = mb.parse_eedscan(payload)
    assert result == {0x21: 0x1A, 0x22: 0x1B}


def test_parse_eedscan_truncates_dangling_channel():
    """channel に energy が pair してない末尾は drop."""
    payload = "0 21 1A 22"
    result = mb.parse_eedscan(payload)
    assert result == {0x21: 0x1A}


def test_parse_eedscan_ignores_garbage_tokens():
    payload = "garbage 0 21 1A xx 22 1B"
    result = mb.parse_eedscan(payload)
    # status byte の 0 が見つかるまで skip、 その後の garbage は drop
    # 厳密: token-by-token で hex parse、 ダメなら skip
    assert 0x21 in result


# ---------------------------------------------------------------------------
# EedScanState
# ---------------------------------------------------------------------------

def _make_state(interval_sec=300):
    return mb.EedScanState(interval_sec=interval_sec)


def test_should_run_initially():
    """起動直後 (last_run=0) は即実行可."""
    s = _make_state(interval_sec=300)
    assert s.should_run(now=1000.0) is True


def test_should_not_run_within_interval():
    s = _make_state(interval_sec=300)
    s.record({0x21: 0x1A}, ts=1000.0)
    assert s.should_run(now=1100.0) is False  # 100 秒経過


def test_should_run_after_interval():
    s = _make_state(interval_sec=300)
    s.record({0x21: 0x1A}, ts=1000.0)
    assert s.should_run(now=1300.0) is True  # 300 秒経過


def test_record_keeps_latest_and_history():
    s = _make_state(interval_sec=300)
    s.record({0x21: 0x1A}, ts=1000.0)
    s.record({0x21: 0x1B}, ts=1300.0)
    assert s.last_result == {0x21: 0x1B}
    assert len(s.recent) == 2


def test_record_caps_history():
    """直近 N 件のみ保持 (deque maxlen)."""
    s = _make_state(interval_sec=300)
    for i in range(150):
        s.record({0x21: i}, ts=1000.0 + i * 300)
    assert len(s.recent) <= 100  # maxlen=100


def test_snapshot_emits_pan_channel_energy():
    """`pan_channel` を渡せば現使用 channel の energy が main metric として出る."""
    s = _make_state()
    s.record({0x21: 0x1A, 0x39: 0x0B}, ts=1000.0)
    snap = s.snapshot(pan_channel=0x39)
    assert snap["eedscan_pan_channel_energy"] == 0x0B


def test_snapshot_emits_max_min():
    s = _make_state()
    s.record({0x21: 0x1A, 0x22: 0x05, 0x23: 0x2E}, ts=1000.0)
    snap = s.snapshot(pan_channel=0x21)
    assert snap["eedscan_max_energy"] == 0x2E
    assert snap["eedscan_min_energy"] == 0x05


def test_snapshot_empty_when_no_data():
    s = _make_state()
    snap = s.snapshot(pan_channel=0x39)
    assert snap == {}


def test_snapshot_missing_pan_channel_omits_pan_energy():
    s = _make_state()
    s.record({0x21: 0x1A}, ts=1000.0)
    snap = s.snapshot(pan_channel=0x39)
    # pan_channel が結果に無いので pan_channel_energy は出ない
    assert "eedscan_pan_channel_energy" not in snap
    # 他の集計は出る
    assert snap["eedscan_max_energy"] == 0x1A
