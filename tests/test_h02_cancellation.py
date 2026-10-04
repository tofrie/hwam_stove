"""H02: preserve factory cancellation without taking ownership of a client."""

import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_platform
import pytest

from .helpers import DOMAIN, HOST, registry_entries


def assert_no_client_runtime(hass, entry, stove, coordinator, forward):
    """The pre-existing empty namespace is not a client/runtime entry."""
    stove.destroy.assert_not_called()
    coordinator.assert_not_called()
    forward.assert_not_called()
    assert hass.data[DOMAIN] == {"stoves": {}}
    assert entry.entry_id not in hass.data[DOMAIN]["stoves"]
    assert not registry_entries(hass)
    assert not entity_platform.async_get_platforms(hass, DOMAIN)


@pytest.mark.parametrize("through_ha", [False, True], ids=["integration", "ha"])
@pytest.mark.parametrize("cancel_args", [(), ("synthetic setup cancellation",)],
                         ids=["no-message", "message"])
async def test_H02_setup_preserves_cancellation(
    hass, entry, stove, stove_factory, through_ha, cancel_args
):
    """Converted strict-xfail: actual task cancellation, also through HA setup."""
    from custom_components import hwam_stove

    entered = asyncio.Event()
    factory_cancellations = []

    async def wait_forever(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            factory_cancellations.append(error.args)
            raise

    stove_factory.side_effect = wait_forever
    with (
        patch.object(hwam_stove, "StoveCoordinator") as coordinator,
        patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
    ):
        setup = (hass.config_entries.async_setup(entry.entry_id) if through_ha
                 else hwam_stove.async_setup_entry(hass, entry))
        task = asyncio.create_task(setup)
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            task.cancel(*cancel_args)
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert type(caught.value) is asyncio.CancelledError
            assert task.cancelled()
            assert caught.value.args == cancel_args
            assert factory_cancellations == [cancel_args]
            stove_factory.assert_awaited_once_with(HOST)
            assert_no_client_runtime(hass, entry, stove, coordinator, forward)
            if through_ha:
                assert entry.state == ConfigEntryState.SETUP_ERROR
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            hass.data.pop(DOMAIN, None)


@pytest.mark.parametrize("args", [(), ("factory cancelled", 42)])
async def test_factory_cancelled_error_is_propagated_unchanged(
    hass, entry, stove, stove_factory, args
):
    """Direct exception identity/args; no claim about repeated Task awaits."""
    from custom_components import hwam_stove

    error = asyncio.CancelledError(*args)
    stove_factory.side_effect = error
    try:
        with (
            patch.object(hwam_stove, "StoveCoordinator") as coordinator,
            patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
        ):
            with pytest.raises(asyncio.CancelledError) as caught:
                await hwam_stove.async_setup_entry(hass, entry)
            assert caught.value is error
            assert caught.value.args == args
            assert_no_client_runtime(hass, entry, stove, coordinator, forward)
    finally:
        hass.data.pop(DOMAIN, None)


async def test_create_timeout_keeps_config_entry_not_ready_and_cause(
    hass, entry, stove, stove_factory
):
    from custom_components import hwam_stove

    error = TimeoutError("synthetic create timeout")
    stove_factory.side_effect = error
    try:
        with (
            patch.object(hwam_stove, "StoveCoordinator") as coordinator,
            patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
        ):
            with pytest.raises(ConfigEntryNotReady) as caught:
                await hwam_stove.async_setup_entry(hass, entry)
            assert type(caught.value) is ConfigEntryNotReady
            assert caught.value.__cause__ is error
            assert error.args == ("synthetic create timeout",)
            assert_no_client_runtime(hass, entry, stove, coordinator, forward)
    finally:
        hass.data.pop(DOMAIN, None)
