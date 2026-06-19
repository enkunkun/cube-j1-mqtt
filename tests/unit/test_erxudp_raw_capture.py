"""Capture the last raw ERXUDP line for firmware-format inspection.

ROHM SKSTACK の ERXUDP token 数は firmware で異なる。 RSSI / LQI が含まれる
かを判別するため、 直近 1 行の raw を保持して admin UI から確認できるよう
にする (spec 007 で正式な LQI モニタリングを決める前の preparatory)。
"""
import mqtt_bridge as mb


def _make_state():
    return mb.DiagState(start_time=1000.0, version="1.0.0+test")


def test_diag_starts_with_no_raw_line():
    s = _make_state()
    assert s.last_erxudp_raw_line is None


def test_on_erxudp_raw_records_line():
    s = _make_state()
    line = "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D 1 0 0001 1081"
    s.on_erxudp_raw(line)
    assert s.last_erxudp_raw_line == line


def test_on_erxudp_raw_keeps_only_latest():
    s = _make_state()
    s.on_erxudp_raw("first")
    s.on_erxudp_raw("second")
    assert s.last_erxudp_raw_line == "second"


def test_read_erxudp_records_raw_via_diag(monkeypatch):
    s = _make_state()
    target = "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D 1 0 0001 10810001028801"
    lines = [target]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    class _FakeFd(object):
        def fileno(self):
            return -1

    data = mb.read_erxudp(_FakeFd(), timeout=2, diag_state=s)
    assert data is not None
    assert s.last_erxudp_raw_line == target


def test_read_erxudp_without_diag_state_does_not_crash(monkeypatch):
    lines = ["ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D 1 0 0001 1081"]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    class _FakeFd(object):
        def fileno(self):
            return -1

    data = mb.read_erxudp(_FakeFd(), timeout=2)
    assert data is not None  # back-compat preserved
