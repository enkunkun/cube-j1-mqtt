"""spec 041 Phase 2b: uncaught-exception hook records tracebacks to a file
and mirrors a summary into the JSON logger (FR-002)."""
import sys

import mqtt_bridge as mb


class _FakeLogger(object):
    def __init__(self):
        self.errors = []

    def error(self, event="log", msg=None, context=None):
        self.errors.append((event, msg, context))


def _raise_value_error():
    raise ValueError("boom-detail")


def _capture_hook(hook):
    """Run hook() with the exc_info of a fresh ValueError."""
    try:
        _raise_value_error()
    except ValueError:
        hook(*sys.exc_info())


def test_hook_writes_traceback_file_and_chains_prev(tmp_path):
    path = str(tmp_path / "stderr.log")
    logger = _FakeLogger()
    prev_calls = []
    hook = mb._make_uncaught_exception_hook(
        logger=logger, stderr_path=path,
        prev_hook=lambda *args: prev_calls.append(args))

    _capture_hook(hook)

    with open(path) as f:
        text = f.read()
    assert "=====" in text
    assert "ValueError" in text
    assert "boom-detail" in text
    assert len(prev_calls) == 1
    assert [event for event, _, _ in logger.errors] == ["uncaught_exception"]
    assert "ValueError" in logger.errors[0][2]["summary"]


def test_hook_appends_per_crash(tmp_path):
    path = str(tmp_path / "stderr.log")
    hook = mb._make_uncaught_exception_hook(stderr_path=path)

    _capture_hook(hook)
    _capture_hook(hook)

    with open(path) as f:
        text = f.read()
    headers = [ln for ln in text.splitlines() if ln.startswith("=====")]
    assert len(headers) == 2
    # Each crash contributes the raise-site line + the final exception line.
    assert text.count("ValueError: boom-detail") == 2


def test_hook_truncates_when_over_cap(tmp_path):
    import os
    path = str(tmp_path / "stderr.log")
    with open(path, "w") as f:
        f.write("x" * (mb._STDERR_LOG_MAX_BYTES + 1))
    hook = mb._make_uncaught_exception_hook(stderr_path=path)

    _capture_hook(hook)

    size = os.path.getsize(path)
    assert 0 < size < mb._STDERR_LOG_MAX_BYTES


def test_install_excepthook_replaces_and_restores(tmp_path):
    old = sys.excepthook
    try:
        mb.install_excepthook(stderr_path=str(tmp_path / "s.log"))
        installed = sys.excepthook
        assert installed is not old
        assert callable(installed)

        # The factory-built hook chains into the previous hook it was given.
        seen = []
        probe_hook = mb._make_uncaught_exception_hook(
            stderr_path=str(tmp_path / "s.log"),
            prev_hook=lambda *args: seen.append(args))
        _capture_hook(probe_hook)
        assert len(seen) == 1
    finally:
        sys.excepthook = old
