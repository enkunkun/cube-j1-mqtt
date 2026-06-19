"""read_config_masked reads config.json and replaces admin_password with '***'."""
import json

import mqtt_bridge as mb


def test_read_config_masked_returns_dict_with_password_masked(tmp_path):
    config_path = str(tmp_path / "config.json")
    with open(config_path, "w") as f:
        json.dump({"admin_user": "admin", "admin_password": "secret",
                   "mqtt_host": "x"}, f)
    out = mb.read_config_masked(config_path)
    assert out["admin_password"] == "***"
    assert out["admin_user"] == "admin"
    assert out["mqtt_host"] == "x"


def test_read_config_masked_does_not_modify_disk_file(tmp_path):
    config_path = str(tmp_path / "config.json")
    with open(config_path, "w") as f:
        json.dump({"admin_password": "secret"}, f)
    mb.read_config_masked(config_path)
    with open(config_path) as f:
        on_disk = json.load(f)
    assert on_disk["admin_password"] == "secret"


def test_read_config_masked_handles_missing_admin_password(tmp_path):
    config_path = str(tmp_path / "config.json")
    with open(config_path, "w") as f:
        json.dump({"mqtt_host": "x"}, f)
    out = mb.read_config_masked(config_path)
    assert "admin_password" not in out
