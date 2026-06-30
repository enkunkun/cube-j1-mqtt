"""Wi-SUN health diag: latency + SK EVENT/FAIL classification (spec 006)."""
import time

import pytest

import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# classify_sk_line: pure helper
# ---------------------------------------------------------------------------

def test_classify_sk_line_erxudp():
    line = "ERXUDP FE80:... FE80:... 0E1A 0E1A 001D129... 1 0 001C 1081000102880105FF017264E704000000F0"
    kind, payload = mb.classify_sk_line(line)
    assert kind == "erxudp"
    assert payload.startswith("1081000102")


def test_classify_sk_line_event_simple():
    assert mb.classify_sk_line("EVENT 22 FE80:0000:0000:0000:021D:1290:1234:5678") == ("event", "22")


def test_classify_sk_line_event_no_args():
    assert mb.classify_sk_line("EVENT 32") == ("event", "32")


def test_classify_sk_line_event_lowercase_normalized():
    assert mb.classify_sk_line("EVENT 2a FE80::1") == ("event", "2A")


def test_classify_sk_line_fail_er05():
    assert mb.classify_sk_line("FAIL ER05") == ("error", "05")


def test_classify_sk_line_fail_er10():
    assert mb.classify_sk_line("FAIL ER10") == ("error", "10")


def test_classify_sk_line_unknown_returns_none():
    assert mb.classify_sk_line("OK") is None
    assert mb.classify_sk_line("SKSCAN ...") is None
    assert mb.classify_sk_line("") is None


def test_classify_sk_line_ignores_fail_without_er_prefix():
    # 仕様外フォーマットは弾く
    assert mb.classify_sk_line("FAIL 05") is None


# ---------------------------------------------------------------------------
# DiagState: latency deque + percentiles
# ---------------------------------------------------------------------------

def _make_state():
    return mb.DiagState(start_time=1000.0, version="1.0.0+test")


def test_on_erxudp_latency_records_in_deque():
    s = _make_state()
    s.on_erxudp_latency(123.4)
    s.on_erxudp_latency(456.7)
    assert list(s.erxudp_latency_ms_recent) == [123.4, 456.7]


def test_on_erxudp_latency_caps_at_200():
    s = _make_state()
    for i in range(250):
        s.on_erxudp_latency(float(i))
    assert len(s.erxudp_latency_ms_recent) == 200
    assert s.erxudp_latency_ms_recent[0] == 50.0  # oldest 50 dropped
    assert s.erxudp_latency_ms_recent[-1] == 249.0


def test_snapshot_emits_latency_percentiles():
    s = _make_state()
    for v in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0):
        s.on_erxudp_latency(v)
    snap = s.snapshot(1234.0)
    assert snap["erxudp_latency_p50_ms"] == pytest.approx(55.0, abs=10.0)
    assert snap["erxudp_latency_p95_ms"] == pytest.approx(95.0, abs=10.0)
    assert snap["erxudp_latency_max_ms"] == 100.0


def test_snapshot_omits_latency_when_empty():
    s = _make_state()
    snap = s.snapshot(1234.0)
    assert "erxudp_latency_p50_ms" not in snap
    assert "erxudp_latency_p95_ms" not in snap
    assert "erxudp_latency_max_ms" not in snap


# ---------------------------------------------------------------------------
# DiagState: SK event / error counters
# ---------------------------------------------------------------------------

def test_on_sk_event_increments_counter():
    s = _make_state()
    s.on_sk_event("22")
    s.on_sk_event("22")
    s.on_sk_event("28")
    assert s.sk_event_counts == {"22": 2, "28": 1}


def test_on_sk_error_increments_counter():
    s = _make_state()
    s.on_sk_error("05")
    s.on_sk_error("10")
    s.on_sk_error("10")
    assert s.sk_error_counts == {"05": 1, "10": 2}


