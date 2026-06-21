"""spec 014: extract ECHONET Lite TID from ERXUDP payload (pure helper)."""
import mqtt_bridge as mb


def test_extract_el_tid_normal():
    payload = bytearray(b"\x10\x81\x12\x34\x05\xff\x01\x02\x88\x01\x72\x01")
    assert mb.extract_el_tid(payload) == 0x1234


def test_extract_el_tid_zero_tid():
    payload = bytearray(b"\x10\x81\x00\x00\x05\xff\x01\x02\x88\x01\x72\x01")
    assert mb.extract_el_tid(payload) == 0


def test_extract_el_tid_short_payload_returns_none():
    assert mb.extract_el_tid(bytearray(b"\x10\x81\x12")) is None
    assert mb.extract_el_tid(bytearray()) is None
