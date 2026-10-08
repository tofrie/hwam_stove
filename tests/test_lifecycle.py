"""Successful setup/unload and coordinator contracts in real HA."""

from datetime import timedelta

import aiohttp
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from .helpers import DOMAIN, HOST, registry_entries

pytestmark = pytest.mark.contract


async def test_setup(loaded, hass, entry, stove, stove_factory):
    stove_factory.assert_awaited_once_with(HOST)
    stove.get_data.assert_awaited_once_with()
    assert loaded.data == stove.data
    assert loaded.config_entry is entry
    assert len(registry_entries(hass)) == 44
    assert {e.domain for e in registry_entries(hass)} == {
        "binary_sensor", "button", "datetime", "number", "sensor", "switch", "time",
    }
    assert len(hass.states.async_all()) == 42


async def test_normal_unload(hass, entry, stove):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    stove.destroy.assert_awaited_once_with()
    assert DOMAIN not in hass.data
    assert all(s.state == "unavailable" for s in hass.states.async_all())


@pytest.mark.parametrize("phase,seconds", [("Ignition", 10), ("Burn", 10),
                                             ("Glow", 10), ("Standby", 60)])
async def test_poll_interval(loaded, stove, phase, seconds):
    stove.data["phase"] = phase
    await loaded.async_refresh()
    assert loaded.update_interval == timedelta(seconds=seconds)
    assert loaded.data["phase"] == phase


async def test_equal_data_does_not_notify(loaded):
    from unittest.mock import Mock

    listener = Mock()
    unsubscribe = loaded.async_add_listener(listener)
    try:
        await loaded.async_refresh()
        listener.assert_not_called()
        assert loaded.always_update is False
    finally:
        unsubscribe()


async def test_none_means_update_failed(loaded, stove):
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    with pytest.raises(UpdateFailed, match="Got empty response"):
        await loaded._async_update_data()


@pytest.mark.parametrize(
    "failure", [TimeoutError(), aiohttp.ClientConnectionError(), None]
)
async def test_offline_and_recovery(loaded, hass, stove, failure):
    from .helpers import entity_id_for

    entity_id = entity_id_for(hass, "sensor", "room_temperature")
    stove.get_data.side_effect = failure
    stove.get_data.return_value = None
    await loaded.async_refresh()
    assert not loaded.last_update_success
    assert hass.states.get(entity_id).state == "unavailable"
    stove.get_data.side_effect = stove._read
    await loaded.async_refresh()
    assert loaded.last_update_success
    assert hass.states.get(entity_id).state == "21"
