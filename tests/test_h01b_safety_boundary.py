"""H01B / Forwarding Safety Boundary: offline HA lifecycle diagnostics.

The suspended-platform case adds an await to HWAM's otherwise non-suspending
setup body. Keep it as a general HA counterexample, not a current-HWAM failure.
Close callbacks below are diagnostic injections, never production behavior.
Explicit final cleanup belongs to the harness after pending work has finished.
"""

import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_platform
import pytest

from .helpers import DOMAIN, entity_id_for, registry_entries


async def test_failed_setup_does_not_automatically_unload_platforms(hass, entry, stove):
    from custom_components.hwam_stove import PLATFORMS

    original = hass.config_entries.async_forward_entry_setups

    async def partial_then_fail(config_entry, platforms):
        await original(config_entry, ["button"])
        raise RuntimeError("Injected failure after button platform setup")

    try:
        with patch.object(
            hass.config_entries, "async_forward_entry_setups",
            side_effect=partial_then_fail
        ):
            assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.SETUP_ERROR
        button_id = entity_id_for(hass, "button", "start")
        platforms = entity_platform.async_get_platforms(hass, DOMAIN)
        button = next(p for p in platforms if p.domain == "button")
        assert button_id in button.entities
        assert button.entities[button_id].stove is stove
        stove.destroy.assert_not_called()
        assert await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        assert not button.entities
    finally:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        await stove.destroy()
        hass.data.pop(DOMAIN, None)


@pytest.mark.parametrize("register_close_callback", [False, True])
async def test_platform_unload_does_not_join_shielded_setup(
    hass, entry, stove, register_close_callback
):
    from custom_components.hwam_stove import PLATFORMS, button

    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = button.async_setup_entry
    setup_task = None
    cleanup_observations = []

    async def close_from_entry_callback():
        cleanup_observations.append(setup_task.done())
        await stove.destroy()

    if register_close_callback:
        entry.async_on_unload(close_from_entry_callback)

    async def suspended_platform(*args, **kwargs):
        nonlocal setup_task
        setup_task = asyncio.current_task()
        entered.set()
        try:
            await release.wait()
            await original(*args, **kwargs)
        finally:
            finished.set()

    with patch.object(button, "async_setup_entry", side_effect=suspended_platform):
        outer = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            platforms = entity_platform.async_get_platforms(hass, DOMAIN)
            button_platform = next(p for p in platforms if p.domain == "button")
            outer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await outer
            assert entry.state == ConfigEntryState.SETUP_ERROR
            assert not setup_task.done()
            if register_close_callback:
                assert cleanup_observations == [False]
                stove.destroy.assert_awaited_once_with()
            else:
                stove.destroy.assert_not_called()
            assert await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            assert not setup_task.done()
            assert not button_platform.entities
            # The callback variant has already closed the simulated client.
            release.set()
            async with asyncio.timeout(5):
                await finished.wait()
            await hass.async_block_till_done()
            button_id = entity_id_for(hass, "button", "start")
            assert button_id in button_platform.entities
            assert button_platform.entities[button_id].stove is stove
            remaining = entity_platform.async_get_platforms(hass, DOMAIN)
            assert button_platform not in remaining
        finally:
            release.set()
            if not outer.done():
                outer.cancel()
            await asyncio.gather(outer, return_exceptions=True)
            if setup_task:
                await asyncio.gather(setup_task, return_exceptions=True)
            await hass.async_block_till_done()
            if "button_platform" in locals():
                await button_platform.async_reset()
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            if not register_close_callback:
                await stove.destroy()
            hass.data.pop(DOMAIN, None)


async def test_unmodified_hwam_platform_bodies_complete_eagerly(hass, entry, stove):
    """Observe real task creation without changing the platform setup functions."""
    original = entity_platform.create_eager_task
    observed = []

    def observe(coro, **kwargs):
        filename = coro.cr_code.co_filename
        task = original(coro, **kwargs)
        if "/custom_components/hwam_stove/" in filename:
            observed.append((filename.rsplit("/", 1)[-1], task.done()))
        return task

    with patch.object(entity_platform, "create_eager_task", side_effect=observe):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert len(observed) == 7
    assert all(done for _, done in observed)
    assert {name for name, _ in observed} == {
        "sensor.py", "binary_sensor.py", "button.py", "switch.py",
        "number.py", "time.py", "datetime.py",
    }
    assert len(registry_entries(hass)) == 40
    stove.destroy.assert_not_called()
    assert await hass.config_entries.async_unload(entry.entry_id)
    stove.destroy.assert_awaited_once_with()


async def test_cancellation_at_real_ha_translation_await(hass, entry, stove):
    """Delay HA translation I/O; do not add an await/shield to HWAM setup."""
    from custom_components.hwam_stove import PLATFORMS

    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = entity_platform.PlatformData.async_load_translations
    waiting_task = None

    async def delayed_translation(self):
        nonlocal waiting_task
        if self.platform_name == DOMAIN and self.domain == "button":
            waiting_task = asyncio.current_task()
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return await original(self)

    with patch.object(
        entity_platform.PlatformData, "async_load_translations", delayed_translation
    ):
        outer = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            outer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await outer
            assert cancelled.is_set()
            assert waiting_task.done()
            assert entry.state == ConfigEntryState.SETUP_ERROR
            stove.destroy.assert_not_called()
            assert await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            release.set()
            await hass.async_block_till_done()
            assert not entity_platform.async_get_platforms(hass, DOMAIN)
            assert not any(e.domain == "button" for e in registry_entries(hass))
        finally:
            release.set()
            if not outer.done():
                outer.cancel()
            await asyncio.gather(outer, return_exceptions=True)
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            await stove.destroy()
            hass.data.pop(DOMAIN, None)


