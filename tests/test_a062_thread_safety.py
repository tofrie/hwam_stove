"""Exercise real HA timer dispatch, not direct calls to Freshness._expire."""

from copy import deepcopy
from datetime import timedelta
import threading

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
import pytest

from custom_components.hwam_stove._freshness import Freshness

from .helpers import COMMANDS, entity_id_for
from .test_a04_shadow import START


@pytest.fixture
def timers(hass, monkeypatch):
    """Retain actual asyncio handles; dispatch due callbacks without wall sleeps."""
    handles = []
    original = hass.loop.call_at

    def record(when, action, *args, **kwargs):
        handle = original(when, action, *args, **kwargs)
        if (getattr(action, "__name__", None) == "_run_async_call_action"
                and isinstance(getattr(args[-1].target, "__self__", None), Freshness)):
            handles.append(handle)
        return handle

    monkeypatch.setattr(hass.loop, "call_at", record)
    return handles


async def fire(hass, handle):
    # Run the real HA dispatcher stored by async_call_later. It decides whether
    # the target is an event-loop callback or an executor job (the rc9 defect).
    assert not handle.cancelled()
    handle._run()
    handle.cancel()
    await hass.async_block_till_done()


@pytest.mark.parametrize("interval", [10, 60])
async def test_scheduler_dispatches_expiry_on_loop(hass, timers, interval):
    thread = threading.get_ident()
    seen = []
    now = [0.0]
    f = Freshness(hass, lambda: seen.append(threading.get_ident()),
                  clock=lambda: now[0])
    f.accept(START, 0, interval)
    assert len(timers) == 2
    assert timers[1].when() - timers[0].when() == pytest.approx(
        120 - interval - 1.001, abs=0.01,
    )
    for age, handle in zip((interval + 1.001, 120), timers, strict=True):
        now[0] = age
        await fire(hass, handle)
    f.close()
    assert seen == [thread, thread]


async def test_expiry_listener_and_real_entity_writes_on_loop(
    hass, loaded, stove, timers, monkeypatch,
):
    loop_thread = threading.get_ident()
    seen, listeners = [], []
    original = Entity.async_write_ha_state

    def checked_write(entity):
        if entity.platform.platform_name == "hwam_stove":
            seen.append((entity.entity_id.split('.')[0], threading.get_ident()))
            assert threading.get_ident() == loop_thread
        return original(entity)

    @callback
    def listener():
        listeners.append(threading.get_ident())

    monkeypatch.setattr(Entity, "async_write_ha_state", checked_write)
    remove = loaded.async_add_listener(listener)
    now = [0.0]
    f = loaded.freshness
    f.clock = lambda: now[0]
    f.accept(START, 0, 10)
    active = timers[-2:]
    analytics = deepcopy(loaded.analytics.state)
    shadow = deepcopy(loaded.shadow.model.dump())
    requests = stove.get_data.await_count
    temperature = entity_id_for(hass, "sensor", "stove_temperature")
    for age, handle in zip((11.001, 120), active, strict=True):
        now[0] = age
        await fire(hass, handle)
        assert hass.states.get(temperature).state == (
            "unavailable" if age == 120 else str(stove.data['stove_temperature'])
        )
    assert listeners == [loop_thread, loop_thread]
    assert {domain for domain, _ in seen} >= {
        "sensor", "binary_sensor", "number", "switch", "time",
    }
    assert all(thread == loop_thread for _, thread in seen)
    assert loaded.analytics.state == analytics
    assert loaded.shadow.model.dump() == shadow
    assert stove.get_data.await_count == requests
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()
    remove()


