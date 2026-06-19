"""RotatingFileHandler integration: respects log_max_bytes / log_backup_count.

Spec FR-008, SC-002. Uses very small maxBytes to force rotation quickly.
"""
import glob
import os

import mqtt_bridge as mb


def test_log_rotation_creates_backup_files(tmp_path):
    log_path = str(tmp_path / "mqtt_bridge.log")
    log = mb.JsonLogger(log_path, level="info",
                        max_bytes=200, backup_count=3)
    for i in range(200):
        log.info(event="bridge_start",
                 context={"i": i, "padding": "x" * 30})
    log.close()

    files = sorted(glob.glob(log_path + "*"))
    # Current file + at least one backup must exist.
    assert log_path in files
    assert len(files) >= 2


def test_log_rotation_caps_backup_count_at_three(tmp_path):
    log_path = str(tmp_path / "mqtt_bridge.log")
    log = mb.JsonLogger(log_path, level="info",
                        max_bytes=100, backup_count=3)
    # Write enough to force many rotations.
    for i in range(500):
        log.info(event="evt", context={"i": i, "padding": "x" * 50})
    log.close()

    files = set(glob.glob(log_path + "*"))
    expected_max = {log_path, log_path + ".1",
                    log_path + ".2", log_path + ".3"}
    # No higher-numbered backup must exist beyond backup_count.
    assert log_path + ".4" not in files
    assert log_path + ".5" not in files
    # Files that DO exist must be a subset of the allowed set.
    assert files.issubset(expected_max)


def test_log_rotation_respects_smaller_backup_count(tmp_path):
    log_path = str(tmp_path / "mqtt_bridge.log")
    log = mb.JsonLogger(log_path, level="info",
                        max_bytes=100, backup_count=1)
    for i in range(200):
        log.info(event="evt", context={"i": i, "padding": "x" * 50})
    log.close()

    files = set(glob.glob(log_path + "*"))
    assert files.issubset({log_path, log_path + ".1"})
    assert log_path + ".2" not in files


def test_existing_file_is_appended_to(tmp_path):
    log_path = str(tmp_path / "mqtt_bridge.log")
    log = mb.JsonLogger(log_path, level="info",
                        max_bytes=1_000_000, backup_count=3)
    log.info(event="first")
    log.close()

    log = mb.JsonLogger(log_path, level="info",
                        max_bytes=1_000_000, backup_count=3)
    log.info(event="second")
    log.close()

    with open(log_path) as f:
        lines = [line for line in f.read().splitlines() if line]
    assert len(lines) == 2
