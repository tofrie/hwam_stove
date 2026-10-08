"""Deterministic observations, never controller semantics or clock sleeps."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from custom_components.hwam_stove._analytics_model import (
    DAY_LIMIT,
    LEDGER_LIMIT,
    MONTH_LIMIT,
    Observation,
    State,
    dump,
    gap,
    reduce,
    restore,
)

START = datetime(2026, 10, 1, 12, tzinfo=UTC)


def sample(sequence, phase, *, refill=False, temp=100, start=START, seconds=None):
    seconds = sequence * 10 if seconds is None else seconds
    return Observation(sequence, start + timedelta(seconds=seconds), float(seconds),
                       phase, refill, temp, "Europe/Berlin")


def run(phases, state=None, *, start=START, offset=0):
    state = state or State()
    for i, phase in enumerate(phases, offset):
        state = reduce(state, sample(i, phase, start=start))
    return state


@pytest.mark.parametrize("phases,complete,partial", [
    (["Ignition", "Burn", "Glow", "Standby"], 1, 0),
    (["Standby", "Ignition", "Burn", "Standby"], 1, 0),
    (["Ignition", "Standby"], 0, 0),
    (["Ignition", "Ignition", "Standby"], 0, 0),
    (["Burn", "Standby"], 0, 1),
    (["Glow", "Standby"], 0, 1),
    (["Ignition", "Glow", "Standby"], 0, 1),
    (["Ignition", "Burn", "Glow", "Burn", "Glow", "Burn", "Standby"], 1, 0),
    (["Ignition", "Burn", "Ignition", "Burn", "Standby"], 0, 1),
    (["Standby"] * 5, 0, 0),
    (["Ignition", "Burn", "Standby", "Standby", "Standby"], 1, 0),
])
def test_phase_paths(phases, complete, partial):
    state = run(phases)
    assert state.completed == complete
    assert state.partial_completed == partial
    assert len(state.ledger) == complete + partial
    assert state.current is None


def test_pure_reducer_and_first_ignition_boundary():
    previous = run(["Ignition", "Ignition"])
    before = deepcopy(previous)
    state = run(["Burn", "Glow", "Standby"], previous, offset=2)
    assert previous == before
    record = state.last_complete
    assert record.start == START.isoformat()
    assert record.elapsed == 40
    assert record.end == (START + timedelta(seconds=40)).isoformat()
    assert record.completion_timezone == "Europe/Berlin"


@pytest.mark.parametrize("phase", ["Ignition", "Burn", "Glow", "Standby"])
@pytest.mark.parametrize("kind", ["gap", "reload", "restart"])
def test_restart_every_phase_is_not_a_new_session(phase, kind):
    state = run(["Ignition", "Burn", phase])
    original = state.current.first_observed if state.current else None
    result = gap(state) if kind == "gap" else restore(dump(state))
    assert result.completed == state.completed
    assert result.request_edge_known is False
    resumed = run([phase, "Standby"], result, offset=3)
    assert resumed.completed == state.completed
    if original:
        assert resumed.ledger[-1].first_observed == original
        assert not resumed.ledger[-1].complete


@pytest.mark.parametrize("phase", ["Burn", "Glow"])
def test_active_start_unknown_stays_unknown_on_prospective_completion(phase):
    state = run([phase, "Burn", "Glow", "Standby"])
    assert state.ledger[0].start is None
    assert state.ledger[0].end is not None
    assert state.ledger[0].established
    assert state.completed == 0


def test_duplicate_out_of_order_and_identical_payloads():
    state = run(["Ignition", "Burn"])
    assert reduce(state, sample(1, "Standby")) is state
    assert reduce(state, sample(0, "Standby")) is state
    state = reduce(state, sample(2, "Burn"))
    assert state.current.elapsed == 20
    state = reduce(state, sample(3, "Standby"))
    assert reduce(state, sample(3, "Standby")) is state
    assert state.completed == 1


@pytest.mark.parametrize("gap_kind", ["unavailable", "unknown_phase"])
def test_gap_does_not_accrue_elapsed_or_replay_edge(gap_kind):
    state = run(["Ignition", "Burn"])
    state = gap(state) if gap_kind == "unavailable" else reduce(
        state, sample(2, "Unknown")
    )
    state = reduce(state, sample(3, "Burn", refill=True, seconds=5000))
    assert state.current.elapsed == 10
    assert state.requests == 0
    assert state.current.coverage_uncertain


@pytest.mark.parametrize("alarms,expected", [
    ([False, True, True, False], 1),
    ([True, True, True], 0),
    ([False, True, False, True], 2),
    ([True, False, True], 1),
    ([None, True, True], 0),
    ([False, None, True], 0),
])
def test_refill_episodes(alarms, expected):
    state = State()
    for i, alarm in enumerate(alarms):
        state = reduce(state, sample(i, "Burn", refill=alarm))
    assert state.requests == state.current.requests == expected
    assert state.request_active == alarms[-1]


@pytest.mark.parametrize("alarm", [False, True])
def test_alarm_across_restart_does_not_invent_edge(alarm):
    state = reduce(run(["Ignition"]), sample(1, "Burn", refill=alarm))
    count = state.requests
    state = restore(dump(state))
    state = reduce(state, sample(0, "Burn", refill=True))
    assert state.requests == count
    state = reduce(state, sample(1, "Burn", refill=False))
    state = reduce(state, sample(2, "Burn", refill=True))
    assert state.requests == count + 1


@pytest.mark.parametrize("temperature", [0, -10, 20, 999.5, None, float("nan")])
def test_only_observed_finite_session_samples_contribute_peak(temperature):
    state = reduce(State(), sample(0, "Standby", temp=5000))
    state = reduce(state, sample(1, "Ignition", temp=-20))
    state = reduce(state, sample(2, "Burn", temp=temperature))
    state = reduce(state, sample(3, "Glow", temp=-15))
    state = reduce(state, sample(4, "Standby", temp=6000))
    expected = max(-15, temperature) if temperature is not None else -15
    assert state.last_complete.peak == expected


@pytest.mark.parametrize("start,date,month", [
    ("2026-01-31T22:59:50+00:00", "2026-02-01", "2026-02"),
    ("2026-10-01T21:59:50+00:00", "2026-10-02", "2026-10"),
    ("2026-03-29T00:59:50+00:00", "2026-03-29", "2026-03"),
    ("2026-10-25T00:59:50+00:00", "2026-10-25", "2026-10"),
])
def test_calendar_completion_and_dst(start, date, month):
    state = run(["Ignition", "Burn", "Standby"], start=datetime.fromisoformat(start))
    assert state.last_complete.elapsed == 20
    assert state.completed == 1
    assert state.days == {date: {"completed": 1, "requests": 0}}
    assert state.months == {month: {"completed": 1, "requests": 0}}


def test_monotonic_duration_not_controller_or_utc_clock():
    state = run(["Ignition", "Burn"])
    # A wall-clock advance cannot add hours to elapsed seconds.
    observation = replace(sample(2, "Standby"), utc=START + timedelta(hours=5))
    state = reduce(state, observation)
    assert state.last_complete.elapsed == 20
    state = run(["Ignition", "Burn"])
    state = reduce(state, replace(sample(2, "Burn"), utc=START - timedelta(days=1)))
    assert state.current.coverage_uncertain


def test_bounded_retention_preserves_authoritative_totals():
    state = State()
    for day in range(800):
        start = START + timedelta(days=day)
        state = run(["Ignition", "Burn", "Standby"], state,
                    start=start, offset=day * 3)
    assert state.completed == 800
    assert len(state.ledger) == LEDGER_LIMIT
    assert len(state.days) == DAY_LIMIT
    assert len(state.months) == MONTH_LIMIT
    assert restore(json.loads(json.dumps(dump(state)))).completed == 800


@pytest.mark.parametrize("change", [
    {"schema": 99}, {"schema": 0}, {"schema": True},
    {"completed": -1}, {"completed": 0}, {"requests": True},
    {"partial_completed": 1.5}, {"phase": "New undocumented phase"},
    {"request_edge_known": 1}, {"request_active": 3},
    {"last_observed": "2026-01-01T00:00:00"}, {"unexpected": 1},
    {"days": {"2026-10-01": {"completed": -1, "requests": 0}}},
    {"months": {"nonsense": {"completed": 0, "requests": 0}}},
    {"ledger": [None]},
])
def test_corruption_never_silently_resets_counters(change):
    payload = dump(run(["Ignition", "Burn", "Standby"]))
    payload.update(change)
    with pytest.raises((ValueError, TypeError)):
        restore(payload)


def test_crash_checkpoint_loses_only_uncheckpointed_observations():
    state = run(["Ignition", "Burn"])
    saved = dump(state)
    run(["Glow", "Standby"], state, offset=2)  # Not committed.
    recovered = restore(saved)
    assert recovered.completed == 0
    assert recovered.current.coverage_uncertain
    recovered = reduce(recovered, sample(0, "Standby"))
    assert recovered.completed == 0 and recovered.partial_completed == 1
    saved = dump(run(["Ignition", "Burn", "Standby"]))
    recovered = reduce(restore(saved), sample(0, "Standby"))
    assert recovered.completed == 1  # No double finalization after committed end.
