"""M02: real HA coordinator readback, without controller freshness assumptions."""

import asyncio
from copy import deepcopy
from datetime import time
import gc
from unittest.mock import Mock, call, patch
import warnings

from aiohttp import ClientConnectionError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
import pytest

from .command_cases import CASES, invoke
from .helpers import DOMAIN, entity_id_for

pytestmark = pytest.mark.contract


@pytest.fixture(autouse=True)
async def no_loop_errors_or_resource_warnings():
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    errors = []
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        try:
            yield
        finally:
            gc.collect()
            await asyncio.sleep(0)
            loop.set_exception_handler(previous)
        assert not errors, errors
        assert not [w for w in caught if issubclass(w.category, ResourceWarning)]


def registered(hass, platform, key):
    target = entity_id_for(hass, platform, key)
    return next(p.entities[target] for p in
                entity_platform.async_get_platforms(hass, DOMAIN)
                if target in p.entities)


async def expire_debounce(hass, coordinator):
    """Fire HA's queued cooldown timer, without sleeping/changing its policy."""
    debounce = coordinator._debounced_refresh
    assert debounce.cooldown == 10 and debounce.immediate is True
    assert debounce._timer_task is not None
    debounce._timer_task.cancel()
    debounce._on_debounce()
    await hass.async_block_till_done()


async def wait(event):
    async with asyncio.timeout(5):
        await event.wait()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
async def test_M02_confirmed_command_refreshes(loaded, entities, stove, case):
    """The ten former strict xfails: actual read, not just request invocation."""
    before = stove.get_data.await_count
    stove.data["room_temperature"] = 25
    await invoke(case, entities)
    stove.assert_only_command(case.method, *case.expected_args,
                              **dict(case.expected_kwargs))
    assert stove.get_data.await_count == before + 1
    assert loaded.data["room_temperature"] == 25
    assert loaded.last_update_success


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
@pytest.mark.parametrize("outcome", [False, None, 1, "OK", "error", "cancel"])
async def test_only_exact_true_requests_readback(
    loaded, entities, stove, case, outcome
):
    method = getattr(stove, case.method)
    error = RuntimeError("command failed")
    cancel = asyncio.CancelledError("command cancelled")
    if outcome == "error":
        method.side_effect = error
    elif outcome == "cancel":
        method.side_effect = cancel
    else:
        method.return_value = outcome
    before = stove.get_data.await_count
    with patch.object(
        loaded, "async_request_refresh", wraps=loaded.async_request_refresh
    ) as request:
        if outcome is False:
            with pytest.raises(HomeAssistantError) as raised:
                await invoke(case, entities)
            assert raised.value.translation_key == "command_not_confirmed"
        elif outcome in ("error", "cancel"):
            expected = error if outcome == "error" else cancel
            with pytest.raises(type(expected)) as raised:
                await invoke(case, entities)
            assert raised.value is expected
        else:
            await invoke(case, entities)
        request.assert_not_called()
    assert stove.get_data.await_count == before
    assert getattr(stove, case.method).await_count == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
@pytest.mark.parametrize("failure", [None, TimeoutError, ClientConnectionError,
                                     RuntimeError, NotImplementedError])
async def test_confirmed_command_stays_confirmed_when_read_fails(
    loaded, entities, stove, case, failure
):
    before = stove.get_data.await_count
    stove.get_data.side_effect = failure if failure else None
    stove.get_data.return_value = None
    await invoke(case, entities)  # Never command_not_confirmed or read exception.
    assert not loaded.last_update_success
    assert stove.get_data.await_count == before + 1
    stove.assert_only_command(case.method, *case.expected_args,
                              **dict(case.expected_kwargs))
    stove.get_data.side_effect = stove._read
    await loaded.async_refresh()  # Independent ordinary recovery, no command retry.
    assert loaded.last_update_success
    assert getattr(stove, case.method).await_count == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
