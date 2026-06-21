"""AP toggle: command builder + state parser (spec 008).

subprocess を呼ぶ部分は injectable にして、 ここでは pure helper のみテスト。
"""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# build_wpa_cli_cmd: pure helper
# ---------------------------------------------------------------------------

def test_build_wpa_cli_cmd_disable():
    cmd = mb.build_wpa_cli_cmd("p2p-wlan0-0", "disable")
    assert cmd == [
        "wpa_cli", "-p", "/data/misc/wifi/sockets",
        "-i", "p2p-wlan0-0", "p2p_group_remove", "p2p-wlan0-0",
    ]


def test_build_wpa_cli_cmd_enable():
    cmd = mb.build_wpa_cli_cmd("p2p-wlan0-0", "enable")
    assert cmd == [
        "wpa_cli", "-p", "/data/misc/wifi/sockets",
        "-i", "p2p-wlan0-0", "p2p_group_add", "persistent=0", "freq=2412",
    ]


def test_build_wpa_cli_cmd_unknown_action_raises():
    import pytest
    with pytest.raises(ValueError):
        mb.build_wpa_cli_cmd("p2p-wlan0-0", "bogus")


# ---------------------------------------------------------------------------
# parse_ap_state: read getprop output
# ---------------------------------------------------------------------------

def test_parse_ap_state_enabled_for_created():
    assert mb.parse_ap_state("created\n") is True


def test_parse_ap_state_enabled_for_enabled():
    assert mb.parse_ap_state("enabled\n") is True


def test_parse_ap_state_disabled_for_disabled():
    assert mb.parse_ap_state("disabled\n") is False


def test_parse_ap_state_disabled_for_removed():
    assert mb.parse_ap_state("removed\n") is False


def test_parse_ap_state_none_for_empty():
    assert mb.parse_ap_state("") is None
    assert mb.parse_ap_state("\n") is None


def test_parse_ap_state_handles_whitespace():
    assert mb.parse_ap_state("  created  \n") is True


# ---------------------------------------------------------------------------
# ApController: orchestrator with injectable runner
# ---------------------------------------------------------------------------

class _FakeRunner(object):
    def __init__(self, getprop_returns="created", wpa_cli_returns="OK"):
        self.getprop_returns = getprop_returns
        self.wpa_cli_returns = wpa_cli_returns
        self.calls = []

    def run(self, cmd, timeout=5):
        self.calls.append(cmd)
        if cmd[0] == "getprop":
            return self.getprop_returns
        if cmd[0] == "wpa_cli":
            return self.wpa_cli_returns
        raise RuntimeError("unexpected cmd: " + " ".join(cmd))


def test_ap_controller_get_returns_enabled_when_created():
    runner = _FakeRunner(getprop_returns="created\n")
    ctrl = mb.ApController(interface="p2p-wlan0-0", runner=runner.run)
    assert ctrl.get() == {"enabled": True, "interface": "p2p-wlan0-0"}


def test_ap_controller_get_returns_disabled_when_removed():
    runner = _FakeRunner(getprop_returns="disabled\n")
    ctrl = mb.ApController(interface="p2p-wlan0-0", runner=runner.run)
    assert ctrl.get() == {"enabled": False, "interface": "p2p-wlan0-0"}


def test_ap_controller_disable_calls_wpa_cli_group_remove():
    runner = _FakeRunner(getprop_returns="disabled\n")
    ctrl = mb.ApController(interface="p2p-wlan0-0", runner=runner.run)
    ctrl.disable()
    # First call is wpa_cli group_remove
    assert runner.calls[0][0] == "wpa_cli"
    assert "p2p_group_remove" in runner.calls[0]


def test_ap_controller_enable_calls_wpa_cli_group_add():
    runner = _FakeRunner(getprop_returns="created\n")
    ctrl = mb.ApController(interface="p2p-wlan0-0", runner=runner.run)
    ctrl.enable()
    assert runner.calls[0][0] == "wpa_cli"
    assert "p2p_group_add" in runner.calls[0]