def test_snapshot_exposes_known_event_counters():
    s = _make_state()
    s.on_sk_event("22")
    s.on_sk_event("24")
    s.on_sk_event("28")
    snap = s.snapshot(1234.0)
    assert snap["sk_event_22_total"] == 1
    assert snap["sk_event_24_total"] == 1
    assert snap["sk_event_28_total"] == 1


def test_snapshot_exposes_known_error_counters():
    s = _make_state()
    s.on_sk_error("05")
    s.on_sk_error("10")
    snap = s.snapshot(1234.0)
    assert snap["sk_error_ER05_total"] == 1
    assert snap["sk_error_ER10_total"] == 1


def test_snapshot_omits_zero_event_counters():
    """HA に空 entity を作らないため 0 件の counter は publish しない."""
    s = _make_state()
    s.on_sk_event("22")
    snap = s.snapshot(1234.0)
    assert snap["sk_event_22_total"] == 1
    assert "sk_event_24_total" not in snap
    assert "sk_event_28_total" not in snap
    assert "sk_error_ER05_total" not in snap


# ---------------------------------------------------------------------------
# read_erxudp: dispatches EVENT / FAIL into diag while waiting for ERXUDP
# ---------------------------------------------------------------------------

class _FakeFd(object):
    """Drives serial_readline by line, used to drive read_erxudp's loop."""

    def __init__(self, lines):
        self._lines = list(lines)

    def fileno(self):
        return -1


def test_read_erxudp_records_event_then_returns_payload(monkeypatch):
    s = _make_state()
    lines = [
        "EVENT 22 FE80::1",
        "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D129 1 0 0001 10810001028801",
    ]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    data = mb.read_erxudp(_FakeFd(lines), timeout=2, diag_state=s)
    assert data is not None
    assert s.sk_event_counts.get("22") == 1


def test_read_erxudp_records_fail_and_keeps_waiting(monkeypatch):
    s = _make_state()
    lines = [
        "FAIL ER10",
        "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D129 1 0 0001 10810001028801",
    ]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    data = mb.read_erxudp(_FakeFd(lines), timeout=2, diag_state=s)
    assert data is not None
    assert s.sk_error_counts.get("10") == 1


def test_read_erxudp_handles_missing_diag_state(monkeypatch):
    """既存呼び出し（diag_state なし）で regression を起こさない."""
    lines = [
        "EVENT 22 FE80::1",
        "ERXUDP FE80::1 FE80::2 0E1A 0E1A 001D129 1 0 0001 10810001028801",
    ]

    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None

    monkeypatch.setattr(mb, "serial_readline", fake_readline)

    data = mb.read_erxudp(_FakeFd(lines), timeout=2)
    assert data is not None


# ---------------------------------------------------------------------------
# spec 036: BP35A1 SKSTACK-IP 公式仕様整合 (= EVENT ラベル誤記訂正)
# ---------------------------------------------------------------------------
# 公式 BP35A1 コマンドリファレンス Ver 1.3.2 p.51 (= docs/vendor/bp35a1-skstack-ip/
# bp35a1_commandmanual_tr-j.pdf) に従い、 DIAG_SENSOR_DEFS のラベル文字列が
# 公式仕様の意味と整合していることを assert する。 過去の誤記
# ("PANA OK", "Re-auth", "Scan Done", "Scan Started", "Session End",
# "Session Timeout") の混入を防止する regression test。


def _diag_label(metric_name):
    """DIAG_SENSOR_DEFS から metric_name に対応するラベル文字列を抽出。"""
    for entry in mb.DIAG_SENSOR_DEFS:
        if entry[0] == metric_name:
            return entry[1]
    raise AssertionError(
        "metric {} not found in DIAG_SENSOR_DEFS".format(metric_name))


def test_diag_label_event_22_is_active_scan_done():
    """EVENT 0x22 = アクティブスキャン完了 (公式 p.51)、 "PANA OK" は誤記。"""
    label = _diag_label("sk_event_22_total")
    assert "Active Scan" in label, label
    assert "PANA OK" not in label, label


