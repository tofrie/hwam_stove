"""A04.1 observational reasons, frozen rc4 behavior, and offline HA diagnostics."""

import ast
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import random
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.hwam_stove._shadow import Shadow
from custom_components.hwam_stove._shadow_model import (
    ABSTENTION_REASONS,
    CALIBRATED_AT,
    REASON_COUNTER_LIMIT,
    ShadowModel,
)

from .helpers import COMMANDS, registry_entries
from .test_a04_shadow import START, sample, warm
from .test_o01_diagnostics import inert_diagnostics

BASE = "9f2e4c9df7158287d27e25b480e0677b777e694c"
ROOT = Path(__file__).resolve().parents[1]


def exercise(reason):
    m = ShadowModel()
    if reason == "startup":
        return m
    if reason == "restart":
        return ShadowModel.restore(m.dump())
    if reason in {
        "shutdown",
        "failed_read",
        "command_boundary",
        "sampling_gap",
        "storage_failure",
    }:
        m.gap(reason)
    elif reason == "clock_discontinuity":
        m.step(sample(0, at=START.replace(tzinfo=None)))
    elif reason == "session_change":
        m.step(sample(0))
        m.step(sample(1, session=(START + timedelta(days=1)).isoformat()))
    elif reason == "missing_observations":
        m.step(sample(0, door=None))
    elif reason == "partial_session":
        m.step(sample(0, qualified=False))
    elif reason == "session_end":
        m.step(sample(0, qualified=False, phase="Standby"))
    elif reason == "door_open":
        m.step(sample(0, door=True))
    elif reason == "unseen_phase":
        m.step(sample(0, phase="Glow"))
    elif reason == "insufficient_history":
        m.step(sample(0))
    elif reason == "before_calibration":
        m.step(sample(0, at=CALIBRATED_AT - timedelta(days=1)))
    elif reason in {"not_cooling", "beyond_range"}:
        for n in range(61):
            m.step(
                sample(n, temperature=300 if reason == "not_cooling" else 400 - n / 6)
            )
    elif reason == "request_active":
        m.step(sample(0, alarm=True))
    elif reason == "prediction_expired":
        m = warm()
        for n in range(61, 182):
            m.step(sample(n))
    elif reason == "cycle_spent":
        m.step(sample(0, door=True))
        m.step(sample(1))
    else:
        raise AssertionError(f"Unexercised reason: {reason}")
    return m


@pytest.mark.parametrize("reason", sorted(ABSTENTION_REASONS))
def test_every_reason_is_reachable_and_counted(reason):
    m = exercise(reason)
    d = m.abstention_diagnostics()
    assert d["reason_transition_counts"][reason] == 1
    assert d["reason"] == m.reason
    assert d["reason_category"] == ABSTENTION_REASONS[m.reason]
    if reason != "session_change":
        assert d["reason"] == reason
    else:
        assert d["last_coverage_boundary"] == reason
        assert d["reason"] == "insufficient_history"


def test_normal_transitions_and_request_edge_suppression():
    m = ShadowModel()
    m.step(sample(0, phase="Ignition"))
    assert m.reason == "unseen_phase"
    for n in range(1, 62):
        m.step(sample(n, temperature=300))
    assert m.reason == "not_cooling"
    m.step(sample(62, alarm=True))
    assert m.reason == "request_active"
    assert m.abstention_diagnostics()["last_unpredicted_request_prior_reason"] == (
        "not_cooling"
    )
    for n in range(63, 80):
        m.step(sample(n, alarm=True))
    assert m.summary()["requests_without_prediction"] == 1
    assert m.abstention_diagnostics()["reason_transition_counts"]["request_active"] == 1
    m.step(sample(80))
    assert m.reason == "insufficient_history"
    m.step(sample(81, door=True))
    m.step(sample(82))
    assert m.reason == "cycle_spent"
    m.step(sample(83, alarm=True))
    assert m.abstention_diagnostics()["last_unpredicted_request_prior_reason"] == (
        "cycle_spent"
    )
    m.step(sample(84))
    assert m.reason == "insufficient_history"


@pytest.mark.parametrize(
    "changes,missing",
    [
        ({"phase": "unknown"}, ["phase"]),
        ({"alarm": 1}, ["refill_alarm"]),
        ({"door": None}, ["door_open"]),
        ({"temperature": float("nan")}, ["stove_temperature"]),
        ({"oxygen": None}, ["oxygen_level"]),
        ({"valves": (1, 2)}, ["valve_positions"]),
        ({"door": None, "oxygen": None}, ["door_open", "oxygen_level"]),
    ],
)
def test_missing_inputs_are_allowlisted_names_not_values(changes, missing):
    m = ShadowModel()
    m.step(sample(0, **changes))
    assert m.abstention_diagnostics()["missing_inputs"] == missing
    m.step(sample(1))
    assert m.abstention_diagnostics()["missing_inputs"] == []


