"""EPC tier rotation (spec 011 C)."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# decide_epc_tier
# ---------------------------------------------------------------------------

def test_decide_epc_tier_zero_is_tier4():
    """spec 018: Cycle 0 (起動直後) は tier4 (定時積算電力量) を最優先で取得。
    tier4_every=30 と tier3_every=60 両方マッチするが tier4 が優先 (起動直後
    の直近 30 分境界値を逃さない)。"""
    assert mb.decide_epc_tier(cycle_number=0) == "tier4"


def test_decide_epc_tier_1_to_4_are_tier1():
    for i in range(1, 5):
        assert mb.decide_epc_tier(cycle_number=i) == "tier1"


def test_decide_epc_tier_5_is_tier2():
    """5 cycle に 1 回 tier2 (積算電力量)."""
    assert mb.decide_epc_tier(cycle_number=5) == "tier2"


def test_decide_epc_tier_10_is_tier2():
    assert mb.decide_epc_tier(cycle_number=10) == "tier2"


def test_decide_epc_tier_60_is_tier4():
    """spec 018: 60 cycle (= 1 時間) は tier4 (定時積算電力量) 優先。
    tier3 (係数) は cycle 120 で取得。 tier4 を取り損ねると 30 分粒度の
    累積電力量データが欠落するため、 ほぼ静的な tier3 より優先される。"""
    assert mb.decide_epc_tier(cycle_number=60) == "tier4"


def test_decide_epc_tier_30_is_tier4():
    """spec 018: tier4 default every=30 → cycle 30 で発火。"""
    assert mb.decide_epc_tier(cycle_number=30) == "tier4"


def test_decide_epc_tier_tier4_disabled_when_every_zero():
    """spec 018: tier4_every=0 で機能無効化、 既存挙動互換。"""
    # cycle 31 はどの倍数にも当てはまらず tier1 (cycle 30 は tier2 倍数で衝突)
    assert mb.decide_epc_tier(cycle_number=31, tier4_every=0) == "tier1"
    # cycle 60 は tier4 無効でも tier3 (元の挙動、 spec 011 C 互換)
    assert mb.decide_epc_tier(cycle_number=60, tier4_every=0) == "tier3"
    # cycle 0 も tier4 無効で tier3 (元の挙動互換)
    assert mb.decide_epc_tier(cycle_number=0, tier4_every=0) == "tier3"


def test_decide_epc_tier_tier4_wins_over_tier3_when_both_match():
    """spec 018 優先順: tier4 > tier3 > tier2 > tier1."""
    # cycle 60: tier3 (60%60==0) と tier4 (60%30==0) 両方マッチ → tier4
    assert mb.decide_epc_tier(
        cycle_number=60, tier2_every=5, tier3_every=60, tier4_every=30) == "tier4"


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


def test_tier4_includes_cumulative_energy_fixed():
    """spec 018: tier4 (定時積算電力量 fwd/rev)."""
    assert 0xEA in mb.TIER4_EPCS  # 定時積算 forward
    assert 0xEB in mb.TIER4_EPCS  # 定時積算 reverse


# ---------------------------------------------------------------------------
# epcs_for_tier helper
# ---------------------------------------------------------------------------

def test_epcs_for_tier_returns_correct_list():
    assert mb.epcs_for_tier("tier1") == mb.TIER1_EPCS
    assert mb.epcs_for_tier("tier2") == mb.TIER2_EPCS
    assert mb.epcs_for_tier("tier3") == mb.TIER3_EPCS
    assert mb.epcs_for_tier("tier4") == mb.TIER4_EPCS  # spec 018


def test_epcs_for_tier_unknown_falls_back_to_tier1():
    assert mb.epcs_for_tier("garbage") == mb.TIER1_EPCS
