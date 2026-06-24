"""spec 020: SendHistoryRing class tests.

TID → (send_ts, epc_tuple) の bounded FIFO ring buffer。
main loop only (admin thread からは触らない) → thread-safe 不要。
spec 014 で破棄される TID mismatch frame の send_ts を逆引きで救済する用途。
"""
import mqtt_bridge as mb


def test_initial_len_is_zero():
    ring = mb.SendHistoryRing(maxlen=10)
    assert len(ring) == 0


def test_record_stores_send_ts_and_epcs():
    ring = mb.SendHistoryRing(maxlen=10)
    ring.record(0x1234, 100.0, [0xE7, 0xE8])
    assert len(ring) == 1


def test_lookup_returns_recorded_entry():
    ring = mb.SendHistoryRing(maxlen=10)
    ring.record(0x1234, 100.0, [0xE7])
    hit = ring.lookup(0x1234)
    assert hit is not None
    send_ts, epcs = hit
    assert send_ts == 100.0
    assert epcs == (0xE7,)


def test_lookup_returns_none_for_unknown_tid():
    ring = mb.SendHistoryRing(maxlen=10)
    ring.record(0x1234, 100.0, [0xE7])
    assert ring.lookup(0x9999) is None


def test_eviction_keeps_only_maxlen_entries():
    """maxlen=3 で 4 件 record → 最古 1 件 evict、 残り 3 件."""
    ring = mb.SendHistoryRing(maxlen=3)
    ring.record(0x1, 100.0, [0xE0])
    ring.record(0x2, 101.0, [0xE0])
    ring.record(0x3, 102.0, [0xE0])
    ring.record(0x4, 103.0, [0xE0])
    assert len(ring) == 3
    assert ring.lookup(0x1) is None  # evicted
    assert ring.lookup(0x4) is not None  # newest


def test_record_same_tid_refreshes_and_does_not_evict_others():
    """同 TID 再 record で順序更新 (= move to end)、 他 entry は evict されない."""
    ring = mb.SendHistoryRing(maxlen=3)
    ring.record(0x1, 100.0, [0xE0])
    ring.record(0x2, 101.0, [0xE0])
    ring.record(0x3, 102.0, [0xE0])
    ring.record(0x1, 103.0, [0xE0])  # same TID re-record
    assert len(ring) == 3
    # 0x1 が最新になり 0x2 が次に古い、 maxlen 内なので全部残る
    assert ring.lookup(0x1) is not None
    assert ring.lookup(0x2) is not None
    assert ring.lookup(0x3) is not None


def test_record_overwrites_send_ts_for_same_tid():
    """同 TID 再 record で send_ts が更新される (= 最新値保持)."""
    ring = mb.SendHistoryRing(maxlen=10)
    ring.record(0x1234, 100.0, [0xE0])
    ring.record(0x1234, 200.0, [0xE0])
    send_ts, _ = ring.lookup(0x1234)
    assert send_ts == 200.0


# ---------------------------------------------------------------------------
# spec 020 v1.5: lookup_latest (= 直近 send entry、 got_tid=0 救済用)
# ---------------------------------------------------------------------------

def test_lookup_latest_empty_ring_returns_none():
    ring = mb.SendHistoryRing(maxlen=10)
    assert ring.lookup_latest() is None


def test_lookup_latest_returns_most_recent_entry():
    ring = mb.SendHistoryRing(maxlen=10)
    ring.record(0x1, 100.0, [0xE0])
    ring.record(0x2, 200.0, [0xE3])
    ring.record(0x3, 300.0, [0xE7])
    hit = ring.lookup_latest()
    assert hit is not None
    send_ts, epcs = hit
    assert send_ts == 300.0
    assert epcs == (0xE7,)


def test_lookup_latest_reflects_refresh_via_record():
    """同 TID 再 record で latest が更新される."""
    ring = mb.SendHistoryRing(maxlen=10)
    ring.record(0x1, 100.0, [0xE0])
    ring.record(0x2, 200.0, [0xE3])
    ring.record(0x1, 300.0, [0xE0])  # refresh、 末尾に移動
    send_ts, _ = ring.lookup_latest()
    assert send_ts == 300.0
