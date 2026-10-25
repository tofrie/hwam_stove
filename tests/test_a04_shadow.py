"""Offline causal shadow contracts, persistence, HA state and request budget."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.hwam_stove._shadow import Shadow
from custom_components.hwam_stove._shadow_model import (
    CALIBRATED_AT,
    CONFIDENCE,
    LIMIT,
    MODEL,
    Sample,
    ShadowModel,
    crossing,
)
from custom_components.hwam_stove.diagnostics import async_get_config_entry_diagnostics

from .helpers import COMMANDS, entity_id_for, registry_entries

START = datetime(2026, 10, 17, 10, tzinfo=UTC)


def sample(n, **changes):
    row = Sample(
        n,
        START + timedelta(seconds=n * 10),
        n * 10.0,
        START.isoformat(),
        True,
        "Burn",
        False,
        False,
        259 - n / 6,
        15,
        (20, 30, 40),
        n * 10.0,
        30.0,
    )
    return replace(row, **changes)


def warm(model=None):
    model = model or ShadowModel()
    for n in range(61):
        model.step(sample(n))
    assert model.state == "prediction_available"
    return model


def test_frozen_formula_and_immutable_cycle():
    m = warm()
    assert crossing([sample(n) for n in range(61)]) == (20.0, "available")
    first = deepcopy(m.pending)
    assert first["model_version"] == MODEL
    assert first["confidence"] == CONFIDENCE
    for n in range(61, 70):
        m.step(sample(n, temperature=240))
    assert m.pending == first
    m.step(sample(70, alarm=True))
    row = m.ledger[-1]
    for k in ("prediction_at", "predicted_request_at", "session_id"):
        assert row[k] == first[k]
    assert row["observed_request_at"] == sample(70).at.isoformat()
    assert row["request_after"] == sample(69).at.isoformat()
    assert row["error_minutes"] == pytest.approx(18 + 1 / 3)
    assert m.state == "unknown" and m.reason == "request_active"
    for n in range(71, 150):
        m.step(sample(n, alarm=True))
    assert len(m.ledger) == 1 and m.pending is None
    m.step(sample(150))
    assert m.state == "insufficient_data"


def test_duplicate_equal_and_future_invariance():
    prefix = [sample(n) for n in range(80)]
    a, b = ShadowModel(), ShadowModel()
    states = []
    for s in prefix:
        a.step(s)
        states.append((deepcopy(a.dump()), a.predicted))
    for s in prefix:
        b.step(s)
        before = deepcopy(b.dump())
        b.step(s)
        assert b.dump() == before
    assert a.dump() == b.dump()
    a.step(sample(81, alarm=True))
    b.step(sample(81, temperature=500))
    c = ShadowModel()
    for i, s in enumerate(prefix):
        c.step(s)
        assert (c.dump(), c.predicted) == states[i]
    assert a.ledger != b.ledger  # Only future scoring differs.


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"qualified": False}, "partial_session"),
        ({"door": True}, "door_open"),
        ({"door": None}, "missing_observations"),
        ({"temperature": None}, "missing_observations"),
        ({"oxygen": float("nan")}, "missing_observations"),
        ({"valves": (20, None, 30)}, "missing_observations"),
        ({"phase": "Glow"}, "unseen_phase"),
        ({"phase": "Standby", "session": None}, "session_end"),
    ],
)
def test_unknown_closes_pending_without_score(change, reason):
    m = warm()
    original = deepcopy(m.pending)
    m.step(sample(61, **change))
    assert m.state == "unknown" and m.predicted is None and m.reason == reason
    assert m.ledger[-1]["error_minutes"] is None
    assert m.ledger[-1]["prediction_at"] == original["prediction_at"]


@pytest.mark.parametrize(
    "reason", ["restart", "sampling_gap", "failed_read", "command_boundary", "shutdown"]
)
def test_gap_never_scores_or_bridges(reason):
    m = warm()
    m.gap(reason)
    m.step(sample(70, alarm=True))
    assert len(m.ledger) == 1 and m.ledger[0]["gap_invalid"]
    assert m.ledger[0]["error_minutes"] is None
    assert m.pending is None


def test_startup_true_and_unpredicted_request():
    m = ShadowModel()
    m.step(sample(0, alarm=True))
    assert m.ledger == []
    m.step(sample(1))
    m.step(sample(2, alarm=True))
    assert m.ledger[0]["outcome"] == "no_prediction"
    assert m.ledger[0]["prediction_at"] is None
    assert m.summary()["requests_without_prediction"] == 1
    assert ShadowModel.restore(m.dump()).ledger == m.ledger


def test_expiry_is_not_rewritten_and_bounded_negative_window():
    m = warm()
    for n in range(61, 242):
        m.step(sample(n))
    assert m.pending is None
    assert m.ledger[-1]["outcome"] == "no_request_within_30m"
    assert m.summary()["negative_windows_without_request"] == 1
    assert m.summary()["invalidated_predictions"] == 0
    m.step(sample(242, alarm=True))
    assert len(m.ledger) == 1


def test_no_calibration_leak_into_historical_prefix():
    m = ShadowModel()
    for n in range(100):
        m.step(sample(n, at=CALIBRATED_AT - timedelta(days=1, seconds=-10 * n)))
    assert m.pending is None and m.reason == "before_calibration"


@pytest.mark.parametrize(
    "utc",
    [
        datetime(2026, 3, 29, 0, 55, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 55, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 55, tzinfo=UTC),
    ],
)
def test_calendar_and_dst_use_elapsed_utc(utc):
    # The spring test is before calibration and honestly remains insufficient.
    m = ShadowModel()
    for n in range(61):
        at = (utc + timedelta(seconds=n * 10)).astimezone(ZoneInfo("Europe/Berlin"))
        m.step(sample(n, at=at, session=utc.isoformat()))
    if utc > CALIBRATED_AT:
        assert m.predicted.utcoffset() == timedelta(0)  # normalized public output
    else:
        assert m.state == "insufficient_data"


def test_clock_rollback_breaks_coverage():
    m = warm()
    m.step(sample(61, at=START))
    assert m.ledger[-1]["gap_invalid"]
    assert m.ledger[-1]["error_minutes"] is None
    assert m.state == "insufficient_data"


def test_restore_does_not_revive_pending_and_preserves_scores():
    m = warm()
    payload = m.dump()
    restored = ShadowModel.restore(payload)
    assert payload["pending"] is not None
    assert restored.pending is None
    assert restored.ledger[-1]["outcome"] == "restart"
    assert restored.state == "unknown"
    restored.step(sample(100, alarm=True))
    assert len(restored.ledger) == 1


@pytest.mark.parametrize("mutation", ["schema", "model", "score", "extra", "huge"])
def test_corrupt_store_rejected(mutation):
    m = warm()
    m.step(sample(70, alarm=True))
    p = m.dump()
    if mutation == "schema":
        p["schema"] = 2
    if mutation == "model":
        p["model_version"] = "future"
    if mutation == "score":
        p["ledger"][0]["error_minutes"] = 999
    if mutation == "extra":
        p["host"] = "secret"
    if mutation == "huge":
        p["ledger"] *= LIMIT + 1
    with pytest.raises(ValueError):
        ShadowModel.restore(p)


def test_bounded_ledger_and_no_premature_error_summary():
    m = ShadowModel()
    for _cycle in range(LIMIT + 3):
        one = warm()
        m.pending = one.pending
        m.close("failed_read", gap=True)
    assert len(m.ledger) == LIMIT and m.evicted == 3
    assert m.summary()["median_absolute_error_minutes"] is None
    assert "ledger" not in m.summary() and "session_id" not in m.summary()


async def test_store_restart_and_no_poll_history(hass):
    s = Shadow(hass, "private-test")
    await s.async_load()
    s.model = warm()
    await s._save()
    before = await s.store().async_load()
    assert "history" not in before and "previous" not in before
    restarted = Shadow(hass, "private-test")
    await restarted.async_load()
    assert restarted.ready and restarted.healthy
    assert restarted.model.ledger[-1]["outcome"] == "restart"
    assert restarted.model.pending is None
    await restarted.async_stop()


async def test_failed_persistence_degrades_only_shadow(hass):
    s = Shadow(hass, "failure")
    await s.async_load()
    s.model = warm()
    with patch.object(
        s,
        "store",
        return_value=SimpleNamespace(
            async_save=AsyncMock(side_effect=OSError("sensitive"))
        ),
    ):
        await s._save()
    assert not s.healthy and s.model.predicted is None


async def test_corrupt_store_not_overwritten(hass):
    s = Shadow(hass, "corrupt")
    await s.store().async_save({"schema": 999})
    await s.async_load()
    assert not s.healthy
    await s.async_stop()
    assert await s.store().async_load() == {"schema": 999}


async def test_integration_equal_observations_no_new_requests(
    hass,
    loaded,
    entry,
    stove,
    freezer,
    monkeypatch,
):
    a, s = loaded.analytics, loaded.shadow
    tick = [0.0]
    a.clock = s.clock = lambda: tick[0]
    a._ready_at = s._ready_at = None
    s.boundary("restart")
    a.state.current = None
    a.state.phase = "Standby"
    a.state.monotonic = None
    # Simulated cache only; library strict freshness is covered by A05.
    monkeypatch.setattr(loaded, "_accepted_door", lambda *_: False)
    before = stove.get_data.await_count
    entity = entity_id_for(hass, "sensor", "shadow_refill_request")
    state = stove.data
    state.update(phase="Ignition", refill_alarm=False, stove_temperature=260)
    await loaded.async_refresh()
    for n in range(1, 63):
        tick[0] += 10
        freezer.tick(10)
        state.update(phase="Burn", stove_temperature=259 - n / 6)
        await loaded.async_refresh()
    assert stove.get_data.await_count == before + 63
    assert hass.states.get(entity).state != "unavailable"
    assert hass.states.get(entity).attributes["device_class"] == "timestamp"
    assert hass.states.get(entity).attributes["model_version"] == MODEL
    old = deepcopy(s.model.pending)
    # Identical data still updates shadow minutes via its own listener.
    for _ in range(6):
        tick[0] += 10
        freezer.tick(10)
        await loaded.async_refresh()
    assert s.model.pending == old
    assert len(registry_entries(hass, entry.entry_id)) == 49
    assert len(er.async_get(hass).entities) == 49
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["shadow"]["model_version"] == MODEL
    assert "session_id" not in json.dumps(result["shadow"])
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()
    # Command boundary cannot turn a readback into an observation/evaluation.
    count = s._sequence
    loaded.shadow.boundary()
    assert hass.states.get(entity).state == "unavailable"
    loaded._command_read = True
    await loaded.async_refresh()
    loaded._command_read = False
    assert s._sequence == count
    assert s.model.pending is None


async def test_failure_and_cancel_invalidate_immediately(hass, loaded, stove):
    s = loaded.shadow
    s.model = warm()
    s.notify()
    entity = entity_id_for(hass, "sensor", "shadow_refill_request")
    stove.get_data.side_effect = TimeoutError()
    await loaded.async_refresh()
    assert hass.states.get(entity).state == "unavailable"
    assert s.model.ledger[-1]["gap_invalid"]
    s.model = warm()
    s.notify()
    started = asyncio.Event()

    async def pending():
        started.set()
        await asyncio.Event().wait()

    stove.get_data.side_effect = pending
    task = asyncio.create_task(loaded.async_refresh())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hass.states.get(entity).state == "unavailable"
    assert s.model.pending is None


async def test_reload_keeps_48_identities_and_does_not_revive_prediction(
    hass,
    loaded,
    entry,
    stove,
):
    before = {
        (r.domain, r.unique_id): (r.id, r.entity_id, r.device_id)
        for r in registry_entries(hass, entry.entry_id)
    }
    loaded.shadow.model = warm()
    loaded.shadow.notify()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    after = {
        (r.domain, r.unique_id): (r.id, r.entity_id, r.device_id)
        for r in registry_entries(hass, entry.entry_id)
    }
    assert before == after and len(after) == 49
    fresh = hass.data["hwam_stove"]["stoves"][entry.entry_id]
    assert fresh.shadow.model.pending is None
    assert fresh.shadow.model.ledger[-1]["gap_invalid"]
    assert stove.destroy.await_count == 1


@pytest.mark.parametrize(
    "phase,temperature,reason",
    [
        ("Glow", 250, "unseen_phase"),
        ("Burn", 400, "beyond_range"),
        ("Burn", 229, "available"),
    ],
)
def test_candidate_abstention_range_and_zero(phase, temperature, reason):
    rows = [
        sample(n, phase=phase, temperature=temperature + (60 - n) / 6)
        for n in range(61)
    ]
    eta, why = crossing(rows)
    assert why == reason
    if temperature == 229:
        assert eta == 0


def test_error_summary_requires_independent_session_count():
    m = warm()
    m.step(sample(70, alarm=True))
    row = deepcopy(m.ledger[0])
    m.ledger = [deepcopy(row) for _ in range(30)]
    assert m.summary()["median_absolute_error_minutes"] is None
    for i, r in enumerate(m.ledger):
        r["session_id"] = (START + timedelta(days=i % 20)).isoformat()
    assert m.summary()["median_absolute_error_minutes"] == 18.3


async def test_adapter_missing_poll_uses_existing_cadence(hass):
    tick = [0.0]
    s = Shadow(
        hass,
        "gap",
        clock=lambda: tick[0],
        utcnow=lambda: START + timedelta(seconds=tick[0]),
    )
    await s.async_load()
    session = SimpleNamespace(
        first_observed=START.isoformat(),
        start=START.isoformat(),
        coverage_uncertain=False,
        elapsed=0,
    )
    a = SimpleNamespace(
        healthy=True, online=True, state=SimpleNamespace(current=session)
    )
    data = dict(
        phase="Burn",
        refill_alarm=False,
        stove_temperature=259,
        oxygen_level=15,
        valve1_position=20,
        valve2_position=30,
        valve3_position=40,
        time_to_new_fire_wood=timedelta(minutes=10),
    )
    for n in range(61):
        tick[0] = n * 10
        data["stove_temperature"] = 259 - n / 6
        await s.async_observe(n, s.read_started(), a, data, False)
    assert s.model.pending and s.model.pending["hwam_minutes_at_prediction"] == 10
    tick[0] += 12  # Beyond the existing active 10s + HA scheduling allowance.
    await s.async_observe(61, s.read_started(), a, data, False)
    assert s.model.ledger[-1]["outcome"] == "sampling_gap"
    assert s.model.pending is None
    assert s.model.state == "unknown" and s.model.reason == "cycle_spent"
    assert s.model.history  # Sparse numerical rows survive; opportunity stays spent.


async def test_overlapping_command_boundary_rejects_read(hass):
    s = Shadow(hass, "overlap")
    await s.async_load()
    s.model = warm()
    token = s.read_started()
    s.boundary()
    await s.async_observe(1, token, None, {}, None)
    assert s.model.pending is None
    assert s.model.ledger[-1]["outcome"] == "command_boundary"
    assert s.model.ledger[-1]["error_minutes"] is None


async def test_owned_disk_write_settles_repeated_cancellation(hass):
    s = Shadow(hass, "cancel")
    await s.async_load()
    s.model = warm()
    started, finish = asyncio.Event(), asyncio.Event()
    written = []

    async def save(payload):
        started.set()
        await finish.wait()
        written.append(payload)

    fake = SimpleNamespace(
        async_save=save, async_load=AsyncMock(side_effect=lambda: written[-1])
    )
    with patch.object(s, "store", return_value=fake):
        task = asyncio.create_task(s._save())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(written) == 1
    assert not s.healthy and s.model.pending is None


async def test_a05_upgrade_adds_only_shadow_preserving_all_48(
    hass,
    entry,
    stove_factory,
):
    from custom_components.hwam_stove import sensor

    setup = sensor.async_setup_entry

    async def previous(hass, entry, add):
        await setup(
            hass,
            entry,
            lambda entities: add(
                [
                    e
                    for e in entities
                    if e.entity_description.key != "shadow_refill_request"
                ]
            ),
        )

    with patch.object(sensor, "async_setup_entry", previous):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    before = {
        r.id: (r.entity_id, r.unique_id, r.device_id, r.disabled_by, r.hidden_by)
        for r in registry_entries(hass, entry.entry_id)
    }
    assert len(before) == 48
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    after = {
        r.id: (r.entity_id, r.unique_id, r.device_id, r.disabled_by, r.hidden_by)
        for r in registry_entries(hass, entry.entry_id)
    }
    assert len(after) == 49 and all(after[k] == v for k, v in before.items())
    assert len(after.keys() - before.keys()) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_door_only_abstention_does_not_hide_observed_request():
    m = ShadowModel()
    m.step(sample(0))
    m.step(sample(1, door=True))
    m.step(sample(2))
    m.step(sample(3, alarm=True))
    assert m.ledger[-1]["outcome"] == "no_prediction"
    assert m.ledger[-1]["prediction_at"] is None
    m.step(sample(4, alarm=True))
    assert len(m.ledger) == 1


@pytest.mark.parametrize("mutation", ["bool_schema", "missing_actual", "bad_bracket"])
def test_store_never_invents_observed_request_evidence(mutation):
    m = ShadowModel()
    m.step(sample(0))
    m.step(sample(1, alarm=True))
    payload = m.dump()
    if mutation == "bool_schema":
        payload["schema"] = True
    elif mutation == "missing_actual":
        payload["ledger"][0]["observed_request_at"] = None
    else:
        payload["ledger"][0]["request_after"] = sample(2).at.isoformat()
    with pytest.raises(ValueError):
        ShadowModel.restore(payload)
