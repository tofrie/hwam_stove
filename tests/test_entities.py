"""Frozen description inventory and every sensor/alarm value."""

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from pystove import pystove

from .helpers import DOMAIN, ENTRY_ID

ROWS = json.loads((Path(__file__).parent / "fixtures/entities.json").read_text())
pytestmark = pytest.mark.contract


def test_inventory_counts():
    assert Counter(row["platform"] for row in ROWS) == {
        "sensor": 14, "binary_sensor": 18, "button": 2, "switch": 2,
        "time": 2, "datetime": 1, "number": 1,
    }
    assert len({(r["platform"], r["key"]) for r in ROWS}) == 40


@pytest.mark.parametrize("row", ROWS, ids=lambda r: f'{r["platform"]}.{r["key"]}')
async def test_entity_contract(row, entities, hass):
    platform, key = row["platform"], row["key"]
    entity = entities[platform, key]
    description = entity.entity_description
    assert entity.unique_id == f"{ENTRY_ID}-{key}"
    identifier = (DOMAIN, f'{ENTRY_ID}-{row["device"]}')
    assert entity.device_info["identifiers"] == {identifier}
    assert entity.has_entity_name is True
    assert description.translation_key == row["translation_key"]
    assert entity.entity_category == row["category"]
    assert entity.device_class == row["device_class"]
    assert description.entity_registry_enabled_default == row["enabled_default"]
    if "alarm_source" in row:
        assert description.value_source_key == row["alarm_source"]
        assert description.alarm_str == row["alarm_text"]
    for attr, field in (("native_unit_of_measurement", "unit"),
                        ("options", "options"),
                        ("suggested_unit_of_measurement", "suggested_unit"),
                        ("suggested_display_precision", "precision")):
        assert getattr(description, attr, None) == row[field]
    registry = er.async_get(hass)
    registry_id = registry.async_get_entity_id(platform, DOMAIN, entity.unique_id)
    registered = registry.async_get(registry_id)
    assert registered.config_entry_id == ENTRY_ID
    device = dr.async_get(hass).async_get(registered.device_id)
    assert device.identifiers == entity.device_info["identifiers"]
    assert (registered.disabled_by is None) == row["enabled_default"]


SENSOR_VALUES = {
    "algorithm": 7, "message_id": 42, "new_fire_wood_estimate": None,
    "night_lowering": "on_day", "operation_mode": "normal", "oxygen_level": 20,
    "phase": "burn", "room_temperature": 21, "stove_temperature": 273,
    "time_since_remote_msg": 31, "time_to_new_fire_wood": 4020,
    "valve1_position": 10, "valve2_position": 20, "valve3_position": 30,
}


@pytest.mark.parametrize("key,value", SENSOR_VALUES.items())
async def test_sensor_value(key, value, entities):
    assert entities["sensor", key].native_value == value


async def test_glow_estimate_has_ha_timezone(entities, loaded):
    loaded.data["phase"] = "Glow"
    entity = entities["sensor", "new_fire_wood_estimate"]
    entity._handle_coordinator_update()
    assert entity.native_value == datetime(
        2024, 3, 1, 1, 5, 57, tzinfo=ZoneInfo("Europe/Berlin")
    )


@pytest.mark.parametrize("field,values,expected", [
    ("phase", pystove.PHASE, ["ignition", "burn", "burn", "burn", "glow", "standby"]),
    ("night_lowering", pystove.NIGHT_LOWERING_STATES,
     ["disabled", "init", "on_day", "on_night", "on_manual_night"]),
    ("operation_mode", pystove.OPERATION_MODES,
     ["init", "self_test", "normal", "temperature_fault", "o2_fault", "calibration",
      "safety", "manual", "motor_test", "slow_combustion", "low_voltage"]),
])
async def test_all_enum_values(field, values, expected, entities, loaded):
    entity = entities["sensor", field]
    for source, output in zip(values, expected, strict=True):
        loaded.data[field] = source
        entity._handle_coordinator_update()
        assert entity.native_value == output
        assert output in entity.options


async def test_no_alarms_and_refill(entities, loaded):
    loaded.data["refill_alarm"] = 0
    for (platform, _), entity in entities.items():
        if platform == "binary_sensor":
            entity._handle_coordinator_update()
            assert entity.is_on is False
    loaded.data["refill_alarm"] = 1
    refill = entities["binary_sensor", "refill_alarm"]
    refill._handle_coordinator_update()
    assert refill.is_on is True


@pytest.mark.parametrize("field,alarm", [
    *(('maintenance_alarms', x) for x in pystove.MAINTENANCE_ALARMS),
    *(('safety_alarms', x) for x in pystove.SAFETY_ALARMS),
    ("maintenance_alarms", "Synthetic unknown maintenance"),
    ("safety_alarms", "Synthetic unknown safety"),
])
async def test_every_alarm(field, alarm, entities, loaded):
    loaded.data[field] = [alarm]
    for (platform, key), entity in entities.items():
        if platform != "binary_sensor" or key == "refill_alarm":
            continue
        description = entity.entity_description
        entity._handle_coordinator_update()
        expected = description.value_source_key == field and (
            description.alarm_str is None or description.alarm_str == alarm
        )
        assert entity.is_on is expected
