"""spec 019: ApStateStore persists Wi-Fi AP toggle state across reboots."""
import io
import os
import tempfile
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# Read tests — use _FakeOpener (dict-based, no real filesystem)
# ---------------------------------------------------------------------------


def _make_fake_opener(files):
    """Returns opener(path, mode) callable that reads from `files` dict."""

    def opener(path, mode="r"):
        if path not in files:
            raise IOError("[Errno 2] No such file or directory: {}".format(path))
        return io.BytesIO(files[path])

    return opener


def test_read_returns_none_when_file_missing():
    store = mb.ApStateStore("/tmp/nonexistent", opener=_make_fake_opener({}))
    assert store.read() is None


def test_read_returns_enabled_when_file_contains_enabled():
    files = {"/x": b"enabled"}
    store = mb.ApStateStore("/x", opener=_make_fake_opener(files))
    assert store.read() == "enabled"


def test_read_returns_disabled_when_file_contains_disabled():
    files = {"/x": b"disabled"}
    store = mb.ApStateStore("/x", opener=_make_fake_opener(files))
    assert store.read() == "disabled"


def test_read_returns_none_when_file_empty():
    files = {"/x": b""}
    store = mb.ApStateStore("/x", opener=_make_fake_opener(files))
    assert store.read() is None


def test_read_returns_none_when_file_invalid_value():
    files = {"/x": b"foobar"}
    store = mb.ApStateStore("/x", opener=_make_fake_opener(files))
    assert store.read() is None


def test_read_strips_whitespace_and_newline():
    files = {"/x": b"  enabled\n"}
    store = mb.ApStateStore("/x", opener=_make_fake_opener(files))
    assert store.read() == "enabled"


# ---------------------------------------------------------------------------
# Write tests — use real tempfile.mkstemp (AtomicWriter touches the FS)
# ---------------------------------------------------------------------------


def test_write_then_read_round_trip_enabled():
    fd, path = tempfile.mkstemp(prefix="ap-state-test-")
    os.close(fd)
    try:
        store = mb.ApStateStore(path)  # real open for read
        store.write("enabled")
        assert store.read() == "enabled"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_write_then_read_round_trip_disabled():
    fd, path = tempfile.mkstemp(prefix="ap-state-test-")
    os.close(fd)
    try:
        store = mb.ApStateStore(path)
        store.write("disabled")
        assert store.read() == "disabled"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_write_rejects_invalid_value():
    store = mb.ApStateStore("/tmp/should-not-be-written")
    try:
        store.write("foobar")
    except ValueError as e:
        assert "foobar" in str(e) or "enabled" in str(e) or "disabled" in str(e)
    else:
        raise AssertionError("expected ValueError, got nothing")