def test_bounded_copy_transition_counters_not_polls_or_store_data():
    m = exercise("not_cooling")
    before = deepcopy(m.dump())
    m.step(sample(60))  # Duplicate, even though its temperature differs.
    assert m.dump() == before
    d = m.abstention_diagnostics()
    assert d["counter_scope"] == "current_runtime"
    assert d["reason_transition_counts"]["not_cooling"] == 1
    d["reason_transition_counts"]["not_cooling"] = 999
    assert m.abstention_diagnostics()["reason_transition_counts"]["not_cooling"] == 1
    m._reason_counts["sampling_gap"] = REASON_COUNTER_LIMIT
    m.gap("sampling_gap")
    assert m.abstention_diagnostics()["reason_transition_counts"]["sampling_gap"] == (
        REASON_COUNTER_LIMIT
    )
    assert set(m.dump()) == {
        "schema",
        "model_version",
        "ledger",
        "pending",
        "evicted",
        "sessions",
    }
    assert len(m.abstention_diagnostics()["reason_transition_counts"]) == 21


@pytest.fixture(scope="module")
def rc4():
    """Load exact approved rc4 model locally for behavioral differential tests."""
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:custom_components/hwam_stove/_shadow_model.py"],
        cwd=ROOT,
    )
    module = ModuleType("a04_rc4_reference")
    sys.modules[module.__name__] = module
    exec(compile(source, "a04_rc4_reference", "exec"), module.__dict__)
    yield module
    del sys.modules[module.__name__]


def assert_same(a, b):
    assert a.dump() == b.dump()
    assert a.summary() == b.summary()
    for field in (
        "state",
        "reason",
        "predicted",
        "minutes",
        "revision",
        "spent",
        "cycle_recorded",
        "previous",
        "last_request",
    ):
        assert getattr(a, field) == getattr(b, field), field
    assert list(a.history) == list(b.history)


def test_frozen_behavior_and_records_match_rc4_at_every_step(rc4):
    new, old = ShadowModel(), rc4.ShadowModel()
    rng = random.Random(41)
    for n in range(1500):
        change = {}
        # First exercise successful immutable prediction/scoring then all guards.
        if n == 80:
            change["alarm"] = True
        if n > 160:
            change = dict(
                phase=rng.choice(["Burn"] * 15 + ["Glow", "Standby", "Ignition"]),
                door=rng.choice([False] * 15 + [True, None]),
                alarm=rng.choice([False] * 15 + [True, None]),
                temperature=rng.choice([250, 229, 300, None]),
                qualified=rng.choice([True] * 15 + [False]),
            )
        s = sample(n, **change)
        if n in (90, 111, 400):
            new.gap("sampling_gap")
            old.gap("sampling_gap")
        new.step(s)
        old.step(s)
        assert_same(new, old)
        # Diagnostics are read-only and cannot affect the next forecast/Store.
        json.dumps(new.abstention_diagnostics(), allow_nan=False)
        assert_same(new, old)
    assert_same(ShadowModel.restore(new.dump()), rc4.ShadowModel.restore(old.dump()))


def test_only_diagnostic_statements_added_to_frozen_model():
    """Strip the named, isolated observations: every old AST decision is identical."""
    path = "custom_components/hwam_stove/_shadow_model.py"
    previous = ast.parse(
        subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    )
    current = ast.parse((ROOT / path).read_text())
    added = {
        "_reason_counts",
        "_last_diagnostic_reason",
        "_last_coverage_boundary",
        "_last_unpredicted_request_prior_reason",
        "_missing_inputs",
    }

    class RemoveDiagnostics(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.name in {"_diagnostic_reason", "abstention_diagnostics"}:
                return None
            return self.generic_visit(node)

        def visit_Assign(self, node):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {
                "REASON_COUNTER_LIMIT",
                "ABSTENTION_REASONS",
            }:
                return None
            if isinstance(target, ast.Attribute) and target.attr in added:
                return None
            return self.generic_visit(node)

        def visit_Expr(self, node):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "_diagnostic_reason"
            ):
                return None
            return self.generic_visit(node)

    assert ast.dump(RemoveDiagnostics().visit(current)) == ast.dump(previous)


