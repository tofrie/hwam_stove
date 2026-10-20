"""Injected clocks and offline HA state: retained display is never observation."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import Mock

from aiohttp import ClientPayloadError, ServerDisconnectedError
import pytest

from custom_components.hwam_stove._freshness import MEASUREMENTS, Freshness
from custom_components.hwam_stove._shadow_model import ShadowModel

from .helpers import COMMANDS, entity_id_for
from .test_a04_shadow import START
from .test_o01_diagnostics import inert_diagnostics
from .test_pystove_boundary import real_transport

__all__ = ["real_transport"]


@pytest.mark.parametrize("interval", [10, 60])
@pytest.mark.parametrize("age", [0, 10, 11, 12, 30, 60, 61, 62, 119, 120, 150])
async def test_age_policy_and_no_network(hass, interval, age):
    now = [0.0]
    f = Freshness(hass, Mock(), clock=lambda: now[0], utcnow=lambda: START)
    assert f.state() == "unavailable"
    f.accept(START, 0, interval)
    now[0] = age
    assert f.state() == (
        "unavailable" if age >= 120 else "stale" if age > interval + 1 else "fresh"
    )
    before = f.observed_at
    f.fail(TimeoutError())
    assert f.state() == ("unavailable" if age >= 120 else "stale")
    assert f.observed_at == before and f.age() == age
    f.close()
    assert not f._cancel and f.state() == "unavailable"


@pytest.mark.parametrize(
    "error,category",
    [
        (TimeoutError(), "timeout"),
        (ServerDisconnectedError(), "connection"),
        (ClientPayloadError(), "payload"),
        (json.JSONDecodeError("x", "{}", 0), "invalid_json"),
        (RuntimeError("secret-body-host"), "unexpected_error"),
        (asyncio.CancelledError(), "cancelled"),
    ],
)
async def test_failure_categories_never_exception_text(hass, error, category):
    f = Freshness(hass, Mock())
    f.fail(error)
    assert f.summary()["last_error_category"] == category
    assert "secret" not in json.dumps(f.summary())
    assert not f.summary()["failures_are_transport_proof"]
    f.close()


@pytest.mark.parametrize("key", sorted(MEASUREMENTS))
async def test_real_ha_retention_expiry_recovery_no_replayed_observations(
    hass, loaded, stove, key
):
    now = [0.0]
    f = loaded.freshness
    f.clock = lambda: now[0]
    f.utcnow = lambda: START + timedelta(seconds=now[0])
    f.accept(START, 0, 10)
    entity = entity_id_for(hass, "sensor", key)
    old = hass.states.get(entity)
    data = deepcopy(loaded.data)
    requests = loaded.analytics.state.requests
    predictions = loaded.shadow.model.dump()
    stove.get_data.side_effect = TimeoutError()
    await loaded.async_refresh()
    current = hass.states.get(entity)
    assert current.state == old.state and current.last_changed == old.last_changed
    assert current.attributes["data_freshness"] == "stale"
    assert current.attributes["last_observed_at"] == START.isoformat()
    assert loaded.data == data and loaded.analytics.state.requests == requests
    assert loaded.shadow.model.ledger == predictions["ledger"]
    events = []
    unsub = hass.bus.async_listen(
        "state_changed", lambda e: events.append(e.data["entity_id"])
    )
    now[0] = 25
    await loaded.async_refresh()
    await hass.async_block_till_done()
    assert (
        entity not in events
    )  # Identical retained values/metadata generate no change.
    assert f.observed_at == START
    before = stove.get_data.await_count
    now[0] = 120
    f._expire()
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "unavailable"
    assert stove.get_data.await_count == before
    now[0] = 121
    stove.get_data.side_effect = stove._read
    await loaded.async_refresh()
    assert hass.states.get(entity).state == old.state
    assert hass.states.get(entity).attributes["data_freshness"] == "fresh"
    assert f.observed_at == START + timedelta(seconds=121)
    assert loaded.analytics.state.requests == requests
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()
    unsub()


async def test_critical_states_and_buttons_never_get_measurement_grace(
    hass, loaded, stove
):
    now = [0.0]
    f = loaded.freshness
    f.clock = lambda: now[0]
    f.accept(START, 0, 10)
    loaded.door_open = False
    loaded.async_update_listeners()
    critical = [
        entity_id_for(hass, "binary_sensor", "door_open"),
        entity_id_for(hass, "binary_sensor", "refill_alarm"),
        entity_id_for(hass, "number", "burn_level"),
    ]
    # All normal coordinator consumers, including safety alarms, use strict freshness.
    from .helpers import registry_entries

    safety = [
        e.entity_id
        for e in registry_entries(hass)
        if e.domain in {"button", "binary_sensor"}
    ]
    now[0] = 12
    before = stove.get_data.await_count
    f._expire()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(e).state == "unavailable"
        for e in {*critical, *safety}
        if hass.states.get(e)
    )
    assert stove.get_data.await_count == before
    assert (
        hass.states.get(entity_id_for(hass, "sensor", "stove_temperature")).state
        != "unavailable"
    )
    stove.get_data.side_effect = TimeoutError()
    now[0] = 1
    await loaded.async_refresh()
    assert loaded.door_open is None
    assert all(hass.states.get(e).state == "unavailable" for e in critical)


async def test_equal_success_advances_real_timestamp_failure_does_not(
    hass, loaded, stove, entry, monkeypatch
):
    now = [0.0]
    f = loaded.freshness
    f.clock = lambda: now[0]
    f.utcnow = lambda: START + timedelta(seconds=now[0])
    await loaded.async_refresh()
    cached = deepcopy(loaded.data)
    now[0] = 10
    await loaded.async_refresh()
    assert loaded.data == cached and f.observed_at == START + timedelta(seconds=10)
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    now[0] = 20
    await loaded.async_refresh()
    assert f.observed_at == START + timedelta(seconds=10)
    assert f.error == "empty_or_invalid_status"
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["connection"]["data_age_seconds"] == 10
    assert result["connection"]["display_state"] == "stale"


async def test_cancellation_invalidates_safety_but_not_last_measurement(
    hass, loaded, stove
):
    f = loaded.freshness
    f.accept(START, f.clock(), 10)
    loaded.door_open = False
    stove.get_data.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await loaded._async_update_data()
    assert loaded.door_open is None and f.failed
    assert f.observed_at == START
    assert (
        hass.states.get(entity_id_for(hass, "sensor", "stove_temperature")).state
        != "unavailable"
    )


async def test_new_runtime_never_restores_display_safety_cache(hass):
    f = Freshness(hass, Mock())
    assert f.observed_at is None and f.state() == "unavailable"
    assert ShadowModel().pending is None
    f.close()


async def test_delayed_success_is_not_observed_combustion_time(hass):
    from custom_components.hwam_stove._analytics import Analytics

    now = [0.0]
    a = Analytics(
        hass,
        "delayed",
        clock=lambda: now[0],
        utcnow=lambda: START + timedelta(seconds=now[0]),
    )
    await a.async_load()
    data = {"phase": "Ignition", "refill_alarm": False, "stove_temperature": 200}
    await a.async_observe(a.read_started(), data)
    now[0] = 10
    token = a.read_started()  # On-time request, but its response is delayed.
    now[0] = 80
    await a.async_observe(token, data | {"phase": "Burn", "refill_alarm": True})
    assert a.state.current.coverage_uncertain
    assert a.state.current.elapsed == 0 and a.state.requests == 0
    await a.async_stop()


async def test_real_rc3_invalid_json_retains_only_display_and_recovers(
    hass, entry, real_transport
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data["hwam_stove"]["stoves"][entry.entry_id]
    session, = real_transport.sessions
    measurement = entity_id_for(hass, "sensor", "stove_temperature")
    door = entity_id_for(hass, "binary_sensor", "door_open")
    before = hass.states.get(measurement).state
    observed = coordinator.freshness.observed_at
    calls = len(session.calls)
    session.queue("GET", "/get_stove_data", body="{invalid-synthetic-json")
    await coordinator.async_refresh()
    assert session.calls[calls:] == [("GET", "/get_stove_data", {})]
    assert not coordinator.last_update_success
    assert coordinator.freshness.error == "empty_or_invalid_status"
    assert coordinator.freshness.observed_at == observed
    assert hass.states.get(measurement).state == before
    assert hass.states.get(measurement).attributes["data_freshness"] == "stale"
    assert hass.states.get(door).state == "unavailable"
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.freshness.state() == "fresh"
    assert len(session.calls) == calls + 2
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