def test_diag_label_event_26_is_session_termination_requested():
    """EVENT 0x26 = 接続相手からセッション終了要求を受信 (公式 p.51)、 "Re-auth" は誤記。"""
    label = _diag_label("sk_event_26_total")
    assert "Session Termination" in label, label
    assert "Re-auth" not in label, label


def test_diag_label_event_28_is_session_termination_timeout():
    """EVENT 0x28 = セッション終了要求への応答が無く timeout (公式 p.51)。"""
    label = _diag_label("sk_event_28_total")
    assert "Session Termination Timeout" in label, label


def test_diag_label_event_29_is_session_lifetime_expired():
    """EVENT 0x29 = セッションのライフタイム経過 (公式 p.51)。"""
    label = _diag_label("sk_event_29_total")
    assert "Session Lifetime Expired" in label, label


def test_diag_label_event_32_is_arib_transmit_limit_hit():
    """EVENT 0x32 = ARIB108 送信総和時間制限の発動 (公式 p.51)、 "Scan Done" は誤記。"""
    label = _diag_label("sk_event_32_total")
    assert "ARIB Transmit Limit Hit" in label, label
    assert "Scan Done" not in label, label


def test_diag_label_event_33_is_arib_transmit_limit_released():
    """EVENT 0x33 = ARIB108 送信総和時間制限の解除 (公式 p.51)、 "Scan Started" は誤記。"""
    label = _diag_label("sk_event_33_total")
    assert "ARIB Transmit Limit Released" in label, label
    assert "Scan Started" not in label, label


def test_classify_sk_line_event_26():
    """EVENT 26 (= 相手からセッション終了要求受信) の classify。"""
    assert mb.classify_sk_line("EVENT 26 FE80::1") == ("event", "26")


def test_classify_sk_line_event_33():
    """EVENT 33 (= ARIB 送信制限解除) の classify。"""
    assert mb.classify_sk_line("EVENT 33") == ("event", "33")


def test_on_sk_event_increments_26_32_33():
    """EVENT 26/32/33 の on_sk_event で sk_event_NN_total が increment される。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_sk_event("26")
    diag.on_sk_event("32")
    diag.on_sk_event("32")
    diag.on_sk_event("33")
    snap = diag.snapshot(time.time())
    assert snap["sk_event_26_total"] == 1
    assert snap["sk_event_32_total"] == 2
    assert snap["sk_event_33_total"] == 1


# ---------------------------------------------------------------------------
# spec 038/040: EVENT 21 (= UDP 送信結果通知) / EVENT 27 (= セッション終了完了)
# Phase 1 観察追加 (= PARAM 区別なし最小化、 公式 BP35A1 Ver 1.3.2 p.51)
# ---------------------------------------------------------------------------
# spec 038 (P-NEW-3) は EVENT 21 PARAM=0/1/2 完全 ignore を改修するが、
# Phase 1 では「件数だけ」 観測して ROI を確定 (= 1h で 0 件なら spec close、
# 多数なら PARAM 区別 Phase 2 へ)。 spec 040 (P-NEW-5) は PANA 720s 自動再認証
# 周期と erxudp_timeout の相関を見るため EVENT 27 (= セッション終了完了) を
# 観察追加。 どちらも _PUBLISHED_SK_EVENT_IDS に id を入れるだけで snapshot
# に publish される。


def test_published_sk_event_ids_contains_21_and_27():
    """spec 038/040 観察用: _PUBLISHED_SK_EVENT_IDS に 21 / 27 が含まれる。"""
    assert "21" in mb._PUBLISHED_SK_EVENT_IDS
    assert "27" in mb._PUBLISHED_SK_EVENT_IDS


def test_diag_label_event_21_is_tx_result_notification():
    """EVENT 0x21 = UDP 送信結果通知 (= TX Result、 公式 p.51)。"""
    label = _diag_label("sk_event_21_total")
    assert "TX Result" in label, label


def test_diag_label_event_27_is_session_termination_done():
    """EVENT 0x27 = セッション終了完了 (公式 p.51)、 26 (= 要求) と 28 (= timeout) と区別。"""
    label = _diag_label("sk_event_27_total")
    assert "Session Termination" in label, label
    assert "Done" in label, label


def test_classify_sk_line_event_21_with_param():
    """EVENT 21 は "EVENT 21 <ipv6> <PARAM>" 形式、 classify は id のみ返す (= PARAM 切り捨て)。"""
    assert mb.classify_sk_line("EVENT 21 FE80::1 1") == ("event", "21")
    assert mb.classify_sk_line("EVENT 21 FE80::1 0") == ("event", "21")
    assert mb.classify_sk_line("EVENT 21 FE80::1 2") == ("event", "21")


def test_classify_sk_line_event_27():
    """EVENT 27 (= セッション終了完了) の classify。"""
    assert mb.classify_sk_line("EVENT 27 FE80::1") == ("event", "27")


def test_on_sk_event_increments_21_27_snapshot():
    """EVENT 21/27 の on_sk_event で sk_event_NN_total が snapshot に publish される。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_sk_event("21")
    diag.on_sk_event("21")
    diag.on_sk_event("21")
    diag.on_sk_event("27")
    snap = diag.snapshot(time.time())
    assert snap["sk_event_21_total"] == 3
    assert snap["sk_event_27_total"] == 1


