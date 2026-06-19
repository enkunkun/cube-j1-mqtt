"""tail_log returns the last N non-empty lines from a file, clamping N."""
import mqtt_bridge as mb


def test_tail_log_returns_last_n_lines(tmp_path):
    log_path = str(tmp_path / "bridge.log")
    with open(log_path, "w") as f:
        f.write("\n".join('{{"i":{}}}'.format(i) for i in range(20)) + "\n")
    assert mb.tail_log(log_path, 3) == [
        '{"i":17}', '{"i":18}', '{"i":19}',
    ]


def test_tail_log_clamps_n_to_upper_bound(tmp_path):
    log_path = str(tmp_path / "bridge.log")
    with open(log_path, "w") as f:
        f.write("\n".join("line{}".format(i) for i in range(2000)) + "\n")
    result = mb.tail_log(log_path, 5000)
    assert len(result) == 1000
    assert result[-1] == "line1999"


def test_tail_log_clamps_n_to_at_least_one(tmp_path):
    log_path = str(tmp_path / "bridge.log")
    with open(log_path, "w") as f:
        f.write("only\n")
    assert mb.tail_log(log_path, 0) == ["only"]
    assert mb.tail_log(log_path, -5) == ["only"]


def test_tail_log_returns_empty_when_file_missing(tmp_path):
    assert mb.tail_log(str(tmp_path / "nope.log"), 10) == []


def test_tail_log_skips_blank_trailing_lines(tmp_path):
    log_path = str(tmp_path / "bridge.log")
    with open(log_path, "w") as f:
        f.write("first\nsecond\n\n\n")
    assert mb.tail_log(log_path, 5) == ["first", "second"]


def test_tail_log_handles_file_smaller_than_n(tmp_path):
    log_path = str(tmp_path / "bridge.log")
    with open(log_path, "w") as f:
        f.write("a\nb\n")
    assert mb.tail_log(log_path, 10) == ["a", "b"]