def test_ap_controller_resolves_interface_from_property():
    """interface 引数 None → getprop net.wifi.ap.interface から取得."""
    seq = {"net.wifi.ap.interface": "p2p-wlan0-0\n",
           "net.wifi.ap.state": "created\n"}

    def runner(cmd, timeout=5):
        if cmd[0] == "getprop":
            return seq.get(cmd[1], "")
        if cmd[0] == "wpa_cli":
            return "OK"
        raise RuntimeError(cmd)

    ctrl = mb.ApController(interface=None, runner=runner)
    state = ctrl.get()
    assert state["interface"] == "p2p-wlan0-0"
    assert state["enabled"] is True


def test_ap_controller_disable_returns_observed_state():
    """disable() 後の get() で disabled が返るのを確認."""
    # getprop は disable 後に呼ばれるので、 既に disabled を返す
    runner = _FakeRunner(getprop_returns="disabled\n", wpa_cli_returns="OK")
    ctrl = mb.ApController(interface="p2p-wlan0-0", runner=runner.run)
    result = ctrl.disable()
    assert result == {"enabled": False, "interface": "p2p-wlan0-0"}


# ---------------------------------------------------------------------------
# spec 019: ApController + ApStateStore wiring
# ---------------------------------------------------------------------------


class _FakeStateStore(object):
    def __init__(self, write_raises=None):
        self.writes = []
        self._write_raises = write_raises

    def write(self, state):
        if self._write_raises is not None:
            raise self._write_raises
        self.writes.append(state)


def test_ap_controller_without_state_store_still_works():
    """既存挙動互換: state_store 未指定で従来通り動く。"""
    runner = _FakeRunner(getprop_returns="created\n")
    ctrl = mb.ApController(interface="p2p-wlan0-0", runner=runner.run)
    ctrl.enable()
    assert any(c[0] == "wpa_cli" for c in runner.calls)


def test_enable_writes_enabled_to_state_store_when_provided():
    runner = _FakeRunner(getprop_returns="created\n")
    store = _FakeStateStore()
    ctrl = mb.ApController(interface="p2p-wlan0-0",
                            runner=runner.run, state_store=store)
    ctrl.enable()
    assert store.writes == ["enabled"]


def test_disable_writes_disabled_to_state_store_when_provided():
    runner = _FakeRunner(getprop_returns="disabled\n")
    store = _FakeStateStore()
    ctrl = mb.ApController(interface="p2p-wlan0-0",
                            runner=runner.run, state_store=store)
    ctrl.disable()
    assert store.writes == ["disabled"]


def test_state_store_write_failure_does_not_break_toggle():
    """store.write が IOError 等で爆発しても toggle 自体は成功扱い。"""
    runner = _FakeRunner(getprop_returns="disabled\n")
    store = _FakeStateStore(write_raises=IOError("disk full"))
    ctrl = mb.ApController(interface="p2p-wlan0-0",
                            runner=runner.run, state_store=store)
    # 例外を上に飛ばさない
    result = ctrl.disable()
    assert result == {"enabled": False, "interface": "p2p-wlan0-0"}


# ---------------------------------------------------------------------------
# spec 019: apply_ap_state_restore pure helper
# ---------------------------------------------------------------------------


class _RecordingController(object):
    def __init__(self):
        self.calls = []

    def enable(self):
        self.calls.append("enable")

    def disable(self):
        self.calls.append("disable")


def test_apply_ap_state_restore_calls_enable_when_stored_enabled():
    ctrl = _RecordingController()
    result = mb.apply_ap_state_restore("enabled", ctrl)
    assert ctrl.calls == ["enable"]
    assert result == "enabled"


def test_apply_ap_state_restore_calls_disable_when_stored_disabled():
    ctrl = _RecordingController()
    result = mb.apply_ap_state_restore("disabled", ctrl)
    assert ctrl.calls == ["disable"]
    assert result == "disabled"


def test_apply_ap_state_restore_noop_when_none():
    ctrl = _RecordingController()
    result = mb.apply_ap_state_restore(None, ctrl)
    assert ctrl.calls == []
    assert result is None
