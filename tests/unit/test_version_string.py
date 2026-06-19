"""Self-reported bridge version string.

`bridge_version()` joins `BRIDGE_SEMVER` and `BRIDGE_GIT_HASH` with `+`. When
`scripts/embed_git_hash.sh` is skipped before USB distribution the hash stays
at the default `"unknown"` and the version reads `<semver>+unknown`.
"""
import re

import mqtt_bridge as mb


def test_bridge_version_joins_semver_and_hash_with_plus(monkeypatch):
    monkeypatch.setattr(mb, "BRIDGE_SEMVER", "1.2.3")
    monkeypatch.setattr(mb, "BRIDGE_GIT_HASH", "abc1234")
    assert mb.bridge_version() == "1.2.3+abc1234"


def test_bridge_version_falls_back_to_unknown_hash(monkeypatch):
    monkeypatch.setattr(mb, "BRIDGE_SEMVER", "0.1.0")
    monkeypatch.setattr(mb, "BRIDGE_GIT_HASH", "unknown")
    assert mb.bridge_version() == "0.1.0+unknown"


def test_bridge_semver_default_matches_semver_regex():
    assert re.match(r"^\d+\.\d+\.\d+$", mb.BRIDGE_SEMVER)


def test_bridge_git_hash_default_is_hex_or_unknown():
    assert re.match(r"^([a-f0-9]{4,40}|unknown)$", mb.BRIDGE_GIT_HASH)
