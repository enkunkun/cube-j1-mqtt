"""EPC tier rotation (spec 011 C)."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# decide_epc_tier
# ---------------------------------------------------------------------------

def test_decide_epc_tier_zero_is_tier3():
    """Cycle 0 (起動直後) は tier3 (係数・単位) を最優先で取りに行く."""
    assert mb.decide_epc_tier(cycle_number=0) == "tier3"


def test_decide_epc_tier_1_to_4_are_tier1():
    for i in range(1, 5):
        assert mb.decide_epc_tier(cycle_number=i) == "tier1"


def test_decide_epc_tier_5_is_tier2():
    """5 cycle に 1 回 tier2 (積算電力量)."""
    assert mb.decide_epc_tier(cycle_number=5) == "tier2"


def test_decide_epc_tier_10_is_tier2():
    assert mb.decide_epc_tier(cycle_number=10) == "tier2"


def test_decide_epc_tier_60_is_tier3():
    """60 cycle = 1 時間に 1 回 tier3 (= tier2 の倍数だが tier3 優先)."""
    assert mb.decide_epc_tier(cycle_number=60) == "tier3"


def test_decide_epc_tier_custom_intervals():
    assert mb.decide_epc_tier(
        cycle_number=2, tier2_every=2, tier3_every=10) == "tier2"
    assert mb.decide_epc_tier(
        cycle_number=10, tier2_every=2, tier3_every=10) == "tier3"


# ---------------------------------------------------------------------------
# Tier EPC lists are exposed as module constants
# ---------------------------------------------------------------------------

def test_tier1_includes_power_and_current():
    """Tier1 はリアルタイム性が必要な瞬時電力 + 瞬時電流."""
    assert 0xE7 in mb.TIER1_EPCS  # 瞬時電力
    assert 0xE8 in mb.TIER1_EPCS  # 瞬時電流


def test_tier2_includes_cumulative_energy():
    assert 0xE0 in mb.TIER2_EPCS  # 積算電力量 forward
    assert 0xE3 in mb.TIER2_EPCS  # 積算電力量 reverse


def test_tier3_includes_coefficient_and_unit():
    assert 0xD3 in mb.TIER3_EPCS  # 係数
    assert 0xE1 in mb.TIER3_EPCS  # 単位


# ---------------------------------------------------------------------------
# epcs_for_tier helper
# ---------------------------------------------------------------------------

def test_epcs_for_tier_returns_correct_list():
    assert mb.epcs_for_tier("tier1") == mb.TIER1_EPCS
    assert mb.epcs_for_tier("tier2") == mb.TIER2_EPCS
    assert mb.epcs_for_tier("tier3") == mb.TIER3_EPCS


def test_epcs_for_tier_unknown_falls_back_to_tier1():
    assert mb.epcs_for_tier("garbage") == mb.TIER1_EPCS
