"""Causal display and separate persistence. No hardware, polling or scoring."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.hwam_stove._live_forecast import HWAM, LiveForecast, LiveState
from custom_components.hwam_stove._shadow_model import MODEL, timestamp

from .helpers import COMMANDS, entity_id_for, registry_entries

START = datetime(2026, 11, 4, 12, tzinfo=UTC)


def observe(
    state,
    seconds,
    minutes=45,
    *,
    phase="Burn",
    alarm=False,
    door=False,
    issuance=None,
    clock=None,
):
    at = START + timedelta(seconds=seconds)
    state.step(
        at, seconds if clock is None else clock, phase, door, alarm, minutes, issuance
    )
    return state.display(at, "fresh")


def test_absolute_utc_revisions_and_unchanged_duration():
    state = LiveState()
    assert observe(state, 0)[0] is None
    first = observe(state, 10)[0]
    assert first == START + timedelta(minutes=45)
    assert observe(state, 20)[0] == first
    earlier = observe(state, 30, 43)[0]
    later = observe(state, 40, 46)[0]
    assert earlier < first < later
    assert state.last["source"] == "hwam" and state.last["model"] == HWAM


@pytest.mark.parametrize(
    "raw", [None, True, -1, 0, 1.25, 181, 14276, 120, float("nan")]
)
def test_invalid_or_ambiguous_burn_countdown(raw):
    state = LiveState()
    observe(state, 0, raw)
    assert observe(state, 10, raw)[0] is None


def test_new_regime_needs_corroboration_before_large_jump():
    state = LiveState()
    observe(state, 0)
    first = observe(state, 10)[0]
    eta, status, reason = observe(state, 20, 150)
    assert eta is None and status == "uncertain" and "jump" in reason
    assert timestamp(state.last["eta"]) == first
    assert observe(state, 30, 150)[0] > first
    assert observe(state, 40, 14276)[0] is None


def test_saynwerk_priority_source_switch_and_no_issuance_edit():
    state = LiveState()
    observe(state, 0)
    original = (START, START + timedelta(minutes=1))
    eta = observe(state, 10, issuance=original)[0]
    assert eta == original[1] and state.last["model"] == MODEL
    assert observe(state, 20, 44)[0] != eta
    assert state.last["source"] == "hwam"
    assert observe(state, 30, issuance=original)[0] == original[1]
    state.boundary("failed_read")
    assert observe(state, 40, issuance=original)[0] is None
    assert observe(state, 50, 44, issuance=original)[0] != original[1]


def test_expiry_and_recovery_without_spent_latch():
    state = LiveState()
    for sec in range(0, 80, 10):
        value = observe(state, sec, 1)
    assert value[:2] == (None, "expired")
    assert state.last["eta"] == (START + timedelta(minutes=1)).isoformat()
    assert observe(state, 80, 2)[0] is not None
    assert observe(state, 90, 2, alarm=True)[:2] == (None, "request_active")
    assert observe(state, 100, 42)[0] is None
    assert observe(state, 110, 41)[0] is not None


@pytest.mark.parametrize("delay", [12, 30, 120, 86400])
def test_gaps_invalidate_and_build_new_window(delay):
    state = LiveState()
    observe(state, 0)
    first = observe(state, 10)[0]
    assert state.display(START + timedelta(seconds=22), "stale")[0] is None
    assert observe(state, 10 + delay, 45) == (None, "uncertain", "sampling_gap")
    assert timestamp(state.last["eta"]) == first
    observe(state, 20 + delay, 41)
    assert observe(state, 30 + delay, 40)[0] is not None


@pytest.mark.parametrize("phases", [("Glow", "Burn"), ("Burn", "Burn")])
def test_door_only_or_possible_intervention_starts_fresh(phases):
    state = LiveState()
    observe(state, 0, 40, phase=phases[0])
    observe(state, 10, 41, phase=phases[0])
    assert observe(state, 20, 41, phase=phases[0], door=True)[2] == "door_open"
    assert observe(state, 30, 46, phase=phases[1])[0] is None
    observe(state, 40, 46, phase=phases[1])
    assert observe(state, 50, 45, phase=phases[1])[0] is not None
    assert set(state.last) == {
        "eta",
        "model",
        "source",
        "minutes",
        "seen",
        "calculated",
    }


def test_request_first_seen_after_outage_and_missing_inputs():
    state = LiveState()
    observe(state, 0)
    assert observe(state, 180, door=True, alarm=True)[1] == "request_active"
    assert observe(state, 190, door=None)[2] == "missing_edge_input"
    assert observe(state, 200, alarm=None)[2] == "missing_edge_input"
    assert observe(state, 210, clock=7)[2] == "clock_discontinuity"


async def test_checkpoint_roundtrip_reload_and_corruption(hass):
    a = LiveForecast(hass, "synthetic")
    await a.load()
    observe(a.state, 0)
    observe(a.state, 10)
    await a.checkpoint(10)
    first = await a.store.async_load()
    observe(a.state, 20, 43)
    await a.checkpoint(20)
    assert await a.store.async_load() == first
    await a.stop()
    b = LiveForecast(hass, "synthetic")
    await b.load()
    assert b.state.last == a.state.last != first
    assert b.state.display(START + timedelta(minutes=5), "fresh")[1:] == (
        "stale",
        "restored_checkpoint",
    )
    assert b.state.display(START + timedelta(hours=1), "fresh")[1] == "expired"
    observe(b.state, 3600, 36)
    assert observe(b.state, 3610, 35)[0] is not None
    await b.store.async_save({"unknown": 7})
    c = LiveForecast(hass, "synthetic")
    await c.load()
    assert c.storage_failed
    observe(c.state, 0)
    observe(c.state, 10)
    await c.stop()
    assert await c.store.async_load() == {"unknown": 7}


async def test_failed_checkpoint_fails_only_optional_persistence(hass, monkeypatch):
    live = LiveForecast(hass, "failure")
    await live.load()
    observe(live.state, 0)
    observe(live.state, 10)
    monkeypatch.setattr(live.store, "async_load", AsyncMock(return_value=None))
    await live.checkpoint(10)
    assert live.storage_failed and observe(live.state, 20, 44)[0] is not None


async def test_regular_reads_entity_equal_poll_command_failure_and_reload(
    hass,
    loaded,
    stove,
    entry,
    monkeypatch,
):
    hub = loaded
    elapsed = [0]
    hub.freshness.clock = lambda: elapsed[0]
    hub.freshness.utcnow = lambda: START + timedelta(seconds=elapsed[0])
    hub.live.state.boundary("restart")
    monkeypatch.setattr(hub, "_accepted_door", lambda *_: False)
    stove.data.update(
        phase="Burn", refill_alarm=False, time_to_new_fire_wood=timedelta(minutes=50)
    )
    uid = entity_id_for(hass, "sensor", "shadow_refill_request")
    before = stove.get_data.await_count
    for seconds in (0, 10, 20):
        elapsed[0] = seconds
        await hub.async_refresh()
    state = hass.states.get(uid)
    assert state.attributes["prediction_source"] == "hwam"
    assert state.attributes["prediction_status"] == "active"
    assert timestamp(state.state) == START + timedelta(minutes=50)
    assert stove.get_data.await_count == before + 3
    frozen = deepcopy(hub.shadow.model.dump())
    bench = deepcopy(hub.shadow.benchmark.data)
    await hub.live.checkpoint(20)
    assert hub.shadow.model.dump() == frozen and hub.shadow.benchmark.data == bench
    readings = deepcopy(hub.live.state.last)
    await hub.async_refresh_after_command()
    assert hub.live.state.last == readings
    assert hass.states.get(uid).state == "unknown"
    stove.get_data.side_effect = TimeoutError()
    await hub.async_refresh()
    assert hass.states.get(uid).attributes["prediction_status"] == "stale"
    for cmd in COMMANDS:
        getattr(stove, cmd).assert_not_called()
    stove.get_data.side_effect = stove._read
    old = {e.id: e.entity_id for e in registry_entries(hass, entry.entry_id)}
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert len(old) == 49
    assert old == {e.id: e.entity_id for e in registry_entries(hass, entry.entry_id)}
    after = hass.states.get(uid)
    assert after.attributes["predicted_refill_at"] == readings["eta"]
    assert after.state == "unknown"
