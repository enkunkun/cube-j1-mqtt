"""Deadline-based poll pacing.

現状 main loop は `time.sleep(poll_interval)` を「1 サイクル完了後」に
呼ぶ dead reckoning なので、 ERXUDP timeout (15s) で 1 サイクル取り
こぼすと、 次の取得まで `15 + poll_interval` 秒の間隔が空いてしまう。

deadline ベースに変えて「前回 poll 開始時刻 + poll_interval」を次の
ターゲットにすれば、 失敗しても間隔が伸びない。
"""
import mqtt_bridge as mb


def test_compute_next_poll_sleep_when_cycle_was_fast():
    """1 サイクルが 1s で終わったら 60 - 1 = 59s 寝る."""
    assert mb.compute_next_poll_sleep(
        last_poll_start=1000.0, now=1001.0, poll_interval=60,
    ) == 59.0


def test_compute_next_poll_sleep_when_cycle_was_slow():
    """1 サイクルが 30s かかったら 60 - 30 = 30s 寝る."""
    assert mb.compute_next_poll_sleep(
        last_poll_start=1000.0, now=1030.0, poll_interval=60,
    ) == 30.0


def test_compute_next_poll_sleep_zero_when_cycle_equals_interval():
    """ちょうど poll_interval で終わったら寝ない."""
    assert mb.compute_next_poll_sleep(
        last_poll_start=1000.0, now=1060.0, poll_interval=60,
    ) == 0.0


def test_compute_next_poll_sleep_clamped_to_zero_when_overran():
    """ERXUDP timeout (15s) + 余計な処理で 75s かかっても、 マイナス値で
    sleep しないように 0 にクランプする。"""
    assert mb.compute_next_poll_sleep(
        last_poll_start=1000.0, now=1075.0, poll_interval=60,
    ) == 0.0


def test_compute_next_poll_sleep_respects_custom_interval():
    assert mb.compute_next_poll_sleep(
        last_poll_start=1000.0, now=1005.0, poll_interval=30,
    ) == 25.0
