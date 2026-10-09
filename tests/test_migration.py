"""B01 regression: real HA registries, historical fixtures, no controller I/O."""

from unittest.mock import patch

import attr
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
import orjson
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .helpers import DOMAIN, ENTRY_ID, registry_entries
from .registry_helpers import HISTORICAL, seed_historical
from .test_entities import ROWS

pytestmark = pytest.mark.contract


async def migrate(hass, entry):
    from custom_components.hwam_stove import async_migrate_entry

    return await async_migrate_entry(hass, entry)


def snapshot(hass):
    """All records, including unknown/foreign ones and their user metadata."""
    return (
        {d.id: d for d in dr.async_get(hass).devices},
        dict(er.async_get(hass).entities)
    )


def identity_snapshot(hass):
    devices, entities = snapshot(hass)
    return (
        {key: value.identifiers for key, value in devices.items()},
        {key: (value.unique_id, value.device_id) for key, value in entities.items()},
    )


def assert_current(hass, entry, count=40, device_count=2):
    # B01 still migrates exactly the original inventory. A02/A02.1 additions are
    # separately validated and never enter the historical migration mapping.
    from custom_components.hwam_stove._analytics_sensor import DESCRIPTIONS
    from custom_components.hwam_stove._request_statistics import (
        DESCRIPTIONS as REQUEST_DESCRIPTIONS,
    )

    added = {f"{entry.entry_id}-{d.key}"
             for d in (*DESCRIPTIONS, *REQUEST_DESCRIPTIONS)}
    entities = [e for e in registry_entries(hass, entry.entry_id)
                if e.unique_id not in added]
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(entities) == count
    assert len(devices) == device_count
    expected = {(r["platform"], f'{entry.entry_id}-{r["key"]}') for r in ROWS}
    assert {(e.domain, e.unique_id) for e in entities} <= expected
    assert all(any(identifier == (DOMAIN, f"{entry.entry_id}-{kind}")
                   for kind in ("stove", "remote"))
               for d in devices for identifier in d.identifiers)
    assert entry.version == 2


def test_all_40_mapping_keys_match_frozen_fixture():
    from custom_components.hwam_stove.migration import LEGACY_ENTITY_KEYS

    actual = [(domain, key)
              for domain, keys in LEGACY_ENTITY_KEYS.items() for key in keys]
    assert len(actual) == len(set(actual)) == 40
    assert set(actual) == {(row["platform"], row["key"]) for row in ROWS}


