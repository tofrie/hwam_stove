"""M03: real HA button availability, service filtering and listener ownership."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch

from aiohttp import ClientConnectionError
from homeassistant.components.button import DATA_COMPONENT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .command_cases import SYNC_LOCAL_TIME
from .helpers import COMMANDS, DOMAIN, SimulatedStove, entity_id_for
from .test_h04_commands import assert_unconfirmed, isolated_readback_request

pytestmark = pytest.mark.contract

BUTTONS = [("start", "start"), ("sync_clock", "set_time")]


def clock_args(method):
    """M07 intentionally supplies explicit HA-local time to sync only."""
    return (SYNC_LOCAL_TIME,) if method == "set_time" else ()


def buttons(hass):
    """Return the actual registered entities, not extra fixture instances."""
    component = hass.data[DATA_COMPONENT]
    result = {
        key: component.get_entity(entity_id_for(hass, "button", key))
        for key, _ in BUTTONS
    }
    assert all(entity is not None for entity in result.values())
    return result


def assert_available(hass, expected):
    for entity in buttons(hass).values():
        assert entity.available is expected
        assert not entity.should_poll
        assert (hass.states.get(entity.entity_id).state != "unavailable") is expected


def assert_no_commands(stove):
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()


def button_listeners(coordinator, entity):
    """Inspect HA's pinned listener table only in tests, never in runtime."""
    return [callback for callback, _ in coordinator._listeners.values()
            if getattr(callback, "__self__", None) is entity]


async def press(hass, key):
    await hass.services.async_call("button", "press", {
        "entity_id": entity_id_for(hass, "button", key),
    }, blocking=True)


@pytest.mark.parametrize("phase", ["Burn", "Standby"])
async def test_M03_initial_success_and_standby(hass, entry, stove, phase):
    stove.data["phase"] = phase
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
        assert coordinator.last_update_success
        assert_available(hass, True)
        for entity in buttons(hass).values():
            assert len(button_listeners(coordinator, entity)) == 1
        assert stove.get_data.await_count == 1
        assert_no_commands(stove)
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_M03_unavailable_before_entities_are_added(hass, entry, stove):
    """Fail a real read after first refresh, before any platform is forwarded."""
    forward = hass.config_entries.async_forward_entry_setups

    async def fail_before_forward(config_entry, platforms):
        coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
        stove.get_data.side_effect = None
        stove.get_data.return_value = None
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        stove.get_data.side_effect = stove._read
        await forward(config_entry, platforms)

    with patch.object(hass.config_entries, "async_forward_entry_setups",
                      side_effect=fail_before_forward):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        assert_available(hass, False)
        assert stove.get_data.await_count == 2  # first refresh + injected failure
        assert_no_commands(stove)
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("failure", [None, TimeoutError, ClientConnectionError],
                         ids=["none", "timeout", "connection"])
@pytest.mark.parametrize("identical", [True, False], ids=["equal", "changed"])
async def test_M03_offline_and_recovery(loaded, hass, stove, failure, identical):
    original = deepcopy(loaded.data)
    read_count = stove.get_data.await_count
    notified = Mock()
    unsubscribe = loaded.async_add_listener(notified)
    try:
        with patch.object(loaded, "async_request_refresh",
                          wraps=loaded.async_request_refresh) as requested:
            assert_available(hass, True)
            stove.get_data.side_effect = failure() if failure else None
            stove.get_data.return_value = None
            await loaded.async_refresh()  # represents one ordinary status read
            await hass.async_block_till_done()
            assert_available(hass, False)
            assert loaded.data == original
            assert stove.get_data.await_count == read_count + 1
            notified.assert_called_once_with()

            if not identical:
                stove.data["message_id"] += 1
            stove.get_data.side_effect = stove._read
            notified.reset_mock()
            await loaded.async_refresh()
            await hass.async_block_till_done()
            assert_available(hass, True)
            assert (loaded.data == original) is identical
            notified.assert_called_once_with()
            assert stove.get_data.await_count == read_count + 2
            requested.assert_not_called()
            assert_no_commands(stove)
    finally:
        unsubscribe()


@pytest.mark.parametrize("phase,seconds", [("Burn", 10), ("Standby", 60)])
async def test_M03_regular_poll_is_the_only_request(
    loaded, hass, stove, phase, seconds
):
    stove.data["phase"] = phase
    await loaded.async_refresh()
    original = deepcopy(loaded.data)
    count = stove.get_data.await_count
    assert loaded.update_interval == timedelta(seconds=seconds)
    with patch.object(loaded, "async_request_refresh",
                      wraps=loaded.async_request_refresh) as requested:
        stove.get_data.side_effect = None
        stove.get_data.return_value = None
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds + 1))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert_available(hass, False)
        assert stove.get_data.await_count == count + 1
        stove.get_data.side_effect = stove._read
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds + 1))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert_available(hass, True)
        assert loaded.data == original
        assert stove.get_data.await_count == count + 2
        assert loaded.update_interval == timedelta(seconds=seconds)
        requested.assert_not_called()
        assert_no_commands(stove)


