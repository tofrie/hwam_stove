"""H05: paired commands, cancellation and existing-read ordering in real HA."""

import asyncio
from copy import deepcopy
from datetime import time
from string import Formatter
from unittest.mock import AsyncMock, Mock, call, patch

from aiohttp import ClientConnectionError
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform, translation
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .helpers import COMMANDS, DOMAIN, ENTRY_ID, SimulatedStove, entity_id_for

pytestmark = pytest.mark.contract

MESSAGES = {
    "en": "The current night times must be read from the stove again "
          "before they can be changed.",
    "de": "Die aktuellen Nachtzeiten müssen erneut vom Ofen gelesen werden, "
          "bevor sie geändert werden können.",
    "nl": "De huidige nachttijden moeten opnieuw van de kachel worden uitgelezen "
          "voordat ze kunnen worden gewijzigd.",
}


@pytest.fixture
async def night_runtime(hass, entry, stove):
    stove.data["night_begin_time"] = time(20)
    stove.data["night_end_time"] = time(6)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
    # Keep these focused H05 ordering tests independent of requested readback.
    # M02's real/deferred reads plus H05 are exercised in test_m02_refresh.py.
    # Test-driven regular reads still use the unmodified async_refresh.
    with patch.object(
        coordinator, "async_request_refresh",
        new_callable=AsyncMock,
    ):
        yield coordinator
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def entity(hass, side, entry_id=ENTRY_ID):
    key = "night_begin_time" if side == "begin" else "night_end_time"
    target = entity_id_for(hass, "time", key, entry_id)
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if target in platform.entities:
            return platform.entities[target]
    raise AssertionError(f"Time entity missing: {target}")


async def action(hass, side, *, direct=False, entry_id=ENTRY_ID):
    target = entity(hass, side, entry_id)
    value = time(21) if side == "begin" else time(7)
    if direct:
        await target.async_set_value(value)
    else:
        await hass.services.async_call("time", "set_value", {
            "entity_id": target.entity_id, "time": value.isoformat(),
        }, blocking=True)


def assert_commands(stove, pairs):
    assert stove.set_night_lowering_hours.await_args_list == [
        call(start=start, end=end) for start, end in pairs
    ]
    assert stove.set_night_lowering_hours.call_count == len(pairs)
    for method in COMMANDS:
        if method != "set_night_lowering_hours":
            getattr(stove, method).assert_not_called()


def assert_error(error, key):
    assert type(error) is HomeAssistantError
    assert error.translation_domain == DOMAIN
    assert error.translation_key == key
    assert error.translation_placeholders is None
    if key == "night_times_not_synchronized":
        assert str(error) == MESSAGES["en"].rstrip(".")


async def assert_blocked(hass, side="end", *, direct=False, entry_id=ENTRY_ID):
    with pytest.raises(HomeAssistantError) as raised:
        await action(hass, side, direct=direct, entry_id=entry_id)
    assert_error(raised.value, "night_times_not_synchronized")


async def make_uncertain(hass, stove):
    stove.set_night_lowering_hours.return_value = False
    with pytest.raises(HomeAssistantError) as raised:
        await action(hass, "begin")
    assert_error(raised.value, "command_not_confirmed")
    stove.set_night_lowering_hours.return_value = True


async def wait(event):
    async with asyncio.timeout(5):
        await event.wait()


