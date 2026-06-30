"""DiagState aggregates bridge diagnostics for MQTT publish.

Invariants from specs/001-bridge-observability/data-model.md:
- Counters are monotonically non-decreasing (no decrement methods).
- `snapshot(now)` returns a dict with None-valued attributes EXCLUDED so HA
  treats them as unknown rather than receiving empty string.
- `uptime_seconds` is non-negative even if `now < start_time`.
- `snapshot` key order is deterministic.
"""
import mqtt_bridge as mb


def make_state(now=1_000_000.0, version="1.0.0+test"):
    return mb.DiagState(start_time=now, version=version)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_snapshot_includes_zero_counters_uptime_and_version():
    """Counters default to 0 and are published with the baseline. None-valued
    timestamps and PAN info are excluded so HA keeps them "unknown" until the
    first real event.
    """
    state = make_state(now=1000.0)
    snap = state.snapshot(now=1042.0)
    assert snap == {
        "scan_retries_total": 0,
        "wisun_reconnects_total": 0,
        "mqtt_reconnects_total": 0,
        "erxudp_timeouts_total": 0,
        # spec 011 retry counters also baseline at 0
        "erxudp_intra_cycle_retries_total": 0,
        "erxudp_recovered_by_retry_total": 0,
        # spec 012 counters also baseline at 0
        "erxudp_tid_mismatch_total": 0,
        "noise_adaptive_skips_total": 0,
        # spec 016 discovery counter also baselines at 0; last_ts is None
        # so the snapshot omits it (verified separately).
        "discovery_republish_total": 0,
        # spec 017 serial reopen counter also baselines at 0
        # (consecutive_wisun_connect_failures / pending_wisun_rejoin are
        # internal-only and intentionally not in the snapshot schema).
        "serial_reopen_total": 0,
        # spec 022: realtime burst mode counters + mode gauge.
        # realtime_effective_interval_seconds is None at boot so omitted.
        "realtime_burst_started_total": 0,
        "realtime_burst_completed_total": 0,
        "realtime_burst_aborted_total": 0,
        "realtime_mode_current": "off",
        # spec 020: TID mismatch late publish recovery counter.
        "erxudp_recovered_from_mismatch_total": 0,
        # spec 028: 瞬時電力 recovery backfill counter (= 別 channel で 0xE7 救済).
        "power_w_recovered_backfill_total": 0,
        # spec 029: 累積系 (energy_*_kwh) recovery backfill counter.
        "cumulative_recovered_backfill_total": 0,
        # spec 034: SKSCAN channel mask cache counters.
        "wisun_reconnect_short_scan_total": 0,
        "wisun_reconnect_fallback_full_scan_total": 0,
        # spec 035: SKLL64 cached + SKJOIN 直行 counters.
        "wisun_reconnect_cached_skjoin_total": 0,
        "wisun_reconnect_cached_skjoin_fallback_total": 0,
        # spec 037: WOPT FLASH 書込み寿命対策 (= ROPT で skip カウント)。
        # zero でも常時 publish (= skip が機能しているかの観測点)。
        "wopt_write_skipped_total": 0,
        "wopt_write_total": 0,
        # spec 042: SKADDNBR 発行 counter (= 0 でも常時 publish)。
        "skaddnbr_total": 0,
        "skaddnbr_fail_total": 0,
        # spec 038 Phase 2: EVENT 21 PARAM 別 counter (= 0 でも常時 publish)。
        "sk_event_21_param0_total": 0,
        "sk_event_21_param1_total": 0,
        "sk_event_21_param2_total": 0,
        # spec 040 Phase 2a: S17=0 設定 counter (= 0 でも常時 publish)。
        # last_event_25_seconds は last_event_25_ts=None で omit (= 別 test で検証済)。
        "s17_off_total": 0,
        "uptime_seconds": 42,
        "version": "1.0.0+test",
    }


def test_first_scan_retry_increments_counter_from_zero():
    state = make_state()
    state.on_scan_retry()
    snap = state.snapshot(now=state.start_time)
    assert snap["scan_retries_total"] == 1


# ---------------------------------------------------------------------------
# Counter updates
# ---------------------------------------------------------------------------

def test_on_scan_retry_increments_counter():
    state = make_state()
    state.on_scan_retry()
    state.on_scan_retry()
    state.on_scan_retry()
    assert state.snapshot(now=state.start_time)["scan_retries_total"] == 3


