"""H01A: client ownership before any platform forwarding, using real HA setup."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry, entity_platform
import pytest

from .helpers import DOMAIN, SimulatedStove, registry_entries
from .lifecycle_checks import assert_no_client_consumers


@pytest.fixture
def observed_setup(monkeypatch, stove):
    """Observe the integration's exception before HA maps it; track a fake resource."""
    from custom_components import hwam_stove

    original = hwam_stove.async_setup_entry
    observations = {"errors": [], "active": True, "events": []}

    async def setup(*args):
        try:
            return await original(*args)
        except BaseException as error:
            observations["errors"].append(error)
            raise

    async def close():
        assert observations["active"]
        observations["active"] = False
        observations["events"].append("destroy")

    monkeypatch.setattr(hwam_stove, "async_setup_entry", setup)
    stove.destroy.side_effect = close
    return observations


def assert_pre_forward_cleanup(hass, stove, forward, observed_setup, error):
    forward.assert_not_called()
    stove.destroy.assert_awaited_once_with()
    assert not observed_setup["active"]
    assert observed_setup["errors"] == [error]
    assert observed_setup["errors"][0] is error
    assert DOMAIN not in hass.data
    assert not registry_entries(hass)
    assert not entity_platform.async_get_platforms(hass, DOMAIN)
    assert not hass.states.async_all()


