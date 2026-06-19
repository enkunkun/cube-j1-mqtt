"""Constitution I gate: production_tool/ must keep its original 6-file layout.

This catches accidental scope creep into the auto-executed USB payload.
The bridge changes are additive *inside* mqtt_bridge.py / config.json, never
new files in production_tool/.
"""
import os

import mqtt_bridge as mb


EXPECTED_FILES = {
    "config.json",
    "led_effect.sh",
    "mqtt_bridge.py",
    "mqtt_ha_bridge.rc",
    "ndeclite_disabled.rc",
    "production_tool",       # Android shell launcher script
    "wisund_disabled.rc",
    "wpa_supplicant.conf",
}


def _production_tool_dir():
    return os.path.abspath(os.path.join(
        os.path.dirname(mb.__file__),  # production_tool/mqtt_bridge.py
    ))


def test_production_tool_directory_has_only_canonical_files():
    found = set()
    for name in os.listdir(_production_tool_dir()):
        full = os.path.join(_production_tool_dir(), name)
        if not os.path.isfile(full):
            continue
        if name.startswith(".") or name.endswith(".pyc"):
            continue
        if name.startswith("__"):
            continue
        found.add(name)
    assert found == EXPECTED_FILES, (
        "production_tool/ layout drifted. Expected={!r} Found={!r}".format(
            EXPECTED_FILES, found))
