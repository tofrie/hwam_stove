"""Observed sequences are not validated wood additions; no future inputs."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess

import pytest

from custom_components.hwam_stove import _shadow
from custom_components.hwam_stove._forecast_benchmark import (
    GAP_POLICY as BENCH_POLICY,
    LEGACY_POLICY,
    Benchmark,
)
from custom_components.hwam_stove._interventions import (
    FOLLOWUP,
    LIMIT,
    Interventions,
    validate,
)
from custom_components.hwam_stove._shadow_evidence import Evidence
from custom_components.hwam_stove._shadow_model import MODEL, ShadowModel

from .test_a04_shadow import sample, warm
from .test_a042_persistence import disk  # noqa: F401
from .test_a043_benchmark import analytics, native_cycle


def step(d, n, *, phase="Glow", door=False, alarm=False, **kwargs):
    s = sample(n, phase=phase, door=door, alarm=alarm, **kwargs)
    boundary = d.observe(s, {"time_to_new_fire_wood": timedelta(minutes=20)})
    validate(d.dump())
    return boundary


def test_sampled_early_refill_pattern_remains_possible_not_probable():
    d = Interventions()
    step(d, 0)
    assert step(d, 1, door=True) == "possible_door_intervention"
    step(d, 2, door=True)
    step(d, 3, door=False)
    assert step(d, 4, phase="Burn", temperature=280) == "possible_early_intervention"
    row = next(x for x in d.data["ledger"] if x["kind"] == "door_intervention")
    assert row["glow_open_close_burn"]
    assert row["category"] == "possible_intervention" and row["wood_added"] == "unknown"
    assert row["after"] == sample(0).at.isoformat()
    assert row["observed_at"] == sample(1).at.isoformat()
    assert row["end_observed_at"] == sample(4).at.isoformat()
    assert row["door_closed_at"] == sample(3).at.isoformat()
    assert d.summary()["probable_physical_additions"] is None
    assert d.data["counts"]["observed_request"] == 0


def test_door_only_is_not_refill_and_followup_window_is_only_a_bound():
    d = Interventions()
    step(d, 0)
    step(d, 1, door=True)
    step(d, 2)
    for n in range(3, FOLLOWUP // 10 + 3):
        step(d, n)
    row = d.data["ledger"][0]
    assert (
        row["kind"] == "door_intervention" and row["reason"] == "followup_window_ended"
    )
    assert not row["glow_open_close_burn"] and row["wood_added"] == "unknown"
    assert d.data["counts"]["observed_request"] == 0


def test_unsampled_transition_and_temperature_recovery_never_confirm_refill():
    d = Interventions()
    step(d, 0)
    assert step(d, 1, phase="Burn") == "possible_unsampled_intervention"
    for n in range(2, 20):
        step(d, n, phase="Burn", temperature=250 + n)
    assert d.data["counts"]["glow_to_burn_unsampled"] == 1
    assert d.data["counts"]["door_intervention"] == 0
    assert all(r["wood_added"] == "unknown" for r in d.data["ledger"])


@pytest.mark.parametrize("sample_door", [True, False])
def test_alarm_triggered_request_is_factual_but_wood_action_unknown(sample_door):
    d = Interventions()
    step(d, 0)
    step(d, 1, alarm=True)
    step(d, 2, alarm=True, door=sample_door)
    step(d, 3, alarm=True)
    step(d, 4, phase="Burn")
    assert d.data["counts"]["observed_request"] == 1
    row = next(r for r in d.data["ledger"] if r["kind"] == "observed_request")
    assert (
        row["category"] == "observed_refill_request" and row["wood_added"] == "unknown"
    )


@pytest.mark.parametrize("stage", [0, 1, 2, 3])
def test_restart_at_each_candidate_stage_preserves_but_never_bridges(stage):
    d = Interventions()
    for n in range(stage + 1):
        step(d, n, door=n in (1, 2))
    before = deepcopy(d.data["ledger"])
    d = Interventions.restore(d.dump())
    assert d.data["ledger"][: len(before)] == before
    assert d.previous is None and d.data["pending"] is None
    if stage:
        assert d.data["ledger"][-1]["coverage"] == "incomplete"
    step(d, stage + 20, phase="Burn", alarm=True)
    assert d.data["counts"]["observed_request"] == 0
    assert not any(r["glow_open_close_burn"] for r in d.data["ledger"])
    again = Interventions.restore(d.dump())
    assert again.data == d.data


@pytest.mark.parametrize(
    "reason", ["failed_read", "sampling_gap", "shutdown", "command_boundary"]
)
def test_gaps_preserve_evidence_and_avoid_duplicate_edges(reason):
    d = Interventions()
    step(d, 0)
    step(d, 1, door=True)
    d.gap(reason)
    checkpoint = d.dump()
    d.gap(reason)
    assert d.dump() == checkpoint
    step(d, 2, phase="Burn", alarm=True)
    assert d.data["counts"]["observed_request"] == 0
    assert d.data["counts"]["door_intervention"] == 1
    assert d.data["ledger"][0]["coverage"] == "incomplete"
    step(d, 3, alarm=False)
    step(d, 4, alarm=True)
    assert d.data["counts"]["observed_request"] == 1


def test_no_future_leakage_and_duplicate_samples():
    a, b = Interventions(), Interventions()
    for n in range(4):
        step(a, n, door=n in (1, 2))
        step(b, n, door=n in (1, 2))
        before = a.dump()
        step(a, n, alarm=True, phase="Burn")
        assert a.dump() == b.dump() == before
    step(a, 4, phase="Burn")
    step(b, 4, phase="Glow")
    assert a.data["counts"]["door_intervention"] == 1
    assert b.data["counts"]["door_intervention"] == 0


def test_multiple_requests_bounded_persistence_and_no_raw_identifiers():
    d = Interventions()
    for n in range(300):
        step(d, n, alarm=bool(n % 2))
    assert d.data["counts"]["observed_request"] == 150
    assert len(d.data["ledger"]) == LIMIT and d.data["evicted"] == 150 - LIMIT
    restored = Interventions.restore(d.dump())
    assert restored.data == d.data
    text = json.dumps(restored.summary())
    assert all(
        k not in text for k in ("session_id", "ledger", "host", "mac", "observed_at")
    )


@pytest.mark.parametrize("key", ["counts", "ledger", "pending", "policy", "schema"])
def test_bad_store_never_silently_resets(key):
    d = Interventions().dump()
    d[key] = {}
    with pytest.raises((ValueError, TypeError)):
        Interventions.restore(d)


def test_separate_censoring_categories_preserve_original_forecast():
    for reason, category in [
        ("possible_door_intervention", "possible_intervention"),
        ("possible_unsampled_intervention", "possible_intervention"),
        ("failed_read", "communication_or_missing_observation"),
        ("shutdown", "incomplete_window"),
    ]:
        b = Benchmark()
        s = sample(0, phase="Glow")
        b.observe(
            s,
            {"time_to_new_fire_wood": timedelta(minutes=20)},
            ShadowModel(),
            analytics(),
        )
        first = deepcopy(b.data["active"]["sources"])
        b.gap(reason)
        assert b.data["ledger"][0]["sources"] == first
        assert b.data["ledger"][0]["actual_request_at"] is None
        assert b.data["ledger"][0]["results"] == {}
        assert b.summary()["censoring_counts"][category] == 1


async def test_rc7_migration_archives_old_policy_and_preserves_model(disk, hass):  # noqa: F811
    s = disk()
    model = warm()
    model.step(sample(70, alarm=True))
    old_b = native_cycle(actual=40).dump()
    old_b.pop("censoring_counts")
    old_b["schema"], old_b["policy"] = 1, LEGACY_POLICY
    for row in old_b["ledger"]:
        row["policy"] = LEGACY_POLICY
    payload = Evidence.migrate(model.dump()).dump(model)
    payload.update(schema=3, benchmark=old_b)
    old = _shadow.Store(hass, 1, s.rc7_key, atomic_writes=True)
    await old.async_save(payload)
    original = Path(old.path).read_bytes()
    await s.async_load()
    assert s.healthy and s.summary()["schema_version"] == 5
    assert s.legacy_benchmark["ledger"] == old_b["ledger"]
    assert s.legacy_benchmark["sources"] == old_b["sources"]
    assert (
        s.benchmark.data["policy"] == BENCH_POLICY
        and s.benchmark.data["cycles_observed"] == 0
    )
    assert s.model.ledger == model.ledger
    assert Path(old.path).read_bytes() == original
    step(s.interventions, 0)
    step(s.interventions, 1, door=True)
    await s._save()
    again = disk()
    await again.async_load()
    assert again.healthy and again.model.ledger == model.ledger
    assert again.legacy_benchmark == s.legacy_benchmark
    assert again.interventions.data["ledger"][-1]["reason"] == "restart"
    assert Path(old.path).read_bytes() == original


async def test_corrupt_v4_never_overwrites_or_falls_back(disk, hass):  # noqa: F811
    s = disk()
    await s.async_load()
    p = Path(s.store().path)
    payload = json.loads(p.read_text())
    payload["data"]["interventions"]["pending"] = {}
    p.write_text(json.dumps(payload))
    before = p.read_bytes()
    await _shadow.Store(hass, 1, s.rc7_key).async_save(Evidence().dump(ShadowModel()))
    fresh = disk()
    await fresh.async_load()
    assert not fresh.healthy and p.read_bytes() == before


def test_frozen_formula_dependency_and_registry_descriptions_unchanged():
    root = Path(__file__).resolve().parents[1]
    base = "b8b5c2490756fb55553e2185841167b4e37e65ab"
    for name in [
        "_shadow_model.py",
        "_analytics_model.py",
        "_request_statistics.py",
        "binary_sensor.py",
        "migration.py",
        "number.py",
        "switch.py",
        "time.py",
        "datetime.py",
        "button.py",
        "config_flow.py",
    ]:
        path = "custom_components/hwam_stove/" + name
        assert (root / path).read_bytes() == subprocess.check_output(
            ["git", "show", base + ":" + path], cwd=root
        )
    manifest = json.loads(
        (root / "custom_components/hwam_stove/manifest.json").read_text()
    )
    assert manifest["requirements"] == ["saynwerk-pystove==0.3.0rc3"]


async def test_shadow_adapter_censors_native_intervention_not_a04_history(hass):
    from .test_a041_diagnostics import adapter_inputs

    tick = [0.0]
    s = _shadow.Shadow(
        hass,
        "intervention",
        clock=lambda: tick[0],
        utcnow=lambda: sample(int(tick[0] / 10)).at,
    )
    await s.async_load()
    a, data = adapter_inputs()
    data.update(phase="Glow", time_to_new_fire_wood=timedelta(minutes=20))
    for n, door in [(0, False), (1, True), (2, False), (3, False)]:
        tick[0] = n * 10
        if n == 3:
            data["phase"] = "Burn"
        await s.async_observe(n, s.read_started(), a, data, door)
    assert s.healthy
    assert s.benchmark.data["censoring_counts"]["possible_intervention"] >= 1
    rows = s.benchmark.data["ledger"]
    assert all(
        row["actual_request_at"] is None and row["results"] == {} for row in rows
    )
    assert s.interventions.data["counts"]["door_intervention"] == 1
    assert s.evidence.data["models"][MODEL]["scored"] == 0
    await s.async_stop()


async def test_full_observed_expiry_is_separate_from_gap_or_intervention(hass):
    from .test_a041_diagnostics import adapter_inputs

    tick = [0.0]
    s = _shadow.Shadow(
        hass,
        "expiry",
        clock=lambda: tick[0],
        utcnow=lambda: sample(int(tick[0] / 10)).at,
    )
    await s.async_load()
    a, data = adapter_inputs()
    for n in range(245):
        tick[0] = n * 10
        data["stove_temperature"] = sample(n).temperature
        await s.async_observe(n, s.read_started(), a, data, False)
    assert s.healthy
    assert s.interventions.data["counts"]["model_expiry"] == 1
    assert s.interventions.data["counts"]["coverage_gap"] == 0
    assert s.interventions.data["counts"]["door_intervention"] == 0
    assert s.model.ledger[-1]["outcome"] == "no_request_within_30m"
    await s.async_stop()
