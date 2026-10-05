"""adbd の立ち上げ直し (spec 054 FR-001)."""
import mqtt_bridge as mb


def test_restart_adbd_runs_commands_in_order():
    """production_tool と同じく、TCP ポートを設定してから adbd を止めて起こす。"""
    calls = []
    mb._restart_adbd(run=lambda argv: calls.append(argv) or 0)
    assert calls == [
        ["setprop", "service.adb.tcp.port", "5555"],
        ["stop", "adbd"],
        ["start", "adbd"],
    ]


def test_restart_adbd_continues_after_a_command_fails():
    """1 つのコマンドが例外を投げても、残りのコマンドは実行する。"""
    calls = []

    def run(argv):
        calls.append(argv[0])
        if argv[0] == "stop":
            raise OSError("stop not found")
        return 0

    mb._restart_adbd(run=run)
    assert calls == ["setprop", "stop", "start"]
