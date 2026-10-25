"""Prospective measurement policy, causal scoring and real HA persistence."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.hwam_stove import _shadow
from custom_components.hwam_stove._forecast_benchmark import (
    GAP_POLICY,
    HORIZONS,
    MAX_AGE,
    MAX_CHANGES,
    MAX_LEDGER,
    MAX_SAMPLES,
    MODEL,
    NATIVE,
    POLICY,
    SOURCES,
    Benchmark,
    at_horizon,
    meaningful,
    score_stats,
    validate,
    witnesses,
)
from custom_components.hwam_stove._shadow_evidence import Evidence
from custom_components.hwam_stove._shadow_model import ShadowModel
from scripts.check_integrity import before_a06

from .test_a04_shadow import START, sample, warm
from .test_a042_persistence import disk  # noqa: F401 - real atomic HA Store fixture
from .test_o01_diagnostics import inert_diagnostics

BASE = "01c2ca462eeff68c2f9a2036619ec616ca015147"


def analytics(uncertain=False):
    return SimpleNamespace(
        healthy=True,
        online=True,
        state=SimpleNamespace(
            current=SimpleNamespace(start=START, coverage_uncertain=uncertain)
        ),
    )


def empty_model():
    return SimpleNamespace(pending=None, ledger=[], reason="unseen_phase")


def feed(b, n, *, phase="Glow", alarm=False, duration=30, m=None, **changes):
    row = sample(n, phase=phase, alarm=alarm, **changes)
    data = (
        {"time_to_new_fire_wood": timedelta(minutes=duration)}
        if duration is not None
        else {}
    )
    b.observe(row, data, m or empty_model(), analytics())
    return row


def native_cycle(actual=780, error=2, policy=POLICY):
    b = Benchmark(policy)
    feed(b, 0, phase="Ignition")
    for n in range(1, actual):
        feed(b, n, duration=(actual - n) / 6 + error)
    feed(b, actual, alarm=True, duration=999999)
    validate(b.dump())
    return b


def test_native_fields_one_forecast_and_existing_glow_rule():
    s = sample(0, phase="Glow")
    data = {
        "time_to_new_fire_wood": timedelta(hours=2),
        "new_fire_wood_estimate": datetime(1970, 1, 1),
    }
    assert Benchmark.native(s, data) == (s.at.timestamp() + 7200, "available")
    assert Benchmark.native(replace(s, phase="Burn"), data)[0] is None
    for value in (None, True, 2, "30", timedelta(seconds=-1), timedelta.max):
        assert Benchmark.native(s, {"time_to_new_fire_wood": value})[0] is None
    assert (
        Benchmark.native(s, {"time_to_new_fire_wood": timedelta(0)})[0]
        == s.at.timestamp()
    )


def test_first_revisions_and_disappearance_are_retained_without_carry_forward():
    b = Benchmark()
    feed(b, 0, phase="Ignition")
    feed(b, 1, duration=20)
    feed(b, 2, duration=20)  # 10 seconds of forecast drift is below report threshold.
    feed(b, 3, duration=25)
    feed(b, 4, duration=None)
    feed(b, 5, duration=24)
    cycle = b.data["active"]
    t = cycle["sources"][NATIVE]
    assert t["first"]["observed_at"] == sample(1).at.timestamp()
    assert t["revision_count"] == 1 and len(t["revisions"]) == 1
    assert [p["available"] for p in t["periods"]] == [False, True, False, True]
    assert (
        at_horizon(cycle, sample(4).at.timestamp(), NATIVE, sample(9).at.timestamp())[
            "state"
        ]
        == "unavailable"
    )
    assert (
        at_horizon(
            cycle,
            sample(5).at.timestamp() + MAX_AGE + 0.1,
            NATIVE,
            sample(9).at.timestamp(),
        )["state"]
        == "invalidated"
    )
    assert (
        at_horizon(
            cycle, sample(1).at.timestamp() - 1, NATIVE, sample(9).at.timestamp()
        )["state"]
        == "unavailable"
    )
    validate(b.dump())


@pytest.mark.parametrize("h", HORIZONS)
@pytest.mark.parametrize("error", [-2, 0, 3])
def test_exact_horizons_causal_last_observation(h, error):
    b = native_cycle(error=error)
    r = b.data["ledger"][0]["results"][NATIVE]
    result = r["horizons"][str(h)]
    assert result["state"] == "valid"
    assert result["signed_error_minutes"] == pytest.approx(error)
    assert result["observed_at"] == sample(780 - h * 6).at.timestamp()
    if error >= 0:
        assert r["final"]["signed_error_minutes"] == pytest.approx(error)
    else:
        assert r["final"]["state"] == "unavailable"  # Negative duration rejected.
    assert r["final"]["observed_at"] == sample(779).at.timestamp()
    assert all(w["range_minutes"] == 0 for w in r["stability"].values())
    assert b.summary()["preliminary"]
    assert (
        b.summary()["sources"][NATIVE]["horizons"][str(h)]["signed_error_minutes"][
            "mean"
        ]
        is None
    )


def test_horizon_between_samples_uses_earlier_not_nearest_future():
    b = Benchmark()
    feed(b, 0, phase="Ignition")
    feed(b, 1, duration=20)
    feed(b, 2, duration=100)
    r = at_horizon(
        b.data["active"],
        sample(1).at.timestamp() + 9,
        NATIVE,
        sample(30).at.timestamp(),
    )
    assert r["observed_at"] == sample(1).at.timestamp()
    assert r["forecast_at"] == sample(1).at.timestamp() + 1200


def test_actual_edge_no_startup_true_duplicates_or_request_forecast_leakage():
    b = Benchmark()
    feed(b, 0, alarm=True)
    feed(b, 1, alarm=True)
    assert b.data["actual_requests_observed"] == 0
    feed(b, 2)
    before = b.dump()
    feed(b, 2, duration=999999)
    assert before == b.dump()
    feed(b, 3, alarm=True, duration=999999)
    feed(b, 4, alarm=True)
    feed(b, 5)
    feed(b, 6, alarm=True)
    assert b.data["actual_requests_observed"] == 2
    assert len(b.data["ledger"]) == 2
    assert all(r["start_kind"] == "alarm_cleared" for r in b.data["ledger"])
    assert (
        b.data["ledger"][0]["results"][NATIVE]["final"]["forecast_at"]
        == sample(2).at.timestamp() + 1800
    )


@pytest.mark.parametrize("stage", [0, 1, 30, 60, 120, 300, 600, 720])
def test_restart_at_benchmark_stages_preserves_knowledge_without_bridging(stage):
    b = Benchmark()
    feed(b, 0, phase="Ignition")
    for n in range(1, stage + 1):
        feed(b, n)
    old_track = deepcopy(b.data["active"]["sources"])
    b = Benchmark.restore(b.dump())
    assert b.data["ledger"][0]["sources"] == old_track
    assert b.data["ledger"][0]["outcome"] == "gap"
    assert b.data["ledger"][0]["actual_request_at"] is None
    assert b.data["actual_requests_observed"] == 0
    for n in range(stage + 2, stage + 42):
        feed(b, n)
    feed(b, stage + 42, alarm=True)
    record = b.data["ledger"][-1]
    assert record["qualified"] is False
    assert record["results"][NATIVE]["horizons"]["5"]["state"] == "valid"
    assert record["results"][NATIVE]["horizons"]["10"]["state"] == "invalidated"
    snapshot = deepcopy(b.data["ledger"])
    fresh = Benchmark.restore(b.dump())
    assert fresh.data["ledger"] == snapshot
    validate(fresh.dump())


@pytest.mark.parametrize(
    "reason", ["sampling_gap", "failed_read", "shutdown", "command_boundary"]
)
def test_gap_censors_only_open_segment_preserves_completed_scores(reason):
    b = native_cycle(actual=60)
    first = deepcopy(b.data["ledger"][0])
    totals = deepcopy(b.data["sources"])
    feed(b, 61)
    b.gap(reason)
    assert b.data["ledger"][0] == first
    assert b.data["ledger"][1]["reason"] == reason
    assert b.data["sources"][NATIVE]["horizons"] == totals[NATIVE]["horizons"]
    feed(b, 65, alarm=True)
    assert b.data["actual_requests_observed"] == 1


def test_missing_polls_clock_discontinuity_and_partial_session():
    for changed in ({}, {"at": sample(0).at}, {"monotonic": 999}):
        b = Benchmark()
        feed(b, 0, phase="Ignition")
        feed(b, 2, **changed)
        assert b.data["ledger"][0]["outcome"] == "gap"
        assert b.data["active"]["qualified"] is False
    b = Benchmark()
    b.observe(sample(0, phase="Ignition"), {}, empty_model(), analytics(True))
    feed(b, 1, alarm=True)
    assert b.data["ledger"][0]["qualified"] is False


def test_frozen_model_scoring_and_immutable_prediction():
    m, b = ShadowModel(), Benchmark()
    for n in range(100):
        s = sample(n, phase="Ignition" if n == 0 else "Burn")
        m.step(s)
        b.observe(s, {}, m, analytics())
    issued = deepcopy(b.data["active"]["sources"][MODEL]["issued"])
    assert issued["predicted_request_at"] == m.pending["predicted_request_at"]
    s = sample(100, alarm=True)
    m.step(s)
    b.observe(s, {}, m, analytics())
    record = b.data["ledger"][0]
    assert record["sources"][MODEL]["issued"] == issued
    assert (
        record["results"][MODEL]["final"]["signed_error_minutes"]
        == m.ledger[-1]["error_minutes"]
    )
    assert record["results"][NATIVE]["final"]["state"] == "unavailable"
    validate(b.dump())


@pytest.mark.parametrize("change", [{"door": True}, {"phase": "Glow"}])
def test_censored_saynwerk_forecast_never_rescored_as_valid(change):
    m, b = ShadowModel(), Benchmark()
    for n in range(100):
        s = sample(n)
        m.step(s)
        b.observe(s, {}, m, analytics())
    s = sample(100, **change)
    m.step(s)
    b.observe(s, {"time_to_new_fire_wood": timedelta(minutes=2)}, m, analytics())
    for n in range(101, 131):
        s = sample(n, alarm=n == 130)
        m.step(s)
        b.observe(s, {}, m, analytics())
    r = b.data["ledger"][0]["results"][MODEL]
    assert r["horizons"]["10"]["state"] == "invalidated"
    assert r["horizons"]["10"]["signed_error_minutes"] is None
    assert r["final"]["state"] == "unavailable"


def test_prefix_future_independence_and_native_never_influences_model():
    models, benches = [ShadowModel(), ShadowModel()], [Benchmark(), Benchmark()]
    for n in range(80):
        for index, (m, b) in enumerate(zip(models, benches, strict=True)):
            s = sample(n)
            m.step(s)
            b.observe(
                s,
                {"time_to_new_fire_wood": timedelta(minutes=index * 999)},
                m,
                analytics(),
            )
        assert models[0].dump() == models[1].dump()
    prefix = deepcopy(benches[0].data["active"]["sources"][MODEL]["issued"])
    feed(benches[0], 80, m=models[0], alarm=True)
    feed(benches[1], 80, m=models[1], duration=999)
    assert benches[0].data["ledger"][0]["sources"][MODEL]["issued"] == prefix
    assert benches[1].data["active"]["sources"][MODEL]["issued"] == prefix


@pytest.mark.parametrize(
    "date",
    [
        datetime(2026, 3, 29, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
    ],
)
def test_dst_uses_utc_durations_not_controller_local_clock(date):
    b = Benchmark()
    for n in range(361):
        at = date + timedelta(seconds=n * 10)
        s = sample(n, at=at, session=date.isoformat(), phase="Glow", alarm=n == 360)
        b.observe(
            s,
            {
                "time_to_new_fire_wood": timedelta(seconds=3600 - n * 10),
                "new_fire_wood_estimate": at.astimezone(
                    ZoneInfo("Europe/Berlin")
                ).replace(tzinfo=None),
            },
            empty_model(),
            analytics(),
        )
    assert (
        b.data["ledger"][0]["results"][NATIVE]["horizons"]["30"]["signed_error_minutes"]
        == 0
    )


def test_storage_bounds_and_copy_return():
    b = Benchmark()
    for n in range(1300):
        feed(b, n, duration=(20 if n % 2 else 50) if n % 3 else None)
    active = b.data["active"]
    assert len(active["samples"]) <= MAX_SAMPLES
    assert len(active["sources"][NATIVE]["revisions"]) <= MAX_CHANGES
    assert len(active["sources"][NATIVE]["periods"]) <= MAX_CHANGES
    assert active["sources"][NATIVE]["revision_records_evicted"] > 0
    for n in range(1300, 1500):
        feed(b, n, alarm=bool(n % 2))
    assert len(b.data["ledger"]) == MAX_LEDGER and b.data["evicted"] > 0
    checkpoint = b.dump()
    checkpoint["ledger"].clear()
    assert len(b.data["ledger"]) == MAX_LEDGER
    validate(b.dump())


def test_independent_evidence_gate_not_many_requests_from_one_session():
    item = score_stats()
    item["paired"] = 1000
    for _ in range(1000):
        witnesses(item, START.isoformat())
    assert not meaningful(item)
    for n in range(20):
        witnesses(item, (START + timedelta(days=n)).isoformat())
    assert meaningful(item)
    assert len(item["sessions"]) == 20 and len(item["dates"]) == 10


@pytest.mark.parametrize(
    "key",
    [
        "schema",
        "policy",
        "source_ids",
        "ledger",
        "sources",
        "cycles_observed",
        "active",
    ],
)
def test_corrupt_benchmark_rejected(key):
    payload = native_cycle(actual=40).dump()
    payload[key] = {} if key == "active" else None
    with pytest.raises((ValueError, TypeError, KeyError)):
        Benchmark.restore(payload)


async def test_rc6_migration_preserves_exact_store_and_existing_results(disk, hass):  # noqa: F811
    s = disk()
    model = warm()
    model.step(sample(70, alarm=True))
    evidence = Evidence.migrate(model.dump())
    payload = evidence.dump(model)
    store = _shadow.Store(hass, 1, s.rc6_key, atomic_writes=True)
    await store.async_save(payload)
    original = Path(store.path).read_bytes()
    await s.async_load()
    assert s.healthy and s.summary()["schema_version"] == 5
    assert s.model.ledger == model.ledger
    assert s.summary()["valid_scored_predictions"] == 1
    assert s.benchmark.data["cycles_observed"] == 0
    assert Path(store.path).read_bytes() == original
    s.benchmark = native_cycle(actual=40, policy=GAP_POLICY)
    checkpoint = s.benchmark.dump()
    await s._save()
    fresh = disk()
    await fresh.async_load()
    assert fresh.healthy and fresh.benchmark.data["ledger"] == checkpoint["ledger"]
    assert fresh.benchmark.data["sources"] == checkpoint["sources"]
    assert fresh.model.ledger == model.ledger
    assert Path(store.path).read_bytes() == original


async def test_corrupt_v3_benchmark_does_not_fallback_to_valid_rc6(disk, hass):  # noqa: F811
    s = disk()
    await s.async_load()
    path = Path(s.store().path)
    raw = json.loads(path.read_text())
    raw["data"]["benchmark"]["cycles_observed"] = -1
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    await _shadow.Store(hass, 1, s.rc6_key).async_save(Evidence().dump(ShadowModel()))
    fresh = disk()
    await fresh.async_load()
    assert not fresh.healthy and path.read_bytes() == before


async def test_cached_diagnostics_no_network_no_identifiers_no_entities(
    hass, entry, loaded, stove, monkeypatch
):
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    section = result["forecast_benchmark"]["current"]
    assert set(section["sources"]) == set(SOURCES)
    assert section["preliminary"] and section["conclusion"] == "no_source_ranking"
    encoded = json.dumps(section)
    for forbidden in (
        entry.entry_id,
        "ledger",
        "session_id",
        "forecast_at",
        "observed_at",
        "host",
        "ip",
    ):
        assert f'"{forbidden}"' not in encoded
    monkeypatch.setattr(loaded, "_accepted_door", lambda *_: False)
    before = stove.get_data.await_count
    with patch.object(
        loaded.shadow.benchmark, "observe", wraps=loaded.shadow.benchmark.observe
    ) as observe:
        await loaded.async_refresh()
    assert observe.call_count == 1 and stove.get_data.await_count == before + 1


def test_runtime_scope_frozen_model_and_all_entity_definitions():
    root = Path(__file__).resolve().parents[1]
    prefix = "custom_components/hwam_stove/"
    allowed = {"_shadow.py", "diagnostics.py", "manifest.json"}
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASE, "--", prefix], cwd=root, text=True
    ).splitlines()
    for name in names:
        if name.removeprefix(prefix) not in allowed:
            assert before_a06(
                name, (root / name).read_bytes()
            ) == subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=root)
    frozen = (root / prefix / "_shadow_model.py").read_bytes()
    assert (
        hashlib.sha256(frozen).hexdigest()
        == "ac6dd276fe9012a10bb9f707a68fc2dd40a5557c197d6e9ef11a23f89a3fc334"
    )


def test_horizon_max_age_boundary_is_explicit():
    b = Benchmark()
    feed(b, 0)
    cycle = b.data["active"]
    epoch = sample(0).at.timestamp()
    assert at_horizon(cycle, epoch + MAX_AGE, NATIVE, epoch + 100)["state"] == "valid"
    assert (
        at_horizon(cycle, epoch + MAX_AGE + 0.001, NATIVE, epoch + 100)["state"]
        == "invalidated"
    )


def test_no_future_edge_after_missing_or_non_boolean_alarm():
    b = Benchmark()
    feed(b, 0)
    feed(b, 1, alarm=None)
    feed(b, 2, alarm=True)
    assert b.data["actual_requests_observed"] == 0
    assert b.data["ledger"][0]["outcome"] == "gap"
    feed(b, 3, alarm=False)
    feed(b, 4, alarm=True)
    assert b.data["actual_requests_observed"] == 1


def test_benchmark_does_not_reset_model_or_history_when_its_stricter_age_expires():
    m = warm()
    b = Benchmark()
    feed(b, 61, m=m)
    before = deepcopy(m.dump())
    feed(b, 63, m=m)
    assert m.dump() == before
    assert b.data["ledger"][0]["outcome"] == "gap"


def test_qualified_coverage_readiness_and_per_source_accuracy_have_separate_gates():
    b = Benchmark()
    for cycle in range(30):
        session = (START + timedelta(days=cycle)).isoformat()
        # Use separate well-observed heating sessions, not gaps within a session.
        b._previous = None
        for k in range(3):
            n = cycle * 3 + k
            at = datetime.fromisoformat(session) + timedelta(seconds=k * 10)
            feed(
                b,
                n,
                phase="Ignition" if k == 0 else "Glow",
                alarm=k == 2,
                session=session,
                at=at,
                monotonic=n * 10.0,
            )
    validate(b.dump())
    summary = b.summary()
    assert summary["eligible_request_cycles"] == 30 and not summary["preliminary"]
    assert summary["conclusion"] == "no_source_ranking"
    native = summary["sources"][NATIVE]["final"]
    assert native["source_evidence_reached"]
    assert not native["paired_evidence_reached"]
    assert native["signed_error_minutes"]["mean"] == pytest.approx(30 - 1 / 6)
    assert native["paired_signed_error_minutes"]["mean"] is None
    assert not summary["sources"][MODEL]["final"]["source_evidence_reached"]
    assert all(
        not h["source_evidence_reached"]
        for h in summary["sources"][NATIVE]["horizons"].values()
    )


def test_only_paired_errors_enter_paired_summary():
    # Policy-level fixture; no claim these two forecasts overlap on real firmware.
    b = Benchmark()
    feed(b, 0, phase="Ignition")
    pending = deepcopy(warm().pending)
    pending["prediction_at"] = sample(1).at.isoformat()
    pending["predicted_request_at"] = sample(5).at.isoformat()
    m = empty_model()
    m.pending = pending
    feed(b, 1, m=m)
    m.ledger = [
        {
            **pending,
            "outcome": "request_observed",
            "gap_invalid": False,
            "observed_request_at": sample(2).at.isoformat(),
        }
    ]
    m.pending = None
    feed(b, 2, alarm=True, m=m)
    for source in SOURCES:
        stats = b.data["sources"][source]["final"]
        assert (
            stats["paired"]
            == stats["paired_errors"]["n"]
            == stats["eligible_errors"]["n"]
            == 1
        )
    validate(b.dump())


def test_model_only_input_gap_keeps_distinct_immutable_issuances_and_scores():
    m, b = ShadowModel(), Benchmark()
    for n in range(201):
        s = sample(n, door=None if n == 100 else False, alarm=n == 200)
        m.step(s)
        b.observe(s, {}, m, analytics())
    record = b.data["ledger"][0]
    issuances = record["sources"][MODEL]["issuances"]
    assert len(issuances) == 2
    assert issuances[0]["prediction_at"] != issuances[1]["prediction_at"]
    # Same predicted timestamp is not the same immutable prediction cycle.
    assert issuances[0]["predicted_request_at"] == issuances[1]["predicted_request_at"]
    horizons = record["results"][MODEL]["horizons"]
    assert horizons["20"]["state"] == "invalidated"
    assert horizons["5"]["state"] == "valid"
    assert (
        record["results"][MODEL]["final"]["signed_error_minutes"]
        == m.ledger[-1]["error_minutes"]
    )
    assert record["sources"][MODEL]["issued"] == issuances[0]
    assert m.ledger[0]["outcome"] == "missing_observations"
    assert m.ledger[-1]["outcome"] == "request_observed"
    validate(b.dump())
