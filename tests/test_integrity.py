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
    assert result["h02_runtime_files_byte_equal"] == 16
    assert result["h02_changed_files"] == ["__init__.py"]
    assert result["h03_runtime_files_byte_equal"] == 16
    assert result["h03_changed_files"] == ["config_flow.py"]
    assert result["h04_runtime_files_byte_equal"] == 9
    assert result["h04_changed_files"] == [
        "button.py", "datetime.py", "number.py", "switch.py", "time.py",
        "translations/de.json", "translations/en.json", "translations/nl.json",
    ]
    assert result["h04_added_files"] == ["_commands.py"]
    assert result["h05_runtime_files_byte_equal"] == 13
    assert result["h05_changed_files"] == [
        "coordinator.py", "time.py", "translations/de.json",
        "translations/en.json", "translations/nl.json",
    ]
    assert result["h05_added_files"] == ["_night_times.py"]
    assert result["m03_runtime_files_byte_equal"] == 18
    assert result["m03_changed_files"] == ["button.py"]
