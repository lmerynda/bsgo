"""Importing this restores the packed test images (tools/test-fixtures) first."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "test-fixtures" / "restore.py"
_spec = importlib.util.spec_from_file_location("bsgo_test_fixtures", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_module.ensure()
