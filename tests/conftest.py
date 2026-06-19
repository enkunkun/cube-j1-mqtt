"""Make `production_tool/mqtt_bridge.py` importable from tests.

Cube J1 上では `mqtt_bridge.py` は単一スクリプトとして起動される（パッケージ化しない）。
ホスト側テストでは `sys.path` を介してモジュールとして読み込む。
"""
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PROD_TOOL = os.path.join(_REPO_ROOT, "production_tool")
if _PROD_TOOL not in sys.path:
    sys.path.insert(0, _PROD_TOOL)