@pytest.mark.parametrize("key,method", BUTTONS)
@pytest.mark.parametrize("previous_press", [False, True])
async def test_M03_unavailable_service_is_filtered(
    loaded, hass, stove, key, method, previous_press
):
    entity = buttons(hass)[key]
    if previous_press:
        await press(hass, key)
        stove.assert_only_command(method, *clock_args(method))
        stove.reset_commands()
    previous_state = entity.state
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    await loaded.async_refresh()
    assert_available(hass, False)
    with isolated_readback_request(loaded, stove):
        # The pinned HA service resolver filters unavailable targets. It does
        # not call async_press and does not promise an action exception here.
        await press(hass, key)
        await hass.async_block_till_done()
    assert_no_commands(stove)
    assert entity.state == previous_state
    assert hass.states.get(entity.entity_id).state == "unavailable"
    stove.get_data.side_effect = stove._read
    await loaded.async_refresh()
    assert_available(hass, True)
    assert entity.state == previous_state
    assert hass.states.get(entity.entity_id).state == (previous_state or "unknown")


@pytest.mark.parametrize("key,method", BUTTONS)
@pytest.mark.parametrize("confirmed", [True, False], ids=["true", "false"])
async def test_M03_available_service_preserves_command_and_timestamp(
    loaded, hass, stove, key, method, confirmed
):
    entity = buttons(hass)[key]
    assert entity.available
    assert entity.state is None
    getattr(stove, method).return_value = confirmed
    with isolated_readback_request(loaded, stove, confirmed=confirmed):
        if confirmed:
            await press(hass, key)
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await press(hass, key)
            assert_unconfirmed(raised.value)
        await hass.async_block_till_done()
    stove.assert_only_command(method, *clock_args(method))
    assert dt_util.parse_datetime(entity.state) is not None
    assert hass.states.get(entity.entity_id).state == entity.state
    assert_available(hass, True)


@pytest.mark.parametrize("key,method", BUTTONS)
@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError,
                                        ClientConnectionError])
async def test_M03_available_exceptions_unchanged(
    loaded, hass, stove, key, method, error_type
):
    entity = buttons(hass)[key]
    failure = error_type("original command error")
    getattr(stove, method).side_effect = failure
    with isolated_readback_request(loaded, stove), pytest.raises(error_type) as raised:
        await entity.async_press()
    assert raised.value is failure
    stove.assert_only_command(method, *clock_args(method))
    assert_available(hass, True)


@pytest.mark.parametrize("key,method", BUTTONS)
async def test_M03_available_task_cancellation(loaded, hass, stove, key, method):
    entity = buttons(hass)[key]
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def pending(*args):
        entered.set()
        try:
            await release.wait()
        finally:
            finished.set()

    getattr(stove, method).side_effect = pending
    with isolated_readback_request(loaded, stove):
        task = asyncio.create_task(entity.async_press())
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            task.cancel("M03 cancellation")
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    stove.assert_only_command(method, *clock_args(method))
    assert_available(hass, True)


async def test_M03_removal_unregisters_exactly_one_listener(loaded, hass, stove):
    component = hass.data[DATA_COMPONENT]
    read_count = stove.get_data.await_count
    for entity in buttons(hass).values():
        assert len(button_listeners(loaded, entity)) == 1
        total = len(loaded._listeners)
        await component.async_remove_entity(entity.entity_id)
        await hass.async_block_till_done()
        assert len(loaded._listeners) == total - 1
        assert button_listeners(loaded, entity) == []
        assert component.get_entity(entity.entity_id) is None
        # A subsequent ordinary notification must not call a removed entity.
        with patch.object(entity, "async_write_ha_state") as write:
            loaded.async_update_listeners()
            write.assert_not_called()
    assert stove.get_data.await_count == read_count
    assert_no_commands(stove)


async def test_M03_repeated_reload_preserves_ids_and_listener_counts(
    loaded, hass, entry, stove, stove_factory
):
    coordinators = [loaded]
    clients = [stove]
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    original_entities = dict(registry.entities)
    original_devices = {device.id: device for device in devices.devices}
    original_ids = {key: entity.entity_id for key, entity in buttons(hass).items()}
    listener_count = len(loaded._listeners)
    for _ in range(2):
        current = coordinators[-1]
        old_entities = buttons(hass)
        for entity in old_entities.values():
            assert len(button_listeners(current, entity)) == 1
        clients[-1].get_data.side_effect = None
        clients[-1].get_data.return_value = None
        await current.async_refresh()
        assert_available(hass, False)

        new_client = SimulatedStove()
        stove_factory.return_value = new_client
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        new = hass.data[DOMAIN]["stoves"][entry.entry_id]
        assert new is not current
        assert current._listeners == {}
        clients[-1].destroy.assert_awaited_once_with()
        assert_available(hass, True)
        assert len(new._listeners) == listener_count
        for key, entity in buttons(hass).items():
            assert entity is not old_entities[key]
            assert entity.entity_id == original_ids[key]
            assert len(button_listeners(new, entity)) == 1
        assert dict(registry.entities) == original_entities
        assert {device.id: device for device in devices.devices} == original_devices
        assert new_client.get_data.await_count == 1
        assert_no_commands(new_client)
        coordinators.append(new)
        clients.append(new_client)

    # Explicit final unload also checks the newest coordinator; fixture teardown
    # can safely call unload again once the entry is already not loaded.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    for coordinator, client in zip(coordinators, clients, strict=True):
        assert coordinator._listeners == {}
        client.destroy.assert_awaited_once_with()
        assert_no_commands(client)
