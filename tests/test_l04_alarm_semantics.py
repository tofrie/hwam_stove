"""L04: actual HA alarm metadata, published parsing, registry and history."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    device_condition,
    device_trigger,
)
from homeassistant.components.recorder import history
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_platform,
    entity_registry as er,
    translation,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.hwam_stove import binary_sensor as alarm_module
from pystove import Stove

from .helpers import COMMANDS, DOMAIN, entity_id_for
from .test_m06_reconfigure import identities

pytestmark = pytest.mark.contract
DOOR = "safety_alarms_door_open_too_long"
CHIMNEY = "safety_alarms_stove_overheat"
ALARMS = {DOOR: (11, "Door Open Too Long"), CHIMNEY: (10, "Chimney Overheat")}
NAMES = {
    "de": ("Tür zu lange offen", "Schornsteinüberhitzung", "Inaktiv", "Aktiv"),
    "en": ("Door open too long", "Chimney overheating", "Inactive", "Active"),
    "nl": ("Deur te lang open", "Oververhitting van de schoorsteen",
           "Inactief", "Actief"),
}


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """Configure isolated SQLite before HA starts, as required by HA fixtures."""
    assert recorder_db_url == "sqlite://"


def live_alarm(hass, key):
    target = entity_id_for(hass, "binary_sensor", key)
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if target in platform.entities:
            return platform.entities[target]
    raise AssertionError(f"Alarm missing: {key}")


async def setup_old_metadata(hass, entry, monkeypatch):
    """Simulate only pre-L04 descriptions/names, through normal public HA setup."""
    old_init = alarm_module.HwamStoveAlarmSensor.__init__

    def init(self, coordinator, description):
        old_init(self, coordinator, description)
        if description.key in ALARMS:
            self._attr_name = "Overheat" if description.key == CHIMNEY else (
                "Door open too long"
            )

    with monkeypatch.context() as old:
        old.setattr(alarm_module, "BINARY_SENSOR_LIST_DESCRIPTIONS", [
            replace(d, device_class=BinarySensorDeviceClass.DOOR)
            if d.key == DOOR else d
            for d in alarm_module.BINARY_SENSOR_LIST_DESCRIPTIONS
        ])
        old.setattr(alarm_module.HwamStoveAlarmSensor, "__init__", init)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert live_alarm(hass, DOOR).device_class == BinarySensorDeviceClass.DOOR
    assert live_alarm(hass, CHIMNEY).name == "Overheat"


@pytest.mark.parametrize("mask", [0, *(1 << n for n in range(14)), 3072])
async def test_published_alarm_bits_to_real_ha_states(hass, loaded, stove, mask):
    raw = json.loads((Path(__file__).parent / "fixtures/raw_status.json").read_text())
    raw["safety_alarms"] = mask
    parser = Stove()  # No create/session: the installed parser only.
    parser.get_raw_data = AsyncMock(return_value=raw)
    stove.data = await parser.get_data()
    reads = stove.get_data.await_count
    await loaded.async_refresh()
    await hass.async_block_till_done()
    for key, (bit, text) in ALARMS.items():
        entity = live_alarm(hass, key)
        expected = bool(mask & (1 << bit))
        assert (text in stove.data["safety_alarms"]) is expected
        assert entity.entity_description.alarm_str == text
        assert entity.entity_description.value_source_key == "safety_alarms"
        assert entity.is_on is expected
        state = hass.states.get(entity.entity_id)
        assert state.state == ("on" if expected else "off")
        assert state.attributes["device_class"] == (
            "problem" if key == DOOR else "heat"
        )
        assert entity.entity_description.translation_key == key
        assert entity.entity_category == "diagnostic"
        registered = er.async_get(hass).async_get(entity.entity_id)
        assert registered.disabled_by is None
        assert registered.unique_id.endswith(f"-{key}")
        assert registered.device_id == er.async_get(hass).async_get(
            entity_id_for(hass, "sensor", "stove_temperature")
        ).device_id
    assert stove.get_data.await_count == reads + 1
    parser.get_raw_data.assert_awaited_once_with()
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()


@pytest.mark.parametrize("language", NAMES)
async def test_ha_loaded_translations(language, hass, loaded):
    strings = await translation.async_get_translations(
        hass, language, "entity", {DOMAIN}
    )
    prefix = f"component.{DOMAIN}.entity.binary_sensor."
    door, chimney, inactive, active = NAMES[language]
    assert strings[f"{prefix}{DOOR}.name"] == door
    assert strings[f"{prefix}{CHIMNEY}.name"] == chimney
    assert strings[f"{prefix}{DOOR}.state.off"] == inactive
    assert strings[f"{prefix}{DOOR}.state.on"] == active


@pytest.mark.parametrize("key", ALARMS)
async def test_identical_update_unavailable_and_recovery(hass, loaded, stove, key):
    entity = live_alarm(hass, key)
    before = hass.states.get(entity.entity_id)
    with patch.object(entity, "async_write_ha_state",
                      wraps=entity.async_write_ha_state) as write:
        await loaded.async_refresh()
        write.assert_not_called()
    assert hass.states.get(entity.entity_id) == before
    with patch.object(stove, "get_data", AsyncMock(return_value=None)):
        await loaded.async_refresh()
    assert hass.states.get(entity.entity_id).state == "unavailable"
    stove.data["safety_alarms"] = [ALARMS[key][1]]
    await loaded.async_refresh()
    assert hass.states.get(entity.entity_id).state == "on"
    assert entity.is_on and entity.available
    stove.data["safety_alarms"] = []
    await loaded.async_refresh()
    assert hass.states.get(entity.entity_id).state == "off"
    assert entity.is_on is False


async def test_generated_device_automation_choices_are_alarm_semantics(hass, loaded):
    registry = er.async_get(hass)
    door = registry.async_get(entity_id_for(hass, "binary_sensor", DOOR))
    chimney = registry.async_get(entity_id_for(hass, "binary_sensor", CHIMNEY))
    triggers = await device_trigger.async_get_triggers(hass, door.device_id)
    conditions = await device_condition.async_get_conditions(hass, door.device_id)
    assert {x["type"] for x in triggers if x["entity_id"] == door.id} == {
        "problem", "no_problem",
    }
    assert {x["type"] for x in conditions if x["entity_id"] == door.id} == {
        "is_problem", "is_no_problem",
    }
    assert {x["type"] for x in triggers if x["entity_id"] == chimney.id} == {
        "hot", "not_hot",
    }


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("key", ALARMS)
async def test_upgrade_keeps_customizations_and_disabled_state(
    hass, entry, stove, monkeypatch, key, disabled,
):
    await setup_old_metadata(hass, entry, monkeypatch)
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Custom area")
    original = entity_id_for(hass, "binary_sensor", key)
    updated = registry.async_update_entity(
        original, new_entity_id="binary_sensor.my_alarm", name="My alarm",
        area_id=area.id, icon="mdi:alert", hidden_by=er.RegistryEntryHider.USER,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        dr.async_get(hass).async_update_device(device.id, name_by_user="My stove")
    await hass.async_block_till_done()
    before = identities(hass, entry)
    entry_before = (entry.entry_id, entry.version, entry.minor_version,
                    dict(entry.data), dict(entry.options))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert identities(hass, entry) == before
    assert (entry.entry_id, entry.version, entry.minor_version,
            dict(entry.data), dict(entry.options)) == entry_before
    assert len(before[0]) == 2 and len(before[1]) == 40
    assert entity_id_for(hass, "binary_sensor", key) == updated.entity_id
    state = hass.states.get(updated.entity_id)
    if disabled:
        assert state is None
    else:
        assert "My alarm" in state.attributes["friendly_name"]
        assert state.attributes["device_class"] == (
            "problem" if key == DOOR else "heat"
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert stove.destroy.await_count == 2


@pytest.mark.parametrize("device_automation", [False, True])
async def test_saved_automations_remain_attached_after_metadata_upgrade(
    hass, entry, stove, monkeypatch, device_automation,
):
    """Saved old door trigger types still read on/off; new choices use PROBLEM."""
    await setup_old_metadata(hass, entry, monkeypatch)
    ids = {k: entity_id_for(hass, "binary_sensor", k) for k in ALARMS}
    seen = []

    async def record(call):
        seen.append(call.data["label"])

    hass.services.async_register("test", "record", record)
    configs = []
    for key, entity_id in ids.items():
        registered = er.async_get(hass).async_get(entity_id)
        for active in (False, True):
            if device_automation:
                trigger = {
                    "platform": "device", "domain": "binary_sensor",
                    "device_id": registered.device_id, "entity_id": registered.id,
                    "type": (("opened" if active else "not_opened") if key == DOOR
                             else ("hot" if active else "not_hot")),
                }
            else:
                trigger = {"platform": "state", "entity_id": entity_id,
                           "from": "off" if active else "on",
                           "to": "on" if active else "off"}
            configs.append({
                "alias": f"{key}_{active}", "trigger": trigger,
                "action": {"service": "test.record",
                           "data": {"label": f"{key}_{active}"}},
            })
    assert await async_setup_component(hass, "automation", {"automation": configs})
    await hass.async_start()
    await hass.async_block_till_done()

    async def assert_transitions():
        seen.clear()
        coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
        for active in (True, False):
            stove.data["safety_alarms"] = (
                [v[1] for v in ALARMS.values()] if active else []
            )
            await coordinator.async_refresh()
            await hass.async_block_till_done()
        assert sorted(seen) == sorted(f"{k}_{v}" for k in ALARMS for v in (False, True))

    await assert_transitions()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert {k: entity_id_for(hass, "binary_sensor", k) for k in ALARMS} == ids
    await assert_transitions()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert stove.destroy.await_count == 2
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()


async def test_actual_recorder_keeps_old_history_and_entity_ids(
    hass, entry, stove, monkeypatch, freezer, async_setup_recorder_instance,
):
    freezer.move_to("2026-10-07T10:00:00+00:00")
    start = dt_util.utcnow()
    recorder = await async_setup_recorder_instance(hass)
    await hass.async_start()
    await hass.async_block_till_done()
    freezer.move_to(start + timedelta(seconds=1))
    await setup_old_metadata(hass, entry, monkeypatch)
    ids = {k: entity_id_for(hass, "binary_sensor", k) for k in ALARMS}
    before = identities(hass, entry)

    async def read_history(end):
        return await recorder.async_add_executor_job(
            history.get_significant_states, hass, start, end, list(ids.values()),
        )

    def samples(rows):
        return {eid: [(s.state, s.last_updated, dict(s.attributes)) for s in states]
                for eid, states in rows.items()}

    freezer.move_to(start + timedelta(seconds=10))
    stove.data["safety_alarms"] = [v[1] for v in ALARMS.values()]
    await hass.data[DOMAIN]["stoves"][entry.entry_id].async_refresh()
    await async_wait_recording_done(hass)
    end_old = start + timedelta(seconds=20)
    old_samples = samples(await read_history(end_old))
    assert set(old_samples) == set(ids.values())
    for rows in old_samples.values():
        assert [s[0] for s in rows] == ["off", "on"]
    assert old_samples[ids[DOOR]][0][2]["device_class"] == "door"

    freezer.move_to(start + timedelta(seconds=30))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert identities(hass, entry) == before
    assert {k: entity_id_for(hass, "binary_sensor", k) for k in ALARMS} == ids
    assert live_alarm(hass, CHIMNEY).name == NAMES["en"][1]
    assert live_alarm(hass, DOOR).name == NAMES["en"][0]
    assert hass.states.get(ids[DOOR]).attributes["device_class"] == "problem"

    freezer.move_to(start + timedelta(seconds=40))
    stove.data["safety_alarms"] = []
    await hass.data[DOMAIN]["stoves"][entry.entry_id].async_refresh()
    await async_wait_recording_done(hass)
    assert samples(await read_history(end_old)) == old_samples
    for key, rows in (await read_history(start + timedelta(seconds=50))).items():
        assert rows[-1].state == "off"
        assert rows[-1].attributes["device_class"] == (
            "problem" if key == ids[DOOR] else "heat"
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert stove.destroy.await_count == 2
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()