async def test_cancellation_during_post_command_read(loaded, entities, stove, case):
    entered = asyncio.Event()

    async def read():
        entered.set()
        await asyncio.Future()

    stove.get_data.side_effect = read
    before = stove.get_data.await_count
    task = asyncio.create_task(invoke(case, entities))
    try:
        await wait(entered)
        task.cancel("readback cancelled")
        with pytest.raises(asyncio.CancelledError, match="readback cancelled"):
            await task
        assert not loaded.last_update_success
        assert not loaded._command_read
        assert stove.get_data.await_count == before + 1
        assert getattr(stove, case.method).await_count == 1
        if case.platform == "time":
            assert loaded.night_times._confirmed_pair is not None
            assert not loaded.night_times._uncertain
        stove.get_data.side_effect = stove._read
        await loaded.async_refresh()
        assert loaded.last_update_success
        assert getattr(stove, case.method).await_count == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("platform,key,action,value", [
    ("number", "burn_level", "async_set_native_value", 4),
    ("switch", "night_lowering", "async_turn_on", None),
    ("switch", "remote_refill_alarm", "async_turn_on", None),
])
@pytest.mark.parametrize("changed", [False, True])
async def test_optimistic_entity_reconciles_even_identical_data(
    loaded, hass, stove, platform, key, action, value, changed
):
    entity = registered(hass, platform, key)
    before = hass.states.get(entity.entity_id).state
    listener = Mock()
    remove = loaded.async_add_listener(listener)
    if changed:
        stove.data["room_temperature"] += 1
    try:
        await getattr(entity, action)(*(() if value is None else (value,)))
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == before
        listener.assert_called_once_with()
        assert loaded.always_update is False
        listener.reset_mock()
        await loaded.async_refresh()
        listener.assert_not_called()  # No global always_update workaround.
    finally:
        remove()


@pytest.mark.parametrize("platform,key,action,value,field,result", [
    ("number", "burn_level", "async_set_native_value", 4, "burn_level", 4),
    ("switch", "night_lowering", "async_turn_on", None, "night_lowering", "Night"),
    ("switch", "remote_refill_alarm", "async_turn_on", None, "remote_refill_alarm", 1),
])
async def test_changed_readback_reaches_registered_entity(
    loaded, hass, stove, platform, key, action, value, field, result
):
    target = registered(hass, platform, key)
    stove.data[field] = result
    await getattr(target, action)(*(() if value is None else (value,)))
    await hass.async_block_till_done()
    expected = "4" if platform == "number" else "on"
    assert hass.states.get(target.entity_id).state == expected


async def test_failure_marks_unavailable_then_identical_recovery_reconciles(
    loaded, hass, stove
):
    target = registered(hass, "number", "burn_level")
    stove.get_data.side_effect = ClientConnectionError
    await target.async_set_native_value(4)
    await hass.async_block_till_done()
    assert hass.states.get(target.entity_id).state == "unavailable"
    assert not registered(hass, "button", "start").available  # M03 unchanged.
    stove.get_data.side_effect = stove._read
    await loaded.async_refresh()
    assert hass.states.get(target.entity_id).state == "3"
    assert not loaded._command_reconcile
    stove.set_burn_level.assert_awaited_once_with(4)


async def test_debounce_coalesces_commands_and_does_not_await_queued_read(
    loaded, entities, stove, hass
):
    before = stove.get_data.await_count
    await invoke(CASES[0], entities)
    for case in (CASES[1], CASES[3], CASES[5]):
        await invoke(case, entities)
    assert stove.get_data.await_count == before + 1
    assert loaded._debounced_refresh._execute_at_end_of_timer
    await expire_debounce(hass, loaded)
    assert stove.get_data.await_count == before + 2
    for method in (
        "set_burn_level", "set_night_lowering", "set_remote_refill_alarm", "start"
    ):
        assert getattr(stove, method).await_count == 1
    await expire_debounce(hass, loaded)
    assert stove.get_data.await_count == before + 2  # Cooldown alone is not a retry.


async def test_concurrent_confirmations_during_inflight_read(
    loaded, entities, stove, hass
):
    entered, release = asyncio.Event(), asyncio.Event()
    before = stove.get_data.await_count

    async def read():
        if stove.get_data.await_count == before + 1:
            entered.set()
            await release.wait()
        return deepcopy(stove.data)

    stove.get_data.side_effect = read
    task = asyncio.create_task(invoke(CASES[0], entities))
    try:
        await wait(entered)
        await invoke(CASES[1], entities)
        await invoke(CASES[3], entities)
        assert not task.done()
        assert stove.get_data.await_count == before + 1
        release.set()
        await task
        await expire_debounce(hass, loaded)
        assert stove.get_data.await_count == before + 2
        for method in (
            "set_burn_level", "set_night_lowering", "set_remote_refill_alarm"
        ):
            assert getattr(stove, method).await_count == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_start_does_not_require_immediate_phase_change(loaded, entities, stove):
    stove.data["phase"] = "Standby"
    await loaded.async_refresh()
    before = stove.get_data.await_count
    await invoke(CASES[5], entities)
    assert loaded.data["phase"] == "Standby"
    assert loaded.update_interval.total_seconds() == 60
    assert stove.get_data.await_count == before + 1
    stove.start.assert_awaited_once_with()


