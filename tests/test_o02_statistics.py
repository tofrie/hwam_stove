"""O02: existing sensor values/identities and real Recorder statistics, offline."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder import history, statistics
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
    recorder as sensor_recorder,
)
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
)
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_platform,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.hwam_stove import sensor as sensor_module
from pystove import Stove

from .helpers import COMMANDS, DOMAIN, entity_id_for
from .test_entities import ROWS
from .test_m06_reconfigure import identities

pytestmark = pytest.mark.contract
MEASUREMENTS = (
    "room_temperature", "stove_temperature", "oxygen_level",
    "valve1_position", "valve2_position", "valve3_position",
)
EXCLUDED = {
    "algorithm", "message_id", "new_fire_wood_estimate", "night_lowering",
    "operation_mode", "phase", "time_since_remote_msg", "time_to_new_fire_wood",
}


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """HA's recorder fixture requires DB configuration before the hass fixture."""
    assert recorder_db_url == "sqlite://"


def live_sensor(hass, key):
    target = entity_id_for(hass, "sensor", key)
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if target in platform.entities:
            return platform.entities[target]
    raise AssertionError(f"Registered sensor missing: {key}")


@pytest.mark.parametrize("key", (*MEASUREMENTS, *sorted(EXCLUDED)))
async def test_complete_sensor_audit_and_existing_metadata(key, entities):
    entity = entities["sensor", key]
    row = next(r for r in ROWS if r["platform"] == "sensor" and r["key"] == key)
    expected = SensorStateClass.MEASUREMENT if key in MEASUREMENTS else None
    assert entity.state_class == expected
    assert entity.last_reset is None
    assert entity.device_class == row["device_class"]
    assert entity.native_unit_of_measurement == row["unit"]
    assert entity.entity_description.translation_key == row["translation_key"]
    assert entity.entity_description.entity_registry_enabled_default == row[
        "enabled_default"
    ]
    assert entity.entity_category == row["category"]
    if key in MEASUREMENTS and entity.device_class is not None:
        assert expected in DEVICE_CLASS_STATE_CLASSES[entity.device_class]
        assert entity.native_unit_of_measurement in DEVICE_CLASS_UNITS[
            entity.device_class
        ]


# Synthetic numeric coverage, not claimed physical limits of any firmware/model.
# In particular, preserve the pinned parser's integer truncation and pass-through.
VALUES = [
    *(pytest.param(key, raw, native, id=f"{key}-{raw}")
      for key in ("room_temperature", "stove_temperature")
      for raw, native in [(0, 0), (2100, 21), (-999, -9), (-27315, -273),
                          (60000, 600), (2199, 21)]),
    *(pytest.param("oxygen_level", raw, native, id=f"oxygen-{raw}")
      for raw, native in [(0, 0), (2095, 20), (10000, 100), (-155, -1)]),
    *(pytest.param(key, value, value, id=f"{key}-{value}")
      for key in ("valve1_position", "valve2_position", "valve3_position")
      for value in (0, 50, 100, -1, 37.5)),
]


@pytest.mark.parametrize("key,raw_value,native", VALUES)
async def test_real_parser_values_and_types_unchanged(
    hass, entry, loaded, stove, key, raw_value, native,
):
    raw = json.loads((Path(__file__).parent / "fixtures/raw_status.json").read_text())
    raw[key] = raw_value
    parser = Stove()  # No create/session; only published parsing, never transport.
    parser.get_raw_data = AsyncMock(return_value=raw)
    processed = await parser.get_data()
    assert processed[key] == native
    stove.data = processed
    sensor = live_sensor(hass, key)
    registered = er.async_get(hass).async_get(sensor.entity_id)
    reads = stove.get_data.await_count
    await loaded.async_refresh()
    await hass.async_block_till_done()
    assert sensor.native_value == native
    assert type(sensor.native_value) is type(native)
    assert loaded.data[key] == native
    state = hass.states.get(sensor.entity_id)
    assert float(state.state) == native
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["unit_of_measurement"] == (
        "°C" if "temperature" in key else "%"
    )
    assert er.async_get(hass).async_get(sensor.entity_id) == registered
    assert stove.get_data.await_count == reads + 1
    parser.get_raw_data.assert_awaited_once_with()
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()


