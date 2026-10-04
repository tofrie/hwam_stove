"""M04: real HA import flows/repairs, exact hosts and unchanged registry ownership."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from string import Formatter
from unittest.mock import AsyncMock, call, patch

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER, ConfigEntryDisabler
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import orjson
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_test_home_assistant,
)

from custom_components.hwam_stove import (
    _YAML_IMPORT_LOCK,
    _YAML_ISSUE,
    CONFIG_SCHEMA,
    _async_import_yaml,
    async_setup,
)
from pystove import pystove

from .helpers import COMMANDS, DOMAIN, HOST, SimulatedStove, registry_entries
from .registry_helpers import HISTORICAL, seed_historical
from .test_migration import assert_current

pytestmark = pytest.mark.contract


@pytest.fixture
async def imports(hass):
    """Keep entry setup independent; real import validation/entry persistence run."""
    with (
        patch(
            "custom_components.hwam_stove.async_setup_entry",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.hwam_stove.async_unload_entry",
            AsyncMock(return_value=True),
        ),
    ):
        yield
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_unload(entry.entry_id)


def add_entry(hass, host, name="Existing", **kwargs):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        version=2,
        data={"host": host, "name": name},
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


def entries(hass):
    return hass.config_entries.async_entries(DOMAIN)


def counts(hass):
    issue = ir.async_get(hass).async_get_issue(DOMAIN, _YAML_ISSUE)
    assert issue is not None
    return {k: int(v) for k, v in issue.translation_placeholders.items()}


async def run(hass, devices):
    config = CONFIG_SCHEMA({DOMAIN: devices})
    before = deepcopy(config)
    assert await async_setup(hass, config)
    await hass.async_block_till_done()
    assert config == before
    assert not hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, include_uninitialized=True
    )


@pytest.mark.parametrize(
    "devices",
    [
        {"one": {"host": HOST}},
        {"one": {"host": HOST}, "two": {"host": "second.invalid"}},
    ],
)
async def test_no_entries_imports_every_yaml_device(
    imports, hass, stove_factory, devices
):
    await run(hass, devices)
    assert {e.data["host"]: e.data["name"] for e in entries(hass)} == {
        value["host"]: name for name, value in devices.items()
    }
    assert all(e.source == SOURCE_IMPORT and e.version == 2 for e in entries(hass))
    assert all(
        e.unique_id is None and set(e.data) == {"host", "name"} for e in entries(hass)
    )
    assert stove_factory.await_args_list == [call(d["host"]) for d in devices.values()]
    assert counts(hass) == dict(
        configured=len(devices), total=len(devices), missing=0, duplicates=0
    )


async def test_M04_import_additional_yaml_device(imports, hass, entry, stove_factory):
    """Converted final strict xfail: preserve the existing entry, import another."""
    before = dict(entry.data)
    await run(hass, {"second": {"host": "second.invalid"}})
    assert len(entries(hass)) == 2
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert dict(entry.data) == before and entry.version == 2  # HA ran existing B01.
    stove_factory.assert_awaited_once_with("second.invalid")
    assert counts(hass)["missing"] == 0


@pytest.mark.parametrize("disabled", [None, ConfigEntryDisabler.USER])
async def test_existing_matching_entry_keeps_identity_name_and_disabled_state(
    imports, hass, stove_factory, disabled
):
    existing = add_entry(hass, HOST, "User's name", disabled_by=disabled)
    await run(hass, {"yaml_key": {"host": HOST, "name": "Ignored"}})
    assert entries(hass) == [existing]
    assert existing.data == {"host": HOST, "name": "User's name"}
    assert existing.title == "User's name" and existing.disabled_by == disabled
    stove_factory.assert_not_called()
    assert counts(hass) == dict(configured=1, total=1, missing=0, duplicates=0)


async def test_mixed_entries_and_duplicate_yaml_hosts(imports, hass, stove_factory):
    first = add_entry(hass, HOST)
    unrelated = add_entry(hass, "unrelated.invalid")
    await run(
        hass,
        {
            "old": {"host": HOST},
            "new": {"host": "new.invalid"},
            "alias": {"host": "new.invalid"},
            "third": {"host": "third.invalid"},
            "old_again": {"host": HOST},
        },
    )
    assert len(entries(hass)) == 4
    assert first in entries(hass) and unrelated in entries(hass)
    assert stove_factory.await_args_list == [call("new.invalid"), call("third.invalid")]
    assert (
        next(e for e in entries(hass) if e.data["host"] == "new.invalid").title == "new"
    )
    assert counts(hass) == dict(configured=3, total=3, missing=0, duplicates=2)


@pytest.mark.parametrize(
    "failure", [ConnectionError, TimeoutError, RuntimeError, "identity", "close"]
)
async def test_one_failure_does_not_suppress_other_imports(
    imports, hass, stove_factory, failure
):
    clients = []

    async def create(host):
        if host == "bad.invalid" and isinstance(failure, type):
            raise failure("synthetic import failure")
        client = SimulatedStove()
        clients.append(client)
        if host == "bad.invalid":
            if failure == "identity":
                client.name = pystove.UNKNOWN
            else:
                client.destroy.side_effect = RuntimeError("synthetic close failure")
        return client

    stove_factory.side_effect = create
    await run(
        hass,
        {
            "bad": {"host": "bad.invalid"},
            "duplicate_bad": {"host": "bad.invalid"},
            "good": {"host": "good.invalid"},
        },
    )
    assert [(e.data["host"], e.title) for e in entries(hass)] == [
        ("good.invalid", "good")
    ]
    assert stove_factory.await_args_list == [call("bad.invalid"), call("good.invalid")]
    assert counts(hass) == dict(configured=1, total=2, missing=1, duplicates=1)
    for client in clients:
        client.destroy.assert_awaited_once_with()
        for command in COMMANDS:
            getattr(client, command).assert_not_called()


@pytest.mark.parametrize("name", [None, "Optional name", "", "Öfen im Haus"])
async def test_yaml_mapping_key_is_historical_name(imports, hass, name):
    config = {"host": HOST}
    if name is not None:
        config["name"] = name
    await run(hass, {"Kamin Süd": config})
    assert entries(hass)[0].data == {"host": HOST, "name": "Kamin Süd"}
    assert entries(hass)[0].title == "Kamin Süd"


@pytest.mark.parametrize(
    "selection", [None, [], ["room_temperature"], ["unknown_legacy_sensor"], "phase"]
)
async def test_monitored_variables_schema_accepted_but_not_persisted(
    imports, hass, selection
):
    data = {"host": HOST}
    if selection is not None:
        data["monitored_variables"] = selection
    config = CONFIG_SCHEMA({DOMAIN: {"old": data}})
    expected = (
        []
        if selection is None
        else ([selection] if isinstance(selection, str) else selection)
    )
    assert config[DOMAIN]["old"]["monitored_variables"] == expected
    await run(hass, {"old": data})
    assert entries(hass)[0].data == {"host": HOST, "name": "old"}
    assert entries(hass)[0].options == {}
    for language in ("en", "de", "nl"):
        document = json.loads(
            (
                Path(__file__).parents[1]
                / "custom_components"
                / DOMAIN
                / "translations"
                / f"{language}.json"
            ).read_text()
        )
        description = document["issues"][_YAML_ISSUE]["description"]
        assert "monitored_variables" in description
        placeholders = {
            field
            for _, field, _, _ in Formatter().parse(description)
            if field is not None
        }
        assert placeholders == {"configured", "total", "missing", "duplicates"}
        description.format(**{k: str(v) for k, v in counts(hass).items()})


async def test_exact_host_comparison_is_not_normalized(imports, hass, stove_factory):
    existing = add_entry(hass, "STOVE.invalid")
    await run(
        hass, {"case": {"host": "stove.invalid"}, "space": {"host": "STOVE.invalid "}}
    )
    assert existing in entries(hass) and len(entries(hass)) == 3
    assert stove_factory.await_args_list == [
        call("stove.invalid"),
        call("STOVE.invalid "),
    ]


async def test_repeated_setup_is_idempotent(imports, hass, stove_factory):
    devices = {"one": {"host": HOST}, "two": {"host": "two.invalid"}}
    await run(hass, devices)
    original = [(e.entry_id, e.title, dict(e.data), e.version) for e in entries(hass)]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, _YAML_ISSUE)
    ir.async_get(hass).async_ignore(DOMAIN, _YAML_ISSUE, True)
    dismissed = (
        ir.async_get(hass).async_get_issue(DOMAIN, _YAML_ISSUE).dismissed_version
    )
    for _ in range(3):
        await run(hass, devices)
        assert [
            (e.entry_id, e.title, dict(e.data), e.version) for e in entries(hass)
        ] == original
    assert stove_factory.await_count == 2
    current = ir.async_get(hass).async_get_issue(DOMAIN, _YAML_ISSUE)
    assert current.created == issue.created and current.dismissed_version == dismissed
    assert len([key for key in ir.async_get(hass).issues if key[0] == DOMAIN]) == 1


async def test_retry_only_on_later_setup_does_not_retry_successful_host(
    imports, hass, stove_factory
):
    failed = True

    async def create(host):
        if failed and host == HOST:
            raise ConnectionError
        return SimulatedStove()

    stove_factory.side_effect = create
    devices = {"one": {"host": HOST}, "two": {"host": "two.invalid"}}
    await run(hass, devices)
    second_id = entries(hass)[0].entry_id
    assert counts(hass)["missing"] == 1
    failed = False
    await run(hass, devices)
    assert len(entries(hass)) == 2
    assert second_id in {e.entry_id for e in entries(hass)}
    assert stove_factory.await_args_list == [
        call(HOST),
        call("two.invalid"),
        call(HOST),
    ]
    assert counts(hass)["missing"] == 0


async def test_simultaneous_batches_serialize_validation_and_do_not_duplicate(
    imports, hass, stove_factory
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(host):
        entered.set()
        await release.wait()
        return SimulatedStove()

    stove_factory.side_effect = create
    devices = {"one": {"host": HOST}, "alias": {"host": HOST}}
    try:
        assert await async_setup(hass, {DOMAIN: devices})
        async with asyncio.timeout(5):
            await entered.wait()
        assert await async_setup(hass, {DOMAIN: devices})
        assert stove_factory.await_count == 1
        release.set()
        await hass.async_block_till_done()
        assert len(entries(hass)) == 1
        stove_factory.assert_awaited_once_with(HOST)
    finally:
        release.set()
        await hass.async_block_till_done()


async def test_entry_appearing_during_validation_is_not_overwritten(
    imports, hass, stove_factory
):
    added = []

    async def create(host):
        added.append(add_entry(hass, host, "Created elsewhere"))
        return SimulatedStove()

    stove_factory.side_effect = create
    await run(hass, {"yaml_name": {"host": HOST}})
    assert entries(hass) == added
    assert entries(hass)[0].title == "Created elsewhere"
    assert counts(hass)["missing"] == 0


@pytest.mark.parametrize("point", ["create", "close"])
async def test_cancelled_import_cleans_owned_resources_and_only_its_flow(
    imports, hass, stove_factory, point
):
    other = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    entered, release = asyncio.Event(), asyncio.Event()
    client = SimulatedStove()

    async def close():
        entered.set()
        await release.wait()

    async def create(host):
        if point == "create":
            entered.set()
            await release.wait()
        return client

    if point == "close":
        client.destroy.side_effect = close
    stove_factory.side_effect = create
    task = asyncio.create_task(_async_import_yaml(hass, {"old": {"host": HOST}}))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        task.cancel("YAML startup cancelled")
        if point == "close":
            await asyncio.sleep(0)
            assert not task.done()  # H03 owns the in-flight close until it ends.
            release.set()
        with pytest.raises(asyncio.CancelledError, match="YAML startup cancelled"):
            await task
        assert not entries(hass)
        assert counts(hass)["missing"] == 1
        flows = hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, include_uninitialized=True
        )
        assert [f["flow_id"] for f in flows] == [other["flow_id"]]
        assert not hass.data[_YAML_IMPORT_LOCK].locked()
        assert client.destroy.await_count == int(point == "close")
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        hass.config_entries.flow.async_abort(other["flow_id"])


@pytest.mark.parametrize("config", [{}, {DOMAIN: {}}])
async def test_absent_or_empty_yaml_clears_old_repair_without_import(
    imports, hass, stove_factory, config
):
    await run(hass, {"old": {"host": HOST}})
    before = [e.entry_id for e in entries(hass)]
    assert await async_setup(hass, config)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, _YAML_ISSUE) is None
    assert [e.entry_id for e in entries(hass)] == before
    stove_factory.assert_awaited_once_with(HOST)


@pytest.mark.parametrize("old_count", [38, 40])
async def test_B01_historical_records_and_additional_yaml_setup(
    hass, entry, stove_factory, old_count
):
    old_ids, device_ids = seed_historical(hass, entry)
    registry = er.async_get(hass)
    if old_count == 38:
        for entity_id in sorted(old_ids)[-2:]:
            registry.async_remove(entity_id)
            old_ids.remove(entity_id)
    for entity_id in old_ids:
        registry.async_update_entity(
            entity_id, name="User custom name", icon="mdi:fire"
        )
    before = {entity_id: registry.async_get(entity_id) for entity_id in old_ids}
    clients = []

    async def create(host):
        client = SimulatedStove()
        clients.append(client)
        return client

    stove_factory.side_effect = create
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert_current(hass, entry)
    await run(
        hass,
        {
            "legacy": {"host": HISTORICAL["entry_data"]["host"]},
            "additional": {
                "host": "another.invalid",
                "monitored_variables": ["room_temperature"],
            },
            "alias": {"host": "another.invalid"},
        },
    )
    assert len(entries(hass)) == 2
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.data == HISTORICAL["entry_data"] and entry.version == 2
    assert {
        d.id
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    } == device_ids
    for entity_id, old in before.items():
        new = registry.async_get(entity_id)
        assert (
            new.id,
            new.entity_id,
            new.device_id,
            new.name,
            new.icon,
            new.disabled_by,
        ) == (old.id, old.entity_id, old.device_id, old.name, old.icon, old.disabled_by)
        for domain, options in old.options.items():
            for key, value in options.items():
                assert new.options[domain][key] == value
    for current in entries(hass):
        assert_current(hass, current)
        assert len(registry_entries(hass, current.entry_id)) == 40
    assert len(dr.async_get(hass).devices) == 4
    assert len(registry.entities) == 80
    for current in entries(hass):
        assert await hass.config_entries.async_unload(current.entry_id)
    assert len(clients) == 3  # old setup; new validation; new setup
    for client in clients:
        client.destroy.assert_awaited_once_with()
        for command in COMMANDS:
            getattr(client, command).assert_not_called()


async def test_persisted_config_entries_survive_fresh_ha_instance(
    imports, hass, hass_storage, stove_factory
):
    devices = {"one": {"host": HOST}, "two": {"host": "two.invalid"}}
    await run(hass, devices)
    before = {e.entry_id: (e.title, dict(e.data), e.version) for e in entries(hass)}
    saved = orjson.loads(orjson.dumps(hass.config_entries._data_to_save()))
    ir.async_get(hass).async_ignore(DOMAIN, _YAML_ISSUE, True)
    original_issue = ir.async_get(hass).async_get_issue(DOMAIN, _YAML_ISSUE)
    issue_store = ir.async_get(hass)._store
    hass_storage[issue_store.key] = {
        "version": issue_store.version,
        "minor_version": issue_store.minor_version,
        "data": orjson.loads(orjson.dumps(ir.async_get(hass)._data_to_save())),
    }
    for entry in entries(hass):
        await hass.config_entries.async_unload(entry.entry_id)
    hass_storage[config_entries.STORAGE_KEY] = {
        "version": config_entries.STORAGE_VERSION,
        "minor_version": config_entries.STORAGE_VERSION_MINOR,
        "data": saved,
    }
    async with async_test_home_assistant() as restarted:
        try:
            await restarted.config_entries.async_initialize()
            restarted.data.pop(ir.DATA_REGISTRY)
            await ir.async_load(restarted)
            assert _YAML_IMPORT_LOCK not in restarted.data
            await run(restarted, devices)
            assert {
                e.entry_id: (e.title, dict(e.data), e.version)
                for e in entries(restarted)
            } == before
            assert all(
                e is not hass.config_entries.async_get_entry(e.entry_id)
                for e in entries(restarted)
            )
            assert counts(restarted)["missing"] == 0
            restored_issue = ir.async_get(restarted).async_get_issue(
                DOMAIN, _YAML_ISSUE
            )
            assert restored_issue.created == original_issue.created
            assert restored_issue.dismissed_version == original_issue.dismissed_version
            assert (
                len([key for key in ir.async_get(restarted).issues if key[0] == DOMAIN])
                == 1
            )
        finally:
            await restarted.async_stop(force=True)
    assert stove_factory.await_count == 2


@pytest.mark.parametrize("count", [1, 2])
async def test_real_component_startup_does_not_deadlock(imports, hass, count):
    from homeassistant.setup import async_setup_component

    devices = {f"stove_{i}": {"host": f"stove-{i}.invalid"} for i in range(count)}
    async with asyncio.timeout(5):
        assert await async_setup_component(hass, DOMAIN, {DOMAIN: devices})
        await hass.async_block_till_done()
    assert len(entries(hass)) == count
    assert counts(hass)["missing"] == 0


async def test_entry_presence_does_not_claim_successful_runtime_setup(
    imports, hass, stove_factory
):
    from homeassistant.config_entries import ConfigEntryState

    devices = {"old": {"host": HOST}}
    with patch(
        "custom_components.hwam_stove.async_setup_entry", AsyncMock(return_value=False)
    ):
        await run(hass, devices)
    entry = entries(hass)[0]
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert counts(hass) == dict(configured=1, total=1, missing=0, duplicates=0)
    await run(hass, devices)
    assert entries(hass) == [entry]
    stove_factory.assert_awaited_once_with(HOST)
