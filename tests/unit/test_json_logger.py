"""JsonLogger emits one JSON object per line with ts/level/event/context.

Spec FR-006/007/009. Backed by logging.handlers.RotatingFileHandler so it
works under Python 2.7 stdlib on the Cube J1.
"""
import io
import json
import logging
import os

import pytest

import mqtt_bridge as mb


@pytest.fixture
def tmp_log_path(tmp_path):
    return str(tmp_path / "mqtt_bridge.log")


def _read_lines(path):
    with open(path) as f:
        return [line for line in f.read().splitlines() if line]


def _parse_lines(path):
    return [json.loads(line) for line in _read_lines(path)]


# ---------------------------------------------------------------------------
# JSON Lines shape
# ---------------------------------------------------------------------------

def test_logger_emits_required_keys(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="info")
    log.info(event="bridge_start", context={"device_id": "cubej1"})
    log.close()

    [entry] = _parse_lines(tmp_log_path)
    assert set(entry.keys()) >= {"ts", "level", "event"}
    assert entry["level"] == "info"
    assert entry["event"] == "bridge_start"
    assert entry["context"] == {"device_id": "cubej1"}


def test_logger_timestamp_is_iso8601_utc(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="info")
    log.info(event="ping")
    log.close()

    [entry] = _parse_lines(tmp_log_path)
    # YYYY-MM-DDTHH:MM:SSZ
    assert len(entry["ts"]) == 20
    assert entry["ts"].endswith("Z")
    assert entry["ts"][4] == "-" and entry["ts"][10] == "T"


def test_logger_each_level_emits_correct_level_string(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="debug")
    log.debug(event="d")
    log.info(event="i")
    log.warn(event="w")
    log.error(event="e")
    log.close()

    levels = [entry["level"] for entry in _parse_lines(tmp_log_path)]
    assert levels == ["debug", "info", "warn", "error"]


def test_logger_omits_context_when_not_supplied(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="info")
    log.info(event="bare")
    log.close()

    [entry] = _parse_lines(tmp_log_path)
    assert "context" not in entry


def test_logger_msg_field_carried_through(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="info")
    log.info(event="legacy", msg="something happened")
    log.close()

    [entry] = _parse_lines(tmp_log_path)
    assert entry["msg"] == "something happened"


# ---------------------------------------------------------------------------
# Level threshold (FR-007)
# ---------------------------------------------------------------------------

def test_logger_drops_events_below_threshold(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="warn")
    log.debug(event="d")
    log.info(event="i")
    log.warn(event="w")
    log.error(event="e")
    log.close()

    levels = [entry["level"] for entry in _parse_lines(tmp_log_path)]
    assert levels == ["warn", "error"]


def test_logger_default_level_is_info(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path)  # no explicit level
    log.debug(event="d")
    log.info(event="i")
    log.close()

    levels = [entry["level"] for entry in _parse_lines(tmp_log_path)]
    assert levels == ["info"]


# ---------------------------------------------------------------------------
# stderr fallback (FR-009)
# ---------------------------------------------------------------------------

def test_logger_falls_back_to_stderr_when_path_unwritable(tmp_path, capsys):
    # A directory-as-path makes RotatingFileHandler raise.
    bogus = str(tmp_path / "readonly_dir")
    os.mkdir(bogus)

    log = mb.JsonLogger(bogus, level="info")  # constructor must NOT raise
    log.info(event="bridge_start", context={"k": "v"})
    log.close()

    captured = capsys.readouterr()
    assert "bridge_start" in captured.err


# ---------------------------------------------------------------------------
# JSON serialisability for non-trivial context payloads
# ---------------------------------------------------------------------------

def test_logger_serialises_nested_context(tmp_log_path):
    log = mb.JsonLogger(tmp_log_path, level="info")
    log.info(event="poll_success", context={
        "power_w": 340,
        "current": {"r": 1.4, "t": 1.5},
        "tags": ["a", "b"],
    })
    log.close()

    [entry] = _parse_lines(tmp_log_path)
    assert entry["context"]["current"]["r"] == 1.4
    assert entry["context"]["tags"] == ["a", "b"]