@pytest.mark.parametrize("key", MEASUREMENTS)
async def test_identical_update_unavailable_and_recovery(
    hass, loaded, stove, key, caplog,
):
    sensor = live_sensor(hass, key)
    native, native_type = sensor.native_value, type(sensor.native_value)
    before = hass.states.get(sensor.entity_id)
    with patch.object(sensor, "async_write_ha_state",
                      wraps=sensor.async_write_ha_state) as write:
        await loaded.async_refresh()
        write.assert_not_called()  # always_update=False remains effective.
    assert hass.states.get(sensor.entity_id) == before
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    await loaded.async_refresh()
    unavailable = hass.states.get(sensor.entity_id)
    assert unavailable.state == "unavailable"
    assert unavailable.attributes["state_class"] == "measurement"
    assert sensor.native_value == native
    stove.get_data.return_value = deepcopy(loaded.data)
    await loaded.async_refresh()
    recovered = hass.states.get(sensor.entity_id)
    assert sensor.available
    assert sensor.native_value == native
    assert type(sensor.native_value) is native_type
    assert recovered.state == before.state
    assert recovered.attributes == before.attributes
    assert not [r for r in caplog.records
                if r.name.startswith("homeassistant.components.sensor")
                and r.levelno >= 30]


@pytest.mark.parametrize("key", ["room_temperature", "stove_temperature"])
async def test_existing_user_unit_option_remains_respected(hass, loaded, key):
    sensor = live_sensor(hass, key)
    registry = er.async_get(hass)
    native, native_type = sensor.native_value, type(sensor.native_value)
    registry.async_update_entity_options(
        sensor.entity_id, "sensor", {"unit_of_measurement": "°F"}
    )
    await hass.async_block_till_done()
    state = hass.states.get(sensor.entity_id)
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.native_value == native and type(sensor.native_value) is native_type
    assert state.attributes["unit_of_measurement"] == "°F"
    assert float(state.state) == pytest.approx(native * 1.8 + 32)
    assert state.attributes["device_class"] == SensorDeviceClass.TEMPERATURE
    assert state.attributes["state_class"] == "measurement"