async def test_H01_failed_first_refresh_closes_client(
    hass, entry, stove, observed_setup
):
    """Converted original H01 xfail: the refresh precedes every platform call."""
    from custom_components.hwam_stove import StoveCoordinator

    error = ConfigEntryNotReady("synthetic offline")
    original_shutdown = StoveCoordinator.async_shutdown

    async def shutdown(self):
        observed_setup["events"].append("shutdown")
        await original_shutdown(self)

    with (
        patch.object(StoveCoordinator, "async_config_entry_first_refresh",
                     side_effect=error),
        patch.object(StoveCoordinator, "async_shutdown", shutdown),
        patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state == ConfigEntryState.SETUP_RETRY
        assert_pre_forward_cleanup(hass, stove, forward, observed_setup, error)
        # HA also calls its registered coordinator shutdown; that API is idempotent.
        assert observed_setup["events"] == ["shutdown", "destroy", "shutdown"]
    await hass.config_entries.async_unload(entry.entry_id)
    stove.destroy.assert_awaited_once_with()


@pytest.mark.parametrize("point", ["constructor", "partial_constructor",
                                       "store_before", "store_after", "refresh"])
async def test_pre_forward_failure_points(
    hass, entry, stove, observed_setup, monkeypatch, point
):
    from custom_components import hwam_stove

    error = RuntimeError(f"synthetic {point} failure")
    if point == "constructor":
        # Construction is synchronous: fail immediately after create returns.
        monkeypatch.setattr(hwam_stove, "StoveCoordinator", Mock(side_effect=error))
    elif point == "partial_constructor":
        registry = device_registry.async_get(hass)
        original = registry.async_get_or_create

        def create_device(**kwargs):
            if kwargs["translation_key"] == "hwam_remote_device":
                raise error
            return original(**kwargs)

        monkeypatch.setattr(registry, "async_get_or_create", create_device)
    elif point.startswith("store_"):
        class FailingStorage(dict):
            def __setitem__(self, key, value):
                if point == "store_after":
                    super().__setitem__(key, value)
                raise error

        hass.data[DOMAIN] = {"stoves": FailingStorage()}
    else:
        monkeypatch.setattr(hwam_stove.StoveCoordinator,
                            "async_config_entry_first_refresh",
                            AsyncMock(side_effect=error))

    with patch.object(hass.config_entries, "async_forward_entry_setups") as forward:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state == ConfigEntryState.SETUP_ERROR
        assert_pre_forward_cleanup(hass, stove, forward, observed_setup, error)


async def test_missing_runtime_container_still_closes_client(
    hass, entry, stove, observed_setup
):
    hass.data[DOMAIN] = {}
    with patch.object(hass.config_entries, "async_forward_entry_setups") as forward:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        error, = observed_setup["errors"]
        assert isinstance(error, KeyError)
        assert_pre_forward_cleanup(hass, stove, forward, observed_setup, error)


async def test_real_cancellation_after_create_cleans_up_and_propagates(
    hass, entry, stove, observed_setup
):
    entered, finished = asyncio.Event(), asyncio.Event()

    async def read():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    original_close = stove.destroy.side_effect

    async def close_after_read_finished():
        assert finished.is_set()
        await original_close()

    stove.get_data.side_effect = read
    stove.destroy.side_effect = close_after_read_finished
    with patch.object(hass.config_entries, "async_forward_entry_setups") as forward:
        task = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert task.cancelled()
            assert entry.state == ConfigEntryState.SETUP_ERROR
            assert_pre_forward_cleanup(
                hass, stove, forward, observed_setup, caught.value
            )
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cleanup_error", [
    RuntimeError("close failed"), asyncio.CancelledError("close cancelled")
])
async def test_destroy_failure_keeps_primary_error(
    hass, entry, stove, observed_setup, caplog, cleanup_error
):
    from custom_components.hwam_stove import StoveCoordinator

    error = RuntimeError("primary refresh failure")
    stove.destroy.side_effect = cleanup_error
    with (
        patch.object(StoveCoordinator, "async_config_entry_first_refresh",
                     side_effect=error),
        patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        forward.assert_not_called()
    assert observed_setup["errors"][0] is error
    assert DOMAIN not in hass.data
    stove.destroy.assert_awaited_once_with()
    # A failing destroy cannot promise the underlying resource actually closed.
    assert observed_setup["active"]
    records = [r for r in caplog.records
               if r.message.startswith("Failed to close Stove")]
    assert len(records) == 1
    assert records[0].exc_info[1] is cleanup_error


@pytest.mark.parametrize("cleanup_point", ["shutdown", "runtime_remove"])
async def test_other_cleanup_failure_does_not_skip_destroy(
    hass, entry, stove, observed_setup, caplog, cleanup_point
):
    from custom_components.hwam_stove import StoveCoordinator

    error = RuntimeError("primary failure")
    shutdown = StoveCoordinator.async_shutdown
    shutdown_failed = False

    async def failed_shutdown(self):
        nonlocal shutdown_failed
        await shutdown(self)
        if not shutdown_failed:
            shutdown_failed = True
            raise RuntimeError("synthetic shutdown failure after stopping")

    class FailingRemove(dict):
        def pop(self, key, *args):
            raise RuntimeError("synthetic runtime removal failure")

    if cleanup_point == "runtime_remove":
        hass.data[DOMAIN] = {"stoves": FailingRemove()}
    try:
        with (
            patch.object(StoveCoordinator, "async_config_entry_first_refresh",
                         side_effect=error),
            patch.object(StoveCoordinator, "async_shutdown",
                         failed_shutdown if cleanup_point == "shutdown" else shutdown),
        ):
            assert not await hass.config_entries.async_setup(entry.entry_id)
        assert observed_setup["errors"][0] is error
        assert not observed_setup["active"]
        stove.destroy.assert_awaited_once_with()
        assert "during pre-forward cleanup" in caplog.text
    finally:
        # Only this injected broken mapping cannot be pruned by production cleanup.
        hass.data.pop(DOMAIN, None)


async def test_repeated_failures_close_each_client_without_accumulating_runtime(
    hass, entry, stove_factory
):
    from custom_components.hwam_stove import StoveCoordinator

    clients = [SimulatedStove() for _ in range(3)]
    stove_factory.side_effect = clients
    with patch.object(StoveCoordinator, "async_config_entry_first_refresh",
                      side_effect=RuntimeError("synthetic retry failure")):
        for client in clients:
            assert not await hass.config_entries.async_setup(entry.entry_id)
            client.destroy.assert_awaited_once_with()
            assert DOMAIN not in hass.data
            assert not registry_entries(hass)
            assert not entity_platform.async_get_platforms(hass, DOMAIN)
            # Use HA's public transition before a manual retry, not private state.
            assert await hass.config_entries.async_unload(entry.entry_id)
    assert stove_factory.await_count == 3
    for client in clients:
        client.destroy.assert_awaited_once_with()
    # Persistent device identities are stable and must not be deleted by H01A.
    assert len(device_registry.async_entries_for_config_entry(
        device_registry.async_get(hass), entry.entry_id
    )) == 2


async def test_failed_setup_preserves_other_runtime_data(hass, entry, stove):
    from custom_components.hwam_stove import StoveCoordinator

    other_entry, other_data = object(), object()
    hass.data[DOMAIN] = {"stoves": {"other": other_entry}, "other_data": other_data}
    with patch.object(StoveCoordinator, "async_config_entry_first_refresh",
                      side_effect=RuntimeError("synthetic failure")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert hass.data[DOMAIN] == {
        "stoves": {"other": other_entry}, "other_data": other_data
    }
    stove.destroy.assert_awaited_once_with()
    hass.data.pop(DOMAIN)


async def test_success_keeps_client_until_normal_unload(
    hass, entry, stove, observed_setup
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.version == 2  # Existing B01 upgrade followed by normal setup.
    assert observed_setup["active"]
    stove.destroy.assert_not_called()
    coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
    await coordinator.async_refresh()
    assert coordinator.data == stove.data
    assert len(registry_entries(hass)) == 44
    close = stove.destroy.side_effect
    platforms = tuple(entity_platform.async_get_platforms(hass, DOMAIN))
    assert len(platforms) == 7
    assert any(p.entities for p in platforms)
    assert coordinator._listeners
    unload = hass.config_entries.async_unload_platforms
    unload_completed = False

    async def observe_unload(*args):
        nonlocal unload_completed
        result = await unload(*args)
        unload_completed = result
        return result

    async def close_after_platforms_before_runtime_removal():
        assert unload_completed
        assert_no_client_consumers(platforms, coordinator)
        assert hass.data[DOMAIN]["stoves"][entry.entry_id] is coordinator
        await close()

    stove.destroy.side_effect = close_after_platforms_before_runtime_removal
    with patch.object(hass.config_entries, "async_unload_platforms",
                      side_effect=observe_unload) as observed_unload:
        assert await hass.config_entries.async_unload(entry.entry_id)
        observed_unload.assert_awaited_once()
    stove.destroy.assert_awaited_once_with()
    assert not observed_setup["active"]
    assert DOMAIN not in hass.data


@pytest.mark.parametrize("error,expected_state", [
    (TimeoutError("create timeout"), ConfigEntryState.SETUP_RETRY),
    (RuntimeError("create failure"), ConfigEntryState.SETUP_ERROR),
])
async def test_create_failure_has_no_owned_client(
    hass, entry, stove, stove_factory, error, expected_state
):
    stove_factory.side_effect = error
    with patch.object(hass.config_entries, "async_forward_entry_setups") as forward:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        forward.assert_not_called()
    assert entry.state == expected_state
    stove.destroy.assert_not_called()
    await hass.config_entries.async_unload(entry.entry_id)
    hass.data.pop(DOMAIN, None)


async def test_H01B_forward_call_failure_is_outside_cleanup_window(
    hass, entry, stove, observed_setup
):
    """Even an immediately failing forward call is beyond H01A ownership."""
    error = RuntimeError("synthetic forwarding failure before any platform")
    try:
        with patch.object(hass.config_entries, "async_forward_entry_setups",
                          side_effect=error) as forward:
            assert not await hass.config_entries.async_setup(entry.entry_id)
            forward.assert_awaited_once()
        assert entry.state == ConfigEntryState.SETUP_ERROR
        assert observed_setup["errors"][0] is error
        assert observed_setup["active"]
        stove.destroy.assert_not_called()
        assert hass.data[DOMAIN]["stoves"][entry.entry_id].stove is stove
        assert not registry_entries(hass)
    finally:
        # Diagnostic fixture cleanup only: this injected forward started no work.
        await stove.destroy()
        hass.data.pop(DOMAIN, None)
