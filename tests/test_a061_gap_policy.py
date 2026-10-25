"""Real timestamp sparsity is not permission to invent an event or next request."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from custom_components.hwam_stove import _shadow
from custom_components.hwam_stove._forecast_benchmark import (
    GAP_POLICY,
    LEGACY_POLICY,
    MODEL,
    NATIVE,
    POLICY,
    Benchmark,
    at_horizon,
    validate,
)
from custom_components.hwam_stove._forecast_gaps import GapPolicy, break_edges
from custom_components.hwam_stove._shadow_evidence import Evidence
from custom_components.hwam_stove._shadow_model import ShadowModel, crossing

from .test_a04_shadow import START, sample, warm
from .test_a041_diagnostics import adapter_inputs
from .test_a042_persistence import disk
from .test_a043_benchmark import analytics, empty_model, native_cycle

__all__ = ["disk"]
BASE = "855cc958a5540eb9f8b4dff2b09b247d90de9156"


def row(seconds, **changes):
    result = replace(
        sample(int(seconds * 1000)),
        at=START + timedelta(seconds=seconds),
        monotonic=seconds,
        temperature=259 - seconds / 60,
    )
    return replace(result, **changes)


@pytest.mark.parametrize(
    "seconds,kind,edge",
    [
        (9, "NORMAL_JITTER", False),
        (10, "NORMAL_JITTER", False),
        (11, "NORMAL_JITTER", False),
        (11.001, "NORMAL_JITTER", True),
        (14.999, "NORMAL_JITTER", True),
        (15, "NORMAL_JITTER", True),
        (15.001, "SHORT_GAP", True),
        (20, "SHORT_GAP", True),
        (29.999, "SHORT_GAP", True),
        (30, "SHORT_GAP", True),
        (30.001, "prolonged_outage", True),
        (40, "prolonged_outage", True),
        (120, "prolonged_outage", True),
    ],
)
def test_exact_thresholds(seconds, kind, edge):
    p = GapPolicy()
    p.accept(row(0))
    assert p.classify(row(seconds)) == (kind, edge)


@pytest.mark.parametrize("seconds", [9, 10, 11, 15, 20, 30])
def test_failure_makes_even_short_numerical_interval_edge_sensitive(seconds):
    p = GapPolicy()
    p.accept(row(0))
    p.failed = True
    assert p.classify(row(seconds))[1]
    assert p.data["edge_sensitive_gaps"] == 1


@pytest.mark.parametrize("seconds", [60, 61, 61.001])
def test_standby_cadence_not_relaxed_into_active_predictions(seconds):
    p = GapPolicy()
    p.accept(row(0, phase="Standby"))
    assert p.classify(row(seconds, phase="Standby"))[0] == (
        "NORMAL_JITTER" if seconds <= 61 else "prolonged_outage"
    )


def test_bounded_persistent_counters_and_readonly_summary():
    p = GapPolicy()
    p.accept(row(0))
    p.classify(row(20))
    p.data["short_gaps"] = 65535
    p.classify(row(30))
    assert p.data["short_gaps"] == 65535
    checkpoint = p.dump()
    p.hard("shutdown")
    assert checkpoint["hard_gap_causes"]["shutdown"] == 0
    q = GapPolicy.restore(checkpoint)
    assert q.previous is None and q.data["short_gaps"] == 65535
    assert q.data["hard_gap_causes"]["restart"] == 1
    q.summary()["hard_gap_causes"]["restart"] = 0
    assert q.data["hard_gap_causes"]["restart"] == 1
    for key in ("short_gaps", "longest_short_gap_seconds", "hard_gap_causes"):
        bad = deepcopy(checkpoint)
        bad[key] = "invalid"
        with pytest.raises(ValueError):
            GapPolicy.restore(bad)


def test_frozen_ols_sparse_history_exact_elapsed_no_synthetic_rows():
    m = ShadowModel()
    for second in range(0, 601, 10):
        if second == 300:
            break_edges(m, "failed_read")
            continue
        m.step(row(second))
    assert len(m.history) == 60
    assert 300 not in [s.monotonic for s in m.history]
    assert crossing(list(m.history))[0] == pytest.approx(20)
    assert m.predicted == START + timedelta(minutes=30)
    first = deepcopy(m.pending)
    break_edges(m, "sampling_gap")
    assert m.pending is None and m.spent
    for second in range(620, 801, 10):
        m.step(row(second))
    assert m.pending is None and m.reason == "cycle_spent"
    assert m.ledger[-1]["prediction_at"] == first["prediction_at"]
    assert m.ledger[-1]["gap_invalid"]


async def adapter(hass):
    tick = [0.0]
    s = _shadow.Shadow(
        hass,
        "gap-test",
        clock=lambda: tick[0],
        utcnow=lambda: START + timedelta(seconds=tick[0]),
    )
    await s.async_load()
    a, data = adapter_inputs()
    return s, a, data, tick


async def observe(s, a, data, tick, seconds, **changes):
    tick[0] = seconds
    data.update(stove_temperature=259 - seconds / 60, **changes)
    await s.async_observe(int(seconds * 1000), s.read_started(), a, data, False)
    assert s.healthy


async def test_adapter_short_gap_600s_history_and_failure_deduplicated(hass):
    s, a, data, tick = await adapter(hass)
    for second in range(0, 601, 10):
        if second == 300:
            tick[0] = second
            await s.async_gap()
            s.boundary("failed_read")  # Outer coordinator reports same read.
            continue
        await observe(s, a, data, tick, second)
    assert len(s.model.history) == 60 and s.model.pending
    assert s.gaps.data["short_gaps"] == 1
    assert s.gaps.data["edge_sensitive_gaps"] == 1
    assert s.gaps.data["numerical_history_preserved_across_short_gap"] == 1
    assert s.model.history[-1].at - s.model.history[0].at == timedelta(seconds=600)
    before = s.model.dump()
    await s.async_observe(600000, s.read_started(), a, data, False)
    assert s.model.dump() == before
    await s.async_stop()


@pytest.mark.parametrize("gap", [30.001, 40, 120])
async def test_adapter_hard_gap_resets_window_and_never_counts_request(hass, gap):
    s, a, data, tick = await adapter(hass)
    for second in range(0, 301, 10):
        await observe(s, a, data, tick, second)
    await observe(s, a, data, tick, 300 + gap, refill_alarm=True)
    assert not s.model.history
    assert s.model.last_request is None
    assert s.interventions.data["counts"]["observed_request"] == 0
    assert s.gaps.data["hard_gap_causes"]["prolonged_outage"] == 1
    await s.async_stop()


@pytest.mark.parametrize(
    "phase,alarm,door",
    [
        ("Burn", True, False),
        ("Burn", False, True),
        ("Glow", False, False),
    ],
)
async def test_short_gap_never_invents_edges(hass, phase, alarm, door):
    s, a, data, tick = await adapter(hass)
    await observe(s, a, data, tick, 0, phase="Glow")
    tick[0] = 20
    data.update(phase=phase, refill_alarm=alarm)
    await s.async_observe(20000, s.read_started(), a, data, door)
    assert s.healthy and s.model.last_request is None
    assert s.interventions.data["counts"]["observed_request"] == 0
    assert s.interventions.data["counts"]["glow_to_burn_unsampled"] == 0
    pending = s.interventions.data["pending"]
    if pending:
        assert pending["coverage"] == "left_censored"
        assert pending["wood_added"] == "unknown"
    await s.async_stop()


@pytest.mark.parametrize("reason", ["command_boundary", "shutdown", "restart"])
async def test_hard_lifecycle_boundary(hass, reason):
    s, a, data, tick = await adapter(hass)
    for second in range(0, 601, 10):
        await observe(s, a, data, tick, second)
    assert s.model.pending
    s.boundary(reason)
    assert s.model.pending is None and not s.model.history
    assert s.model.ledger[-1]["gap_invalid"]
    assert s.gaps.data["hard_gap_causes"][reason] == 1
    await s.async_stop()


def bench_feed(b, seconds, *, alarm=False, phase="Glow"):
    b.observe(
        row(seconds, phase=phase, alarm=alarm),
        {"time_to_new_fire_wood": timedelta(seconds=8100 - seconds)},
        empty_model(),
        analytics(),
    )


@pytest.mark.parametrize("horizon", [120, 90, 60, 45, 30, 20, 15, 10, 5])
def test_known_horizon_survives_unrelated_short_gap_conditionally(horizon):
    b = Benchmark(GAP_POLICY)
    bench_feed(b, 0, phase="Ignition")
    for second in range(10, 8100, 10):
        if second == 7800:  # Gap elsewhere; queried 5-minute point is observed.
            continue
        bench_feed(b, second)
    bench_feed(b, 8100, alarm=True)
    validate(b.dump())
    record = b.data["ledger"][-1]
    assert record["policy"] == GAP_POLICY and record["uncertain_intervals"]
    score = record["results"][NATIVE]["horizons"][str(horizon)]
    if horizon == 5:  # The target itself lies in missing evidence.
        assert score["state"] == "invalidated"
    else:
        assert score["state"] == "valid" and score["signed_error_minutes"] == 0
    assert b.data["eligible_requests"] == 0
    assert not record["qualified"]
    assert b.data["comparisons_preserved_after_short_gap"] >= 8
    assert b.summary()["cross_gap_next_request_proven"] is False


def test_gap_80_minutes_before_request_does_not_destroy_60_minute_endpoint():
    b = Benchmark(GAP_POLICY)
    bench_feed(b, 0, phase="Ignition")
    for second in range(10, 8100, 10):
        if second != 3300:
            bench_feed(b, second)
    bench_feed(b, 8100, alarm=True)
    result = b.data["ledger"][-1]["results"][NATIVE]["horizons"]["60"]
    assert result["state"] == "valid" and not b.data["eligible_requests"]


@pytest.mark.parametrize("failed", [False, True])
def test_unknown_outcome_edge_censors_even_with_numerical_forecasts(failed):
    b = Benchmark(GAP_POLICY)
    for second in range(0, 100, 10):
        bench_feed(b, second)
    original = deepcopy(b.data["active"]["sources"])
    if failed:
        b.suspend()
    bench_feed(b, 110 if not failed else 100, alarm=True)
    validate(b.dump())
    record = b.data["ledger"][-1]
    assert record["sources"] == original and record["results"] == {}
    assert record["reason"] == "outcome_edge_unknown"
    assert b.data["actual_requests_observed"] == 0
    assert b.data["unknown_outcome_edge_cycles"] == 1


def test_queries_inside_gap_and_stability_are_not_interpolated():
    b = Benchmark(GAP_POLICY)
    bench_feed(b, 0)
    bench_feed(b, 20)
    cycle = b.data["active"]
    assert len(cycle["samples"]) == 2
    assert (
        at_horizon(cycle, START.timestamp() + 10, NATIVE, 999)["state"] == "invalidated"
    )
    assert at_horizon(cycle, START.timestamp() + 20, NATIVE, 999)["state"] == "valid"
    b.gap("possible_door_intervention")
    assert b.data["ledger"][-1]["results"] == {}


async def test_rc8_migration_archives_without_rescoring_and_keeps_source(disk, hass):
    s = disk()
    m = warm()
    m.step(sample(70, alarm=True))
    b = native_cycle(actual=40)
    payload = Evidence.migrate(m.dump()).dump(m)
    payload.update(
        schema=4,
        benchmark=b.dump(),
        legacy_benchmark=None,
        interventions=s.interventions.dump(),
    )
    old = _shadow.Store(hass, 1, s.rc8_key, atomic_writes=True)
    await old.async_save(payload)
    original = Path(old.path).read_bytes()
    await s.async_load()
    assert s.healthy and s.summary()["schema_version"] == 5
    assert s.rc8_benchmark == b.dump()
    assert s.benchmark.data["policy"] == GAP_POLICY
    assert s.benchmark.data["cycles_observed"] == 0
    assert s.model.ledger == m.ledger
    assert Path(old.path).read_bytes() == original
    p = await s.store().async_load()
    assert p["schema"] == 5 and p["rc8_benchmark"] == payload["benchmark"]
    again = _shadow.Shadow(hass, "test-entry")
    again.key = s.key
    await again.async_load()
    assert again.healthy and again.rc8_benchmark == s.rc8_benchmark
    assert again.gaps.data["hard_gap_causes"]["restart"] == 1
    await s.async_stop()
    await again.async_stop()


def test_frozen_runtime_contract_and_no_request_changes():
    root = Path(__file__).resolve().parents[1]
    for name in (
        "_shadow_model.py",
        "_analytics_model.py",
        "_analytics.py",
        "_freshness.py",
        "coordinator.py",
        "entity.py",
        "sensor.py",
        "binary_sensor.py",
        "number.py",
        "switch.py",
        "button.py",
    ):
        path = f"custom_components/hwam_stove/{name}"
        old = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=root)
        assert (
            hashlib.sha256(old).digest()
            == hashlib.sha256((root / path).read_bytes()).digest()
        )
    manifest = json.loads(
        (root / "custom_components/hwam_stove/manifest.json").read_text()
    )
    assert manifest["requirements"] == ["saynwerk-pystove==0.3.0rc3"]
    assert manifest["version"] == "1.0.0rc9"
    assert {LEGACY_POLICY, POLICY, GAP_POLICY} == {
        "a043-observed-horizons-v1",
        "a06-intervention-censoring-v2",
        "a061-observed-horizon-endpoints-v1",
    }
    assert MODEL == "a04-phase-matched-r1-v1"


@pytest.mark.parametrize("field", ["door", "alarm", "temperature", "oxygen"])
def test_missing_required_input_is_hard_and_never_becomes_numerical_fact(field):
    p = GapPolicy()
    p.accept(row(0))
    assert p.classify(row(10, **{field: None})) == ("missing_observations", True)


def test_uncertainty_capacity_censors_instead_of_forgetting_unknown_edges():
    from custom_components.hwam_stove._forecast_benchmark import MAX_GAPS

    b = Benchmark(GAP_POLICY)
    for n in range(MAX_GAPS + 2):
        bench_feed(b, n * 20)
    assert b.data["ledger"][0]["reason"] == "uncertainty_capacity"
    assert b.data["ledger"][0]["results"] == {}
    assert len(b.data["ledger"][0]["uncertain_intervals"]) == MAX_GAPS
    validate(b.dump())


async def test_cold_diagnostics_and_duplicates_do_not_add_gaps(hass):
    s, a, data, tick = await adapter(hass)
    await observe(s, a, data, tick, 0)
    await observe(s, a, data, tick, 20)
    before = s.gaps.dump()
    for _ in range(10):
        s.gaps.summary()
        s.benchmark_diagnostics()
        await s.async_observe(20000, s.read_started(), a, data, False)
    assert s.gaps.dump() == before
    await s.async_stop()


async def test_no_loss_of_spent_opportunity_when_short_gap_history_empty(hass):
    s, a, data, tick = await adapter(hass)
    await observe(s, a, data, tick, 0)
    s.model.spent = True
    s.model.history.clear()
    await observe(s, a, data, tick, 20)
    assert s.model.spent and s.model.pending is None
    assert s.gaps.data["numerical_history_preserved_across_short_gap"] == 0
    await s.async_stop()


def test_unknown_outcome_remains_censored_after_explicit_failure_and_long_outage():
    b = Benchmark(GAP_POLICY)
    bench_feed(b, 0)
    original = deepcopy(b.data["active"]["sources"])
    b.suspend()
    bench_feed(b, 40, alarm=True)
    assert b.data["ledger"][0]["sources"] == original
    assert b.data["ledger"][0]["reason"] == "observation_age"
    assert b.data["actual_requests_observed"] == 0
    validate(b.dump())


@pytest.mark.parametrize("mutate", ["duration", "qualified", "counter"])
def test_invalid_new_policy_checkpoint_fails_closed(mutate):
    b = Benchmark(GAP_POLICY)
    bench_feed(b, 0)
    bench_feed(b, 20)
    payload = b.dump()
    if mutate == "duration":
        payload["active"]["uncertain_intervals"][0][1] += 100
    elif mutate == "qualified":
        payload["active"]["qualified"] = True
    else:
        payload["unknown_outcome_edge_cycles"] = -1
    with pytest.raises(ValueError):
        Benchmark.restore(payload)


async def test_short_gap_phase_change_does_not_recreate_consumed_opportunity(hass):
    s, a, data, tick = await adapter(hass)
    for second in range(0, 601, 10):
        await observe(s, a, data, tick, second)
    assert s.model.pending
    await observe(s, a, data, tick, 620, phase="Glow")
    assert s.model.spent and s.model.pending is None and not s.model.history
    for second in range(630, 1241, 10):
        await observe(s, a, data, tick, second, phase="Burn")
    assert s.model.pending is None and s.model.reason == "cycle_spent"
    assert len(s.model.ledger) == 1
    await s.async_stop()


async def test_unhealthy_evidence_never_masks_later_controller_failure(
    hass, monkeypatch
):
    s, a, data, tick = await adapter(hass)
    await observe(s, a, data, tick, 0)
    s.degrade(ValueError("invalid-checkpoint"))

    def must_not_collect(*args):
        raise AssertionError("Unhealthy evidence must remain quarantined")

    monkeypatch.setattr(s.evidence, "collect", must_not_collect)
    s.boundary("failed_read")
    s.boundary("failed_read")
    assert not s.healthy
    await s.async_stop()