@pytest.mark.parametrize("disabled", [False, True])
async def test_upgrade_preserves_customized_candidate_registry(
    hass, entry, stove, monkeypatch, disabled,
):
    with monkeypatch.context() as old:
        old.setattr(sensor_module, "SENSOR_DESCRIPTIONS", [
            replace(d, state_class=None) for d in sensor_module.SENSOR_DESCRIPTIONS
        ])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Custom area")
    original = entity_id_for(hass, "sensor", "valve1_position")
    custom = registry.async_update_entity(
        original, new_entity_id="sensor.my_valve", name="My valve",
        area_id=area.id, icon="mdi:valve", hidden_by=er.RegistryEntryHider.USER,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )
    registry.async_update_entity_options(
        custom.entity_id, "sensor", {"display_precision": 3}
    )
    await hass.async_block_till_done()
    before = identities(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert identities(hass, entry) == before
    assert len(before[0]) == 2 and len(before[1]) == 40
    if not disabled:
        state = hass.states.get(custom.entity_id)
        assert state.attributes["state_class"] == "measurement"
        assert state.state == "10"
    else:
        assert hass.states.get(custom.entity_id) is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert stove.destroy.await_count == 2


async def test_real_recorder_upgrade_history_metadata_and_hourly_statistics(
    hass, entry, stove, freezer, async_setup_recorder_instance, monkeypatch, caplog,
):
    """Real SQLite Recorder, old descriptions -> same entry reload -> real LTS.

    Only HA test clock/old description metadata are simulated. No DB insertion,
    migration, statistic importer, production access or controller communication.
    """
    freezer.move_to("2026-10-07T10:00:00+00:00")
    start = dt_util.utcnow()
    recorder = await async_setup_recorder_instance(hass)
    await hass.async_start()
    await hass.async_block_till_done()
    # Keep initial states strictly inside the history query's time interval.
    freezer.move_to(start + timedelta(seconds=1))

    old_descriptions = [replace(d, state_class=None)
                        for d in sensor_module.SENSOR_DESCRIPTIONS]
    with monkeypatch.context() as old:
        old.setattr(sensor_module, "SENSOR_DESCRIPTIONS", old_descriptions)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Custom room")
    room_id = entity_id_for(hass, "sensor", "room_temperature")
    registry.async_update_entity(
        room_id, new_entity_id="sensor.custom_room_temperature", name="My sensor",
        area_id=area.id, icon="mdi:thermometer", hidden_by=er.RegistryEntryHider.USER,
    )
    await hass.async_block_till_done()
    ids = {key: entity_id_for(hass, "sensor", key) for key in MEASUREMENTS}
    for entity_id in ids.values():
        assert "state_class" not in hass.states.get(entity_id).attributes
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        dr.async_get(hass).async_update_device(device.id, name_by_user="My device")
    before_identity = identities(hass, entry)
    before_entry = (entry.entry_id, entry.version, entry.minor_version,
                    dict(entry.data), dict(entry.options))
    await async_wait_recording_done(hass)

    async def compile_period(minute):
        do_adhoc_statistics(hass, start=start + timedelta(minutes=minute))
        await async_wait_recording_done(hass)

    async def read_statistics(period):
        return await recorder.async_add_executor_job(
            statistics.statistics_during_period, hass, start,
            start + timedelta(hours=1), set(ids.values()), period, None,
            {"min", "max", "mean", "sum"},
        )

    async def read_history():
        return await recorder.async_add_executor_job(
            history.get_significant_states, hass, start,
            start + timedelta(minutes=5), list(ids.values()),
        )

    freezer.move_to(start + timedelta(minutes=2))
    stove.data.update(dict.fromkeys(MEASUREMENTS, 0))
    coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(minutes=5))
    await compile_period(0)
    assert await read_statistics("5minute") == {}
    old_history = await read_history()
    assert set(old_history) == set(ids.values())
    old_samples = {eid: [(s.state, s.last_updated) for s in rows]
                   for eid, rows in old_history.items()}
    assert all(len(rows) >= 2 for rows in old_samples.values()), old_samples

    # Restore current descriptions; public reload keeps entry and registry IDs.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert identities(hass, entry) == before_identity
    assert (entry.entry_id, entry.version, entry.minor_version,
            dict(entry.data), dict(entry.options)) == before_entry
    coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
    meta = await recorder.async_add_executor_job(
        sensor_recorder.list_statistic_ids, hass
    )
    assert set(meta) == set(ids.values())
    for key, entity_id in ids.items():
        assert meta[entity_id]["statistic_id"] == entity_id
        assert meta[entity_id]["mean_type"] is StatisticMeanType.ARITHMETIC
        assert meta[entity_id]["has_sum"] is False
        assert meta[entity_id]["unit_of_measurement"] == (
            "°C" if "temperature" in key else "%"
        )

    freezer.move_to(start + timedelta(minutes=7))
    stove.data.update(dict.fromkeys(MEASUREMENTS, 100))
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(minutes=8))
    with patch.object(stove, "get_data", AsyncMock(return_value=None)):
        await coordinator.async_refresh()
    assert all(hass.states.get(eid).state == "unavailable" for eid in ids.values())
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(minutes=9))
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(minutes=10))
    await compile_period(5)
    short = await read_statistics("5minute")
    assert set(short) == set(ids.values())
    for rows in short.values():
        assert len(rows) == 1
        assert rows[0]["min"] == 0 and rows[0]["max"] == 100
        assert rows[0]["mean"] == pytest.approx(60)
        assert rows[0].get("sum") is None

    # Constant state, no further polls/events required. HA carries it forward.
    for minute in range(10, 60, 5):
        freezer.move_to(start + timedelta(minutes=minute + 5))
        await compile_period(minute)
    hourly = await read_statistics("hour")
    assert set(hourly) == set(ids.values())
    for rows in hourly.values():
        assert len(rows) == 1
        assert rows[0]["min"] == 0 and rows[0]["max"] == 100
        assert rows[0]["mean"] == pytest.approx((60 + 100 * 10) / 11)
        assert rows[0].get("sum") is None
    validation = await recorder.async_add_executor_job(
        sensor_recorder.validate_statistics, hass, {}
    )
    assert not validation
    assert {eid: [(s.state, s.last_updated) for s in rows]
            for eid, rows in (await read_history()).items()} == old_samples
    assert identities(hass, entry) == before_identity
    assert not [r for r in caplog.records
                if r.name == "homeassistant.components.sensor.recorder"
                and r.levelno >= 30]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert stove.destroy.await_count == 2
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()
