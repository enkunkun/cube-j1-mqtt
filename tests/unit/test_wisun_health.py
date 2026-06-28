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
# spec 039: SKSAVE + SFF=1 で reconnect 床値 ~9s → ~7-8s 突破 (= S2/S3 skip)
# ---------------------------------------------------------------------------
# 公式 BP35A1 Ver 1.3.2 p.31/32 で SKSAVE / SFF レジスタによる FLASH 永続化
# 機構が提供されている。 SFF=1 を一度 SKSAVE すれば、 以降の bridge 起動時に
# S02 channel / S03 PAN ID / S0A pairing ID / WOPT が自動復元される。
# sksreg_read() は SKSREG <reg> の応答 (= ESREG <VAL> または ESREG <reg> <VAL>
# + OK の 2 行 sequence) を解析する専用 helper、 sksave() は SKSAVE 発行で
# OK / FAIL を返す helper。 spec 037 ropt() と同じ pattern。


def test_sksreg_read_parses_esreg_val_pattern(monkeypatch):
    """SKSREG SFF 応答 'ESREG 1' + 'OK' で値 1 を返す (= 公式 p.8 ESREG+<VAL> 形式)。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["ESREG 1", "OK"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    assert mb.sksreg_read(_FakeFd([]), "SFF") == 1


def test_sksreg_read_parses_esreg_reg_val_pattern(monkeypatch):
    """SKSREG S02 応答 'ESREG S02 27' + 'OK' で値 0x27 を返す (= reg 含む 3 トークン形式)。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["ESREG S02 27", "OK"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    assert mb.sksreg_read(_FakeFd([]), "S02") == 0x27


def test_sksreg_read_raises_on_fail(monkeypatch):
    """SKSREG FAIL で RuntimeError を raise。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["FAIL ER10"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    with pytest.raises(RuntimeError):
        mb.sksreg_read(_FakeFd([]), "SFF")


def test_sksreg_read_raises_on_timeout(monkeypatch):
    """SKSREG 応答なしで RuntimeError を raise。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory([]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    with pytest.raises(RuntimeError):
        mb.sksreg_read(_FakeFd([]), "SFF", timeout=0.3)


def test_sksave_returns_on_ok(monkeypatch):
    """SKSAVE 応答 'OK' で例外無く返る。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["OK"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    mb.sksave(_FakeFd([]))


def test_sksave_raises_on_fail(monkeypatch):
    """SKSAVE FAIL で RuntimeError を raise。"""
    monkeypatch.setattr(mb, "serial_readline",
                        _fake_readline_factory(["FAIL ER04"]))
    monkeypatch.setattr(mb, "serial_write", lambda fd, data: None)
    with pytest.raises(RuntimeError):
        mb.sksave(_FakeFd([]))


def test_on_sff_autoload_used_increments():
    """DiagState.on_sff_autoload_used() で sff_autoload_used_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_sff_autoload_used()
    diag.on_sff_autoload_used()
    snap = diag.snapshot(time.time())
    assert snap["sff_autoload_used_total"] == 2


def test_on_sksave_issued_increments():
    """DiagState.on_sksave_issued() で sksave_total が増加。"""
    diag = mb.DiagState(start_time=1000.0, version="1.0.0+test")
    diag.on_sksave_issued()
    snap = diag.snapshot(time.time())
    assert snap["sksave_total"] == 1


def test_diag_label_sff_autoload_used_exists():
    """spec 039: sff_autoload_used_total / sksave_total の DIAG_SENSOR_DEFS 登録確認。"""
    used_label = _diag_label("sff_autoload_used_total")
    save_label = _diag_label("sksave_total")
    assert "SFF" in used_label or "Autoload" in used_label, used_label
    assert "SKSAVE" in save_label, save_label