def test_on_wisun_reconnect_increments_counter():
    state = make_state()
    state.on_wisun_reconnect()
    assert state.snapshot(now=state.start_time)["wisun_reconnects_total"] == 1


def test_on_mqtt_reconnect_increments_counter():
    state = make_state()
    for _ in range(5):
        state.on_mqtt_reconnect()
    assert state.snapshot(now=state.start_time)["mqtt_reconnects_total"] == 5


def test_on_erxudp_timeout_increments_counter():
    state = make_state()
    state.on_erxudp_timeout()
    assert state.snapshot(now=state.start_time)["erxudp_timeouts_total"] == 1


def test_on_erxudp_tid_mismatch_increments_counter():
    """spec 012: ERXUDP TID 不一致は専用カウンタで観測する。"""
    state = make_state()
    state.on_erxudp_tid_mismatch()
    state.on_erxudp_tid_mismatch()
    snap = state.snapshot(now=state.start_time)
    assert snap["erxudp_tid_mismatch_total"] == 2


# ---------------------------------------------------------------------------
# spec 016: HA discovery auto-republish
# ---------------------------------------------------------------------------

def test_on_mqtt_reconnect_also_sets_pending_discovery_republish():
    state = make_state()
    assert state.pending_discovery_republish is False
    state.on_mqtt_reconnect()
    assert state.pending_discovery_republish is True


def test_on_discovery_republish_increments_counter_clears_pending_updates_ts():
    state = make_state()
    state.on_mqtt_reconnect()  # set pending
    state.on_discovery_republish(now=1_700_000_000.0)
    assert state.discovery_republish_total == 1
    assert state.pending_discovery_republish is False
    assert state.last_discovery_publish_ts == 1_700_000_000.0


def test_mark_initial_discovery_publish_seeds_ts_without_incrementing_counter():
    """spec 016 Round 1 決定 2: startup publish は ts だけ初期化、 counter は 0 のまま。"""
    state = make_state()
    state.on_mqtt_reconnect()  # would set pending=True
    state.mark_initial_discovery_publish(now=1_700_000_000.0)
    assert state.discovery_republish_total == 0
    assert state.pending_discovery_republish is False
    assert state.last_discovery_publish_ts == 1_700_000_000.0


def test_snapshot_includes_last_discovery_publish_ts_as_iso_when_set():
    state = make_state()
    state.on_discovery_republish(now=1_700_000_000.0)
    snap = state.snapshot(now=1_700_000_000.0)
    assert snap["last_discovery_publish_ts"] == "2023-11-14T22:13:20Z"
    assert snap["discovery_republish_total"] == 1


def test_snapshot_omits_last_discovery_publish_ts_when_none():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert "last_discovery_publish_ts" not in snap
    assert snap["discovery_republish_total"] == 0


# ---------------------------------------------------------------------------
# spec 017: Wi-SUN rejoin observability
# ---------------------------------------------------------------------------


def test_consecutive_wisun_connect_failures_starts_at_zero():
    state = make_state()
    assert state.consecutive_wisun_connect_failures == 0


def test_pending_wisun_rejoin_starts_false():
    state = make_state()
    assert state.pending_wisun_rejoin is False


def test_on_serial_reopen_increments_counter():
    state = make_state()
    state.on_serial_reopen()
    state.on_serial_reopen()
    snap = state.snapshot(now=state.start_time)
    assert snap["serial_reopen_total"] == 2


def test_on_wisun_pana_fail_sets_pending_flag_and_increments_sk_event_counter():
    state = make_state()
    state.on_wisun_pana_fail("24")
    assert state.pending_wisun_rejoin is True
    # on_sk_event "24" should also bump the SK EVENT counter for 24
    assert state.sk_event_counts.get("24") == 1


def test_snapshot_includes_serial_reopen_total_at_zero():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert snap["serial_reopen_total"] == 0


def test_counters_are_monotonically_non_decreasing():
    """No method should ever cause a counter to drop."""
    state = make_state()
    state.on_scan_retry()
    state.on_mqtt_reconnect()
    state.on_wisun_reconnect()
    state.on_erxudp_timeout()
    snap1 = state.snapshot(now=state.start_time)
    # Calling snapshot must not modify counters.
    snap2 = state.snapshot(now=state.start_time + 60)
    for key in (
        "scan_retries_total",
        "mqtt_reconnects_total",
        "wisun_reconnects_total",
        "erxudp_timeouts_total",
    ):
        assert snap2[key] >= snap1[key]


