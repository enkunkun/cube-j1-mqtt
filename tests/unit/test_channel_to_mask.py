"""spec 034: channel_to_mask pure helper unit tests.

BP35CX channel 33-60 を SKSCAN 2 用 32-bit channel mask に変換する
helper。 ch33 = bit 0 (= 0x1), ch60 = bit 27 (= 0x08000000)。
"""

from mqtt_bridge import channel_to_mask


def test_channel_33_returns_bit_0():
    assert channel_to_mask(33) == 0x1


def test_channel_57_returns_known_mask():
    """lab メーター現 ch (= 57) → bit 24"""
    assert channel_to_mask(57) == 0x01000000


def test_channel_60_returns_max_bit():
    """ch60 = bit 27"""
    assert channel_to_mask(60) == 0x08000000


def test_int_string_arg_coerced():
    """diag_state.pan_channel が str 経路で来ても安全に変換"""
    assert channel_to_mask("57") == 0x01000000