async def test_B01_upgrade_preserves_registry(hass, entry, stove):
    """Former B01 xfail, expanded through migration/setup/reload of all platforms."""
    old_entities, old_devices = seed_historical(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    area = ar.async_get(hass).async_create("Living room")
    custom = entities.async_get_entity_id("sensor", DOMAIN, "test_stove-phase")
    custom = entities.async_update_entity(
        custom, new_entity_id="sensor.mein_ofen_xyz", name="Mein Ofen",
        area_id=area.id, hidden_by=er.RegistryEntryHider.USER, icon="mdi:fire",
    )
    entities.async_update_entity_options(
        custom.entity_id, "sensor", {"display_precision": 2}
    )
    disabled = entities.async_get_entity_id("button", DOMAIN, "test_stove-start")
    entities.async_update_entity(disabled, disabled_by=er.RegistryEntryDisabler.USER)
    for device_id in old_devices:
        devices.async_update_device(
            device_id, name_by_user="Mein Gerät", area_id=area.id
        )
    original = snapshot(hass)
    original_data = dict(entry.data)
    original_title = entry.title

    assert await migrate(hass, entry)
    assert_current(hass, entry)
    assert entry.entry_id == ENTRY_ID
    assert dict(entry.data) == original_data == HISTORICAL["entry_data"]
    assert entry.title == original_title
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert set(entities.entities) == set(original[1])
    assert {d.id for d in devices.devices} == old_devices
    # HA changes only identity/bookkeeping fields during migration.
    for entity_id, before in original[1].items():
        after = entities.async_get(entity_id)
        assert attr.asdict(before, filter=lambda a, v: a.name not in {
            "unique_id", "previous_unique_id", "modified_at"
        }) == attr.asdict(after, filter=lambda a, v: a.name not in {
            "unique_id", "previous_unique_id", "modified_at"
        })
    for device_id, before in original[0].items():
        after = devices.async_get(device_id)
        assert attr.asdict(before, filter=lambda a, v: a.name not in {
            "identifiers", "modified_at"
        }) == attr.asdict(after, filter=lambda a, v: a.name not in {
            "identifiers", "modified_at"
        })

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert_current(hass, entry)
    assert set(original[1]) < set(entities.entities)
    assert len(set(entities.entities) - set(original[1])) == 7
    assert {d.id for d in devices.devices} == old_devices
    assert len(old_entities) == 40
    assert {e.domain for e in registry_entries(hass)} == {
        "sensor", "binary_sensor", "button", "switch", "number", "time", "datetime"
    }
    for _ in range(2):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert_current(hass, entry)
        for entity_id, before in original[1].items():
            after = entities.async_get(entity_id)
            for field in ("id", "entity_id", "name", "area_id", "device_id",
                          "disabled_by", "hidden_by", "icon"):
                assert getattr(after, field) == getattr(before, field)
            # Setup may add HA defaults; every existing user option must survive.
            for domain, options in before.options.items():
                for key, value in options.items():
                    assert after.options[domain][key] == value
        for device_id, before in original[0].items():
            after = devices.async_get(device_id)
            for field in ("id", "area_id", "name_by_user", "disabled_by"):
                assert getattr(after, field) == getattr(before, field)
    assert hass.states.get("sensor.mein_ofen_xyz") is not None
    assert hass.states.get(disabled) is None
    for command in ("start", "set_burn_level", "set_time", "set_night_lowering",
                    "set_night_lowering_hours", "set_remote_refill_alarm"):
        getattr(stove, command).assert_not_called()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_framework_runs_migration_before_setup(hass, entry):
    old_entities, old_devices = seed_historical(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert_current(hass, entry)
    after_ids = {e.entity_id for e in registry_entries(hass)}
    assert old_entities < after_ids and len(after_ids - old_entities) == 7
    assert {e.device_id for e in registry_entries(hass)} == old_devices
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("kind", ["stove", "remote", None])
@pytest.mark.parametrize("with_entity", [False, True])
async def test_partial_registry(hass, entry, kind, with_entity):
    hass.config_entries.async_update_entry(entry, data=HISTORICAL["entry_data"])
    device = None
    if kind:
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"test_stove-{kind}")}
        )
    if with_entity:
        entity = er.async_get(hass).async_get_or_create(
            "sensor", DOMAIN, "test_stove-phase", config_entry=entry,
            device_id=device.id if device else None,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    assert await migrate(hass, entry)
    assert_current(hass, entry, int(with_entity), int(kind is not None))
    if with_entity:
        current = er.async_get(hass).async_get(entity.entity_id)
        assert current.disabled_by == er.RegistryEntryDisabler.USER
        assert current.device_id == entity.device_id
    once = snapshot(hass)
    assert await migrate(hass, entry)
    assert snapshot(hass) == once


async def test_device_disabled_and_other_identifiers_preserved(hass, entry):
    seed_historical(hass, entry)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier(
        (DOMAIN, "test_stove-remote"), ENTRY_ID
    )
    extra = ("other_domain", "unrelated")
    devices.async_update_device(
        device.id, disabled_by=dr.DeviceEntryDisabler.USER,
        new_identifiers=device.identifiers | {extra},
    )
    assert await migrate(hass, entry)
    current = devices.async_get(device.id)
    assert current.disabled_by == dr.DeviceEntryDisabler.USER
    assert current.identifiers == {extra, (DOMAIN, f"{ENTRY_ID}-remote")}


@pytest.mark.parametrize("legacy_id", [None, "", 17])
async def test_invalid_legacy_id_fails_without_guessing(hass, entry, legacy_id):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "id": legacy_id})
    before = snapshot(hass)
    assert not await migrate(hass, entry)
    assert entry.version == 1
    assert snapshot(hass) == before


async def test_current_master_registry_untouched(hass, entry, loaded):
    # Restore v1 to represent an installation made by the pre-migration master.
    hass.config_entries.async_update_entry(entry, version=1)
    before = snapshot(hass)
    data = dict(entry.data)
    assert "id" not in data
    assert await migrate(hass, entry)
    assert entry.version == 2
    assert snapshot(hass) == before
    assert dict(entry.data) == data