async def test_night_stale_immediate_and_coalesced_reads_preserve_confirmed_pair(
    loaded, entities, stove, hass
):
    await invoke(CASES[7], entities)
    assert loaded.night_times._confirmed_pair == (time(21), time(6, 30))
    assert loaded.data["night_begin_time"] == time(22, 15)  # Real stale observation.
    await invoke(CASES[8], entities)
    assert loaded.night_times._confirmed_pair == (time(21), time(7))
    await expire_debounce(hass, loaded)
    assert loaded.night_times._confirmed_pair == (time(21), time(7))
    assert stove.set_night_lowering_hours.await_args_list == [
        call(start=time(21), end=time(6, 30)), call(start=time(21), end=time(7))]
    await loaded.async_refresh()  # Existing regular-read contract unchanged.
    assert loaded.night_times._confirmed_pair is None
    assert loaded.night_times._last_read == (time(22, 15), time(6, 30))


async def test_precommand_regular_read_cannot_replace_night_confirmation(
    loaded, entities, stove, hass
):
    entered, release = asyncio.Event(), asyncio.Event()
    old = deepcopy(stove.data)

    async def read():
        entered.set()
        await release.wait()
        return deepcopy(old)

    stove.get_data.side_effect = read
    task = asyncio.create_task(loaded.async_refresh())
    try:
        await wait(entered)
        await invoke(CASES[7], entities)  # Queues behind regular read's HA lock.
        assert loaded.night_times._confirmed_pair == (time(21), time(6, 30))
        release.set()
        await task
        assert loaded.night_times._confirmed_pair == (time(21), time(6, 30))
        await expire_debounce(hass, loaded)
        assert loaded.night_times._confirmed_pair == (time(21), time(6, 30))
        await invoke(CASES[8], entities)
        assert stove.set_night_lowering_hours.await_args.kwargs == {
            "start": time(21), "end": time(7)}
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("outcome", [False, RuntimeError, asyncio.CancelledError])
async def test_uncertain_night_pair_requires_regular_recovery(
    loaded, entities, stove, outcome
):
    if outcome is False:
        stove.set_night_lowering_hours.return_value = False
        exception = HomeAssistantError
    else:
        stove.set_night_lowering_hours.side_effect = outcome
        exception = outcome
    before = stove.get_data.await_count
    with pytest.raises(exception):
        await invoke(CASES[7], entities)
    assert stove.get_data.await_count == before
    assert loaded.night_times._uncertain
    await invoke(CASES[0], entities)  # Even successful unrelated read cannot erase it.
    assert loaded.night_times._uncertain
    stove.set_night_lowering_hours.side_effect = None
    stove.set_night_lowering_hours.return_value = True
    with pytest.raises(HomeAssistantError) as raised:
        await invoke(CASES[8], entities)
    assert raised.value.translation_key == "night_times_not_synchronized"
    stove.data["night_begin_time"] = time(20)
    await loaded.async_refresh()
    assert not loaded.night_times._uncertain
    await invoke(CASES[8], entities)
    assert stove.set_night_lowering_hours.await_args.kwargs == {
        "start": time(20), "end": time(7)}
    assert stove.set_night_lowering_hours.await_count == 2


async def test_concurrent_night_actions_keep_shared_lock_and_pair(
    loaded, entities, stove, hass
):
    entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    lock = loaded.night_times._lock
    acquire = lock.acquire

    async def acquire_observed():
        if lock.locked():
            waiting.set()
        return await acquire()

    async def command(*, start, end):
        if stove.set_night_lowering_hours.await_count == 1:
            entered.set()
            await release.wait()
        return True

    stove.set_night_lowering_hours.side_effect = command
    tasks = []
    with patch.object(lock, "acquire", side_effect=acquire_observed):
        try:
            tasks.append(asyncio.create_task(invoke(CASES[7], entities)))
            await wait(entered)
            tasks.append(asyncio.create_task(invoke(CASES[8], entities)))
            await wait(waiting)
            assert stove.set_night_lowering_hours.await_count == 1
            release.set()
            await asyncio.gather(*tasks)
            await expire_debounce(hass, loaded)
            assert loaded.night_times._confirmed_pair == (time(21), time(7))
            assert stove.set_night_lowering_hours.await_args_list == [
                call(start=time(21), end=time(6, 30)),
                call(start=time(21), end=time(7))]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_regular_read_can_satisfy_pending_refresh_and_unload_cancels_it(
    loaded, entities, stove, hass, entry
):
    before = stove.get_data.await_count
    await invoke(CASES[0], entities)
    await invoke(CASES[1], entities)
    assert loaded._debounced_refresh._execute_at_end_of_timer
    await loaded.async_refresh()
    assert not loaded._debounced_refresh._execute_at_end_of_timer
    assert stove.get_data.await_count == before + 2
    await invoke(CASES[0], entities)
    await invoke(CASES[1], entities)
    assert loaded._debounced_refresh._timer_task is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert loaded._debounced_refresh._timer_task is None
    assert loaded._debounced_refresh._shutdown_requested
    stove.destroy.assert_awaited_once_with()


