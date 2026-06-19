"""Embedded admin HTML sanity checks (T013)."""
import mqtt_bridge as mb


def test_admin_html_has_title():
    assert "<title>Cube J1 Admin</title>" in mb.ADMIN_HTML


def test_admin_html_has_config_form():
    assert 'id="config-form"' in mb.ADMIN_HTML


def test_admin_html_has_wifi_form():
    assert 'id="wifi-form"' in mb.ADMIN_HTML


def test_admin_html_has_update_form_with_file_input():
    assert 'id="update-form"' in mb.ADMIN_HTML
    assert 'type="file"' in mb.ADMIN_HTML


def test_admin_html_has_no_external_script_or_stylesheet():
    # Stay self-contained — no CDN dependencies.
    assert "https://" not in mb.ADMIN_HTML
    assert "http://" not in mb.ADMIN_HTML


def test_admin_html_marks_wifi_form_as_risky():
    """User Story 3 + spec quickstart: warn before changing Wi-Fi."""
    risky_markers = ("⚠", "&#9888;", "危険", "warning", "Warning")
    assert any(marker in mb.ADMIN_HTML for marker in risky_markers)