# ---------------------------------------------------------------------------
# Timestamp updates
# ---------------------------------------------------------------------------

def test_on_poll_success_records_iso_timestamp():
    state = make_state()
    state.on_poll_success(now=1_700_000_000.0)  # 2023-11-14T22:13:20Z
    assert state.snapshot(now=1_700_000_000.0)["last_poll_success_ts"] == \
        "2023-11-14T22:13:20Z"


def test_on_poll_failure_records_iso_timestamp_independently():
    state = make_state()
    state.on_poll_failure(now=1_700_000_100.0)
    snap = state.snapshot(now=1_700_000_100.0)
    assert snap["last_poll_failure_ts"] == "2023-11-14T22:15:00Z"
    assert "last_poll_success_ts" not in snap


def test_last_poll_success_and_failure_coexist_independently():
    state = make_state()
    state.on_poll_success(now=1_700_000_000.0)
    state.on_poll_failure(now=1_700_000_500.0)
    snap = state.snapshot(now=1_700_000_500.0)
    assert snap["last_poll_success_ts"] == "2023-11-14T22:13:20Z"
    assert snap["last_poll_failure_ts"] == "2023-11-14T22:21:40Z"


# ---------------------------------------------------------------------------
# PAN info (LQI, channel)
# ---------------------------------------------------------------------------

def test_on_wisun_joined_decodes_hex_lqi_and_channel():
    state = make_state()
    state.on_wisun_joined({"LQI": "C0", "Channel": "21", "Pan ID": "8888", "Addr": "001D"})
    snap = state.snapshot(now=state.start_time)
    assert snap["lqi"] == 0xC0
    assert snap["pan_channel"] == 0x21


def test_on_wisun_joined_overwrites_previous_values_on_re_scan():
    state = make_state()
    state.on_wisun_joined({"LQI": "10", "Channel": "21"})
    state.on_wisun_joined({"LQI": "FF", "Channel": "22"})
    snap = state.snapshot(now=state.start_time)
    assert snap["lqi"] == 0xFF
    assert snap["pan_channel"] == 0x22


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------

def test_uptime_is_truncated_seconds_since_start():
    state = make_state(now=1000.0)
    assert state.snapshot(now=1000.7)["uptime_seconds"] == 0
    assert state.snapshot(now=1042.9)["uptime_seconds"] == 42


def test_uptime_clamps_to_zero_if_clock_jumps_backwards():
    state = make_state(now=2000.0)
    assert state.snapshot(now=1500.0)["uptime_seconds"] == 0


# ---------------------------------------------------------------------------
# Snapshot omits unknown (None) fields
# ---------------------------------------------------------------------------

def test_snapshot_omits_none_timestamps_and_pan_info():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert "last_poll_success_ts" not in snap
    assert "last_poll_failure_ts" not in snap
    assert "lqi" not in snap
    assert "pan_channel" not in snap


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def test_snapshot_always_includes_version_string():
    state = make_state(version="2.5.0+deadbee")
    snap = state.snapshot(now=state.start_time)
    assert snap["version"] == "2.5.0+deadbee"


# ---------------------------------------------------------------------------
# Auto-recovery: consecutive ERXUDP timeouts
# ---------------------------------------------------------------------------

def test_consecutive_erxudp_timeouts_starts_at_zero():
    state = make_state()
    assert state.consecutive_erxudp_timeouts == 0


def test_consecutive_erxudp_timeouts_increments_on_each_timeout():
    state = make_state()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    assert state.consecutive_erxudp_timeouts == 2


def test_consecutive_erxudp_timeouts_resets_on_poll_success():
    state = make_state()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    state.on_poll_success(now=1.0)
    assert state.consecutive_erxudp_timeouts == 0


def test_consecutive_does_not_reset_on_poll_failure_path():
    """`on_poll_failure` updates the failure timestamp but the consecutive
    counter is only reset by a real success — multiple failure events keep
    the counter rising."""
    state = make_state()
    state.on_erxudp_timeout()
    state.on_poll_failure(now=1.0)
    state.on_erxudp_timeout()
    state.on_poll_failure(now=2.0)
    assert state.consecutive_erxudp_timeouts == 2


