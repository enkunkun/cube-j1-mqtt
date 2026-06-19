"""Regression tests for the pure helpers already present in mqtt_bridge.py.

These pin down current behaviour before adding observability features, so we
catch any accidental change to ECHONET Lite frame handling or MQTT packet
encoding while refactoring around them.
"""
import struct

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# build_el_get
# ---------------------------------------------------------------------------

def test_build_el_get_returns_bytes_with_known_header_for_single_epc():
    frame = mb.build_el_get(tid=0x0001, epcs=[0xE7])
    # EHD (2) + TID (2) + SEOJ (3) + DEOJ (3) + ESV (1) + OPC (1) + EPC(1)+PDC(1)
    assert len(frame) == 14
    assert frame[:2] == b"\x10\x81"  # EHD1 EHD2
    assert frame[2:4] == b"\x00\x01"  # TID big-endian
    assert frame[4:7] == b"\x05\xFF\x01"  # SEOJ controller
    assert frame[7:10] == b"\x02\x88\x01"  # DEOJ smart meter
    assert frame[10:11] == b"\x62"  # ESV Get
    assert frame[11:12] == b"\x01"  # OPC = 1
    assert frame[12:14] == b"\xE7\x00"  # EPC, PDC=0


def test_build_el_get_with_multiple_epcs_encodes_each_with_pdc_zero():
    frame = mb.build_el_get(tid=0x1234, epcs=[0xD3, 0xE1, 0xE7])
    assert frame[2:4] == b"\x12\x34"
    assert frame[11] == 0x03  # OPC = 3
    assert frame[12:18] == b"\xD3\x00\xE1\x00\xE7\x00"


def test_build_el_get_masks_tid_to_16_bits():
    frame = mb.build_el_get(tid=0x1FFFF, epcs=[0xE7])
    assert frame[2:4] == b"\xFF\xFF"


# ---------------------------------------------------------------------------
# parse_el_response
# ---------------------------------------------------------------------------

def _build_response(esv, props):
    """Helper: build a minimal ECHONET Lite response frame."""
    f = bytearray(b"\x10\x81\x00\x01\x02\x88\x01\x05\xFF\x01")
    f.append(esv)
    f.append(len(props))
    for epc, val in props:
        f.append(epc)
        f.append(len(val))
        f += bytearray(val)
    return bytes(f)


def test_parse_el_response_returns_empty_for_short_frame():
    assert mb.parse_el_response(b"\x10\x81") == {}


def test_parse_el_response_handles_get_res_with_single_property():
    frame = _build_response(0x72, [(0xE7, b"\x00\x00\x01\x2C")])
    result = mb.parse_el_response(frame)
    assert 0xE7 in result
    assert bytes(result[0xE7]) == b"\x00\x00\x01\x2C"


def test_parse_el_response_handles_get_sna_too():
    frame = _build_response(0x52, [(0xE7, b"\x00\x00\x00\x00")])
    assert 0xE7 in mb.parse_el_response(frame)


def test_parse_el_response_ignores_unknown_esv():
    frame = _build_response(0x73, [(0xE7, b"\x00\x00\x00\x01")])
    assert mb.parse_el_response(frame) == {}


def test_parse_el_response_parses_all_properties_when_multiple():
    frame = _build_response(0x72, [
        (0xD3, b"\x00\x00\x00\x01"),
        (0xE1, b"\x02"),
        (0xE7, b"\x00\x00\x01\x90"),
    ])
    result = mb.parse_el_response(frame)
    assert set(result.keys()) == {0xD3, 0xE1, 0xE7}
    assert bytes(result[0xE1]) == b"\x02"


# ---------------------------------------------------------------------------
# decode_measurements + apply_energy_scale
# ---------------------------------------------------------------------------

def test_decode_measurements_extracts_instantaneous_power_signed():
    props = {0xE7: bytearray(struct.pack(">i", -123))}
    m = mb.decode_measurements(props)
    assert m["power_w"] == -123


