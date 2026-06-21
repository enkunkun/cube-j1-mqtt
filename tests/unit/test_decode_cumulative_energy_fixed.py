"""spec 018: decode_cumulative_energy_fixed pure helper.

Fixture bytes are from a real probe of the meter on 2026-06-22:

    EPC 0xEA payload (11 bytes): 07EA 06 16 08 1E 00 0000 07D3 6D
      year  = 0x07EA  = 2026
      month = 0x06    = 6
      day   = 0x16    = 22
      hour  = 0x08    = 8
      min   = 0x1E    = 30  (snapped to 30-min boundary)
      sec   = 0x00    = 0
      raw   = 0x0007D36D = 512877  (× coefficient × unit → kWh)

Meter clock confirmed as JST (matched device wall clock JST 08:31 ≈ meter
08:30 snap). decoder appends `+09:00` suffix accordingly.
"""
import mqtt_bridge as mb


REAL_METER_EA = bytearray(
    b"\x07\xEA\x06\x16\x08\x1E\x00\x00\x07\xD3\x6D")  # exactly 11 bytes


def test_decodes_real_meter_payload_with_jst_suffix():
    result = mb.decode_cumulative_energy_fixed(REAL_METER_EA)
    assert result == ("2026-06-22T08:30:00+09:00", 512877)


def test_returns_none_for_short_payload():
    assert mb.decode_cumulative_energy_fixed(bytearray(b"\x07\xEA")) is None
    assert mb.decode_cumulative_energy_fixed(bytearray()) is None
    assert mb.decode_cumulative_energy_fixed(
        bytearray(b"\x07\xEA\x06\x16\x08\x1E\x00\x00\x07\xD3")) is None  # 10 bytes


def test_returns_none_for_year_less_than_2000():
    # year=0 (meter clock not set)
    assert mb.decode_cumulative_energy_fixed(
        bytearray(b"\x00\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00")) is None
    # year=1999
    assert mb.decode_cumulative_energy_fixed(
        bytearray(b"\x07\xCF\x06\x16\x08\x1E\x00\x00\x07\xD3\x6D")) is None


def test_accepts_year_2000_as_lower_boundary():
    # year=2000 = 0x07D0
    result = mb.decode_cumulative_energy_fixed(
        bytearray(b"\x07\xD0\x01\x01\x00\x00\x00\x00\x00\x00\x00"))
    assert result == ("2000-01-01T00:00:00+09:00", 0)


def test_handles_extreme_uint32_value():
    # raw = 0xFFFFFFFF = 4294967295 (uint32 max)
    result = mb.decode_cumulative_energy_fixed(
        bytearray(b"\x07\xEA\x06\x16\x08\x1E\x00\xFF\xFF\xFF\xFF"))
    assert result == ("2026-06-22T08:30:00+09:00", 4294967295)