def test_total_erxudp_counter_continues_to_grow_after_reset():
    state = make_state()
    state.on_erxudp_timeout()
    state.on_erxudp_timeout()
    state.on_poll_success(now=1.0)
    state.on_erxudp_timeout()
    snap = state.snapshot(now=state.start_time)
    assert snap["erxudp_timeouts_total"] == 3
    assert state.consecutive_erxudp_timeouts == 1


# ---------------------------------------------------------------------------
# spec 022: realtime burst mode
# ---------------------------------------------------------------------------

def test_on_realtime_burst_started_increments():
    state = make_state()
    state.on_realtime_burst_started()
    snap = state.snapshot(now=state.start_time)
    assert snap["realtime_burst_started_total"] == 1


def test_set_realtime_state_updates_mode_and_interval_gauge():
    state = make_state()
    state.set_realtime_state("burst", 5)
    snap = state.snapshot(now=state.start_time)
    assert snap["realtime_mode_current"] == "burst"
    assert snap["realtime_effective_interval_seconds"] == 5


# ---------------------------------------------------------------------------
# spec 020: TID mismatch late publish recovery
# ---------------------------------------------------------------------------

def test_on_erxudp_recovered_from_mismatch_increments():
    state = make_state()
    state.on_erxudp_recovered_from_mismatch(60.0)
    snap = state.snapshot(now=state.start_time)
    assert snap["erxudp_recovered_from_mismatch_total"] == 1


def test_recovered_lag_percentiles_omitted_when_empty():
    """deque 空時 percentile key は snapshot に含めない (= HA で「unknown」 維持)."""
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert "erxudp_recovered_lag_p50" not in snap
    assert "erxudp_recovered_lag_p95" not in snap
    assert "erxudp_recovered_lag_max" not in snap


def test_recovered_lag_percentiles_emitted_when_filled():
    state = make_state()
    for lag in [60.0, 120.0, 180.0, 240.0, 300.0]:
        state.on_erxudp_recovered_from_mismatch(lag)
    snap = state.snapshot(now=state.start_time)
    assert "erxudp_recovered_lag_p50" in snap
    assert "erxudp_recovered_lag_p95" in snap
    assert "erxudp_recovered_lag_max" in snap
    assert snap["erxudp_recovered_lag_max"] == 300


# ---------------------------------------------------------------------------
# spec 028: power_w recovery backfill counter (= 直接 += pattern、 on_* method なし)
# ---------------------------------------------------------------------------

def test_power_w_recovered_backfill_total_reflected_in_snapshot():
    """publish_recovery_backfill が attribute を += する pattern を snapshot で観測."""
    state = make_state()
    state.power_w_recovered_backfill_total += 1
    state.power_w_recovered_backfill_total += 1
    snap = state.snapshot(now=state.start_time)
    assert snap["power_w_recovered_backfill_total"] == 2


def test_cumulative_recovered_backfill_total_reflected_in_snapshot():
    """spec 029: 累積系 counter も同 pattern で snapshot に反映."""
    state = make_state()
    state.cumulative_recovered_backfill_total += 1
    state.cumulative_recovered_backfill_total += 1
    state.cumulative_recovered_backfill_total += 1
    snap = state.snapshot(now=state.start_time)
    assert snap["cumulative_recovered_backfill_total"] == 3


# ---------------------------------------------------------------------------
# spec 034: SKSCAN channel mask cache counters
# ---------------------------------------------------------------------------

def test_wisun_reconnect_short_scan_total_baseline_zero():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert snap["wisun_reconnect_short_scan_total"] == 0


def test_on_wisun_reconnect_short_scan_increments():
    state = make_state()
    state.on_wisun_reconnect_short_scan()
    state.on_wisun_reconnect_short_scan()
    snap = state.snapshot(now=state.start_time)
    assert snap["wisun_reconnect_short_scan_total"] == 2


def test_wisun_reconnect_fallback_full_scan_total_baseline_zero():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert snap["wisun_reconnect_fallback_full_scan_total"] == 0


def test_on_wisun_reconnect_fallback_full_scan_increments():
    state = make_state()
    state.on_wisun_reconnect_fallback_full_scan()
    snap = state.snapshot(now=state.start_time)
    assert snap["wisun_reconnect_fallback_full_scan_total"] == 1


def test_snapshot_includes_short_scan_total():
    """spec 034: MQTT publish 経路保護 (feedback-diag-sensor-defs-publish)."""
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert "wisun_reconnect_short_scan_total" in snap