def test_decode_measurements_extracts_currents_in_amperes_with_0_1A_resolution():
    # R = 15 (1.5 A), T = -8 (-0.8 A)
    props = {0xE8: bytearray(struct.pack(">hh", 15, -8))}
    m = mb.decode_measurements(props)
    assert m["current_r_a"] == 1.5
    assert m["current_t_a"] == -0.8


def test_decode_measurements_skips_invalid_current_value_0x7FFE_for_t_phase():
    """ECHONET Lite "未取得" sentinel for 単相2線式 / T 相未接続."""
    # R = 90 (9.0 A), T = 0x7FFE (32766, "not measured")
    props = {0xE8: bytearray(struct.pack(">hH", 90, 0x7FFE))}
    m = mb.decode_measurements(props)
    assert m["current_r_a"] == 9.0
    assert "current_t_a" not in m


def test_decode_measurements_skips_invalid_current_value_0x7FFE_for_r_phase():
    """Same sentinel may appear on R phase too."""
    props = {0xE8: bytearray(struct.pack(">Hh", 0x7FFE, 90))}
    m = mb.decode_measurements(props)
    assert "current_r_a" not in m
    assert m["current_t_a"] == 9.0


def test_decode_measurements_skips_reserved_current_value_0x7FFF():
    """0x7FFF is reserved per spec — also treat as no data."""
    props = {0xE8: bytearray(struct.pack(">HH", 0x7FFF, 0x7FFF))}
    m = mb.decode_measurements(props)
    assert "current_r_a" not in m
    assert "current_t_a" not in m


def test_decode_measurements_keeps_in_range_currents_including_negative():
    """Reverse current flow (solar export) yields negative values; those stay."""
    # R = -32765 (-3276.5 A, just inside valid range), T = -1 (-0.1 A)
    props = {0xE8: bytearray(struct.pack(">hh", -32765, -1))}
    m = mb.decode_measurements(props)
    assert m["current_r_a"] == -3276.5
    assert m["current_t_a"] == -0.1


def test_decode_measurements_extracts_coefficient_and_unit_kwh():
    props = {
        0xD3: bytearray(b"\x00\x00\x00\x02"),  # coeff = 2
        0xE1: bytearray(b"\x02"),               # unit = 0.01 kWh
    }
    m = mb.decode_measurements(props)
    assert m["coefficient"] == 2
    assert m["unit_kwh"] == 0.01


def test_apply_energy_scale_uses_inline_coeff_and_unit_when_present():
    measurements = {
        "coefficient": 2,
        "unit_kwh": 0.01,
        "energy_forward_raw": 100,
        "energy_reverse_raw": 5,
    }
    out = mb.apply_energy_scale(measurements, coeff=1, unit_kwh=1.0)
    assert out["energy_forward_kwh"] == 2.0  # 100 * 2 * 0.01
    assert out["energy_reverse_kwh"] == 0.1


def test_apply_energy_scale_falls_back_to_passed_values_when_absent():
    measurements = {"energy_forward_raw": 50}
    out = mb.apply_energy_scale(measurements, coeff=3, unit_kwh=0.1)
    assert out["energy_forward_kwh"] == 15.0


# ---------------------------------------------------------------------------
# MQTT helpers: _encode_remaining / _encode_str
# ---------------------------------------------------------------------------

def test_encode_remaining_single_byte_for_small_lengths():
    assert mb._encode_remaining(0) == b"\x00"
    assert mb._encode_remaining(127) == b"\x7f"


def test_encode_remaining_uses_continuation_bit_for_large_lengths():
    # 128 = 0x80 0x01 (per MQTT 3.1.1 variable-length encoding)
    assert mb._encode_remaining(128) == b"\x80\x01"
    # 16383 = 0xFF 0x7F (max 2-byte)
    assert mb._encode_remaining(16383) == b"\xff\x7f"
    # 16384 needs 3 bytes
    assert mb._encode_remaining(16384) == b"\x80\x80\x01"


def test_encode_str_prefixes_with_big_endian_length():
    out = mb._encode_str("ab")
    assert out == b"\x00\x02ab"


def test_encode_str_handles_utf8_multibyte():
    # "あ" is 3 bytes in UTF-8
    out = mb._encode_str("あ")
    assert out[:2] == b"\x00\x03"
    assert len(out) == 5