# ---------------------------------------------------------------------------
# spec 037: WOPT FLASH 書込み寿命対策 (= ROPT 確認で WOPT skip)
# ---------------------------------------------------------------------------
# 公式 BP35A1 Ver 1.3.2 p.41 で WOPT は FLASH 書込み 10,000 回制限あり、
# p.42 の ROPT で現在値を読み bit0=1 なら WOPT 1 を skip して FLASH 寿命を
# 延命する。 ropt() helper は応答形式が「OK <MODE:2 桁 hex>」 で skcommand
# の break 条件 (= 完全一致 "OK") では parse 不可能なため専用 helper として
# 実装。


def _fake_readline_factory(lines):
    """Return a fake serial_readline that pops lines one at a time."""
    def fake_readline(fd, timeout=None):
        return lines.pop(0) if lines else None
    return fake_readline


def test_ropt_parses_mode_01(monkeypatch):
    """ROPT 応答 'OK 01' で bit0=1 を返す。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["OK 01"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    assert mb.ropt(_FakeFd([])) == 1


def test_ropt_parses_mode_00(monkeypatch):
    """ROPT 応答 'OK 00' で bit0=0 を返す。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["OK 00"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    assert mb.ropt(_FakeFd([])) == 0


def test_ropt_raises_on_fail(monkeypatch):
    """ROPT FAIL ER10 で RuntimeError を raise。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["FAIL ER10"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    with pytest.raises(RuntimeError):
        mb.ropt(_FakeFd([]))


def test_ropt_raises_on_timeout(monkeypatch):
    """ROPT 応答なし (= 全 None) で RuntimeError を raise。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory([]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    with pytest.raises(RuntimeError):
        mb.ropt(_FakeFd([]), timeout=0.5)


def test_on_wopt_skip_increments():
    """DiagState.on_wopt_skip() で wopt_write_skipped_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_wopt_skip()
    diag.on_wopt_skip()
    snap = diag.snapshot(time.time())
    assert snap["wopt_write_skipped_total"] == 2


def test_on_wopt_write_increments():
    """DiagState.on_wopt_write() で wopt_write_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_wopt_write()
    snap = diag.snapshot(time.time())
    assert snap["wopt_write_total"] == 1


def test_diag_label_wopt_skipped_exists():
    """spec 037 で追加する wopt_write_skipped_total / wopt_write_total の
    ラベルが DIAG_SENSOR_DEFS に登録されている。"""
    skipped_label = _diag_label("wopt_write_skipped_total")
    write_label = _diag_label("wopt_write_total")
    assert "Skipped" in skipped_label or "skip" in skipped_label.lower(), skipped_label
    assert "WOPT" in write_label, write_label


# ---------------------------------------------------------------------------
# spec 044: _wait_skjoin_event25 で EVENT 25/24 を on_sk_event に渡す
# ---------------------------------------------------------------------------
# spec 035 で抽出された _wait_skjoin_event25 が spec 006 の on_sk_event hook を
# 踏襲せず、 SKJOIN 文脈で発火する EVENT 25/24 が metric 計上対象外だった
# (= 2026-06-30 spec 038/040 Phase 1 観察で発見、 sk_event_25_total = 0 件 vs
# wisun_joined log 51 件の不一致が証拠)。 spec 044 で diag_state 引数追加 +
# EVENT 25/24 受信時に on_sk_event を呼ぶ修正。


def _mock_led_and_logger(monkeypatch):
    """spec 044 test 用 LED + threading + LOGGER の no-op mock。"""
    monkeypatch.setattr(mb, "led_read", lambda: (0, 0, 0))
    monkeypatch.setattr(mb, "led_rgb", lambda r, g, b: None)
    monkeypatch.setattr(mb, "_led_blink", lambda *args: None)
    monkeypatch.setattr(mb, "LOGGER", None)


def test_wait_skjoin_event25_calls_on_sk_event_on_event_25(monkeypatch):
    """spec 044: SKJOIN 成功時の EVENT 25 で diag_state.on_sk_event('25') を呼ぶ。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    lines = ["EVENT 25 FE80::1"]
    monkeypatch.setattr(mb, "serial_readline",
                        lambda fd, timeout=None: lines.pop(0) if lines else None)
    _mock_led_and_logger(monkeypatch)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    result = mb._wait_skjoin_event25(_FakeFd([]), pan, "FE80::1",
                                     timeout=2, diag_state=diag)
    assert result is True
    assert diag.sk_event_counts.get("25") == 1


def test_wait_skjoin_event25_calls_on_sk_event_on_event_24(monkeypatch):
    """spec 044: PANA failure 時の EVENT 24 で diag_state.on_sk_event('24') を呼ぶ。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    lines = ["EVENT 24 FE80::1"]
    monkeypatch.setattr(mb, "serial_readline",
                        lambda fd, timeout=None: lines.pop(0) if lines else None)
    _mock_led_and_logger(monkeypatch)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    result = mb._wait_skjoin_event25(_FakeFd([]), pan, "FE80::1",
                                     timeout=2, diag_state=diag)
    assert result is False
    assert diag.sk_event_counts.get("24") == 1


def test_wait_skjoin_event25_diag_state_none_safe(monkeypatch):
    """spec 044: diag_state=None でも既存挙動互換 (= 例外なく動く)。"""
    lines = ["EVENT 25 FE80::1"]
    monkeypatch.setattr(mb, "serial_readline",
                        lambda fd, timeout=None: lines.pop(0) if lines else None)
    _mock_led_and_logger(monkeypatch)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    # diag_state 省略 (= 既存呼出 spec 035 互換)
    result = mb._wait_skjoin_event25(_FakeFd([]), pan, "FE80::1", timeout=2)
    assert result is True


# ---------------------------------------------------------------------------
# spec 042: SKADDNBR で IP 層ネイバーキャッシュ登録 = 初回 SKSENDTO 1-2s 短縮
# ---------------------------------------------------------------------------
# 公式 BP35A1 Ver 1.3.2 p.29 で SKADDNBR は「指定した IPv6 アドレスと MAC
# アドレスを IP 層のネイバーキャッシュに Reachable 状態で登録、 アドレス要請を
# 省略して直接 IP パケットを出力」 と明示。 bridge は SKJOIN 後に毎回打つ揮発
# 前提で、 spec 039 のような SKRESET クリア問題なし。 DiagState で発行成功 /
# 失敗回数を counter 化、 SKADDNBR 自体は skcommand 直接呼出で OK ("OK"/"FAIL")。


def test_on_skaddnbr_success_increments():
    """DiagState.on_skaddnbr_success() で skaddnbr_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_skaddnbr_success()
    diag.on_skaddnbr_success()
    snap = diag.snapshot(time.time())
    assert snap["skaddnbr_total"] == 2


def test_on_skaddnbr_fail_increments():
    """DiagState.on_skaddnbr_fail() で skaddnbr_fail_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_skaddnbr_fail()
    snap = diag.snapshot(time.time())
    assert snap["skaddnbr_fail_total"] == 1


def test_diag_label_skaddnbr_exists():
    """spec 042: skaddnbr_total / skaddnbr_fail_total の DIAG_SENSOR_DEFS 登録確認。"""
    total_label = _diag_label("skaddnbr_total")
    fail_label = _diag_label("skaddnbr_fail_total")
    assert "SKADDNBR" in total_label or "Neighbor" in total_label, total_label
    assert "SKADDNBR" in fail_label or "Fail" in fail_label, fail_label


# ---------------------------------------------------------------------------
# spec 038 Phase 2: EVENT 21 PARAM 区別実装 (= TX Result 0=成功 / 1=失敗 / 2=自動再送)
# ---------------------------------------------------------------------------
# spec 038 Phase 1 で sk_event_21_total = 82 件/24h 計上判明 (= 2026-06-30 reopen)、
# Phase 2 で PARAM 別 counter を追加。 PARAM=1 (= TX 失敗) の割合次第で ROI 確定
# (= 50% 以上なら即 retry 実装 = Phase 3、 10% 以下なら spec close)。 BP35A1
# 公式 Ver 1.3.2 p.51 で EVENT 21 PARAM の意味: 0=success / 1=fail / 2=auto-retry。


def test_on_sk_event_21_param_increments():
    """DiagState.on_sk_event_21_param(0/1/2) で対応 counter が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_sk_event_21_param(0)
    diag.on_sk_event_21_param(0)
    diag.on_sk_event_21_param(1)
    diag.on_sk_event_21_param(2)
    diag.on_sk_event_21_param(2)
    diag.on_sk_event_21_param(2)
    snap = diag.snapshot(time.time())
    assert snap["sk_event_21_param0_total"] == 2
    assert snap["sk_event_21_param1_total"] == 1
    assert snap["sk_event_21_param2_total"] == 3


def test_on_sk_event_21_param_invalid_ignored():
    """範囲外の PARAM は ignore (= 防御的、 仕様外値で例外起こさない)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_sk_event_21_param(3)  # 仕様外
    diag.on_sk_event_21_param(-1)
    snap = diag.snapshot(time.time())
    assert snap["sk_event_21_param0_total"] == 0
    assert snap["sk_event_21_param1_total"] == 0
    assert snap["sk_event_21_param2_total"] == 0


def test_diag_label_sk_event_21_param_exists():
    """spec 038 Phase 2: sk_event_21_param0/1/2_total の DIAG_SENSOR_DEFS 登録確認。"""
    p0 = _diag_label("sk_event_21_param0_total")
    p1 = _diag_label("sk_event_21_param1_total")
    p2 = _diag_label("sk_event_21_param2_total")
    assert "Success" in p0 or "PARAM=0" in p0, p0
    assert "Fail" in p1 or "PARAM=1" in p1, p1
    assert "Retry" in p2 or "PARAM=2" in p2, p2


# ---------------------------------------------------------------------------
# spec 040 Phase 2a: S17=0 + EVENT 25 ts 観測基盤 (= 能動 SKREJOIN の前段)
# ---------------------------------------------------------------------------
# 公式 BP35A1 Ver 1.3.2 S17 (= 自動再認証フラグ、 default=1) を 0 に設定して
# PaC 側の 720s 自動 SKREJOIN を抑制、 bridge が EVENT 25 経過時間を観測 +
# 次セッションで main loop が能動 SKREJOIN を発火する (= Phase 2b で実装)。
# S17 は通常 SKSREG なので SKRESET でクリア (= memory feedback-bp35a1-skreset-clears-sksreg-non-product 通り)、
# _wisun_init_sequence で毎回再設定の idempotent pattern、 SKSAVE 不要 (= FLASH 寿命影響 0)。


def test_on_s17_off_set_increments():
    """DiagState.on_s17_off_set() で s17_off_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_s17_off_set()
    diag.on_s17_off_set()
    snap = diag.snapshot(time.time())
    assert snap["s17_off_total"] == 2


def test_last_event_25_ts_recorded_on_event_25(monkeypatch):
    """_wait_skjoin_event25 で EVENT 25 受信時に diag_state.last_event_25_ts が now() で set される。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    lines = ["EVENT 25 FE80::1"]
    monkeypatch.setattr(mb, "serial_readline",
                        lambda fd, timeout=None: lines.pop(0) if lines else None)
    _mock_led_and_logger(monkeypatch)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    before = time.time()
    result = mb._wait_skjoin_event25(_FakeFd([]), pan, "FE80::1",
                                     timeout=2, diag_state=diag)
    after = time.time()
    assert result is True
    assert diag.last_event_25_ts is not None
    assert before <= diag.last_event_25_ts <= after


def test_last_event_25_seconds_in_snapshot():
    """DiagState.last_event_25_ts set 時 snapshot に last_event_25_seconds が出る (= now - ts)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = 1500.0
    snap = diag.snapshot(now=1610.0)
    assert snap["last_event_25_seconds"] == 110


def test_last_event_25_seconds_omitted_when_none():
    """last_event_25_ts = None なら snapshot から omit (= unknown)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    snap = diag.snapshot(now=1610.0)
    assert "last_event_25_seconds" not in snap


def test_diag_label_s17_off_and_last_event_25():
    """DIAG_SENSOR_DEFS に spec 040 Phase 2a の 2 件登録確認。"""
    s17_label = _diag_label("s17_off_total")
    ts_label = _diag_label("last_event_25_seconds")
    assert "S17" in s17_label, s17_label
    assert "EVENT 25" in ts_label or "Last" in ts_label, ts_label


# ---------------------------------------------------------------------------
# spec 040 Phase 2b: main loop 能動 SKREJOIN tick logic
# ---------------------------------------------------------------------------
# Phase 2a で S17=0 + last_event_25_ts 観測基盤を deploy 済、 Phase 2b で
# main loop の poll_success 完了後に「直近 EVENT 25 から SKREJOIN_TICK_SECONDS
# (= 600s) 経過 + skrejoin_active False」 条件成立で能動 SKREJOIN 発火。
# SKREJOIN は skcommand 発行 → _wait_skjoin_event25 で EVENT 25 待ち、 成功で
# counter inc + last_event_25_ts 自動更新 (= spec 044 hook 経由)、 失敗で
# pending_wisun_rejoin = True で既存 reconnect path に fallback。


def test_skrejoin_active_initial_false():
    """DiagState.skrejoin_active の初期値は False。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    assert diag.skrejoin_active is False


def test_on_skrejoin_success_increments():
    """DiagState.on_skrejoin_success() で skrejoin_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_skrejoin_success()
    diag.on_skrejoin_success()
    snap = diag.snapshot(time.time())
    assert snap["skrejoin_total"] == 2


def test_on_skrejoin_fail_increments():
    """DiagState.on_skrejoin_fail() で skrejoin_fail_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_skrejoin_fail()
    snap = diag.snapshot(time.time())
    assert snap["skrejoin_fail_total"] == 1


def test_diag_label_skrejoin_exists():
    """DIAG_SENSOR_DEFS に spec 040 Phase 2b の 2 件登録確認。"""
    total = _diag_label("skrejoin_total")
    fail = _diag_label("skrejoin_fail_total")
    assert "SKREJOIN" in total, total
    assert "SKREJOIN" in fail or "Fail" in fail, fail


def test_skrejoin_tick_success(monkeypatch):
    """skrejoin_tick 成功 path: skcommand + EVENT 25 受信で skrejoin_total inc。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = time.time() - 700  # 700s 経過
    skcommand_calls = []
    monkeypatch.setattr(mb, "skcommand",
                        lambda fd, cmd, **kw: skcommand_calls.append(cmd))
    monkeypatch.setattr(mb, "_wait_skjoin_event25",
                        lambda fd, pan, ipv6, timeout, diag_state=None: True)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    result = mb.skrejoin_tick(_FakeFd([]), diag, pan, "FE80::1")
    assert result is True
    assert "SKREJOIN" in skcommand_calls
    assert diag.skrejoin_count == 1
    assert diag.skrejoin_fail_count == 0
    assert diag.skrejoin_active is False  # finally で False に戻る
    assert diag.pending_wisun_rejoin is False


def test_skrejoin_tick_fail_event25_timeout(monkeypatch):
    """skrejoin_tick 失敗 path: _wait_skjoin_event25 が False で fallback 発動。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = time.time() - 700
    monkeypatch.setattr(mb, "skcommand", lambda fd, cmd, **kw: None)
    monkeypatch.setattr(mb, "_wait_skjoin_event25",
                        lambda fd, pan, ipv6, timeout, diag_state=None: False)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    result = mb.skrejoin_tick(_FakeFd([]), diag, pan, "FE80::1")
    assert result is False
    assert diag.skrejoin_count == 0
    assert diag.skrejoin_fail_count == 1
    assert diag.skrejoin_active is False
    assert diag.pending_wisun_rejoin is True  # fallback で既存 reconnect 起動


def test_skrejoin_tick_fail_exception(monkeypatch):
    """skrejoin_tick 例外 path: skcommand が throw でも fail counter + fallback。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = time.time() - 700
    def raise_exc(fd, cmd, **kw):
        raise RuntimeError("skcommand failed")
    monkeypatch.setattr(mb, "skcommand", raise_exc)
    pan = {"Pan ID": "D4E3", "Channel": "27", "Addr": "001C64000B03D4E3"}
    result = mb.skrejoin_tick(_FakeFd([]), diag, pan, "FE80::1")
    assert result is False
    assert diag.skrejoin_fail_count == 1
    assert diag.skrejoin_active is False
    assert diag.pending_wisun_rejoin is True


def test_should_fire_skrejoin_below_threshold():
    """should_fire_skrejoin: last_event_25 から 300s なら False (= 600s threshold 未満)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = time.time() - 300
    assert mb.should_fire_skrejoin(diag, time.time()) is False


def test_should_fire_skrejoin_above_threshold():
    """should_fire_skrejoin: last_event_25 から 650s なら True (= 600s threshold 超過)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = time.time() - 650
    assert mb.should_fire_skrejoin(diag, time.time()) is True


def test_should_fire_skrejoin_active_already():
    """should_fire_skrejoin: skrejoin_active=True なら False (= 二重発火防止)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.last_event_25_ts = time.time() - 700
    diag.skrejoin_active = True
    assert mb.should_fire_skrejoin(diag, time.time()) is False


def test_should_fire_skrejoin_no_event_25_yet():
    """should_fire_skrejoin: last_event_25_ts=None なら False (= 起動直後等)。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    assert diag.last_event_25_ts is None
    assert mb.should_fire_skrejoin(diag, time.time()) is False
