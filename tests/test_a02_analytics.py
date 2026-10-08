"""Real HA setup/storage/sensor boundaries with only simulated observations."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.helpers.storage import Store
import pytest

from custom_components.hwam_stove._analytics import Analytics
from custom_components.hwam_stove._analytics_model import dump
from custom_components.hwam_stove._analytics_sensor import DESCRIPTIONS

from .helpers import COMMANDS, entity_id_for, registry_entries
from .test_a02_model import START, run
from .test_m06_reconfigure import identities
from .test_o02_statistics import live_sensor

KEYS = [description.key for description in DESCRIPTIONS]


@pytest.fixture
def clock(monkeypatch):
    """Inject observation clocks only: never sleep or disturb HA timers."""
    value = [0.0]
    original = Analytics.__init__

    def initialize(self, hass, entry_id):
        original(self, hass, entry_id, clock=lambda: value[0],
                 utcnow=lambda: START + timedelta(seconds=value[0]))

    monkeypatch.setattr(Analytics, "__init__", initialize)
    return value


async def observe(analytics, clock, phase, *, alarm=False, temp=100, advance=10):
    clock[0] += advance
    await analytics.async_observe(analytics.read_started(), {
        "phase": phase, "refill_alarm": alarm, "stove_temperature": temp,
    })


async def test_checkpoint_reload_partial_and_no_counter_reset(hass, clock):
    analytics = Analytics(hass, "synthetic")
    await analytics.async_load()
    for phase in ("Ignition", "Burn", "Glow", "Standby"):
        await observe(analytics, clock, phase)
    assert analytics.healthy
    assert analytics.state.completed == 1
    # Ordinary equal-phase elapsed/peak checkpoints may lag by at most 60s
    # of active observations. Boundaries and request edges are immediate.
    await observe(analytics, clock, "Ignition")
    await observe(analytics, clock, "Burn")
    await observe(analytics, clock, "Burn", temp=500)
    saved = await Store(hass, 1, analytics.key).async_load()
    assert saved["current"]["peak"] == 100
    await analytics.async_stop()
    saved = await Store(hass, 1, analytics.key).async_load()
    assert saved["current"]["peak"] == 500
    await analytics.async_stop()  # Idempotent.
    restored = Analytics(hass, "synthetic")
    await restored.async_load()
    assert restored.state.completed == 1
    assert restored.state.current.coverage_uncertain
    await observe(restored, clock, "Standby")
    assert restored.state.completed == 1
    assert restored.state.partial_completed == 1


async def test_checkpoint_cadence_and_readback_verification(hass, clock):
    a = Analytics(hass, "synthetic")
    await a.async_load()
    with patch.object(a.store, "async_save", wraps=a.store.async_save) as save:
        await observe(a, clock, "Ignition")
        await observe(a, clock, "Burn")
        assert save.await_count == 2
        for _ in range(5):
            await observe(a, clock, "Burn")
        assert save.await_count == 2
        await observe(a, clock, "Burn")
        assert save.await_count == 3
        saved = await Store(hass, 1, a.key).async_load()
        assert saved == dump(a.state)


@pytest.mark.parametrize("kind", ["read", "write", "silent_write", "schema"])
async def test_storage_failures_disable_analytics_preserve_stored_totals(
    hass, clock, kind,
):
    a = Analytics(hass, "synthetic")
    payload = dump(run(["Ignition", "Burn", "Standby"]))
    await a.store.async_save(payload)
    if kind == "schema":
        invalid = {**payload, "schema": 999}
        await a.store.async_save(invalid)
        payload = invalid
        await a.async_load()
    elif kind == "read":
        with patch.object(a.store, "async_load", side_effect=OSError("injected")):
            await a.async_load()
    else:
        await a.async_load()
        failure = (AsyncMock(side_effect=OSError("injected"))
                   if kind == "write" else AsyncMock())
        with patch.object(a.store, "async_save", failure):
            await observe(a, clock, "Ignition")
    assert not a.healthy
    assert await Store(hass, 1, a.key).async_load() == payload
    await observe(a, clock, "Standby")
    await a.async_stop()
    assert await Store(hass, 1, a.key).async_load() == payload


@pytest.mark.parametrize("kind", ["existing_empty", "quarantined"])
async def test_none_load_is_not_fresh_if_storage_existed(hass, clock, tmp_path, kind):
    a = Analytics(hass, "synthetic")
    path = tmp_path / "analytics"
    target = path if kind == "existing_empty" else tmp_path / "analytics.corrupt.test"
    target.write_text("{}")
    # Public path and load API simulate HA's corruption quarantine behavior.
    fake = Mock(path=str(path), async_load=AsyncMock(return_value=None),
                async_save=AsyncMock())
    a.store = fake
    await a.async_load()
    assert not a.healthy
    fake.async_save.assert_not_called()


@pytest.mark.parametrize("phase", ["Ignition", "Burn", "Glow", "Standby"])
async def test_public_entry_reload_preserves_analytics_and_registry(
    hass, entry, stove, clock, phase,
):
    stove.data.update(phase="Ignition", refill_alarm=0)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    from .helpers import DOMAIN

    hub = hass.data[DOMAIN]["stoves"][entry.entry_id]
    for p in ("Burn", phase):
        clock[0] += 10
        stove.data["phase"] = p
        await hub.async_refresh()
    before = identities(hass, entry)
    count = hub.analytics.state.completed
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    replacement = hass.data[DOMAIN]["stoves"][entry.entry_id]
    assert identities(hass, entry) == before
    assert replacement.analytics.state.completed == count
    if phase != "Standby":
        assert replacement.analytics.state.current.coverage_uncertain
    assert hub.analytics.closed
    assert not hub.analytics._listeners
    assert stove.destroy.await_count == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert stove.destroy.await_count == 2


async def test_identical_reads_only_notify_new_sensors_no_extra_requests(
    hass, clock, loaded, stove, entities,
):
    old = live_sensor(hass, "stove_temperature")
    before = stove.get_data.await_count
    previous = deepcopy(loaded.data)
    with patch.object(old, "async_write_ha_state") as old_write:
        # The listener captured the original method at registration; spy using
        # an additional analytics listener to count actual model notifications.
        listener = Mock()
        remove = loaded.analytics.async_add_listener(listener)
        for _ in range(3):
            clock[0] += 10
            await loaded.async_refresh()
        old_write.assert_not_called()
        assert listener.call_count == 3
        remove()
    assert loaded.data == previous
    assert stove.get_data.await_count == before + 3
    assert loaded.analytics.state.current.elapsed == 30
    for name in COMMANDS:
        getattr(stove, name).assert_not_called()
    # Optimistic entity/listener writes are never observations.
    sequence = loaded.analytics.state.sequence
    loaded.async_update_listeners()
    assert loaded.analytics.state.sequence == sequence


async def test_m02_readback_and_overlapping_precommand_read_excluded(
    hass, clock, loaded, stove,
):
    before = dump(loaded.analytics.state)
    stove.data.update(phase="Standby", refill_alarm=0)
    await loaded.async_refresh_after_command()
    assert dump(loaded.analytics.state) == before
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        started.set()
        await release.wait()
        return deepcopy(stove.data)

    stove.get_data.side_effect = delayed
    task = asyncio.create_task(loaded.async_refresh())
    await started.wait()
    loaded.analytics.command_boundary()
    release.set()
    await task
    assert loaded.analytics.state.current is not None
    assert loaded.analytics.state.current.coverage_uncertain
    assert not loaded.analytics.online


async def test_out_of_order_and_duplicate_callbacks_do_not_finalize(hass, clock):
    a = Analytics(hass, "synthetic")
    await a.async_load()
    await observe(a, clock, "Ignition")
    earlier, later = a.read_started(), a.read_started()
    await a.async_observe(later, {"phase": "Burn", "refill_alarm": 0})
    before = dump(a.state)
    await a.async_observe(earlier, {"phase": "Standby", "refill_alarm": 1})
    await a.async_gap(earlier)
    await a.async_observe(later, {"phase": "Standby", "refill_alarm": 1})
    assert dump(a.state) == before


@pytest.mark.parametrize("error", [None, OSError("read"), RuntimeError("unexpected")])
async def test_regular_read_error_and_recovery_do_not_replay_request(
    hass, clock, loaded, stove, error,
):
    a = loaded.analytics
    stove.get_data.side_effect = error
    stove.get_data.return_value = None
    await loaded.async_refresh()
    assert not a.online and not a.state.request_edge_known
    assert a.state.current.coverage_uncertain
    stove.get_data.side_effect = stove._read
    stove.data["refill_alarm"] = 1
    clock[0] += 10
    await loaded.async_refresh()
    assert a.online and a.state.requests == 0


async def test_missed_poll_and_controller_clock_have_separate_effects(
    hass, clock, loaded, stove,
):
    a = loaded.analytics
    stove.data["date_time"] += timedelta(days=1000)
    clock[0] += 10
    await loaded.async_refresh()
    assert not a.state.current.coverage_uncertain
    clock[0] += 30  # A scheduled regular observation is missing, no fabricated timer.
    await loaded.async_refresh()
    assert a.state.current.coverage_uncertain
    assert a.state.current.elapsed == 10


async def test_repeated_cancellation_settles_owned_checkpoint(hass, clock):
    a = Analytics(hass, "synthetic")
    await a.async_load()
    saving, release = asyncio.Event(), asyncio.Event()
    original = a.store.async_save

    async def delayed(payload):
        saving.set()
        await release.wait()
        await original(payload)

    with patch.object(a.store, "async_save", side_effect=delayed):
        task = asyncio.create_task(observe(a, clock, "Ignition"))
        await saving.wait()
        task.cancel("first")
        # Event-loop barriers, not sleeps or elapsed-time assertions.
        for _ in range(2):
            barrier = asyncio.Event()
            hass.loop.call_soon(barrier.set)
            await barrier.wait()
            task.cancel("again")
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not a.healthy
    assert (await Store(hass, 1, a.key).async_load())["phase"] == "Ignition"
    assert not [t for t in asyncio.all_tasks()
                if "write_and_verify" in t.get_coro().__qualname__]


async def test_four_entities_metadata_values_and_original_40_preserved(
    hass, clock, loaded, stove,
):
    assert len(registry_entries(hass)) == 44
    rows = json.loads((Path(__file__).parent / "fixtures/entities.json").read_text())
    assert len(rows) == 40
    for row in rows:
        entity_id_for(hass, row["platform"], row["key"])
    for key in KEYS:
        sensor = live_sensor(hass, key)
        assert sensor.state_class is None
        assert sensor.last_reset is None
        assert "ledger" not in sensor.extra_state_attributes
    # The startup Burn observation is partial; end it before a qualified session.
    for phase, temperature in (
        ("Standby", 999), ("Ignition", 0), ("Burn", 400),
        ("Glow", 120), ("Standby", 500),
    ):
        clock[0] += 10
        stove.data.update(phase=phase, stove_temperature=temperature, refill_alarm=0)
        await loaded.async_refresh()
    assert live_sensor(hass, KEYS[1]).native_value == 30
    assert live_sensor(hass, KEYS[2]).native_value == 1
    assert live_sensor(hass, KEYS[3]).native_value == 400
    assert live_sensor(hass, KEYS[2]).extra_state_attributes["today"] == 1
    assert loaded.analytics.state.partial_completed == 1
    assert json.dumps({key: dict(live_sensor(hass, key).extra_state_attributes)
                       for key in KEYS}, allow_nan=False)


async def test_storage_failure_never_breaks_existing_stove_control(
    hass, clock, loaded, stove,
):
    with patch.object(loaded.analytics.store, "async_save", side_effect=OSError):
        stove.data["phase"] = "Standby"
        await loaded.async_refresh()
    assert loaded.last_update_success
    assert not loaded.analytics.healthy
    assert not live_sensor(hass, KEYS[2]).available
    assert live_sensor(hass, "stove_temperature").available
    assert await stove.set_burn_level(3) is True  # Simulator only.
    await loaded.async_refresh_after_command()
    assert loaded.last_update_success


async def test_read_cancellation_preserves_exception_and_client_ownership(
    hass, clock, loaded, stove,
):
    error = asyncio.CancelledError("synthetic cancelled read")
    stove.get_data.side_effect = error
    with pytest.raises(asyncio.CancelledError) as caught:
        await loaded._async_update_data()
    assert caught.value is error
    assert loaded.analytics.state.current.coverage_uncertain
    assert not loaded.analytics.online
    stove.destroy.assert_not_called()


@pytest.mark.parametrize("lang", ["de", "en", "nl"])
def test_all_four_translations_and_no_measurement_history_semantics(lang):
    from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS

    path = Path(__file__).parents[1] / "custom_components/hwam_stove/translations"
    translated = json.loads((path / f"{lang}.json").read_text())["entity"]["sensor"]
    for description in DESCRIPTIONS:
        assert translated[description.translation_key]["name"]
        assert description.state_class is None
        if description.device_class:
            assert description.native_unit_of_measurement in DEVICE_CLASS_UNITS[
                description.device_class
            ]


async def test_unavailable_partial_and_recovery_entity_state(
    hass, clock, loaded, stove,
):
    current = live_sensor(hass, KEYS[0])
    assert not current.available  # Startup already Burn.
    assert not current.extra_state_attributes["start_boundary_known"]
    for phase in ("Standby", "Ignition", "Burn"):
        stove.data["phase"] = phase
        clock[0] += 10
        await loaded.async_refresh()
    assert current.available and current.native_value == 10
    saved = deepcopy(stove.data)
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    await loaded.async_refresh()
    assert not current.available
    stove.get_data.return_value = saved
    clock[0] += 10
    await loaded.async_refresh()
    assert not current.available  # Recovery cannot fill the missing interval.
    assert current.extra_state_attributes["coverage_uncertain"]
    assert live_sensor(hass, KEYS[2]).available  # Durable lifetime total is known.


async def test_alarms_count_requests_only_and_stay_deduplicated_after_reload(
    hass, clock,
):
    a = Analytics(hass, "requests")
    await a.async_load()
    for value in (False, True, True, False):
        await observe(a, clock, "Burn", alarm=value)
    assert a.state.requests == 1
    await observe(a, clock, "Burn", alarm=True)
    assert a.state.requests == 2
    await a.async_stop()
    a = Analytics(hass, "requests")
    await a.async_load()
    await observe(a, clock, "Burn", alarm=True)
    assert a.state.requests == 2
    await observe(a, clock, "Burn", alarm=False)
    await observe(a, clock, "Burn", alarm=True)
    assert a.state.requests == 3
