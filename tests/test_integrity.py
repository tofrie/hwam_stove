"""The independent static checks also run in the full suite."""

import pytest

from scripts.check_integrity import validate


@pytest.mark.contract
def test_runtime_manifest_structure_and_translations():
    result = validate()
    assert result["runtime_files_byte_equal"] == 14
    assert result["b01_changed_files"] == ["__init__.py", "config_flow.py"]
    assert result["b01_added_files"] == ["migration.py"]
    assert result["h01a_runtime_files_byte_equal"] == 16
    assert result["h01a_changed_files"] == ["__init__.py"]