async def test_queued_identical_read_reconciles_optimistic_state(
    loaded, entities, stove, hass
):
    before = stove.get_data.await_count
    await invoke(CASES[5], entities)  # Establish HA's normal cooldown.
    target = registered(hass, "number", "burn_level")
    await target.async_set_native_value(4)
    assert hass.states.get(target.entity_id).state == "4"
    assert stove.get_data.await_count == before + 1  # Request returned, read pending.
    await expire_debounce(hass, loaded)
    assert hass.states.get(target.entity_id).state == "3"
    assert stove.get_data.await_count == before + 2
    stove.set_burn_level.assert_awaited_once_with(4)


async def test_queued_failed_read_is_not_command_failure(loaded, entities, stove, hass):
    before = stove.get_data.await_count
    await invoke(CASES[0], entities)
    await invoke(CASES[1], entities)
    stove.get_data.side_effect = ClientConnectionError
    await expire_debounce(hass, loaded)
    assert not loaded.last_update_success
    assert stove.get_data.await_count == before + 2
    stove.set_burn_level.assert_awaited_once_with(4)
    stove.set_night_lowering.assert_awaited_once_with(True)


async def test_ha_can_absorb_queued_request_into_long_inflight_read(
    loaded, entities, stove, hass
):
    """HA's 'any call is good' rule is not proof of post-command freshness."""
    entered, release = asyncio.Event(), asyncio.Event()
    before = stove.get_data.await_count
    snapshot = deepcopy(stove.data)

    async def read():
        entered.set()
        await release.wait()
        return deepcopy(snapshot)

    stove.get_data.side_effect = read
    task = asyncio.create_task(invoke(CASES[0], entities))
    try:
        await wait(entered)
        await invoke(CASES[7], entities)
        # Fire only the scheduled timer; blocking all HA tasks would await read.
        debounce = loaded._debounced_refresh
        debounce._timer_task.cancel()
        debounce._timer_task = None
        await debounce._handle_timer_finish()
        assert not debounce._execute_at_end_of_timer
        assert stove.get_data.await_count == before + 1
        release.set()
        await task
        await expire_debounce(hass, loaded)
        assert stove.get_data.await_count == before + 1
        assert loaded.night_times._confirmed_pair == (time(21), time(6, 30))
        assert loaded.data == snapshot
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_regular_refresh_waits_for_requested_read_lock(
    loaded, entities, stove
):
    entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    before = stove.get_data.await_count
    acquire = loaded._debounced_refresh._execute_lock.acquire

    async def observed_acquire():
        if loaded._debounced_refresh._execute_lock.locked():
            waiting.set()
        return await acquire()

    async def read():
        if stove.get_data.await_count == before + 1:
            entered.set()
            await release.wait()
        return deepcopy(stove.data)

    stove.get_data.side_effect = read
    tasks = []
    with patch.object(loaded._debounced_refresh._execute_lock,
                      "acquire", side_effect=observed_acquire):
        try:
            tasks.append(asyncio.create_task(invoke(CASES[7], entities)))
            await wait(entered)
            tasks.append(asyncio.create_task(loaded.async_refresh()))
            await wait(waiting)
            assert stove.get_data.await_count == before + 1
            assert loaded._command_read
            release.set()
            await asyncio.gather(*tasks)
            assert stove.get_data.await_count == before + 2
            assert not loaded._command_read
            # The subsequent regular read is not incorrectly tagged as requested.
            assert loaded.night_times._confirmed_pair is None
            assert loaded.night_times._last_read == (time(22, 15), time(6, 30))
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
