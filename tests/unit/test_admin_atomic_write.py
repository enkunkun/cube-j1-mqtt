"""AtomicWriter writes via temp file + os.rename so the target is never seen
in a half-written state, and is fully removed if the write raises."""
import json
import os
import pytest

import mqtt_bridge as mb


def test_write_bytes_creates_target_atomically(tmp_path):
    target = str(tmp_path / "config.json")
    mb.AtomicWriter.write_bytes(target, b"hello world")
    with open(target, "rb") as f:
        assert f.read() == b"hello world"


def test_write_bytes_overwrites_existing(tmp_path):
    target = str(tmp_path / "config.json")
    with open(target, "w") as f:
        f.write("old content")
    mb.AtomicWriter.write_bytes(target, b"new content")
    with open(target, "rb") as f:
        assert f.read() == b"new content"


def test_write_bytes_does_not_leave_temp_files_on_success(tmp_path):
    target = str(tmp_path / "config.json")
    mb.AtomicWriter.write_bytes(target, b"x")
    entries = os.listdir(str(tmp_path))
    assert entries == ["config.json"]


def test_write_json_serialises_and_writes(tmp_path):
    target = str(tmp_path / "config.json")
    mb.AtomicWriter.write_json(target, {"a": 1, "b": "two"})
    with open(target) as f:
        loaded = json.load(f)
    assert loaded == {"a": 1, "b": "two"}


def test_write_bytes_leaves_existing_target_intact_on_failure(tmp_path, monkeypatch):
    target = str(tmp_path / "config.json")
    with open(target, "w") as f:
        f.write("original")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mb.os, "rename", boom)
    with pytest.raises(OSError):
        mb.AtomicWriter.write_bytes(target, b"new content")

    # original content must still be present
    with open(target) as f:
        assert f.read() == "original"
    # and no leftover temp file
    leftover = [name for name in os.listdir(str(tmp_path))
                if name != "config.json"]
    assert leftover == []


def test_write_bytes_temp_is_in_same_directory_for_atomic_rename(tmp_path, monkeypatch):
    target = str(tmp_path / "config.json")
    seen_temp = {}

    real_rename = mb.os.rename

    def capture_rename(src, dst):
        seen_temp["src"] = src
        real_rename(src, dst)

    monkeypatch.setattr(mb.os, "rename", capture_rename)
    mb.AtomicWriter.write_bytes(target, b"x")
    assert os.path.dirname(seen_temp["src"]) == str(tmp_path)