async def test_awaited_worker_result_is_processed_on_loop(
    hass, loaded, stove, monkeypatch,
):
    """Simulate an executor result, never execute controller I/O in this test."""
    loop_thread = threading.get_ident()
    worker_threads, notified = [], []
    original = loaded.freshness.accept

    def accept(*args):
        assert threading.get_ident() == loop_thread
        return original(*args)

    def worker():
        worker_threads.append(threading.get_ident())
        return deepcopy(stove.data) | {"stove_temperature": 260}

    async def read():
        return await hass.async_add_executor_job(worker)

    @callback
    def listener():
        notified.append(threading.get_ident())

    monkeypatch.setattr(loaded.freshness, "accept", accept)
    stove.get_data.side_effect = read
    remove = loaded.async_add_listener(listener)
    await loaded.async_refresh()
    assert worker_threads and all(t != loop_thread for t in worker_threads)
    assert notified == [loop_thread]
    remove()


async def test_reloads_cancel_all_deadlines_without_listener_accumulation(
    hass, entry, stove, timers, monkeypatch,
):
    seen = []
    original = Entity.async_write_ha_state

    def write(entity):
        if entity.platform.platform_name == "hwam_stove":
            seen.append(entity.entity_id)
        return original(entity)

    monkeypatch.setattr(Entity, "async_write_ha_state", write)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    listener_counts = []
    for _ in range(3):
        old = hass.data['hwam_stove']['stoves'][entry.entry_id]
        listener_counts.append(len(old._listeners))
        for _ in range(3):
            await old.async_refresh()
            assert sum(not h.cancelled() for h in timers) == 2
        old_handles = [h for h in timers if not h.cancelled()]
        # Retain scheduled actions to also exercise the closed-guard fallback.
        actions = [(h._callback, h._args) for h in old_handles]
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert old.freshness.closed and not old.freshness._cancel
        assert not old._listeners
        assert all(h.cancelled() for h in timers)
        before = len(seen)
        reads = stove.get_data.await_count
        for action, args in actions:
            action(*args)
        await hass.async_block_till_done()
        assert len(seen) == before and stove.get_data.await_count == reads
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert len(set(listener_counts)) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(h.cancelled() for h in timers)


async def test_ha_stop_cancels_freshness(hass, loaded, timers):
    loaded.freshness.accept(START, loaded.freshness.clock(), 10)
    assert sum(not h.cancelled() for h in timers) == 2
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert loaded.freshness.closed and not loaded.freshness._cancel
    assert all(h.cancelled() for h in timers)


async def test_repeated_failures_reconnection_and_new_deadlines(
    hass, loaded, stove, timers,
):
    now = [0.0]
    f = loaded.freshness
    f.clock = lambda: now[0]
    f.utcnow = lambda: START + timedelta(seconds=now[0])
    await loaded.async_refresh()
    first = timers[-2:]
    stove.get_data.side_effect = TimeoutError()
    for age in (10, 20, 30):
        now[0] = age
        await loaded.async_refresh()
        assert timers[-2:] == first
        assert f.state() == "stale"
    now[0] = 120
    await fire(hass, first[-1])
    assert f.state() == "unavailable"
    now[0] = 121
    stove.get_data.side_effect = stove._read
    await loaded.async_refresh()
    assert f.state() == "fresh"
    assert f.observed_at == START + timedelta(seconds=121)
    assert all(h.cancelled() for h in first)
    assert sum(not h.cancelled() for h in timers) == 2


async def test_late_read_completion_after_unload_cannot_rearm_timers(
    hass, entry, stove, timers,
):
    from asyncio import Event, create_task

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data['hwam_stove']['stoves'][entry.entry_id]
    started, finish = Event(), Event()

    async def late_read():
        started.set()
        await finish.wait()
        return deepcopy(stove.data)

    stove.get_data.side_effect = late_read
    task = create_task(coordinator.async_refresh())
    await started.wait()
    try:
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert coordinator.freshness.closed
    finally:
        finish.set()
        await task
    await hass.async_block_till_done()
    assert not coordinator._listeners
    assert not coordinator.freshness._cancel
    assert all(h.cancelled() for h in timers)
