"""spec 032: Aggressive Polling Defaults — broute-mqtt 並み短 timeout + 早期 reconnect + retry 増.

3 default 変更:
- `erxudp_timeout_sec`: 30 → 6 (broute-mqtt 5s 並み、 6 倍長すぎた)
- `erxudp_intra_cycle_retries`: 0 → 3 (短期集中 retry で沈黙ループ脱出)
- `erxudp_timeout_force_reconnect_threshold`: 30 → 6 (= 6 cycle × 60s = 6 分死で reconnect、 spec 027 30 巻き戻し)

2026-06-26 PoC で device override 33 分実証 (= timeouts/h 49→18 / 沈黙脱出 / backfill 発火率 5.7 倍)、
他社 OSS reference 実装調査 (= hsakoh/broute-mqtt 等) で cube-j1-mqtt の保守的設定が
逆に polling 不安定の主因と確定。
"""
import mqtt_bridge as mb


def test_default_erxudp_timeout_sec_is_6():
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_timeout_sec"] == 6


def test_default_erxudp_intra_cycle_retries_is_3():
    """spec 011 で 0 だった default、 spec 032 で 3 に上げて短期集中 retry。
    broute-mqtt は retry 3 / 5s 間隔で実機運用、 同 pattern 採用."""
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_intra_cycle_retries"] == 3


def test_default_erxudp_timeout_force_reconnect_threshold_is_6():
    """spec 027 で 5→30 にしたが「30 分死」 ループの主因、 spec 032 で 6 に
    巻き戻し。 6 cycle × 60s = 6 分で reconnect、 broute-mqtt 並み反応性。"""
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_timeout_force_reconnect_threshold"] == 6


def test_explicit_override_preserved_for_aggressive_polling_keys():
    """setdefault 標準挙動: ユーザ明示 override (= 旧値復帰 / 緊急 escape) を尊重."""
    cfg = mb.apply_defaults({
        "erxudp_timeout_sec": 30,
        "erxudp_intra_cycle_retries": 0,
        "erxudp_timeout_force_reconnect_threshold": 30,
    })
    assert cfg["erxudp_timeout_sec"] == 30
    assert cfg["erxudp_intra_cycle_retries"] == 0
    assert cfg["erxudp_timeout_force_reconnect_threshold"] == 30
