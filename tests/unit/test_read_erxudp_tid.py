"""spec 014: read_erxudp expected_tid discards mismatched frames.

Uses the injectable `readline` parameter so we can drive ERXUDP frame
sequences from a deterministic queue instead of a serial fd.
"""
import binascii
import time
import mqtt_bridge as mb


def _erxudp_line_for(tid, epc=0xE7, edt=b"\x00\x00\x01\xf4", esv=0x72):
    """Build a fake ERXUDP serial line whose ECHONET Lite TID matches *tid*.

    Wraps the canonical BP35CX/BP35C2 ERXUDP framing: addressing fields are
    placeholders since classify_sk_line only reads the trailing payload.
    """
    payload = bytearray()
    payload += b"\x10\x81"                                    # EHD1, EHD2
    payload += bytes(bytearray([tid >> 8, tid & 0xFF]))       # TID
    payload += b"\x02\x88\x01"                                # SEOJ
    payload += b"\x05\xff\x01"                                # DEOJ
    payload += bytes(bytearray([esv])) + b"\x01"              # ESV, OPC=1
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


# ---------------------------------------------------------------------------
# spec 047: rescue path が on_erxudp_rescued を発火する
# ---------------------------------------------------------------------------


def _diag_and_ring():
    diag = mb.DiagState(start_time=time.time(), version="t")
    ring = mb.SendHistoryRing(maxlen=16)
    return diag, ring


def test_rescue_ring_hit_fires_rescued_counters():
    """got_tid≠0 の正規 ring hit: esv=get_res / ring_hit / lt5s が inc."""
    diag, ring = _diag_and_ring()
    ring.record(0x0010, time.time() - 1.0, [0xE7])
    readline = _make_readline([_erxudp_line_for(0x0010)])
    payload = mb.read_erxudp(fd=None, timeout=1, diag_state=diag,
                             expected_tid=0x0011, readline=readline,
                             send_history=ring)
    assert payload is not None
    snap = diag.snapshot(now=time.time())
    assert snap["erxudp_rescued_esv_get_res_total"] == 1
    assert snap["erxudp_rescued_tid_ring_hit_total"] == 1
    assert snap["erxudp_rescued_tid_zero_total"] == 0
    assert snap["erxudp_rescued_lag_lt5s_total"] == 1


def test_rescue_tid_zero_fallback_fires_tid_zero_counter():
    """got_tid=0 は lookup_latest fallback 経由 → tid_zero が inc."""
    diag, ring = _diag_and_ring()
    ring.record(0x0011, time.time() - 0.5, [0xE7])
    readline = _make_readline([_erxudp_line_for(0x0000)])
    payload = mb.read_erxudp(fd=None, timeout=1, diag_state=diag,
                             expected_tid=0x0011, readline=readline,
                             send_history=ring)
    assert payload is not None
    snap = diag.snapshot(now=time.time())
    assert snap["erxudp_rescued_tid_zero_total"] == 1
    assert snap["erxudp_rescued_tid_ring_hit_total"] == 0


def test_rescue_inf_frame_classified_as_inf():
    """ESV=0x73 (自律通知 INF) の救済 frame は esv=inf に分類 (H2 検出)."""
    diag, ring = _diag_and_ring()
    ring.record(0x0011, time.time() - 0.5, [0xE7])
    readline = _make_readline([_erxudp_line_for(0x0000, esv=0x73)])
    mb.read_erxudp(fd=None, timeout=1, diag_state=diag,
                   expected_tid=0x0011, readline=readline,
                   send_history=ring)
    snap = diag.snapshot(now=time.time())
    assert snap["erxudp_rescued_esv_inf_total"] == 1
    assert snap["erxudp_rescued_esv_get_res_total"] == 0


def test_rescue_late_frame_falls_in_60to300s_bucket():
    """send_ts が 70s 前 → 60to300s bucket (H3 chain 検出)."""
    diag, ring = _diag_and_ring()
    ring.record(0x0010, time.time() - 70.0, [0xE7])
    readline = _make_readline([_erxudp_line_for(0x0010)])
    mb.read_erxudp(fd=None, timeout=1, diag_state=diag,
                   expected_tid=0x0011, readline=readline,
                   send_history=ring)
    snap = diag.snapshot(now=time.time())
    assert snap["erxudp_rescued_lag_60to300s_total"] == 1
