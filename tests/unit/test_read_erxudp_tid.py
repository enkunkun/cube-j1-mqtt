"""spec 014: read_erxudp expected_tid discards mismatched frames.

Uses the injectable `readline` parameter so we can drive ERXUDP frame
sequences from a deterministic queue instead of a serial fd.
"""
import binascii
import time
import mqtt_bridge as mb


def _erxudp_line_for(tid, epc=0xE7, edt=b"\x00\x00\x01\xf4"):
    """Build a fake ERXUDP serial line whose ECHONET Lite TID matches *tid*.

    Wraps the canonical BP35CX/BP35C2 ERXUDP framing: addressing fields are
    placeholders since classify_sk_line only reads the trailing payload.
    """
    payload = bytearray()
    payload += b"\x10\x81"                                    # EHD1, EHD2
    payload += bytes(bytearray([tid >> 8, tid & 0xFF]))       # TID
    payload += b"\x02\x88\x01"                                # SEOJ
    payload += b"\x05\xff\x01"                                # DEOJ
    payload += b"\x72\x01"                                    # ESV=Get_Res, OPC=1
    payload += bytes(bytearray([epc, len(edt)]))              # EPC, PDC
    payload += edt                                            # EDT
    hex_payload = binascii.hexlify(payload).decode("ascii").upper()
    # 9-field ERXUDP header (binary mode disabled). content goes at end.
    return ("ERXUDP FE80:0000:0000:0000:0000:0000:0000:0001 "
            "FE80:0000:0000:0000:0000:0000:0000:0002 0E1A 0E1A "
            "001D129012345678 1 0 {} {}".format(len(payload), hex_payload))


def _make_readline(lines):
    """Closure that returns queued lines, then None forever (timeout)."""
    queue = list(lines)

    def readline(fd, timeout=None):
        if queue:
            return queue.pop(0)
        return None

    return readline


def test_read_erxudp_no_expected_tid_accepts_any_frame():
    """既存 caller (expected_tid を渡さない) は挙動が変わらない."""
    line = _erxudp_line_for(0x1234)
    readline = _make_readline([line])
    payload = mb.read_erxudp(fd=None, timeout=1, readline=readline)
    assert payload is not None
    assert mb.extract_el_tid(payload) == 0x1234


def test_read_erxudp_expected_tid_match_returns_payload():
    line = _erxudp_line_for(0x00AB)
    readline = _make_readline([line])
    payload = mb.read_erxudp(fd=None, timeout=1, expected_tid=0x00AB,
                             readline=readline)
    assert payload is not None
    assert mb.extract_el_tid(payload) == 0x00AB


def test_read_erxudp_expected_tid_mismatch_discards_then_takes_matching():
    """不一致 frame の後に一致 frame が来たら一致のほうを返す."""
    stale = _erxudp_line_for(0x0010)
    fresh = _erxudp_line_for(0x0011)
    readline = _make_readline([stale, fresh])
    payload = mb.read_erxudp(fd=None, timeout=1, expected_tid=0x0011,
                             readline=readline)
    assert payload is not None
    assert mb.extract_el_tid(payload) == 0x0011


def test_read_erxudp_expected_tid_mismatch_increments_diag_counter():
    diag = mb.DiagState(start_time=time.time(), version="t")
    stale = _erxudp_line_for(0x0010)
    fresh = _erxudp_line_for(0x0011)
    readline = _make_readline([stale, fresh])
    mb.read_erxudp(fd=None, timeout=1, diag_state=diag,
                   expected_tid=0x0011, readline=readline)
    assert diag.erxudp_tid_mismatch_total == 1


# ---------------------------------------------------------------------------
# spec 017: EVENT 24/29 dispatch to on_wisun_pana_fail
# ---------------------------------------------------------------------------


class _FakeDiagState(object):
    """Records dispatch calls for spec 017 EVENT 24/29 routing tests."""

    def __init__(self):
        self.sk_event_calls = []
        self.pana_fail_calls = []

    def on_sk_event(self, value):
        self.sk_event_calls.append(value)

    def on_wisun_pana_fail(self, value):
        self.pana_fail_calls.append(value)

    # Stubs for other paths read_erxudp may touch.
    def on_sk_error(self, value):
        pass

    def on_erxudp_raw(self, line):
        pass

    def on_erxudp_tid_mismatch(self):
        pass


def test_read_erxudp_event_24_calls_on_wisun_pana_fail():
    """spec 017: PANA fail → pending flag via on_wisun_pana_fail, NOT on_sk_event."""
    fake = _FakeDiagState()
    readline = _make_readline(["EVENT 24 FE80:0000:0000:0000:0000:0000:0000:0001"])
    mb.read_erxudp(fd=None, timeout=1, diag_state=fake, readline=readline)
    assert fake.pana_fail_calls == ["24"]
    assert fake.sk_event_calls == []


def test_read_erxudp_event_29_calls_on_wisun_pana_fail():
    fake = _FakeDiagState()
    readline = _make_readline(["EVENT 29 FE80:0000:0000:0000:0000:0000:0000:0001"])
    mb.read_erxudp(fd=None, timeout=1, diag_state=fake, readline=readline)
    assert fake.pana_fail_calls == ["29"]
    assert fake.sk_event_calls == []


def test_read_erxudp_event_22_still_calls_on_sk_event():
    """spec 017 互換: non-PANA EVENT goes through the existing path."""
    fake = _FakeDiagState()
    readline = _make_readline(["EVENT 22 FE80:0000:0000:0000:0000:0000:0000:0001"])
    mb.read_erxudp(fd=None, timeout=1, diag_state=fake, readline=readline)
    assert fake.sk_event_calls == ["22"]
    assert fake.pana_fail_calls == []
