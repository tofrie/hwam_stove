"""The independent static checks also run in the full suite."""

import pytest

from scripts.check_integrity import validate


@pytest.mark.contract
def test_runtime_manifest_structure_and_translations():
    result = validate()
    assert result["o02_base"] == "7b5187b617bd2672b27fa9660cf941a547d66bef"
    assert result["o02_changed_files"] == ["sensor.py"]
    assert result["o02_runtime_files_byte_equal"] == 21
    assert len(result["o02_measurement_keys"]) == 6
    assert result["o01_base"] == "2a93f53b8e60d80fa422d64e6b526936686897c9"
    assert result["o01_added_files"] == ["diagnostics.py"]
    assert result["o01_runtime_files_byte_equal"] == 21
    assert result["m01_changed_files"] == [
        "__init__.py", "config_flow.py", "const.py",
    ]
    assert result["m06_changed_files"] == [
        "__init__.py", "config_flow.py", "translations/de.json",
        "translations/en.json", "translations/nl.json",
    ]
    assert result["m05_changed_files"] == [
        "__init__.py", "config_flow.py", "translations/de.json",
        "translations/en.json", "translations/nl.json",
    ]
    assert result["m05_added_files"] == ["_host.py"]
    assert result["m04_changed_files"] == [
        "__init__.py", "config_flow.py", "translations/de.json",
        "translations/en.json", "translations/nl.json",
    ]
    assert result["m02_changed_files"] == [
        "button.py", "coordinator.py", "datetime.py", "number.py", "switch.py",
        "time.py",
    ]
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
    assert result["m08_runtime_files_byte_equal"] == 18
    assert result["m08_changed_files"] == ["sensor.py"]
    assert result["m07_runtime_files_byte_equal"] == 17
    assert result["m07_changed_files"] == ["button.py", "datetime.py"]
    assert result["m07_added_files"] == ["_clock.py"]

    assert result["dependency_migration"]["changed_files"] == [
        "custom_components/hwam_stove/manifest.json"
    ]
    assert result["dependency_migration"]["requirements"] == [
        "saynwerk-pystove==0.3.0rc1"
    ]
