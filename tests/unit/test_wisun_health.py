"""Wi-SUN health diag: latency + SK EVENT/FAIL classification (spec 006)."""
import time

import pytest

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# classify_sk_line: pure helper
# ---------------------------------------------------------------------------

def test_classify_sk_line_erxudp():
    line = "ERXUDP FE80:... FE80:... 0E1A 0E1A 001D129... 1 0 001C 1081000102880105FF017264E704000000F0"
    kind, payload = mb.classify_sk_line(line)
    assert kind == "erxudp"
    assert payload.startswith("1081000102")


def test_classify_sk_line_event_simple():
    assert mb.classify_sk_line("EVENT 22 FE80:0000:0000:0000:021D:1290:1234:5678") == ("event", "22")


def test_classify_sk_line_event_no_args():
    assert mb.classify_sk_line("EVENT 32") == ("event", "32")


def test_classify_sk_line_event_lowercase_normalized():
    assert mb.classify_sk_line("EVENT 2a FE80::1") == ("event", "2A")


def test_classify_sk_line_fail_er05():
    assert mb.classify_sk_line("FAIL ER05") == ("error", "05")


def test_classify_sk_line_fail_er10():
    assert mb.classify_sk_line("FAIL ER10") == ("error", "10")


def test_classify_sk_line_unknown_returns_none():
    assert mb.classify_sk_line("OK") is None
    assert mb.classify_sk_line("SKSCAN ...") is None
    assert mb.classify_sk_line("") is None


def test_classify_sk_line_ignores_fail_without_er_prefix():
    # 仕様外フォーマットは弾く
    assert mb.classify_sk_line("FAIL 05") is None


# ---------------------------------------------------------------------------
# DiagState: latency deque + percentiles
# ---------------------------------------------------------------------------

def _make_state():
    return mb.DiagState(start_time=1000.0, version="1.0.0+test")


def test_on_erxudp_latency_records_in_deque():
    s = _make_state()
    s.on_erxudp_latency(123.4)
    s.on_erxudp_latency(456.7)
    assert list(s.erxudp_latency_ms_recent) == [123.4, 456.7]


def test_on_erxudp_latency_caps_at_200():
    s = _make_state()
    for i in range(250):
        s.on_erxudp_latency(float(i))
    assert len(s.erxudp_latency_ms_recent) == 200
    assert s.erxudp_latency_ms_recent[0] == 50.0  # oldest 50 dropped
    assert s.erxudp_latency_ms_recent[-1] == 249.0


def test_snapshot_emits_latency_percentiles():
    s = _make_state()
    for v in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0):
        s.on_erxudp_latency(v)
    snap = s.snapshot(1234.0)
    assert snap["erxudp_latency_p50_ms"] == pytest.approx(55.0, abs=10.0)
    assert snap["erxudp_latency_p95_ms"] == pytest.approx(95.0, abs=10.0)
    assert snap["erxudp_latency_max_ms"] == 100.0


def test_snapshot_omits_latency_when_empty():
    s = _make_state()
    snap = s.snapshot(1234.0)
    assert "erxudp_latency_p50_ms" not in snap
    assert "erxudp_latency_p95_ms" not in snap
    assert "erxudp_latency_max_ms" not in snap


# ---------------------------------------------------------------------------
# DiagState: SK event / error counters
# ---------------------------------------------------------------------------

def test_on_sk_event_increments_counter():
    s = _make_state()
    s.on_sk_event("22")
    s.on_sk_event("22")
    s.on_sk_event("28")
    assert s.sk_event_counts == {"22": 2, "28": 1}


def test_on_sk_error_increments_counter():
    s = _make_state()
    s.on_sk_error("05")
    s.on_sk_error("10")
    s.on_sk_error("10")
    assert s.sk_error_counts == {"05": 1, "10": 2}


def test_snapshot_exposes_known_event_counters():
    s = _make_state()
    s.on_sk_event("22")
    s.on_sk_event("24")
    s.on_sk_event("28")
    snap = s.snapshot(1234.0)
    assert snap["sk_event_22_total"] == 1
    assert snap["sk_event_24_total"] == 1
    assert snap["sk_event_28_total"] == 1


def test_snapshot_exposes_known_error_counters():
    s = _make_state()
    s.on_sk_error("05")
    s.on_sk_error("10")
    snap = s.snapshot(1234.0)
    assert snap["sk_error_ER05_total"] == 1
    assert snap["sk_error_ER10_total"] == 1


def test_snapshot_omits_zero_event_counters():
    """HA に空 entity を作らないため 0 件の counter は publish しない."""
    s = _make_state()
    s.on_sk_event("22")
    snap = s.snapshot(1234.0)
    assert snap["sk_event_22_total"] == 1
    assert "sk_event_24_total" not in snap
    assert "sk_event_28_total" not in snap
    assert "sk_error_ER05_total" not in snap


# ---------------------------------------------------------------------------
# read_erxudp: dispatches EVENT / FAIL into diag while waiting for ERXUDP
# ---------------------------------------------------------------------------

class _FakeFd(object):
    """Drives serial_readline by line, used to drive read_erxudp's loop."""

    def __init__(self, lines):
        self._lines = list(lines)

    def fileno(self):
        return -1


def test_read_erxudp_records_event_then_returns_payload(monkeypatch):
    s = _make_state()
    lines = [
        "EVENT 22 FE80::1",
        "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D129 1 0 0001 10810001028801",
    ]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    data = mb.read_erxudp(_FakeFd(lines), timeout=2, diag_state=s)
    assert data is not None
    assert s.sk_event_counts.get("22") == 1


def test_read_erxudp_records_fail_and_keeps_waiting(monkeypatch):
    s = _make_state()
    lines = [
        "FAIL ER10",
        "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D129 1 0 0001 10810001028801",
    ]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    data = mb.read_erxudp(_FakeFd(lines), timeout=2, diag_state=s)
    assert data is not None
    assert s.sk_error_counts.get("10") == 1


def test_read_erxudp_handles_missing_diag_state(monkeypatch):
    """既存呼び出し（diag_state なし）で regression を起こさない."""
    lines = [
        "EVENT 22 FE80::1",
        "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D129 1 0 0001 10810001028801",
    ]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    data = mb.read_erxudp(_FakeFd(lines), timeout=2)
    assert data is not None
