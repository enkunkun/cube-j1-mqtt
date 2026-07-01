"""spec 047: rescued frame の ESV / lag bucket 分類 pure helper."""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# classify_rescued_esv: pure helper
# ---------------------------------------------------------------------------

def _el_frame(esv):
    """最小 EL frame (EHD 1081 + TID 0000 + SEOJ/DEOJ 6byte + ESV + OPC=0)."""
    return bytearray(
        b"\x10\x81\x00\x00\x02\x88\x01\x05\xff\x01" + bytes(bytearray([esv])) + b"\x00")


def test_classify_rescued_esv_get_res():
    assert mb.classify_rescued_esv(_el_frame(0x72)) == "get_res"


def test_classify_rescued_esv_get_sna():
    assert mb.classify_rescued_esv(_el_frame(0x52)) == "get_sna"


def test_classify_rescued_esv_inf():
    assert mb.classify_rescued_esv(_el_frame(0x73)) == "inf"


def test_classify_rescued_esv_other():
    assert mb.classify_rescued_esv(_el_frame(0x71)) == "other"


def test_classify_rescued_esv_short_payload_is_other():
    assert mb.classify_rescued_esv(bytearray(b"\x10\x81")) == "other"


# ---------------------------------------------------------------------------
# classify_rescued_lag_bucket: pure helper
# ---------------------------------------------------------------------------

def test_lag_bucket_lt5s():
    assert mb.classify_rescued_lag_bucket(1.0) == "lt5s"
    assert mb.classify_rescued_lag_bucket(4.999) == "lt5s"


def test_lag_bucket_5to60s():
    assert mb.classify_rescued_lag_bucket(5.0) == "5to60s"
    assert mb.classify_rescued_lag_bucket(59.9) == "5to60s"


def test_lag_bucket_60to300s():
    assert mb.classify_rescued_lag_bucket(60.0) == "60to300s"
    assert mb.classify_rescued_lag_bucket(299.9) == "60to300s"


def test_lag_bucket_gt300s():
    assert mb.classify_rescued_lag_bucket(300.0) == "gt300s"
    assert mb.classify_rescued_lag_bucket(15938.0) == "gt300s"


# ---------------------------------------------------------------------------
# DiagState: spec 047 rescued 内訳 counter
# ---------------------------------------------------------------------------

def _state():
    return mb.DiagState(start_time=1000.0, version="test")


def test_on_erxudp_rescued_increments_all_three_axes():
    st = _state()
    st.on_erxudp_rescued("get_res", tid_zero=True, lag_sec=1.0)
    out = st.snapshot(now=1010.0)
    assert out["erxudp_rescued_esv_get_res_total"] == 1
    assert out["erxudp_rescued_esv_inf_total"] == 0
    assert out["erxudp_rescued_tid_zero_total"] == 1
    assert out["erxudp_rescued_tid_ring_hit_total"] == 0
    assert out["erxudp_rescued_lag_lt5s_total"] == 1
    assert out["erxudp_rescued_lag_gt300s_total"] == 0


def test_on_erxudp_rescued_ring_hit_and_late_bucket():
    st = _state()
    st.on_erxudp_rescued("inf", tid_zero=False, lag_sec=65.0)
    out = st.snapshot(now=1010.0)
    assert out["erxudp_rescued_esv_inf_total"] == 1
    assert out["erxudp_rescued_tid_ring_hit_total"] == 1
    assert out["erxudp_rescued_lag_60to300s_total"] == 1


def test_snapshot_emits_all_rescued_keys_even_when_zero():
    out = _state().snapshot(now=1010.0)
    for key in (
        "erxudp_rescued_esv_get_res_total",
        "erxudp_rescued_esv_get_sna_total",
        "erxudp_rescued_esv_inf_total",
        "erxudp_rescued_esv_other_total",
        "erxudp_rescued_tid_zero_total",
        "erxudp_rescued_tid_ring_hit_total",
        "erxudp_rescued_lag_lt5s_total",
        "erxudp_rescued_lag_5to60s_total",
        "erxudp_rescued_lag_60to300s_total",
        "erxudp_rescued_lag_gt300s_total",
        "erxudp_rescued_empty_measurement_total",
    ):
        assert out[key] == 0, key


def test_on_erxudp_rescued_empty_measurement():
    st = _state()
    st.on_erxudp_rescued_empty_measurement()
    assert st.snapshot(now=1010.0)[
        "erxudp_rescued_empty_measurement_total"] == 1


# FR-006 (= tid_mismatch lag は got≠0 のみ記録) の test は挙動の本籍地
# tests/unit/test_tid_lag.py 側に置いた (= test_lag_skipped_when_got_tid_zero)。


# ---------------------------------------------------------------------------
# rescued_measurement_is_empty: pure helper (FR-004 の判定本体)
# ---------------------------------------------------------------------------

def test_rescued_empty_when_no_keys():
    assert mb.rescued_measurement_is_empty({}) is True


def test_rescued_not_empty_with_power_w():
    assert mb.rescued_measurement_is_empty({"power_w": 700}) is False


def test_rescued_not_empty_with_cumulative():
    assert mb.rescued_measurement_is_empty(
        {"energy_forward_kwh": 123.4}) is False


def test_rescued_not_empty_with_current():
    assert mb.rescued_measurement_is_empty({"current_r_a": 7.5}) is False


def test_rescued_empty_with_only_non_publish_keys():
    """coefficient / unit_kwh は rescue publish 対象外 = 実 data なし扱い."""
    assert mb.rescued_measurement_is_empty(
        {"coefficient": 1, "unit_kwh": 0.1}) is True


def test_rescued_empty_when_publishable_value_is_none():
    """None 値は publish 側で skip されるため空扱い."""
    assert mb.rescued_measurement_is_empty({"power_w": None}) is True


# ---------------------------------------------------------------------------
# DIAG_SENSOR_DEFS: 11 counter が HA discovery / MQTT publish 対象に載る
# (= FR-005。 memory feedback-diag-sensor-defs-publish: 登録漏れ = publish されない)
# ---------------------------------------------------------------------------

def test_diag_sensor_defs_includes_all_rescued_counters():
    sids = {sid for (sid, *_rest) in mb.DIAG_SENSOR_DEFS}
    for key in (
        "erxudp_rescued_esv_get_res_total",
        "erxudp_rescued_esv_get_sna_total",
        "erxudp_rescued_esv_inf_total",
        "erxudp_rescued_esv_other_total",
        "erxudp_rescued_tid_zero_total",
        "erxudp_rescued_tid_ring_hit_total",
        "erxudp_rescued_lag_lt5s_total",
        "erxudp_rescued_lag_5to60s_total",
        "erxudp_rescued_lag_60to300s_total",
        "erxudp_rescued_lag_gt300s_total",
        "erxudp_rescued_empty_measurement_total",
    ):
        assert key in sids, key