def test_snapshot_includes_fallback_full_scan_total():
    state = make_state()
    snap = state.snapshot(now=state.start_time)
    assert "wisun_reconnect_fallback_full_scan_total" in snap


# ---------------------------------------------------------------------------
# spec 035: SKLL64 cached + SKJOIN 直行 — cache attrs + counters
# ---------------------------------------------------------------------------

def test_pan_id_starts_none():
    state = make_state()
    assert state.pan_id is None


def test_mac_starts_none():
    state = make_state()
    assert state.mac is None


def test_ipv6_starts_none():
    state = make_state()
    assert state.ipv6 is None


def test_consecutive_skjoin_failures_starts_zero():
    state = make_state()
    assert state.consecutive_skjoin_failures == 0


def test_on_skll64_sets_ipv6():
    state = make_state()
    state.on_skll64("FE80:0000:0000:0000:021C:6400:0B03:D4E3")
    assert state.ipv6 == "FE80:0000:0000:0000:021C:6400:0B03:D4E3"


def test_on_skjoin_failure_increments():
    state = make_state()
    state.on_skjoin_failure(invalidate_threshold=2)
    assert state.consecutive_skjoin_failures == 1


def test_on_skjoin_failure_keeps_cache_below_threshold():
    """1 fail (= threshold 2 未満) なら cache 残る."""
    state = make_state()
    state.pan_channel = 57
    state.pan_id = "D4E3"
    state.mac = "001C64000B03D4E3"
    state.ipv6 = "FE80:0000:0000:0000:021C:6400:0B03:D4E3"
    state.on_skjoin_failure(invalidate_threshold=2)
    assert state.pan_channel == 57
    assert state.pan_id == "D4E3"
    assert state.mac == "001C64000B03D4E3"
    assert state.ipv6 == "FE80:0000:0000:0000:021C:6400:0B03:D4E3"


def test_on_skjoin_failure_invalidates_cache_at_threshold():
    """2 fail 連続で cache 全 None になる."""
    state = make_state()
    state.pan_channel = 57
    state.pan_id = "D4E3"
    state.mac = "001C64000B03D4E3"
    state.ipv6 = "FE80::1"
    state.on_skjoin_failure(invalidate_threshold=2)
    state.on_skjoin_failure(invalidate_threshold=2)
    assert state.pan_channel is None
    assert state.pan_id is None
    assert state.mac is None
    assert state.ipv6 is None


def test_on_skjoin_success_resets_failures():
    state = make_state()
    state.on_skjoin_failure(invalidate_threshold=99)  # 高 threshold で cache 残す
    assert state.consecutive_skjoin_failures == 1
    state.on_skjoin_success()
    assert state.consecutive_skjoin_failures == 0


def test_on_wisun_reconnect_cached_skjoin_increments():
    state = make_state()
    state.on_wisun_reconnect_cached_skjoin()
    state.on_wisun_reconnect_cached_skjoin()
    snap = state.snapshot(now=state.start_time)
    assert snap["wisun_reconnect_cached_skjoin_total"] == 2


def test_on_wisun_reconnect_cached_skjoin_fallback_increments():
    state = make_state()
    state.on_wisun_reconnect_cached_skjoin_fallback()
    snap = state.snapshot(now=state.start_time)
    assert snap["wisun_reconnect_cached_skjoin_fallback_total"] == 1


def test_on_wisun_joined_sets_pan_id_and_mac():
    """spec 035: pan dict から pan_id/mac を cache 記録 (= 既存 lqi/channel に加えて)."""
    state = make_state()
    state.on_wisun_joined({
        "LQI": "C0", "Channel": "21",
        "Pan ID": "D4E3", "Addr": "001C64000B03D4E3",
    })
    assert state.pan_id == "D4E3"
    assert state.mac == "001C64000B03D4E3"


def test_on_wisun_joined_skips_missing_pan_id_and_mac():
    """spec 035: pan dict から Pan ID/Addr 省略時、 既存 attribute は維持 (= 既存 L254 互換)."""
    state = make_state()
    state.pan_id = "OLD"
    state.mac = "OLDMAC"
    state.on_wisun_joined({"LQI": "10", "Channel": "21"})
    # 既存 attribute 維持 (= 上書きしない、 既存 test L254 が dict 省略パターンを assert)
    assert state.pan_id == "OLD"
    assert state.mac == "OLDMAC"
