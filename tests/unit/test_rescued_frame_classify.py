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