@pytest.mark.parametrize("version,expected", [(2, True), (3, False)])
async def test_version_guard_does_not_touch_registries(hass, entry, version, expected):
    seed_historical(hass, entry)
    hass.config_entries.async_update_entry(entry, version=version)
    before = snapshot(hass)
    assert await migrate(hass, entry) is expected
    assert entry.version == version
    assert snapshot(hass) == before


async def test_framework_rejects_future_version(hass, entry, stove_factory):
    hass.config_entries.async_update_entry(entry, version=3)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 3
    stove_factory.assert_not_called()


async def test_partial_migration_and_device_alias_resume(hass, entry):
    seed_historical(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    for old in registry_entries(hass)[:7]:
        entities.async_update_entity(
            old.entity_id,
            new_unique_id=old.unique_id.replace("test_stove-", f"{ENTRY_ID}-")
        )
    device = devices.async_get_device_by_identifier(
        (DOMAIN, "test_stove-stove"), ENTRY_ID
    )
    devices.async_update_device(
        device.id, new_identifiers=device.identifiers | {(DOMAIN, f"{ENTRY_ID}-stove")}
    )
    assert await migrate(hass, entry)
    assert_current(hass, entry)
    once = snapshot(hass)
    # Reproduce lost version save: completed identities with v1 still on disk.
    hass.config_entries.async_update_entry(entry, version=1)
    assert await migrate(hass, entry)
    assert snapshot(hass) == once


@pytest.mark.parametrize("foreign", [False, True])
async def test_entity_target_conflict_preflight(hass, entry, foreign, stove_factory):
    seed_historical(hass, entry)
    owner = entry
    if foreign:
        owner = MockConfigEntry(
            domain=DOMAIN, data={"host": "other.invalid"}, version=2,
            disabled_by=ConfigEntryDisabler.USER,
        )
        owner.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{ENTRY_ID}-phase", config_entry=owner,
    )
    before = snapshot(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 1
    assert snapshot(hass) == before
    stove_factory.assert_not_called()


@pytest.mark.parametrize("kind", ["stove", "remote"])
async def test_device_target_conflict_preflight(hass, entry, kind):
    seed_historical(hass, entry)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{ENTRY_ID}-{kind}")}
    )
    before = snapshot(hass)
    assert not await migrate(hass, entry)
    assert entry.version == 1
    assert snapshot(hass) == before


async def test_ambiguous_shared_device_fails_preflight(hass, entry):
    hass.config_entries.async_update_entry(entry, data=HISTORICAL["entry_data"])
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "test_stove-stove"), (DOMAIN, "test_stove-remote")},
    )
    before = snapshot(hass)
    assert not await migrate(hass, entry)
    assert entry.version == 1
    assert snapshot(hass) == before


async def test_unknown_and_foreign_records_untouched(hass, entry):
    seed_historical(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    foreign = MockConfigEntry(domain=DOMAIN, data={"host": "other.invalid"}, version=2)
    foreign.add_to_hass(hass)
    other_device = devices.async_get_or_create(
        config_entry_id=foreign.entry_id, identifiers={(DOMAIN, "test_stove-stove")}
    )
    unknown_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "test_stove-obsolete")}
    )
    unknown = entities.async_get_or_create(
        "sensor", DOMAIN, "test_stove-obsolete", config_entry=entry,
    )
    other_platform = entities.async_get_or_create(
        "sensor", "other_integration", "test_stove-phase", config_entry=entry,
    )
    # Known old unique_id attached to another config entry is never adopted.
    old_phase = entities.async_get_entity_id("sensor", DOMAIN, "test_stove-phase")
    entities.async_update_entity(old_phase, config_entry_id=foreign.entry_id)
    foreign_entity = entities.async_get(old_phase)
    assert await migrate(hass, entry)
    assert devices.async_get(other_device.id) == other_device
    assert devices.async_get(unknown_device.id) == unknown_device
    assert entities.async_get(unknown.entity_id) == unknown
    assert entities.async_get(other_platform.entity_id) == other_platform
    assert entities.async_get(old_phase) == foreign_entity


