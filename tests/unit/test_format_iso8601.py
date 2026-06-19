"""ISO 8601 UTC timestamp formatter used by DiagState and JsonLogger.

`format_iso8601_utc(epoch)` MUST:
- Return a UTC timestamp string with the trailing `Z` suffix
- Omit fractional seconds
- Render the canonical `YYYY-MM-DDTHH:MM:SSZ` shape
- Be tolerant of float epoch values (truncate, don't round, sub-second part)
"""
import calendar
from datetime import datetime, timezone

import pytest

import mqtt_bridge as mb


def _to_epoch(*args):
    """Build epoch seconds from a UTC datetime tuple."""
    return calendar.timegm(datetime(*args, tzinfo=timezone.utc).timetuple())


def test_format_iso8601_utc_renders_canonical_shape():
    epoch = _to_epoch(2026, 6, 19, 12, 34, 56)
    assert mb.format_iso8601_utc(epoch) == "2026-06-19T12:34:56Z"


def test_format_iso8601_utc_uses_zero_padding():
    epoch = _to_epoch(2026, 1, 2, 3, 4, 5)
    assert mb.format_iso8601_utc(epoch) == "2026-01-02T03:04:05Z"


def test_format_iso8601_utc_drops_fractional_seconds():
    epoch = _to_epoch(2026, 6, 19, 12, 34, 56) + 0.789
    assert mb.format_iso8601_utc(epoch) == "2026-06-19T12:34:56Z"


def test_format_iso8601_utc_handles_epoch_zero():
    assert mb.format_iso8601_utc(0) == "1970-01-01T00:00:00Z"


def test_format_iso8601_utc_handles_int_input():
    epoch = _to_epoch(2026, 6, 19, 0, 0, 0)
    assert mb.format_iso8601_utc(int(epoch)) == "2026-06-19T00:00:00Z"
