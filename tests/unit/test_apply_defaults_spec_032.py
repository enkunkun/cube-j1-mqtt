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


def test_default_erxudp_timeout_sec_is_8():
    """spec 032 は 6s だったが tako 合議 (= 2026-07-01) で 8s に tuning。
    erxudp_latency p95=4.96s / max=5.58s のメーター応答遅延で 6s では tail が
    timeout 側へ落ちる、 8s で 1 cycle 内回収 (= spec 020 late frame 救済に
    委ねる必要が減る)。"""
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_timeout_sec"] == 8


def test_default_erxudp_intra_cycle_retries_is_0():
    """spec 032 は 3 に上げたが tako 合議 (= 2026-07-01) で 0 に戻す tuning。
    実測 490 retries / 2 recovered = 0.4% 効率、 retry でメーター ECHONET
    キューが DDoS 状態になり応答遅延を悪化させる (= p95=5s の主因仮説)。
    0 に抑制でメーター自然応答を待つ、 spec 020 late frame 救済 (= 63 件
    / 5h、 完璧に機能) に fallback して次 cycle で回収。"""
    cfg = mb.apply_defaults({})
    assert cfg["erxudp_intra_cycle_retries"] == 0


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
