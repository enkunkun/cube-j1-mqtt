"""spec 051: PANA session の計画的 refresh (= 受動 blackout → 計画 reconnect)."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# should_fire_proactive_rejoin: pure helper (FR-001)
# ---------------------------------------------------------------------------

def test_no_fire_before_first_session():
    """last_event_25_ts 未記録 (= 起動直後) は発火しない."""
    assert mb.should_fire_proactive_rejoin(
        last_event_25_ts=None, now=1000.0, after_sec=480,
        skrejoin_active=False) is False


def test_fires_after_threshold():
    assert mb.should_fire_proactive_rejoin(
        last_event_25_ts=1000.0, now=1481.0, after_sec=480,
        skrejoin_active=False) is True


def test_no_fire_within_threshold():
    assert mb.should_fire_proactive_rejoin(
        last_event_25_ts=1000.0, now=1480.0, after_sec=480,
        skrejoin_active=False) is False


def test_no_fire_while_skrejoin_active():
    """skrejoin_tick 実行中は二重発火しない."""
    assert mb.should_fire_proactive_rejoin(
        last_event_25_ts=1000.0, now=2000.0, after_sec=480,
        skrejoin_active=True) is False


# ---------------------------------------------------------------------------
# ProactiveSessionRefresh: 例外 class (FR-003)
# ---------------------------------------------------------------------------

def test_proactive_refresh_is_runtime_error():
    """既存 except 経路 (= Exception catch) を通りつつ isinstance で
    計画 refresh と識別できる (= backoff 0 branch の根拠)."""
    err = mb.ProactiveSessionRefresh("planned")
    assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# DiagState counter (FR-005)
# ---------------------------------------------------------------------------

def test_on_proactive_rejoin_increments_counter():
    st = mb.DiagState(start_time=1000.0, version="test")
    st.on_proactive_rejoin()
    assert st.snapshot(now=1010.0)["proactive_rejoin_total"] == 1


def test_proactive_counter_zero_in_snapshot():
    st = mb.DiagState(start_time=1000.0, version="test")
    assert st.snapshot(now=1010.0)["proactive_rejoin_total"] == 0


def test_diag_sensor_defs_includes_proactive_counter():
    sids = {sid for (sid, *_rest) in mb.DIAG_SENSOR_DEFS}
    assert "proactive_rejoin_total" in sids


# ---------------------------------------------------------------------------
# apply_defaults (FR-002/004)
# ---------------------------------------------------------------------------

def _defaults():
    return mb.apply_defaults({"br_id": "x", "br_pwd": "y", "mqtt_host": "z"})


def test_default_proactive_rejoin_enabled():
    assert _defaults()["proactive_rejoin_enabled"] is True


def test_default_proactive_rejoin_after_sec_is_480():
    """spec 040 hotfix の実測 tuning 値 (= reconnect 周期 11-12 分より
    3-4 分早い) を引き継ぐ."""
    assert _defaults()["proactive_rejoin_after_sec"] == 480


def test_default_skrejoin_tick_disabled():
    """spec 051 FR-004: SKREJOIN tick (= 94-100% event_24 reject、 tako
    合議 2026-07-01 で本番 OFF 推奨) を proactive が supersede."""
    assert _defaults()["skrejoin_tick_enabled"] is False
