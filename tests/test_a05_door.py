"""Physical door: real rc3 parser, equal status, HA availability and identities."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.helpers import entity_registry as er
import pytest

from pystove import Stove

from .helpers import COMMANDS, entity_id_for, registry_entries
from .test_m06_reconfigure import identities
from .test_pystove_boundary import real_transport

__all__ = ["real_transport"]
RAW = json.loads((Path(__file__).parent / "fixtures/raw_status.json").read_text())


@pytest.fixture
def door_client(stove, installed_pystove):
    assert installed_pystove["version"] == "0.3.0rc3"
    client = Stove()
    client.name = stove.name
    client.series = stove.series
    client.destroy = AsyncMock()
    client.get_raw_data = AsyncMock(return_value=deepcopy(RAW | {"door_open": False}))
    client.get_raw_data.side_effect = lambda: deepcopy(client.get_raw_data.return_value)
    return client


async def start(hass, entry, stove_factory, door_client):
    stove_factory.return_value = door_client
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data["hwam_stove"]["stoves"][entry.entry_id]
    return coordinator, entity_id_for(hass, "binary_sensor", "door_open")


async def finish(hass, entry, door_client):
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    door_client.destroy.assert_awaited_once()


@pytest.mark.parametrize(
    "door,state",
    [
        (True, "on"),
        (False, "off"),
        (None, "unavailable"),
        (0, "unavailable"),
        (1, "unavailable"),
        ("true", "unavailable"),
        ({}, "unavailable"),
    ],
)
async def test_equal_status_transition_and_notification(
    hass,
    entry,
    stove_factory,
    door_client,
    door,
    state,
):
    coordinator, entity = await start(hass, entry, stove_factory, door_client)
    previous = deepcopy(coordinator.data)
    before = door_client.get_raw_data.await_count
    listener = Mock()
    remove = coordinator.async_add_listener(listener)
    door_client.get_raw_data.return_value = deepcopy(RAW | {"door_open": door})
    await coordinator.async_refresh()
    assert coordinator.data == previous and len(previous) == 25
    assert door_client.get_raw_data.await_count == before + 1
    assert hass.states.get(entity).state == state
    assert hass.states.get(entity).attributes["device_class"] == "door"
    assert listener.call_count == (0 if door is False else 1)
    await coordinator.async_refresh()
    assert listener.call_count == (0 if door is False else 1)
    remove()
    await finish(hass, entry, door_client)


@pytest.mark.parametrize(
    "error", [None, TimeoutError(), RuntimeError(), asyncio.CancelledError()]
)
async def test_failure_cancellation_and_recovery(
    hass,
    entry,
    stove_factory,
    door_client,
    error,
):
    coordinator, entity = await start(hass, entry, stove_factory, door_client)
    listener = Mock()
    remove = coordinator.async_add_listener(listener)
    before = door_client.get_raw_data.await_count
    door_client.get_raw_data.side_effect = error
    door_client.get_raw_data.return_value = None
    if isinstance(error, asyncio.CancelledError):
        # Actual task cancellation, not only a manually raised exception.
        entered = asyncio.Event()

        async def block():
            entered.set()
            await asyncio.Future()

        door_client.get_raw_data.side_effect = block
        task = asyncio.create_task(coordinator.async_refresh())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await coordinator.async_refresh()
    assert door_client.get_raw_data.await_count == before + 1
    assert coordinator.door_open is None
    assert hass.states.get(entity).state == "unavailable"
    assert listener.call_count >= 1
    door_client.get_raw_data.side_effect = None
    door_client.get_raw_data.return_value = deepcopy(RAW | {"door_open": True})
    await coordinator.async_refresh()
    assert hass.states.get(entity).state == "on"
    remove()
    await finish(hass, entry, door_client)


async def test_cancellation_after_library_read_invalidates_coordinator(
    hass,
    entry,
    stove_factory,
    door_client,
):
    coordinator, entity = await start(hass, entry, stove_factory, door_client)
    entered = asyncio.Event()

    async def block(*args):
        entered.set()
        await asyncio.Future()

    with patch.object(coordinator.analytics, "async_observe", side_effect=block):
        task = asyncio.create_task(coordinator.async_refresh())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert coordinator.door_open is None
    assert hass.states.get(entity).state == "unavailable"
    await finish(hass, entry, door_client)


async def test_missing_field_reload_and_47_existing_identities(
    hass,
    entry,
    stove_factory,
    door_client,
):
    coordinator, entity = await start(hass, entry, stove_factory, door_client)
    registry = er.async_get(hass)
    assert len(registry_entries(hass)) == 48
    old = [e for e in registry_entries(hass) if e.entity_id != entity]
    assert len(old) == 47
    target = old[0]
    registry.async_update_entity(
        target.entity_id, name="Preserved customization", icon="mdi:fire"
    )
    before = identities(hass, entry)
    alarm = entity_id_for(hass, "binary_sensor", "safety_alarms_door_open_too_long")
    assert hass.states.get(alarm).attributes["device_class"] == "problem"
    door_client.get_raw_data.return_value = deepcopy(RAW)
    await coordinator.async_refresh()
    assert hass.states.get(entity).state == "unavailable"
    fresh = Stove()
    fresh.name, fresh.series = door_client.name, door_client.series
    fresh.destroy = AsyncMock()
    fresh.get_raw_data = AsyncMock(return_value=deepcopy(RAW))
    stove_factory.return_value = fresh
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    door_client.destroy.assert_awaited_once()
    assert hass.states.get(entity).state == "unavailable"
    assert identities(hass, entry) == before
    assert registry.async_get(target.entity_id).name == "Preserved customization"
    assert len(registry_entries(hass)) == 48
    await finish(hass, entry, fresh)


async def test_stale_generation_and_rc2_like_client_unavailable(
    hass,
    entry,
    stove_factory,
    door_client,
):
    coordinator, entity = await start(hass, entry, stove_factory, door_client)
    accepted = deepcopy(coordinator.data)
    door_client.get_data = AsyncMock(return_value=accepted)
    await coordinator.async_refresh()  # Cached snapshot did not advance.
    assert hass.states.get(entity).state == "unavailable"
    await finish(hass, entry, door_client)


async def test_real_transport_one_get_for_door_only_changes(
    hass,
    entry,
    real_transport,
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data["hwam_stove"]["stoves"][entry.entry_id]
    (session,) = real_transport.sessions
    entity = entity_id_for(hass, "binary_sensor", "door_open")
    for door in (True, False, True):
        before = len(session.calls)
        session.queue(
            "GET", "/get_stove_data", body=json.dumps(RAW | {"door_open": door})
        )
        await coordinator.async_refresh()
        assert session.calls[before:] == [("GET", "/get_stove_data", {})]
        assert hass.states.get(entity).state == ("on" if door else "off")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_no_commands_or_added_reads_on_equal_notification(loaded, stove):
    before = stove.get_data.await_count
    loaded.async_update_listeners()
    assert stove.get_data.await_count == before
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()


async def test_upgrade_existing_47_adds_only_door(
    hass, entry, stove_factory, door_client,
):
    from custom_components.hwam_stove import binary_sensor

    original_setup = binary_sensor.async_setup_entry

    async def without_door(hass, entry, add_entities):
        def old_inventory(entities):
            add_entities([entity for entity in entities
                          if entity.entity_description.key != "door_open"])
        await original_setup(hass, entry, old_inventory)

    stove_factory.return_value = door_client
    with patch.object(binary_sensor, "async_setup_entry", without_door):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert len(registry_entries(hass)) == 47
    before = identities(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    after = identities(hass, entry)
    assert after[0] == before[0]  # Exact device registry IDs/identifiers.
    assert len(after[1]) == 48
    assert {key: after[1][key] for key in before[1]} == before[1]
    new_ids = set(after[1]) - set(before[1])
    assert len(new_ids) == 1
    added = next(e for e in registry_entries(hass) if e.id in new_ids)
    assert added.unique_id == f"{entry.entry_id}-door_open"
    assert added.entity_id == entity_id_for(hass, "binary_sensor", "door_open")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert door_client.destroy.await_count == 2
