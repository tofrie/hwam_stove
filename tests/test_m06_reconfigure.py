"""M06 public HA reconfigure flows, lifecycle, registries and YAML boundary."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientPayloadError, ServerDisconnectedError
from homeassistant import config_entries
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_platform,
    entity_registry as er,
)
import pytest
from pytest_homeassistant_custom_component.common import async_test_home_assistant

from custom_components.hwam_stove import (
    _YAML_HOSTS,
    PLATFORMS,
    _async_import_yaml,
    async_setup,
    config_flow,
)
from pystove import pystove

from .helpers import COMMANDS, DOMAIN, HOST, SimulatedStove, registry_entries
from .lifecycle_checks import assert_no_client_consumers
from .registry_helpers import seed_historical
from .test_m04_yaml_import import add_entry, counts, entries
from .test_m05_hosts import finish, start, wait
from .test_migration import assert_current
from .test_pystove_boundary import expected_file_requests, file_requests, real_transport

pytestmark = pytest.mark.contract
TARGET = "new.invalid"
__all__ = ["real_transport"]


@pytest.fixture(autouse=True)
async def yaml_config(hass, monkeypatch):
    """Known empty startup YAML and a separately controllable current YAML read."""
    assert await async_setup(hass, {})
    reader = AsyncMock(return_value={})
    monkeypatch.setattr(config_flow, "async_integration_yaml_config", reader)
    return reader


@pytest.fixture
def flows(hass, monkeypatch):
    """Only suppress scheduled reload; flow validation and entry update are real."""
    reload = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload)
    return reload


async def reconfigure(hass, entry, host=TARGET):
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert form["type"] == "form" and form["step_id"] == "reconfigure"
    assert form["data_schema"]({}) == {"host": entry.data["host"]}
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], {"host": host}
    )


def assert_no_commands(client):
    for method in COMMANDS:
        getattr(client, method).assert_not_called()


@pytest.mark.parametrize(("host", "normalized"), [
    (TARGET, TARGET), (" NEW.INVALID ", TARGET), ("192.0.2.4", "192.0.2.4"),
    ("2001:0DB8::1", "[2001:db8::1]"), ("NEW.INVALID.", "new.invalid."),
])
async def test_changes_only_host_with_public_update_reload(
    flows, hass, stove, stove_factory, yaml_config, host, normalized
):
    entry = add_entry(hass, HOST, "User's entry")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "unrelated": {"keep": [1, 2]}},
        options={"unchanged": True}, pref_disable_polling=True,
    )
    before = entry.as_dict()
    result = await reconfigure(hass, entry, host)
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    # Explicit reason keeps our honest saved/scheduled translation in both HAs.
    assert "translation_domain" not in result
    expected = {**before, "data": {**before["data"], "host": normalized}}
    actual = entry.as_dict()
    actual.pop("modified_at")
    expected.pop("modified_at")
    assert actual == expected
    assert entries(hass) == [entry]
    assert entry.unique_id is None
    flows.assert_called_once_with(entry.entry_id)
    stove_factory.assert_awaited_once_with(normalized)
    stove.destroy.assert_awaited_once_with()
    stove.get_data.assert_not_called()
    assert_no_commands(stove)
    assert yaml_config.await_count == 2
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


@pytest.mark.parametrize("host", [
    "", " ", "http://new.invalid", "new.invalid:80", "new.invalid/path",
    "user@new.invalid", "new.invalid?q", "new.invalid#f", "[fe80::1%en0]",
    "new..invalid", "new.invalid\n",
])
async def test_invalid_host_makes_no_client_or_entry_update(
    flows, hass, stove_factory, yaml_config, host
):
    entry = add_entry(hass, HOST)
    before = entry.as_dict()
    result = await reconfigure(hass, entry, host)
    assert result["errors"] == {"host": "invalid_host"}
    assert entry.as_dict() == before
    flows.assert_not_called()
    stove_factory.assert_not_called()
    yaml_config.assert_not_called()


@pytest.mark.parametrize(("stored", "input"), [
    (HOST, HOST), (" STOVE.INVALID ", HOST),
    ("[2001:0DB8::1]", "2001:db8::1"),
])
async def test_same_normalized_host_is_noop_even_with_yaml(
    flows, hass, stove_factory, yaml_config, stored, input
):
    entry = add_entry(hass, stored)
    hass.data[_YAML_HOSTS].add((True, config_flow.normalize_host(stored)))
    before = entry.as_dict()
    result = await reconfigure(hass, entry, input)
    assert result["reason"] == "reconfigure_unchanged"
    assert entry.as_dict() == before
    flows.assert_not_called()
    stove_factory.assert_not_called()
    yaml_config.assert_not_called()


@pytest.mark.parametrize("target", [TARGET, " NEW.INVALID "])
async def test_other_entry_owns_target(flows, hass, stove_factory, target):
    entry = add_entry(hass, HOST)
    other = add_entry(hass, "NEW.INVALID")
    before = [entry.as_dict(), other.as_dict()]
    result = await reconfigure(hass, entry, target)
    assert result["reason"] == "already_configured"
    assert [e.as_dict() for e in entries(hass)] == before
    flows.assert_not_called()
    stove_factory.assert_not_called()


@pytest.mark.parametrize("failure", [
    "unknown", "connection", "timeout", "transport", "body", "exception", "close"
])
async def test_failed_validation_preserves_entry_and_can_retry(
    flows, hass, stove, stove_factory, failure
):
    entry = add_entry(hass, HOST)
    before = entry.as_dict()
    primary = RuntimeError("synthetic validation failure")
    if failure == "unknown":
        stove.name = pystove.UNKNOWN
    elif failure == "connection":
        stove_factory.side_effect = ConnectionError
    elif failure in {"timeout", "transport", "body"}:
        stove_factory.side_effect = {
            "timeout": TimeoutError, "transport": ServerDisconnectedError,
            "body": ClientPayloadError,
        }[failure]
    elif failure == "exception":
        stove_factory.side_effect = primary
    else:
        stove.destroy.side_effect = primary
    if failure in {"exception", "close"}:
        with pytest.raises(RuntimeError) as caught:
            await reconfigure(hass, entry)
        assert caught.value is primary
        failed = None
    else:
        failed = await reconfigure(hass, entry)
        assert failed["errors"] == {"base": "cannot_connect"}
    assert entry.as_dict() == before
    assert stove.destroy.await_count == int(failure in {"unknown", "close"})
    flows.assert_not_called()
    replacement = SimulatedStove()
    stove_factory.side_effect = None
    stove_factory.return_value = replacement
    # A failed form can retry; exceptions can start a fresh flow with no stale lock.
    if failed:
        result = await hass.config_entries.flow.async_configure(
            failed["flow_id"], {"host": TARGET}
        )
    else:
        result = await reconfigure(hass, entry)
    assert result["reason"] == "reconfigure_successful"
    assert stove_factory.await_count == 2
    replacement.destroy.assert_awaited_once_with()


@pytest.mark.parametrize("kind", ["user", "import", "reconfigure", "same_entry"])
@pytest.mark.parametrize("first_reconfigure", [False, True])
async def test_races_share_m05_reservation(
    flows, hass, stove_factory, kind, first_reconfigure
):
    entry = add_entry(hass, HOST)
    other = add_entry(hass, "other.invalid")
    entered, release = asyncio.Event(), asyncio.Event()
    client = SimulatedStove()

    async def create(host):
        entered.set()
        await release.wait()
        return client

    async def competitor():
        if kind == "same_entry":
            return await reconfigure(hass, entry, "different.invalid")
        if kind == "reconfigure":
            return await reconfigure(hass, other, " NEW.INVALID ")
        return await start(hass, SOURCE_USER if kind == "user" else SOURCE_IMPORT,
                           " NEW.INVALID ")

    stove_factory.side_effect = create
    first = reconfigure(hass, entry) if first_reconfigure else competitor()
    task = asyncio.create_task(first)
    try:
        await wait(entered)
        result = await (competitor() if first_reconfigure else reconfigure(hass, entry))
        assert result["reason"] == "already_in_progress"
        assert stove_factory.await_count == 1
        release.set()
        # Manual/import success needs HA setup suppressed to isolate flow ownership.
        with (
            patch("custom_components.hwam_stove.async_setup_entry",
                  AsyncMock(return_value=True)),
            patch("custom_components.hwam_stove.async_unload_entry",
                  AsyncMock(return_value=True)),
        ):
            winner = await task
            if winner["type"] == "create_entry":
                await hass.config_entries.async_unload(winner["result"].entry_id)
        assert winner["type"] in {"create_entry", "abort"}
        assert stove_factory.await_count == 1
        client.destroy.assert_awaited_once_with()
        assert len([e for e in entries(hass) if e.data["host"] == TARGET]) <= 1
    finally:
        await finish(task, release)


async def test_different_entries_and_targets_are_independent(
    flows, hass, stove_factory
):
    first, second = add_entry(hass, HOST), add_entry(hass, "other.invalid")
    targets = [TARGET, "different.invalid"]
    entered = {host: asyncio.Event() for host in targets}
    release = asyncio.Event()

    async def create(host):
        entered[host].set()
        await release.wait()
        return SimulatedStove()

    stove_factory.side_effect = create
    tasks = [asyncio.create_task(reconfigure(hass, entry, host))
             for entry, host in zip([first, second], targets, strict=True)]
    try:
        for event in entered.values():
            await wait(event)
        release.set()
        results = await asyncio.gather(*tasks)
        assert all(r["reason"] == "reconfigure_successful" for r in results)
        assert [e.data["host"] for e in [first, second]] == targets
        assert stove_factory.await_count == 2
    finally:
        for task in tasks:
            await finish(task, release)


@pytest.mark.parametrize(
    "change", ["target", "own_host", "other_data", "yaml", "abort"]
)
async def test_recheck_immediately_before_commit(
    flows, hass, stove, yaml_config, change
):
    entry = add_entry(hass, HOST)
    before = entry.as_dict()

    async def close():
        if change == "target":
            add_entry(hass, " NEW.INVALID ")
        elif change == "own_host":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "host": "x"}
            )
        elif change == "other_data":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "keep": 42}, options={"retain": 17}
            )
        elif change == "yaml":
            yaml_config.return_value = {DOMAIN: {"old": {"host": " STOVE.INVALID "}}}
        else:
            for flow in hass.config_entries.flow.async_progress_by_handler(
                DOMAIN, include_uninitialized=True
            ):
                hass.config_entries.flow.async_abort(flow["flow_id"])

    stove.destroy.side_effect = close
    result = await reconfigure(hass, entry)
    if change == "yaml":
        assert result["errors"] == {"base": "yaml_configuration"}
    else:
        expected = {"target": "already_configured",
                    "own_host": "reconfigure_entry_changed",
                    "other_data": "reconfigure_successful",
                    "abort": "reconfigure_cancelled"}
        assert result["reason"] == expected[change]
    if change == "other_data":
        assert entry.data == {**before["data"], "host": TARGET, "keep": 42}
        assert entry.options == {"retain": 17}
        flows.assert_called_once_with(entry.entry_id)
    else:
        assert entry.data["host"] == ("x" if change == "own_host" else HOST)
        flows.assert_not_called()
    stove.destroy.assert_awaited_once_with()


@pytest.mark.parametrize("where", ["create", "close", "yaml_after_close"])
async def test_cancellation_preserves_entry_and_drains_owned_cleanup(
    flows, hass, stove, stove_factory, yaml_config, where
):
    entry = add_entry(hass, HOST)
    before = entry.as_dict()
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(*args):
        entered.set()
        await release.wait()
        return stove

    async def yaml(*args):
        if yaml_config.await_count == 2:
            await pause()
        return {}

    if where == "create":
        stove_factory.side_effect = pause
    elif where == "close":
        stove.destroy.side_effect = pause
    else:
        yaml_config.side_effect = yaml
    task = asyncio.create_task(reconfigure(hass, entry))
    try:
        await wait(entered)
        task.cancel("first cancellation")
        await asyncio.sleep(0)
        if where == "close":
            assert not task.done()
            task.cancel("second cancellation")
            await asyncio.sleep(0)
            result = await start(hass, SOURCE_USER, TARGET)
            assert result["reason"] == "already_in_progress"
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("first cancellation",)
        assert entry.as_dict() == before
        flows.assert_not_called()
        assert stove.destroy.await_count == int(where != "create")
        stove_factory.side_effect = None
        replacement = SimulatedStove()
        stove_factory.return_value = replacement
        yaml_config.side_effect = None
        assert (await reconfigure(hass, entry))["reason"] == "reconfigure_successful"
        replacement.destroy.assert_awaited_once_with()
    finally:
        await finish(task, release)


@pytest.mark.parametrize("where", ["loaded", "disk", "removed_disk", "unknown", "bad"])
async def test_old_yaml_must_be_removed_before_host_change(
    flows, hass, stove_factory, yaml_config, where
):
    entry = add_entry(hass, HOST)
    if where in {"loaded", "removed_disk"}:
        assert await async_setup(hass, {DOMAIN: {"old": {"host": "STOVE.INVALID"}}})
        await hass.async_block_till_done()
        if where == "removed_disk":
            # Another setup call cannot erase an earlier startup/batch snapshot.
            assert await async_setup(hass, {})
    if where == "disk":
        yaml_config.return_value = {DOMAIN: {"old": {"host": HOST}}}
    if where == "unknown":
        hass.data.pop(_YAML_HOSTS)
    if where == "bad":
        yaml_config.side_effect = HomeAssistantError("invalid YAML")
    before = entry.as_dict()
    result = await reconfigure(hass, entry)
    expected = {"unknown": "yaml_not_checked", "bad": "yaml_check_failed"}.get(
        where, "yaml_configuration"
    )
    assert result["errors"] == {"base": expected}
    assert entry.as_dict() == before
    assert entries(hass) == [entry]
    stove_factory.assert_not_called()
    flows.assert_not_called()


async def test_yaml_guard_processes_real_public_config_loader(
    flows, hass, yaml_config, monkeypatch
):
    from homeassistant.helpers.reload import async_integration_yaml_config

    monkeypatch.setattr(config_flow, "async_integration_yaml_config",
                        async_integration_yaml_config)
    entry = add_entry(hass, HOST)
    # Exercise HA's schema processing; never read real configuration/secret files.
    with patch("homeassistant.config.async_hass_config_yaml", AsyncMock(return_value={
        DOMAIN: {"old": {"host": " STOVE.INVALID "}}
    })):
        result = await reconfigure(hass, entry)
    assert result["errors"] == {"base": "yaml_configuration"}
    flows.assert_not_called()


async def test_reconfigured_entry_survives_yaml_setup_without_reimport(
    flows, hass, stove_factory, yaml_config
):
    entry = add_entry(hass, HOST)
    other = add_entry(hass, "other.invalid")
    yaml = {"other": {"host": "OTHER.INVALID"}}
    await async_setup(hass, {DOMAIN: yaml})
    await hass.async_block_till_done()
    yaml_config.return_value = {DOMAIN: yaml}
    assert (await reconfigure(hass, entry))["reason"] == "reconfigure_successful"
    # Only the old device was removed from YAML, distinct devices remain supported.
    await _async_import_yaml(hass, yaml)
    await async_setup(hass, {DOMAIN: yaml})
    await hass.async_block_till_done()
    assert entries(hass) == [entry, other]
    assert entry.data["host"] == TARGET
    assert counts(hass) == dict(configured=1, total=1, missing=0, duplicates=0)
    assert stove_factory.await_count == 1


async def test_reconfigured_import_entry_persists_across_fresh_ha_start(
    flows, hass, hass_storage, stove_factory, yaml_config
):
    entry = add_entry(hass, HOST, source=SOURCE_IMPORT)
    add_entry(hass, "other.invalid", source=SOURCE_IMPORT)
    yaml = {"other": {"host": "OTHER.INVALID"}}
    yaml_config.return_value = {DOMAIN: yaml}
    assert (await reconfigure(hass, entry))["reason"] == "reconfigure_successful"
    before = {e.entry_id: e.as_dict() for e in entries(hass)}
    hass_storage[config_entries.STORAGE_KEY] = {
        "version": config_entries.STORAGE_VERSION,
        "minor_version": config_entries.STORAGE_VERSION_MINOR,
        "data": {"entries": list(before.values())},
    }
    async with async_test_home_assistant() as restarted:
        try:
            await restarted.config_entries.async_initialize()
            assert _YAML_HOSTS not in restarted.data
            assert await async_setup(restarted, {DOMAIN: yaml})
            await restarted.async_block_till_done()
            assert {e.entry_id: e.as_dict() for e in entries(restarted)} == before
            assert counts(restarted)["missing"] == 0
            assert len(entries(restarted)) == 2
            restored = restarted.config_entries.async_get_entry(entry.entry_id)
            assert restored is not entry
            assert restored.data["host"] == TARGET
        finally:
            await restarted.async_stop(force=True)
    assert stove_factory.await_count == 1  # Validation only; no old YAML re-import.


@pytest.mark.parametrize("version", [1, 3])
async def test_reconfigure_cannot_trigger_registry_migration(
    flows, hass, stove_factory, version
):
    entry = add_entry(hass, HOST)
    hass.config_entries.async_update_entry(entry, version=version)
    before = entry.as_dict()
    assert (await reconfigure(hass, entry))["reason"] == "reconfigure_not_ready"
    assert entry.as_dict() == before
    stove_factory.assert_not_called()
    flows.assert_not_called()


@pytest.fixture
async def live(hass, entry, stove_factory):
    clients = []
    events = []

    def create(host):
        client = SimulatedStove()
        client.stove_host = host
        index = len(clients)

        async def close():
            events.append(("destroy", index))

        client.destroy.side_effect = close
        clients.append(client)
        events.append(("create", index, host))
        return client

    stove_factory.side_effect = create
    seed_historical(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert_current(hass, entry)
    try:
        yield entry, clients, events
    finally:
        if entry.state.recoverable:
            await hass.config_entries.async_unload(entry.entry_id)
        else:
            # Harness-only cleanup for an injected failed unload, after all setup
            # work has ended. Production deliberately performs no H01B recovery.
            hub = hass.data.get(DOMAIN, {}).get("stoves", {}).get(entry.entry_id)
            if hub:
                platforms = tuple(entity_platform.async_get_platforms(hass, DOMAIN))
                await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
                await hub.async_shutdown()
                assert_no_client_consumers(platforms, hub)
                await hub.stove.destroy()
                hass.data.pop(DOMAIN)
        await hass.async_block_till_done()
        for client in clients:
            client.destroy.assert_awaited_once_with()
            assert_no_commands(client)


def identities(hass, entry):
    return (
        {d.id: (d.identifiers, d.area_id, d.name_by_user, d.disabled_by)
         for d in dr.async_entries_for_config_entry(
             dr.async_get(hass), entry.entry_id)},
        {e.id: (e.entity_id, e.unique_id, e.device_id, e.name, e.area_id,
                e.disabled_by, e.hidden_by, e.icon, e.options)
         for e in registry_entries(hass, entry.entry_id)},
    )


@pytest.mark.parametrize("disable_remote", [False, True])
async def test_real_reload_preserves_b01_all_registries_and_customizations(
    hass, live, disable_remote
):
    entry, clients, events = live
    area = ar.async_get(hass).async_create("User area")
    entities, devices = er.async_get(hass), dr.async_get(hass)
    phase = entities.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}-phase")
    phase = entities.async_update_entity(
        phase, new_entity_id="sensor.custom_phase", name="My phase", area_id=area.id,
        hidden_by=er.RegistryEntryHider.USER, icon="mdi:fire",
    )
    entities.async_update_entity_options(
        phase.entity_id, "sensor", {"display_precision": 2}
    )
    button = entities.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}-start")
    entities.async_update_entity(button, disabled_by=er.RegistryEntryDisabler.USER)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        devices.async_update_device(
            device.id, area_id=area.id, name_by_user="My device",
            disabled_by=(dr.DeviceEntryDisabler.USER if disable_remote
                         and (DOMAIN, f"{entry.entry_id}-remote") in device.identifiers
                         else None),
        )
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "retained": {"data": 7}}, options={"keep": 2}
    )
    before = deepcopy(identities(hass, entry))
    metadata = entry.as_dict()
    hub = hass.data[DOMAIN]["stoves"][entry.entry_id]
    platforms = tuple(entity_platform.async_get_platforms(hass, DOMAIN))
    old_close = clients[0].destroy.side_effect

    async def close_without_consumers():
        assert_no_client_consumers(platforms, hub)
        await old_close()

    clients[0].destroy.side_effect = close_without_consumers
    result = await reconfigure(hass, entry, " NEW.INVALID ")
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(clients) == 3
    assert events == [
        ("create", 0, HOST), ("create", 1, TARGET), ("destroy", 1),
        ("destroy", 0), ("create", 2, TARGET),
    ]
    assert identities(hass, entry) == before
    assert_current(hass, entry)
    assert entries(hass) == [entry]
    assert entry.version == metadata["version"] == 2
    assert entry.data == {**metadata["data"], "host": TARGET}
    assert entry.options == metadata["options"]
    assert entry.entry_id == metadata["entry_id"]
    assert entry.unique_id == metadata["unique_id"]
    assert entry.title == metadata["title"]
    assert hass.data[DOMAIN]["stoves"][entry.entry_id].stove is clients[2]
    clients[1].get_data.assert_not_called()
    clients[2].get_data.assert_awaited_once_with()
    clients[2].destroy.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["validation", "cancel_validation", "unload", "setup", "refresh"]
)
async def test_live_failure_boundaries(hass, live, stove_factory, failure):
    entry, clients, events = live
    before = deepcopy(identities(hass, entry))
    old = hass.data[DOMAIN]["stoves"][entry.entry_id]
    create = stove_factory.side_effect

    def fail_create(host):
        if failure == "validation":
            raise ConnectionError
        if failure == "cancel_validation":
            raise asyncio.CancelledError("synthetic cancellation")
        if failure == "setup" and len(clients) == 2:
            raise RuntimeError("synthetic reload setup failure")
        client = create(host)
        if failure == "refresh" and len(clients) == 3:
            client.get_data.side_effect = None
            client.get_data.return_value = None
        return client

    stove_factory.side_effect = fail_create
    with patch.object(hass.config_entries, "async_unload_platforms",
                      wraps=hass.config_entries.async_unload_platforms) as unload:
        if failure == "unload":
            unload.side_effect = AsyncMock(return_value=False)
        if failure == "cancel_validation":
            with pytest.raises(asyncio.CancelledError):
                await reconfigure(hass, entry)
        else:
            result = await reconfigure(hass, entry)
            if failure == "validation":
                assert result["errors"] == {"base": "cannot_connect"}
            else:
                assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
    assert identities(hass, entry) == before
    assert len(entries(hass)) == 1
    if failure in {"validation", "cancel_validation"}:
        assert entry.data["host"] == HOST
        assert entry.state is ConfigEntryState.LOADED
        assert hass.data[DOMAIN]["stoves"][entry.entry_id] is old
        clients[0].destroy.assert_not_called()
        # The existing coordinator still reads normally; no additional flow read.
        await old.async_refresh()
        assert old.last_update_success
        assert len(clients) == 1
        unload.assert_not_called()
    else:
        assert entry.data["host"] == TARGET  # Persisted, no speculative rollback.
        clients[1].destroy.assert_awaited_once_with()
        if failure == "unload":
            assert entry.state is ConfigEntryState.FAILED_UNLOAD
            assert hass.data[DOMAIN]["stoves"][entry.entry_id] is old
            clients[0].destroy.assert_not_called()
        else:
            assert entry.state is (ConfigEntryState.SETUP_RETRY if failure == "refresh"
                                   else ConfigEntryState.SETUP_ERROR)
            clients[0].destroy.assert_awaited_once_with()
        assert len(clients) == (3 if failure == "refresh" else 2)
        if failure == "refresh":
            clients[2].destroy.assert_awaited_once_with()  # Existing H01A ownership.


async def test_offline_old_host_can_be_reconfigured_after_normal_b01_migration(
    hass, entry, stove_factory
):
    seed_historical(hass, entry)
    clients = []

    def create(host):
        if host == HOST:
            raise TimeoutError("old address unreachable")
        client = SimulatedStove()
        client.stove_host = host
        clients.append(client)
        return client

    stove_factory.side_effect = create
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert_current(hass, entry)
    before = deepcopy(identities(hass, entry))
    try:
        assert (await reconfigure(hass, entry))["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        after = identities(hass, entry)
        assert after[0] == before[0]
        assert set(after[1]) == set(before[1])
        for entity_id, old in before[1].items():
            assert after[1][entity_id][:8] == old[:8]
            # This is the FIRST successful platform setup of historical fixtures.
            # HA SensorEntity adds its own suggested precision; user options must
            # still survive. The ordinary loaded-entry test checks full equality.
            options = {domain: dict(values)
                       for domain, values in after[1][entity_id][8].items()}
            if (
                "sensor" in options
                and "suggested_display_precision" in options["sensor"]
            ):
                assert "suggested_display_precision" not in old[8].get("sensor", {})
                options["sensor"].pop("suggested_display_precision")
                if not options["sensor"]:
                    options.pop("sensor")
            assert options == old[8]
        assert len(clients) == 2
        assert entry.data["host"] == TARGET
        clients[0].destroy.assert_awaited_once_with()
        clients[1].destroy.assert_not_called()
        assert hass.data[DOMAIN]["stoves"][entry.entry_id].stove is clients[1]
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
    for client in clients:
        client.destroy.assert_awaited_once_with()
        assert_no_commands(client)


async def test_h01b_partial_setup_residual_cannot_be_reconfigured(hass, entry, stove):
    original = hass.config_entries.async_forward_entry_setups

    async def partial(config_entry, platforms):
        await original(config_entry, ["button"])
        raise RuntimeError("synthetic H01B partial setup")

    with patch.object(hass.config_entries, "async_forward_entry_setups", partial):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    try:
        before = entry.as_dict()
        result = await reconfigure(hass, entry)
        assert result["reason"] == "reconfigure_not_ready"
        assert entry.as_dict() == before
        stove.destroy.assert_not_called()
    finally:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        await stove.destroy()
        hass.data.pop(DOMAIN)


async def test_real_library_reconfigure_boundary(hass, entry, real_transport):
    old_session = real_transport.prepare()
    temporary = real_transport.prepare(TARGET)
    new_session = real_transport.prepare(TARGET)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    before = deepcopy(identities(hass, entry))
    try:
        result = await reconfigure(hass, entry)
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert identities(hass, entry) == before
        assert old_session.closed and temporary.closed and not new_session.closed
        for session in (old_session, temporary, new_session):
            assert file_requests(session) == expected_file_requests(True)
            assert all(path in {
                "/esp/get_identification", "/esp/get_current_accesspoint",
                "/open_file", "/read_open_file", "/close_file", "/get_stove_data",
            } for _, path, _ in session.calls)
        assert not any(path == "/get_stove_data" for _, path, _ in temporary.calls)
        client = hass.data[DOMAIN]["stoves"][entry.entry_id].stove
        assert client.stove_host == TARGET
        assert len(real_transport.destroyed) == 2
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
    assert len({id(c) for c in real_transport.destroyed}) == 3


async def test_real_create_cancellation_closes_file_without_touching_runtime(
    hass, entry, real_transport
):
    old_session = real_transport.prepare()
    temporary = real_transport.prepare(TARGET)
    read = temporary.queue("POST", "/read_open_file", hold=True)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    before = entry.as_dict()
    hub = hass.data[DOMAIN]["stoves"][entry.entry_id]
    task = asyncio.create_task(reconfigure(hass, entry))
    try:
        await wait(read.entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert file_requests(temporary) == expected_file_requests(True)
        assert temporary.close_calls == 1 and not old_session.closed
        assert entry.as_dict() == before
        assert hass.data[DOMAIN]["stoves"][entry.entry_id] is hub
        assert not real_transport.destroyed  # Unreturned client owned by pystove.
    finally:
        await finish(task, read.release)
        await hass.config_entries.async_unload(entry.entry_id)


def test_reconfigure_translations():
    for language in ("en", "de", "nl"):
        path = Path(__file__).parents[1] / (
            f"custom_components/hwam_stove/translations/{language}.json"
        )
        config = json.loads(path.read_text())["config"]
        assert set(config["step"]["reconfigure"]["data"]) == {"host"}
        assert config["step"]["reconfigure"]["description"]
        assert {key for key in config["abort"] if key.startswith("reconfigure_")} == {
            "reconfigure_successful", "reconfigure_unchanged",
            "reconfigure_entry_changed",
            "reconfigure_cancelled", "reconfigure_not_ready",
        }
        assert all(config["error"][key] for key in [
            "yaml_configuration", "yaml_not_checked", "yaml_check_failed"
        ])
