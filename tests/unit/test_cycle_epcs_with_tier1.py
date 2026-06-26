"""spec 033 (= spec 011 E): cycle_epcs_with_tier1 pure helper.

全 cycle で tier1 EPCs を含めて OPC batch、 tier2/3/4 cycle で「tier1 + tier 固有」
を 1 frame まとめ送信。 mismatch 発火時 100% spec 028 backfill 対象化、
[[feedback-cycle-counter-reconnect-tier4]] 構造的問題 (= reconnect 直後 cycle 0
= tier4 で必ず skip) も解決。

dig A 決定: 未知 tier (= 万一 decide_epc_tier が fallback 返した場合) も
tier1 path にマージで重複 EPC 送信回避 (= ECHONET Lite 重複 EPC 仕様グレー)。
"""
import mqtt_bridge as mb


def test_tier1_returns_tier1_epcs_only():
    """tier1 cycle: tier1 EPCs (= [0xE7, 0xE8]) のみ、 重複なし."""
    assert mb.cycle_epcs_with_tier1("tier1") == [0xE7, 0xE8]


def test_tier2_returns_tier1_plus_tier2_epcs():
    """tier2 cycle: tier1 [0xE7, 0xE8] + tier2 [0xE0, 0xE3] の OPC=4 batch."""
    assert mb.cycle_epcs_with_tier1("tier2") == [0xE7, 0xE8, 0xE0, 0xE3]


def test_tier3_returns_tier1_plus_tier3_epcs():
    """tier3 cycle: tier1 + tier3 [0xD3, 0xE1] (= 係数/単位) の OPC=4 batch."""
    assert mb.cycle_epcs_with_tier1("tier3") == [0xE7, 0xE8, 0xD3, 0xE1]


def test_tier4_returns_tier1_plus_tier4_epcs():
    """tier4 cycle: tier1 + tier4 [0xEA, 0xEB] (= 定時積算) の OPC=4 batch.
    reconnect 直後 cycle 0 = tier4 の構造的問題 ([[feedback-cycle-counter-reconnect-tier4]])
    がこの batch で解消、 mismatch frame に power_w が必ず含まれる。"""
    assert mb.cycle_epcs_with_tier1("tier4") == [0xE7, 0xE8, 0xEA, 0xEB]


def test_unknown_tier_returns_tier1_only_no_duplication():
    """dig A 決定: 未知 tier (= 万一 decide_epc_tier が fallback 返した場合)
    も tier1 path にマージで重複 EPC 送信回避 (= ECHONET Lite 重複 EPC 仕様
    グレー、 BP35CX/メーター動作未検証 risk 回避)."""
    assert mb.cycle_epcs_with_tier1("unknown") == [0xE7, 0xE8]
    assert mb.cycle_epcs_with_tier1("") == [0xE7, 0xE8]
    assert mb.cycle_epcs_with_tier1(None) == [0xE7, 0xE8]


def test_returns_list_not_tuple():
    """既存 list 慣習保護: send_history.record や send_el_get が list 想定で
    動作、 mutable 操作も可能なように list 型を返す (= TIER1_EPCS の参照
    そのものでなく copy を返す)."""
    result = mb.cycle_epcs_with_tier1("tier2")
    assert isinstance(result, list)
    # mutate しても TIER1_EPCS 元定数に影響しない
    result.append(0xFF)
    assert mb.cycle_epcs_with_tier1("tier1") == [0xE7, 0xE8]
