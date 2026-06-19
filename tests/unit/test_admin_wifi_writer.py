"""_rewrite_wpa_supplicant updates ssid/psk in the existing template while
preserving every other line (freq_list, scan_ssid, key_mgmt, etc).

Tested as a pure function: the read-side reads from a real file, the
write-side returns a string. The atomic write to /data/misc/wifi/... is
exercised separately by AtomicWriter tests.
"""
import mqtt_bridge as mb


# ---------------------------------------------------------------------------
# Template with the realistic Cube J1 fields (freq_list / scan_ssid)
# ---------------------------------------------------------------------------

TEMPLATE = (
    "ctrl_interface=/data/misc/wifi/sockets\n"
    "update_config=1\n"
    "\n"
    "network={\n"
    "        ssid=\"OLD-NET\"\n"
    "        psk=\"oldpassword\"\n"
    "        key_mgmt=WPA-PSK\n"
    "        proto=RSN\n"
    "        pairwise=CCMP\n"
    "        group=CCMP TKIP\n"
    "        freq_list=2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462 2467 2472\n"
    "        scan_ssid=1\n"
    "}\n"
)


def _write_template(tmp_path, body=TEMPLATE):
    p = tmp_path / "wpa_supplicant.conf"
    p.write_text(body)
    return str(p)


def test_rewrites_ssid_and_psk_only(tmp_path):
    path = _write_template(tmp_path)
    out = mb._rewrite_wpa_supplicant(path, ssid="NEW-NET", psk="newsecret")
    assert "ssid=\"NEW-NET\"" in out
    assert "psk=\"newsecret\"" in out
    assert "ssid=\"OLD-NET\"" not in out
    assert "psk=\"oldpassword\"" not in out


def test_preserves_freq_list_and_scan_ssid(tmp_path):
    path = _write_template(tmp_path)
    out = mb._rewrite_wpa_supplicant(path, ssid="X", psk="12345678")
    assert "freq_list=2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462 2467 2472" in out
    assert "scan_ssid=1" in out


def test_preserves_proto_pairwise_group_and_key_mgmt(tmp_path):
    path = _write_template(tmp_path)
    out = mb._rewrite_wpa_supplicant(path, ssid="X", psk="12345678")
    assert "proto=RSN" in out
    assert "pairwise=CCMP" in out
    assert "group=CCMP TKIP" in out
    assert "key_mgmt=WPA-PSK" in out


def test_preserves_ctrl_interface_and_update_config_headers(tmp_path):
    path = _write_template(tmp_path)
    out = mb._rewrite_wpa_supplicant(path, ssid="X", psk="12345678")
    assert out.startswith("ctrl_interface=/data/misc/wifi/sockets\nupdate_config=1\n")


def test_falls_back_to_minimal_template_when_file_missing(tmp_path):
    missing = str(tmp_path / "does-not-exist.conf")
    out = mb._rewrite_wpa_supplicant(missing, ssid="X", psk="12345678")
    # Minimal valid wpa_supplicant.conf
    assert "ctrl_interface=/data/misc/wifi/sockets" in out
    assert "update_config=1" in out
    assert "ssid=\"X\"" in out
    assert "psk=\"12345678\"" in out
    assert "key_mgmt=WPA-PSK" in out


def test_only_modifies_network_block_keys_not_other_occurrences(tmp_path):
    """A header outside the network={...} block that mentions 'ssid' must not
    be rewritten."""
    body = (
        "# example file with stray 'ssid' references\n"
        "# ssid=hint-in-comment\n"
        "ctrl_interface=/data/misc/wifi/sockets\n"
        "update_config=1\n"
        "\n"
        "network={\n"
        "        ssid=\"OLD\"\n"
        "        psk=\"oldpwd\"\n"
        "}\n"
    )
    path = _write_template(tmp_path, body=body)
    out = mb._rewrite_wpa_supplicant(path, ssid="NEW", psk="newpwd")
    assert "# ssid=hint-in-comment" in out
    assert "ssid=\"NEW\"" in out
    assert "psk=\"newpwd\"" in out


def test_appends_ssid_and_psk_if_missing_in_network_block(tmp_path):
    """If the template lacks ssid/psk lines inside `network={ }`, they get
    appended just before the closing brace."""
    body = (
        "ctrl_interface=/data/misc/wifi/sockets\n"
        "update_config=1\n"
        "\n"
        "network={\n"
        "        key_mgmt=WPA-PSK\n"
        "}\n"
    )
    path = _write_template(tmp_path, body=body)
    out = mb._rewrite_wpa_supplicant(path, ssid="FRESH", psk="hello123")
    assert "ssid=\"FRESH\"" in out
    assert "psk=\"hello123\"" in out
    assert "key_mgmt=WPA-PSK" in out
    # Sanity: still exactly one closing brace
    assert out.count("}") == 1
