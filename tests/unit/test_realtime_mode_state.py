"""spec 022: RealtimeModeState class tests.

dig 決定 B: tick() returns (mode, transition) with transition ∈ {None, 'expired', 'aborted'}.
dig 決定 D: start_burst during burst = override expires_at, clear _pending_abort.
thread-safe lock 保護で admin API thread と main loop thread の境界を吸収。
"""
import mqtt_bridge as mb


def test_initial_mode_is_off_transition_none():
    st = mb.RealtimeModeState()
    assert st.tick(now=100.0) == ("off", None)


def test_start_burst_sets_mode_and_expires_at():
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    snap = st.snapshot()
    assert snap["mode"] == "burst"
    assert snap["expires_at"] == 400.0
    assert snap["burst_interval"] == 5


def test_tick_before_expires_returns_burst_none():
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    assert st.tick(now=200.0) == ("burst", None)


def test_tick_at_or_after_expires_returns_off_expired():
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    assert st.tick(now=400.0) == ("off", "expired")


def test_tick_only_once_emits_expired_transition():
    """次 tick は (off, None) を返す idempotent."""
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    st.tick(now=400.0)
    assert st.tick(now=401.0) == ("off", None)


def test_stop_burst_returns_was_active_true():
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    assert st.stop_burst() is True


def test_stop_burst_when_off_returns_false_no_transition():
    st = mb.RealtimeModeState()
    assert st.stop_burst() is False
    assert st.tick(now=100.0) == ("off", None)


def test_stop_burst_then_tick_returns_aborted_transition():
    """dig 決定 B: stop_burst → 次 tick で ('off', 'aborted')."""
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    st.stop_burst()
    assert st.tick(now=110.0) == ("off", "aborted")


def test_aborted_transition_emitted_only_once():
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    st.stop_burst()
    st.tick(now=110.0)
    assert st.tick(now=111.0) == ("off", None)


def test_snapshot_returns_dict_with_mode_expires_at_burst_interval():
    st = mb.RealtimeModeState()
    snap = st.snapshot()
    assert set(snap.keys()) >= {"mode", "expires_at", "burst_interval"}


def test_start_burst_during_burst_overrides_expires_at():
    """dig 決定 D: 残り時間関係なく上書き (リセット相当)."""
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    st.start_burst(now=200.0, duration_sec=60, interval_sec=10)
    snap = st.snapshot()
    assert snap["expires_at"] == 260.0
    assert snap["burst_interval"] == 10


def test_start_burst_clears_pending_abort():
    """stop_burst で _pending_abort=True の状態から再 start すると、
    次 tick は ('burst', None) — abort transition は emit されない."""
    st = mb.RealtimeModeState()
    st.start_burst(now=100.0, duration_sec=300, interval_sec=5)
    st.stop_burst()
    st.start_burst(now=110.0, duration_sec=300, interval_sec=5)
    assert st.tick(now=120.0) == ("burst", None)