async def finish(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def observe_waiter(monkeypatch, coordinator):
    """Observe a real lock acquisition without changing its scheduling/semantics."""
    lock = coordinator.night_times._lock
    acquire = lock.acquire
    waiting = asyncio.Event()

    async def observed_acquire():
        if lock.locked():
            waiting.set()
        return await acquire()

    monkeypatch.setattr(lock, "acquire", observed_acquire)
    return waiting


@pytest.mark.parametrize("first", ["begin", "end"])
@pytest.mark.parametrize("direct", [False, True], ids=["service", "entity"])
async def test_H05_night_edits_preserve_each_other(
    night_runtime, hass, stove, first, direct
):
    """Convert both former xfails; cover both paths before any coordinator poll."""
    second = "end" if first == "begin" else "begin"
    before = deepcopy(night_runtime.data)
    states = [hass.states.get(entity(hass, s).entity_id) for s in ("begin", "end")]
    with patch.object(night_runtime, "async_refresh") as refresh:
        await action(hass, first, direct=direct)
        await action(hass, second, direct=direct)
        refresh.assert_not_called()
    assert_commands(stove, [
        (time(21), time(6)) if first == "begin" else (time(20), time(7)),
        (time(21), time(7)),
    ])
    stove.get_data.assert_awaited_once_with()
    assert night_runtime.data == before
    assert states == [hass.states.get(entity(hass, s).entity_id)
                      for s in ("begin", "end")]


@pytest.mark.parametrize("first", ["begin", "end"])
@pytest.mark.parametrize("outcome", ["true", "false", "exception", "cancel"])
@pytest.mark.parametrize("direct_first", [False, True], ids=["services", "mixed"])
async def test_concurrent_commands(
    night_runtime, hass, stove, monkeypatch, first, outcome, direct_first
):
    """Barrier-controlled actual service calls share the same per-stove lock."""
    second = "end" if first == "begin" else "begin"
    entered, release = asyncio.Event(), asyncio.Event()
    waiting = observe_waiter(monkeypatch, night_runtime)
    failure = RuntimeError("original command failure")
    cancellations = []

    async def command(*, start, end):
        if stove.set_night_lowering_hours.await_count == 1:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError as error:
                cancellations.append(error.args)
                raise
            if outcome == "exception":
                raise failure
            return outcome == "true"
        return True

    stove.set_night_lowering_hours.side_effect = command
    tasks = []
    initial = ((time(21), time(6)) if first == "begin" else (time(20), time(7)))
    try:
        tasks.append(asyncio.create_task(action(hass, first, direct=direct_first)))
        await wait(entered)
        tasks.append(asyncio.create_task(action(hass, second)))
        await wait(waiting)
        assert_commands(stove, [initial])
        assert not tasks[1].done()
        if outcome == "cancel":
            tasks[0].cancel("command cancelled")
        else:
            release.set()
        if outcome == "true":
            await asyncio.gather(*tasks)
            assert_commands(stove, [initial, (time(21), time(7))])
        else:
            expected = {"false": HomeAssistantError, "exception": RuntimeError,
                        "cancel": asyncio.CancelledError}[outcome]
            with pytest.raises(expected) as raised:
                await tasks[0]
            if outcome == "false":
                assert_error(raised.value, "command_not_confirmed")
            elif outcome == "exception":
                assert raised.value is failure
            else:
                assert raised.value.args == ("command cancelled",)
                assert cancellations == [("command cancelled",)]
            with pytest.raises(HomeAssistantError) as blocked:
                await tasks[1]
            assert_error(blocked.value, "night_times_not_synchronized")
            assert_commands(stove, [initial])
        stove.get_data.assert_awaited_once_with()
        # A regular read makes the lock/state usable even after cancellation.
        await night_runtime.async_refresh()
        assert stove.get_data.await_count == 2
        await action(hass, second)
        final = ((time(20), time(7)) if second == "end" else (time(21), time(6)))
        assert_commands(stove, [initial] + (
            [(time(21), time(7))] if outcome == "true" else []
        ) + [final])
        assert stove.get_data.await_count == 2
    finally:
        release.set()
        await finish(tasks)


@pytest.mark.parametrize("direct", [False, True], ids=["service", "entity"])
async def test_cancel_before_action_starts(night_runtime, hass, stove, direct):
    task = asyncio.create_task(action(hass, "begin", direct=direct))
    task.cancel("before invocation")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_commands(stove, [])
    await action(hass, "end", direct=direct)
    assert_commands(stove, [(time(20), time(7))])
    stove.get_data.assert_awaited_once_with()


@pytest.mark.parametrize("first", ["begin", "end"])
async def test_cancel_lock_waiter_does_not_invalidate_pair(
    night_runtime, hass, stove, monkeypatch, first
):
    second = "end" if first == "begin" else "begin"
    entered, release = asyncio.Event(), asyncio.Event()
    waiting = observe_waiter(monkeypatch, night_runtime)

    async def command(*, start, end):
        entered.set()
        await release.wait()
        return True

    stove.set_night_lowering_hours.side_effect = command
    tasks = []
    try:
        tasks.append(asyncio.create_task(action(hass, first)))
        await wait(entered)
        tasks.append(asyncio.create_task(action(hass, second)))
        await wait(waiting)
        tasks[1].cancel("waiting for lock")
        with pytest.raises(asyncio.CancelledError):
            await tasks[1]
        release.set()
        await tasks[0]
        await action(hass, second)
        assert_commands(stove, [
            (time(21), time(6)) if first == "begin" else (time(20), time(7)),
            (time(21), time(7)),
        ])
        stove.get_data.assert_awaited_once_with()
    finally:
        release.set()
        await finish(tasks)


@pytest.mark.parametrize("direct", [False, True], ids=["service", "entity"])
async def test_cancel_waiter_preserves_already_confirmed_pair(
    night_runtime, hass, stove, monkeypatch, direct
):
    """Isolate pre-call cancellation without a later True masking uncertainty."""
    await action(hass, "begin")
    waiting = observe_waiter(monkeypatch, night_runtime)
    async with night_runtime.night_times._lock:
        task = asyncio.create_task(action(hass, "end", direct=direct))
        try:
            await wait(waiting)
            task.cancel("before stove call")
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await finish([task])
    assert_commands(stove, [(time(21), time(6))])
    await action(hass, "end")
    assert_commands(stove, [(time(21), time(6)), (time(21), time(7))])
    stove.get_data.assert_awaited_once_with()


@pytest.mark.parametrize("read_pair", [
    (time(21), time(6)), (time(19), time(5)), (time(20), time(6)),
], ids=["R1-confirmed-read", "R6-different-read", "R7-equal-no-listener"])
async def test_regular_read_replaces_confirmed_pair(
    night_runtime, hass, stove, read_pair
):
    await action(hass, "begin")
    previous = night_runtime.data
    listener = Mock()
    unsubscribe = night_runtime.async_add_listener(listener)
    stove.data["night_begin_time"], stove.data["night_end_time"] = read_pair
    try:
        await night_runtime.async_refresh()
        assert night_runtime.last_update_success
        assert night_runtime.always_update is False
        assert night_runtime.data is not previous
        if read_pair == (time(20), time(6)):
            assert night_runtime.data == previous
            listener.assert_not_called()
        else:
            listener.assert_called_once_with()
        await action(hass, "end")
        assert_commands(stove, [(time(21), time(6)), (read_pair[0], time(7))])
        assert stove.get_data.await_count == 2
    finally:
        unsubscribe()


async def test_R3_R4_R7_equal_read_resyncs_unknown_without_listener(
    night_runtime, hass, stove
):
    await make_uncertain(hass, stove)
    previous = night_runtime.data
    started, release = asyncio.Event(), asyncio.Event()
    listener = Mock()
    unsubscribe = night_runtime.async_add_listener(listener)

    async def read():
        snapshot = deepcopy(stove.data)
        started.set()
        await release.wait()
        return snapshot

    stove.get_data.side_effect = read
    task = asyncio.create_task(night_runtime.async_refresh())
    try:
        await wait(started)
        await assert_blocked(hass)
        assert_commands(stove, [(time(21), time(6))])
        release.set()
        await task
        assert night_runtime.last_update_success
        assert night_runtime.data == previous
        assert night_runtime.data is not previous
        listener.assert_not_called()
        await action(hass, "end")
        assert_commands(stove, [(time(21), time(6)), (time(20), time(7))])
        assert stove.get_data.await_count == 2
    finally:
        release.set()
        await finish([task])
        unsubscribe()


@pytest.mark.parametrize("confirmed", [False, True], ids=["R2-false", "R5-true"])
async def test_read_started_before_command_cannot_resync(
    night_runtime, hass, stove, confirmed
):
    started, release = asyncio.Event(), asyncio.Event()
    previous = night_runtime.data

    async def delayed_read():
        snapshot = deepcopy(stove.data)
        started.set()
        await release.wait()
        return snapshot

    async def applied_command(*, start, end):
        # Explicit simulated execution, including a possible unconfirmed one.
        stove.data["night_begin_time"], stove.data["night_end_time"] = start, end
        return confirmed

    stove.get_data.side_effect = delayed_read
    stove.set_night_lowering_hours.side_effect = applied_command
    task = asyncio.create_task(night_runtime.async_refresh())
    try:
        await wait(started)
        if confirmed:
            await action(hass, "begin")
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await action(hass, "begin")
            assert_error(raised.value, "command_not_confirmed")
        release.set()
        await task
        assert night_runtime.last_update_success
        assert night_runtime.data == previous
        assert night_runtime.data is not previous
        assert stove.data["night_begin_time"] == time(21)
        if confirmed:
            await action(hass, "end")
            assert_commands(stove, [(time(21), time(6)), (time(21), time(7))])
        else:
            await assert_blocked(hass)
            assert_commands(stove, [(time(21), time(6))])
        assert stove.get_data.await_count == 2
    finally:
        release.set()
        await finish([task])


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("read_finishes_first", [False, True])
async def test_read_started_during_command_cannot_resync(
    night_runtime, hass, stove, confirmed, read_finishes_first
):
    entered, release = asyncio.Event(), asyncio.Event()
    read_started, release_read = asyncio.Event(), asyncio.Event()

    async def command(*, start, end):
        entered.set()
        await release.wait()
        return confirmed

    async def read():
        snapshot = deepcopy(stove.data)
        read_started.set()
        await release_read.wait()
        return snapshot

    stove.set_night_lowering_hours.side_effect = command
    stove.get_data.side_effect = read
    tasks = []
    try:
        tasks.append(asyncio.create_task(action(hass, "begin")))
        await wait(entered)
        tasks.append(asyncio.create_task(night_runtime.async_refresh()))
        # Polling must start despite the night-time command holding its own lock.
        await wait(read_started)
        if read_finishes_first:
            release_read.set()
            await tasks[1]
        release.set()
        if confirmed:
            await tasks[0]
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await tasks[0]
            assert_error(raised.value, "command_not_confirmed")
        release_read.set()
        await tasks[1]
        if confirmed:
            await action(hass, "end")
            assert_commands(stove, [(time(21), time(6)), (time(21), time(7))])
        else:
            await assert_blocked(hass)
            assert_commands(stove, [(time(21), time(6))])
        assert stove.get_data.await_count == 2
    finally:
        release.set()
        release_read.set()
        await finish(tasks)


@pytest.mark.parametrize("confirmed", [False, True])
async def test_read_between_commands_cannot_overwrite_later_result(
    night_runtime, hass, stove, confirmed
):
    """A read after command N still becomes obsolete when N+1 starts."""
    await action(hass, "begin")
    started, release = asyncio.Event(), asyncio.Event()

    async def read():
        snapshot = deepcopy(stove.data)
        started.set()
        await release.wait()
        return snapshot

    stove.get_data.side_effect = read
    task = asyncio.create_task(night_runtime.async_refresh())
    try:
        await wait(started)
        stove.set_night_lowering_hours.return_value = confirmed
        if confirmed:
            await action(hass, "end")
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await action(hass, "end")
            assert_error(raised.value, "command_not_confirmed")
        release.set()
        await task
        assert night_runtime.last_update_success
        stove.set_night_lowering_hours.return_value = True
        if confirmed:
            await action(hass, "begin")
        else:
            await assert_blocked(hass, "begin")
        assert_commands(stove, [(time(21), time(6)), (time(21), time(7))]
                        + ([(time(21), time(7))] if confirmed else []))
        assert stove.get_data.await_count == 2
    finally:
        release.set()
        await finish([task])


@pytest.mark.parametrize("failure", [
    None, UpdateFailed("read rejected"), TimeoutError("read timeout"),
    ClientConnectionError("read disconnected"),
], ids=["R8-none", "R8-update-failed", "R9-timeout", "R9-connection"])
async def test_failed_read_does_not_resync(night_runtime, hass, stove, failure):
    await make_uncertain(hass, stove)
    previous = night_runtime.data
    stove.get_data.side_effect = failure
    stove.get_data.return_value = None
    with patch.object(night_runtime.night_times, "read_finished") as completed:
        await night_runtime.async_refresh()
        completed.assert_not_called()
    assert not night_runtime.last_update_success
    assert night_runtime.data is previous
    # Direct method reaches the H05 boundary even while HA marks it unavailable.
    await assert_blocked(hass, direct=True)
    assert_commands(stove, [(time(21), time(6))])
    assert stove.get_data.await_count == 2


async def test_R10_cancelled_read_does_not_resync(night_runtime, hass, stove):
    await make_uncertain(hass, stove)
    started = asyncio.Event()

    async def read():
        started.set()
        await asyncio.Event().wait()

    stove.get_data.side_effect = read
    with patch.object(night_runtime.night_times, "read_finished") as completed:
        task = asyncio.create_task(night_runtime.async_refresh())
        try:
            await wait(started)
            task.cancel("read cancelled")
            with pytest.raises(asyncio.CancelledError) as raised:
                await task
            assert raised.value.args == ("read cancelled",)
            completed.assert_not_called()
        finally:
            await finish([task])
    await assert_blocked(hass, direct=True)
    assert_commands(stove, [(time(21), time(6))])
    assert stove.get_data.await_count == 2


async def test_two_entries_are_independent(
    night_runtime, hass, stove, stove_factory
):
    other_stove = SimulatedStove()
    other_stove.data["night_begin_time"] = time(23)
    other_stove.data["night_end_time"] = time(5)
    other = MockConfigEntry(domain=DOMAIN, title="Other stove", version=2,
                            entry_id="01K00000000000000000000001",
                            data={CONF_HOST: "other.invalid", CONF_NAME: "Other stove"})
    other.add_to_hass(hass)
    stove_factory.return_value = other_stove
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    other_coordinator = hass.data[DOMAIN]["stoves"][other.entry_id]
    assert night_runtime.night_times is not other_coordinator.night_times
    assert night_runtime.night_times._lock is not other_coordinator.night_times._lock
    entered, release = asyncio.Event(), asyncio.Event()

    async def command(*, start, end):
        entered.set()
        await release.wait()
        return False

    stove.set_night_lowering_hours.side_effect = command
    task = asyncio.create_task(action(hass, "begin"))
    try:
        await wait(entered)
        async with asyncio.timeout(5):
            await action(hass, "end", entry_id=other.entry_id)
        assert not task.done()
        assert_commands(other_stove, [(time(23), time(7))])
        release.set()
        with pytest.raises(HomeAssistantError) as raised:
            await task
        assert_error(raised.value, "command_not_confirmed")
        await assert_blocked(hass)
        await action(hass, "begin", entry_id=other.entry_id)
        assert_commands(other_stove, [(time(23), time(7)), (time(21), time(7))])
        await other_coordinator.async_refresh()
        await assert_blocked(hass)
        assert_commands(stove, [(time(21), time(6))])
        stove.get_data.assert_awaited_once_with()
        assert other_stove.get_data.await_count == 3  # setup, M02, regular recovery
    finally:
        release.set()
        await finish([task])
        assert await hass.config_entries.async_unload(other.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("language", ["en", "de", "nl"])
async def test_unsynchronized_translations(night_runtime, hass, language):
    strings = await translation.async_get_translations(
        hass, language, "exceptions", {DOMAIN}
    )
    key = f"component.{DOMAIN}.exceptions.night_times_not_synchronized.message"
    message = strings[key]
    assert message == MESSAGES[language]
    assert {field for _, field, _, _ in Formatter().parse(message)
            if field is not None} == set()