def test_reasons_are_causal_no_future_leakage():
    a, b = ShadowModel(), ShadowModel()
    for n in range(80):
        a.step(sample(n))
        b.step(sample(n))
        assert a.abstention_diagnostics() == b.abstention_diagnostics()
    saved = a.abstention_diagnostics()
    a.step(sample(80, alarm=True))
    b.step(sample(80, temperature=500))
    assert saved["reason"] == "available"
    assert saved["reason_transition_counts"]["request_active"] == 0
    assert a.reason != b.reason


def test_diagnostic_detail_cannot_fail_on_short_circuited_missing_input(rc4):
    new, old = ShadowModel(), rc4.ShadowModel()
    # Original usable() stops at the missing phase before inspecting valves.
    s = sample(0, phase=None, valves=None)
    new.step(s)
    old.step(s)
    assert_same(new, old)
    assert new.abstention_diagnostics()["missing_inputs"] == [
        "phase",
        "valve_positions",
    ]


def test_rc5_adapter_only_added_diagnostic_assignments():
    path = "custom_components/hwam_stove/_shadow.py"
    previous = ast.parse(
        subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    )
    current = ast.parse(subprocess.check_output(
        ["git", "show", f"bd2a9368218efad7946b425dcc129d3e7d2dc881:{path}"],
        cwd=ROOT,
    ))

    class RemoveDiagnostics(ast.NodeTransformer):
        def visit_Assign(self, node):
            target = node.targets[0]
            if isinstance(target, ast.Attribute) and target.attr in {
                "observation_disposition",
                "session_qualification",
            }:
                return None
            return self.generic_visit(node)

    assert ast.dump(RemoveDiagnostics().visit(current)) == ast.dump(previous)


async def test_legacy_store_preserved_and_new_reason_counts_checkpointed(hass, rc4):
    old = warm(rc4.ShadowModel())
    old.step(sample(70, alarm=True))
    payload = old.dump()
    s = Shadow(hass, "upgrade")
    await s.store().async_save(payload)
    await s.async_load()
    assert s.model.dump() == payload
    assert s.model.ledger == old.ledger
    assert s.abstention_diagnostics()["historical_reason_counts_unknown"]
    s.model.step(sample(0, phase="Glow"))
    await s._save()
    assert s.healthy
    again = Shadow(hass, "upgrade")
    await again.async_load()
    assert again.healthy
    assert again.model.ledger == old.ledger
    counts = again.abstention_diagnostics()["reason_transition_counts"]
    assert counts["unseen_phase"] == 1


def adapter_inputs():
    current = SimpleNamespace(
        first_observed=START.isoformat(),
        start=START.isoformat(),
        coverage_uncertain=False,
        elapsed=0,
    )
    analytics = SimpleNamespace(
        healthy=True, online=True, state=SimpleNamespace(current=current)
    )
    data = dict(
        phase="Burn",
        refill_alarm=False,
        stove_temperature=259,
        oxygen_level=15,
        valve1_position=20,
        valve2_position=30,
        valve3_position=40,
    )
    return analytics, data


async def test_transient_sampling_gap_stays_visible_after_recovery(hass):
    tick = [0]
    s = Shadow(
        hass,
        "gap",
        clock=lambda: tick[0],
        utcnow=lambda: START + timedelta(seconds=tick[0]),
    )
    await s.async_load()
    a, data = adapter_inputs()
    await s.async_observe(0, s.read_started(), a, data, False)
    tick[0] = 12
    await s.async_observe(1, s.read_started(), a, data, False)
    d = s.model.abstention_diagnostics()
    assert d["reason"] == "insufficient_history"
    assert d["last_coverage_boundary"] == "sampling_gap"
    assert d["reason_transition_counts"]["sampling_gap"] == 1
    assert d["history_samples"] == 1 and d["history_span_seconds"] == 0


@pytest.mark.parametrize(
    "change,expected",
    [
        ("healthy", "analytics_unhealthy"),
        ("online", "analytics_offline"),
        ("current", "no_current_session"),
        ("start", "session_start_unobserved"),
        ("coverage_uncertain", "session_coverage_uncertain"),
        (None, "qualified"),
    ],
)
async def test_session_qualification_guards(hass, change, expected):
    s = Shadow(hass, "qualified")
    await s.async_load()
    a, data = adapter_inputs()
    if change in {"healthy", "online"}:
        setattr(a, change, False)
    elif change == "current":
        a.state.current = None
    elif change is not None:
        setattr(
            a.state.current, change, True if change == "coverage_uncertain" else None
        )
    await s.async_observe(0, s.read_started(), a, data, False)
    assert s.session_qualification == expected