async def test_ordinary_platform_error_is_consumed_by_ha(hass, entry, stove, caplog):
    """An exception inside a platform is not a parent integration setup error."""
    from custom_components.hwam_stove import button

    async def fail(*args, **kwargs):
        raise RuntimeError("Synthetic platform body failure")

    with patch.object(button, "async_setup_entry", side_effect=fail):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.LOADED
    assert "Synthetic platform body failure" in caplog.text
    assert len(registry_entries(hass)) == 38
    assert not any(e.domain == "button" for e in registry_entries(hass))
    stove.destroy.assert_not_called()
    assert await hass.config_entries.async_unload(entry.entry_id)
    stove.destroy.assert_awaited_once_with()


async def test_escaped_forward_error_leaves_gather_sibling_running(hass, entry, stove):
    """Inject outside HA's platform exception handler to exercise its gather."""
    from custom_components.hwam_stove import PLATFORMS

    entered, release = asyncio.Event(), asyncio.Event()
    original = hass.config_entries._async_forward_entry_setup
    sibling = None

    async def forward(config_entry, domain, preload_platform):
        nonlocal sibling
        if domain == "button":
            sibling = asyncio.current_task()
            entered.set()
            await release.wait()
        elif domain == "number":
            await entered.wait()
            raise RuntimeError("Synthetic escaped forwarding failure")
        return await original(config_entry, domain, preload_platform)

    with patch.object(
        hass.config_entries, "_async_forward_entry_setup", side_effect=forward
    ):
        try:
            assert not await hass.config_entries.async_setup(entry.entry_id)
            assert entry.state == ConfigEntryState.SETUP_ERROR
            assert not sibling.done()
            stove.destroy.assert_not_called()
            release.set()
            await sibling
            await hass.async_block_till_done()
            button_id = entity_id_for(hass, "button", "start")
            platform = next(p for p in entity_platform.async_get_platforms(hass, DOMAIN)
                            if p.domain == "button")
            assert platform.entities[button_id].stove is stove
        finally:
            release.set()
            if sibling:
                await asyncio.gather(sibling, return_exceptions=True)
            await hass.async_block_till_done()
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            await stove.destroy()
            hass.data.pop(DOMAIN, None)


async def test_entry_cleanup_callback_is_not_a_task_join(hass, entry, stove):
    """Public entry APIs run the close callback before awaiting other tasks."""
    started, release, consumer_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observations = []

    async def consumer():
        started.set()
        try:
            await release.wait()
        finally:
            consumer_done.set()

    async def cleanup():
        observations.append(consumer_done.is_set())
        await stove.destroy()
        release.set()

    async def fail_forward(*args, **kwargs):
        # Model a resource consumer using the public, entry-owned task API.
        entry.async_create_task(hass, consumer(), "diagnostic resource consumer")
        await started.wait()
        entry.async_on_unload(cleanup)
        raise RuntimeError("Synthetic failure with entry-owned pending work")

    try:
        with patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=fail_forward
        ):
            assert not await hass.config_entries.async_setup(entry.entry_id)
        assert observations == [False]
        assert consumer_done.is_set()
        stove.destroy.assert_awaited_once_with()
    finally:
        release.set()
        await hass.async_block_till_done()
        hass.data.pop(DOMAIN, None)


async def test_actual_entity_add_tasks_finish_eagerly(hass, entry, stove):
    """Observe the public entry helper without changing entity or platform code."""
    original = entry.async_create_task
    observed = []

    def observe(hass_arg, target, name=None, eager_start=True):
        task = original(hass_arg, target, name, eager_start)
        if name and name.startswith("EntityPlatform async_add_entities_for_entry"):
            observed.append((name, task.done()))
        return task

    with patch.object(entry, "async_create_task", side_effect=observe):
        assert await hass.config_entries.async_setup(entry.entry_id)
    # Binary sensor setup schedules two batches; all other platforms one each.
    assert len(observed) == 8
    assert all(done for _, done in observed)
    assert await hass.config_entries.async_unload(entry.entry_id)
    stove.destroy.assert_awaited_once_with()


async def test_ha_platform_timeout_returns_while_setup_body_is_alive(
    hass, entry, stove
):
    """Use HA's own timeout/shield, with a shortened timer and a delayed body."""
    from custom_components.hwam_stove import PLATFORMS, button

    entered, release = asyncio.Event(), asyncio.Event()
    original = button.async_setup_entry
    inner = None

    async def slow_body(*args, **kwargs):
        nonlocal inner
        inner = asyncio.current_task()
        entered.set()
        await release.wait()
        await original(*args, **kwargs)

    with (
        patch.object(button, "async_setup_entry", side_effect=slow_body),
        patch.object(entity_platform, "SLOW_SETUP_MAX_WAIT", 0.01),
    ):
        try:
            async with asyncio.timeout(5):
                assert await hass.config_entries.async_setup(entry.entry_id)
            assert entered.is_set()
            assert entry.state == ConfigEntryState.LOADED
            assert not inner.done()
            platform = next(p for p in entity_platform.async_get_platforms(hass, DOMAIN)
                            if p.domain == "button")
            assert await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
            assert not inner.done()
            release.set()
            await inner
            await hass.async_block_till_done()
            assert len(platform.entities) == 2
        finally:
            release.set()
            if inner:
                await asyncio.gather(inner, return_exceptions=True)
            await hass.async_block_till_done()
            if "platform" in locals():
                await platform.async_reset()
            await hass.config_entries.async_unload(entry.entry_id)
            stove.destroy.assert_awaited_once_with()