@pytest.mark.parametrize("stage", ["device", "entity", "version"])
@pytest.mark.parametrize("after_write", [False, True])
async def test_failure_rolls_back_and_retry_succeeds(hass, entry, stage, after_write):
    seed_historical(hass, entry)
    before = identity_snapshot(hass)
    if stage == "device":
        owner, method, fail_at = dr.async_get(hass), "async_update_device", 2
    elif stage == "entity":
        owner, method, fail_at = er.async_get(hass), "async_update_entity", 5
    else:
        owner, method, fail_at = hass.config_entries, "async_update_entry", 1
    original = getattr(owner, method)
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            if after_write:
                original(*args, **kwargs)
            raise RuntimeError("Injected registry write failure")
        return original(*args, **kwargs)

    with patch.object(owner, method, side_effect=fail_once):
        assert not await migrate(hass, entry)
    assert entry.version == 1
    assert identity_snapshot(hass) == before
    assert await migrate(hass, entry)
    assert_current(hass, entry)


async def test_failed_rollback_leaves_resumable_v1(hass, entry, caplog):
    seed_historical(hass, entry)
    registry = er.async_get(hass)
    original = registry.async_update_entity
    calls = 0

    def fail_forward_and_rollback(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RuntimeError("Injected persistent failure")
        return original(*args, **kwargs)

    with patch.object(
        registry, "async_update_entity", side_effect=fail_forward_and_rollback
    ):
        assert not await migrate(hass, entry)
    assert "entity rollback failed" in caplog.text
    assert entry.version == 1
    assert len(registry_entries(hass)) == 40
    current = [e for e in registry_entries(hass)
               if e.unique_id.startswith(f"{ENTRY_ID}-")]
    assert len(current) == 2
    assert await migrate(hass, entry)
    assert_current(hass, entry)


async def test_version_rollback_failure_keeps_complete_current_records(hass, entry):
    seed_historical(hass, entry)
    original = hass.config_entries.async_update_entry

    def fail_version(*args, **kwargs):
        if kwargs["version"] == 2:
            original(*args, **kwargs)
        raise RuntimeError("Injected version persistence failure")

    with patch.object(
        hass.config_entries, "async_update_entry", side_effect=fail_version
    ):
        assert not await migrate(hass, entry)
    assert_current(hass, entry)
    assert await migrate(hass, entry)


async def test_entry_data_options_and_same_prefix_preserved(hass, entry):
    seed_historical(hass, entry)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "retained_setting": {"value": 42}},
        options={"user_option": True},
    )
    data, options = dict(entry.data), dict(entry.options)
    assert await migrate(hass, entry)
    assert dict(entry.data) == data
    assert dict(entry.options) == options
    # A legacy id that happens to equal entry_id already denotes current identities.
    hass.config_entries.async_update_entry(
        entry, version=1, data={**entry.data, "id": entry.entry_id}
    )
    before = snapshot(hass)
    assert await migrate(hass, entry)
    assert snapshot(hass) == before


@pytest.mark.parametrize("partial", [False, True])
async def test_persisted_registry_restart(hass, entry, partial, hass_storage):
    """Rebuild registries from HA's stored representation, not shared Python objects."""
    seed_historical(hass, entry)
    assert await migrate(hass, entry)
    if partial:
        hass.config_entries.async_update_entry(entry, version=1)
        entity = registry_entries(hass)[0]
        er.async_get(hass).async_update_entity(
            entity.entity_id,
            new_unique_id=entity.unique_id.replace(f"{ENTRY_ID}-", "test_stove-"),
        )
    before_ids = {e.entity_id for e in registry_entries(hass)}
    # Initial HA fixture registries are read-only (load_empty). Populate its storage
    # fixture with the real serialized schema, then load fresh registry instances.
    for module in (dr, er):
        registry = module.async_get(hass)
        data = orjson.loads(orjson.dumps(registry._data_to_save()))
        hass_storage[module.STORAGE_KEY] = {
            "version": registry._store.version,
            "minor_version": registry._store.minor_version,
            "data": data,
        }
        hass.data.pop(module.DATA_REGISTRY)
        if module is dr:
            dr.async_setup(hass)
        else:
            er.async_get.cache_clear()
        await module.async_load(hass)
        assert module.async_get(hass) is not registry
    assert await migrate(hass, entry)
    assert_current(hass, entry)
    assert {e.entity_id for e in registry_entries(hass)} == before_ids
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert_current(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