@pytest.mark.parametrize(
    "guard",
    [
        "closed",
        "not_ready",
        "unhealthy",
        "duplicate_or_older_observation",
        "pre_boundary_read",
    ],
)
async def test_adapter_suppression_is_distinct_from_model_abstention(hass, guard):
    s = Shadow(hass, "guard")
    if guard != "not_ready":
        await s.async_load()
    token = s.read_started()
    if guard == "closed":
        await s.async_stop()
    elif guard == "unhealthy":
        s.degrade(OSError("private"))
    elif guard == "duplicate_or_older_observation":
        s._sequence = 0
    elif guard == "pre_boundary_read":
        s.boundary()
    before = s.model.dump()
    await s.async_observe(0, token, None, {}, None)
    assert s.observation_disposition == guard
    assert s.model.dump() == before


async def test_ha_cached_reasons_privacy_network_and_failed_coordinator(
    hass,
    loaded,
    entry,
    stove,
    monkeypatch,
):
    loaded.shadow.model = exercise("not_cooling")
    before = stove.get_data.await_count
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    d = result["shadow"]
    assert d["coordinator_input_state"] == "last_update_successful"
    assert d["abstention"]["reason"] == "not_cooling"
    assert d["abstention"]["reason_category"] == "model"
    assert result["integration"]["version"] == "1.0.0rc7"
    assert stove.get_data.await_count == before
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()
    assert len(registry_entries(hass, entry.entry_id)) == 49
    encoded = json.dumps(d)
    for forbidden in (entry.entry_id, "session_id", "ledger", "prediction_at", "host"):
        assert forbidden not in encoded
    stove.get_data.side_effect = TimeoutError()
    await loaded.async_refresh()
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["shadow"]["coordinator_input_state"] == "update_failed"
    assert result["shadow"]["abstention"]["reason"] == "failed_read"
    assert result["shadow"]["abstention"]["reason_category"] == "coverage"
    assert (
        result["shadow"]["abstention"]["reason_transition_counts"]["failed_read"] == 1
    )


async def test_reload_preserves_all_49_identities_and_evaluation_rows(
    hass,
    loaded,
    entry,
    stove,
    monkeypatch,
):
    before = {
        r.id: (r.entity_id, r.unique_id, r.device_id, r.disabled_by, r.hidden_by)
        for r in registry_entries(hass, entry.entry_id)
    }
    m = warm()
    m.step(sample(70, alarm=True))
    loaded.shadow.model = m
    await loaded.shadow._save()
    ledger = deepcopy(m.ledger)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    fresh = hass.data["hwam_stove"]["stoves"][entry.entry_id]
    assert fresh.shadow.model.ledger == ledger
    d = (await inert_diagnostics(hass, entry, monkeypatch, fresh))["shadow"][
        "abstention"
    ]
    assert d["reason_transition_counts"]["request_active"] == 1
    assert d["reason_transition_counts"]["restart"] == 1
    after = {
        r.id: (r.entity_id, r.unique_id, r.device_id, r.disabled_by, r.hidden_by)
        for r in registry_entries(hass, entry.entry_id)
    }
    assert before == after and len(after) == 49


async def test_cancelled_read_is_coverage_loss_with_reported_ha_flag(
    hass,
    loaded,
    entry,
    stove,
    monkeypatch,
):
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
    d = (await inert_diagnostics(hass, entry, monkeypatch, loaded))["shadow"]
    assert d["abstention"]["reason"] == "failed_read"
    assert d["abstention"]["reason_category"] == "coverage"
    assert d["abstention"]["reason_transition_counts"]["failed_read"] == 1
    # Report HA's cached flag honestly, without treating it as a fresh-read proof.
    expected = (
        "last_update_successful" if loaded.last_update_success else "update_failed"
    )
    assert d["coordinator_input_state"] == expected


@pytest.mark.parametrize("kind", ["runtime_missing", "cache_missing", "unknown"])
async def test_coordinator_input_absence_is_not_a_model_decision(
    hass,
    loaded,
    entry,
    monkeypatch,
    kind,
):
    if kind == "runtime_missing":
        with patch.dict(hass.data, {"hwam_stove": None}):
            d = (await inert_diagnostics(hass, entry, monkeypatch))["shadow"]
        assert d["abstention"] == {}
    else:
        monkeypatch.setattr(loaded, "data", None if kind == "cache_missing" else {})
        if kind == "unknown":
            monkeypatch.setattr(loaded, "last_update_success", None)
        d = (await inert_diagnostics(hass, entry, monkeypatch, loaded))["shadow"]
    assert d["coordinator_input_state"] == kind
