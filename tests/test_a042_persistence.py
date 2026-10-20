"""Deterministic A04.2 migration, durable evidence and post-gap ownership."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import storage
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
import pytest

from custom_components.hwam_stove import _shadow
from custom_components.hwam_stove._analytics import Analytics
from custom_components.hwam_stove._shadow import Shadow
from custom_components.hwam_stove._shadow_evidence import (
    BINS,
    Evidence,
    cohort,
    quantile_bounds,
)
from custom_components.hwam_stove._shadow_model import LIMIT, MODEL, ShadowModel
from scripts.check_integrity import before_a06

from .test_a04_shadow import START, sample, warm
from .test_a041_diagnostics import adapter_inputs

BASE = "bd2a9368218efad7946b425dcc129d3e7d2dc881"
ROOT = Path(__file__).resolve().parents[1]
REAL_LOAD = Store._async_load
REAL_WRITE = Store._async_write_data


def restore(evidence, model):
    payload = evidence.dump(model)
    new = Evidence.restore(payload["evidence"], payload["engine"])
    m = ShadowModel.restore(payload["engine"])
    new.gap("restart")
    new.collect(m)
    return new, m


def step(e, m, observation, analytics):
    e.observe(observation, analytics)
    m.step(observation)
    e.collect(m)


def test_frozen_engine_and_unrelated_runtime_byte_identical():
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASE, "custom_components/hwam_stove"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    allowed = {"_shadow.py", "_shadow_sensor.py", "diagnostics.py", "manifest.json"}
    for name in names:
        if Path(name).name not in allowed:
            assert before_a06(
                name, (ROOT / name).read_bytes()
            ) == subprocess.check_output(
                ["git", "show", f"{BASE}:{name}"],
                cwd=ROOT,
            ), name
    before = json.loads(
        subprocess.check_output(
            ["git", "show", f"{BASE}:custom_components/hwam_stove/manifest.json"],
            cwd=ROOT,
        )
    )
    after = json.loads(
        (ROOT / "custom_components/hwam_stove/manifest.json").read_text()
    )
    assert after == {**before, "version": "1.0.0rc8"}
    assert after["requirements"] == ["saynwerk-pystove==0.3.0rc3"]


@pytest.mark.parametrize("point", [0, 59, 60, 61, 179, 180, 181, 190])
@pytest.mark.parametrize("phase", ["Ignition", "Burn", "Glow"])
def test_restart_at_each_cycle_point_and_phase(point, phase):
    e, m = Evidence(), ShadowModel()
    analytics, _ = adapter_inputs()
    for n in range(point + 1):
        step(e, m, sample(n, phase=phase, alarm=n >= 180), analytics)
    original = deepcopy(m.dump())
    totals = deepcopy(e.data["models"])
    for _ in range(4):
        e, m = restore(e, m)
        assert m.pending is None and m.predicted is None and not m.history
        assert m.ledger[: len(original["ledger"])] == original["ledger"]
        if original["pending"]:
            row = m.ledger[-1]
            assert row["prediction_at"] == original["pending"]["prediction_at"]
            assert (
                row["predicted_request_at"]
                == original["pending"]["predicted_request_at"]
            )
            assert row["model_version"] == MODEL
            assert row["error_minutes"] is None and row["gap_invalid"]
            assert e.data["annotations"][-1]["status"] == "invalidated_due_to_gap"
        assert e.data["active"]["session"] == sample(0).session
        assert e.data["models"][MODEL]["scored"] == totals[MODEL]["scored"]
        assert e.data["models"][MODEL]["issued"] == totals[MODEL]["issued"]
        assert e.data["models"][MODEL]["evidence_sessions"] == 1
    assert len(m.ledger) == len(original["ledger"]) + bool(original["pending"])


@pytest.mark.parametrize(
    "before,after", [(False, False), (False, True), (True, False), (True, True)]
)
async def test_alarm_across_restart_matches_a021(hass, before, after):
    tick = [0]
    args = {
        "clock": lambda: tick[0],
        "utcnow": lambda: START + timedelta(seconds=tick[0]),
    }
    a, s = Analytics(hass, "alarm", **args), Shadow(hass, "alarm", **args)
    await a.async_load()
    await s.async_load()
    _, data = adapter_inputs()
    data.update(phase="Ignition", refill_alarm=before)
    await a.async_observe(a.read_started(), data)
    await s.async_observe(1, s.read_started(), a, data, False)
    session = a.state.current.first_observed
    await a.async_stop()
    await s.async_stop()
    tick[0] = 300
    a, s = Analytics(hass, "alarm", **args), Shadow(hass, "alarm", **args)
    await a.async_load()
    await s.async_load()
    data.update(phase="Burn", refill_alarm=after)
    await a.async_observe(a.read_started(), data)
    await s.async_observe(1, s.read_started(), a, data, False)
    assert a.state.current.first_observed == session
    assert a.state.current.coverage_uncertain
    assert a.state.requests == 0 and not s.model.ledger
    assert s.evidence.data["last_alarm"] is after
    assert s.evidence.data["gaps"][-1]["after"] == START.isoformat()
    assert (
        s.evidence.data["gaps"][-1]["through"]
        == (START + timedelta(seconds=300)).isoformat()
    )
    assert s.summary()["evidence_sessions"] == 1


async def test_post_restart_rebuilds_window_without_clearing_a02_partial(hass):
    tick = [0]
    args = {
        "clock": lambda: tick[0],
        "utcnow": lambda: START + timedelta(seconds=tick[0]),
    }
    s = Shadow(hass, "resumption", **args)
    await s.async_load()
    a, data = adapter_inputs()
    for n in range(61):
        tick[0] = n * 10
        data["stove_temperature"] = 259 - n / 6
        await s.async_observe(n, s.read_started(), a, data, False)
    original = deepcopy(s.model.pending)
    assert original
    tick[0] = 1000
    s = Shadow(hass, "resumption", **args)
    await s.async_load()
    a.state.current.coverage_uncertain = True
    for n in range(61):
        tick[0] = 1000 + n * 10
        data["stove_temperature"] = 259 - n / 6
        await s.async_observe(n, s.read_started(), a, data, False)
        assert a.state.current.coverage_uncertain
        if n < 60:
            assert s.model.predicted is None
    assert s.healthy and s.model.predicted is not None
    assert s.model.pending["prediction_at"] != original["prediction_at"]
    assert s.model.ledger[0]["prediction_at"] == original["prediction_at"]
    assert s.summary()["invalidated_predictions"] == 1
    assert s.summary()["evidence_sessions"] == 1
    assert s.evidence.data["active"]["censored"]
    assert s.summary()["per_model"][MODEL]["eligible_request_cycles"] == 0


@pytest.mark.parametrize(
    "gap",
    [
        "failed_read",
        "sampling_gap",
        "command_boundary",
        "clock_discontinuity",
        "missing_observations",
    ],
)
def test_gaps_clear_anchors_not_history(gap):
    e, m = Evidence(), warm()
    e.collect(m)
    pending = deepcopy(m.pending)
    e.gap(gap)
    m.gap(gap)
    e.collect(m)
    for _ in range(3):
        e, m = restore(e, m)
    assert len(m.ledger) == 1
    assert m.ledger[0]["prediction_at"] == pending["prediction_at"]
    assert m.ledger[0]["error_minutes"] is None
    assert e.data["models"][MODEL]["invalidated"] == 1
    assert e.data["models"][MODEL]["issued"] == 1


def test_lifetime_outcomes_errors_and_reasons_survive_eviction():
    e, m = Evidence(), ShadowModel()
    counts = {"scored": 0, "negative": 0, "invalidated": 0, "without_prediction": 0}
    for n in range(LIMIT + 40):
        row = deepcopy(warm().pending)
        shift = timedelta(days=n)
        for key in ("session_id", "prediction_at", "predicted_request_at"):
            from datetime import datetime

            row[key] = (datetime.fromisoformat(row[key]) + shift).isoformat()
        m.pending = row
        e.collect(m)
        if n % 3 == 0:
            m.close("request_observed", sample(180).at + shift, sample(179).at + shift)
            counts["scored"] += 1
        elif n % 3 == 1:
            m.close("no_request_within_30m")
            counts["negative"] += 1
        else:
            m.gap("failed_read")
            counts["invalidated"] += 1
        e.collect(m)
        e, m = restore(e, m)
    assert len(m.ledger) == LIMIT and m.evicted == 40
    totals = e.data["models"][MODEL]
    for key, value in counts.items():
        assert totals[key] == value
    assert sum(totals["errors"]) == counts["scored"]
    assert totals["issued"] == LIMIT + 40
    assert e.data["reasons"]["failed_read"] == counts["invalidated"]
    assert len(json.dumps(e.dump(m))) < 150_000


def completed_session(e, m, day, *, gap=False, requests=(180, 361)):
    a, _ = adapter_inputs()
    begin = START + timedelta(days=day)
    a.state.current.first_observed = a.state.current.start = begin.isoformat()
    for n in range(363):
        s = sample(
            n,
            at=begin + timedelta(seconds=n * 10),
            session=begin.isoformat(),
            monotonic=day * 86400 + n * 10,
            alarm=n in requests,
            temperature=259 - (n if n <= 180 else n - 181) / 6,
        )
        if gap and n == 200:
            e.gap("sampling_gap")
            m.gap("sampling_gap")
            a.state.current.coverage_uncertain = True
        step(e, m, s, a)
    a.state.ledger = [
        SimpleNamespace(first_observed=begin.isoformat(), complete=not gap)
    ]
    a.state.current = None
    step(
        e,
        m,
        replace(
            s,
            sequence=363,
            at=begin + timedelta(seconds=3630),
            monotonic=day * 86400 + 3630,
            phase="Standby",
            session=None,
            qualified=False,
            alarm=False,
        ),
        a,
    )


def test_utility_threshold_progress_survives_months_and_restarts():
    e, m = Evidence(), ShadowModel()
    for day in range(20):
        completed_session(e, m, day)
        before = deepcopy(e.data["models"])
        e, m = restore(e, m)
        assert e.data["models"] == before
    result = e.summary(m)["per_model"][MODEL]
    assert result["qualified_sessions"] == 20
    assert result["qualified_dates_capped_at_10"] == 10
    assert result["eligible_request_cycles"] == 40
    assert result["eligible_scored_predictions"] == 40
    assert result["minimum_evidence_reached"]
    assert result["prediction_coverage"] == 1
    assert result["negative_forecast_fraction"] == 0
    assert result["median_absolute_error_bounds_minutes"][1] <= 5
    assert result["p90_absolute_error_bounds_minutes"][1] <= 10
    before = deepcopy(e.data["models"][MODEL]["utility"])
    completed_session(e, m, 21, gap=True)
    assert e.data["models"][MODEL]["utility"] == before
    # Pre-gap scores remain facts; partial sessions cannot certify utility.
    assert e.data["models"][MODEL]["scored"] > before["scored"]


def test_no_prediction_requests_enter_denominator_and_thresholds_are_not_accuracy():
    e, m = Evidence(), ShadowModel()
    a, _ = adapter_inputs()
    for n in range(182):
        step(e, m, sample(n, temperature=200 + n, alarm=n == 180), a)
    a.state.ledger = [SimpleNamespace(first_observed=sample(0).session, complete=True)]
    a.state.current = None
    step(e, m, sample(182, phase="Standby", session=None, qualified=False), a)
    u = e.summary(m)["per_model"][MODEL]
    assert u["eligible_request_cycles"] == 1
    assert u["prediction_coverage"] == 0
    assert u["without_prediction"] == 1
    assert not u["minimum_evidence_reached"]


def test_bounded_quantiles_expose_uncertainty_not_false_precision():
    histogram = [0] * BINS
    histogram[50], histogram[100] = 1, 1
    assert quantile_bounds(histogram, 0.5) == [7.5, 7.6]
    assert quantile_bounds(histogram, 0.9) == [10, 10.1]
    histogram[-1] = 10
    assert quantile_bounds(histogram, 0.9) == [60, None]
    assert quantile_bounds([0] * BINS, 0.5) is None


def test_causal_prefix_duplicate_and_unobserved_future_invariance():
    ea, eb, ma, mb = Evidence(), Evidence(), ShadowModel(), ShadowModel()
    a, _ = adapter_inputs()
    for n in range(70):
        s = sample(n)
        step(ea, ma, s, a)
        step(eb, mb, s, a)
        before = ea.dump(ma)
        step(ea, ma, s, a)
        assert ea.dump(ma) == before == eb.dump(mb)
    frozen = deepcopy(ma.pending)
    step(ea, ma, sample(70, alarm=True), a)
    step(eb, mb, sample(70, temperature=400), a)
    assert ma.ledger[0]["predicted_request_at"] == frozen["predicted_request_at"]
    assert mb.pending == frozen
    assert ea.data["models"][MODEL]["scored"] == 1
    assert eb.data["models"][MODEL]["scored"] == 0


@pytest.fixture
def disk(hass, tmp_path, monkeypatch):
    class DiskStore(Store):
        _async_load = REAL_LOAD
        _async_write_data = REAL_WRITE

        @property
        def path(self):
            return str(tmp_path / self.key)

    monkeypatch.setattr(_shadow, "Store", DiskStore)
    return lambda: Shadow(hass, "disk", clock=lambda: 0, utcnow=lambda: START)


async def test_real_disk_atomic_migration_leaves_legacy_bytes(disk, hass):
    s = disk()
    old = warm()
    old.step(sample(70, alarm=True))
    old.pending = deepcopy(warm().pending)
    old.evicted = 10
    legacy = _shadow.Store(hass, 1, s.legacy_key, atomic_writes=True)
    await legacy.async_save(old.dump())
    original = Path(legacy.path).read_bytes()
    with patch.object(
        storage, "write_utf8_file_atomic", wraps=storage.write_utf8_file_atomic
    ) as write:
        await s.async_load()
    assert s.healthy
    assert write.call_count == 1
    assert Path(legacy.path).read_bytes() == original
    assert s.model.ledger[0] == old.ledger[0]
    assert s.model.ledger[-1]["outcome"] == "restart"
    assert s.summary()["legacy_evicted_outcomes_unknown"] == 10
    assert s.summary()["valid_scored_predictions"] == 1
    assert s.summary()["per_model"][MODEL]["eligible_request_cycles"] == 0
    snapshot = deepcopy(s.model.ledger)
    for _ in range(3):
        s = disk()
        await s.async_load()
        assert s.healthy and s.model.ledger == snapshot
        assert s.summary()["invalidated_predictions"] == 1
    assert Path(legacy.path).read_bytes() == original


@pytest.mark.parametrize(
    "mutation",
    [
        "json",
        "envelope",
        "schema",
        "counter",
        "histogram",
        "pending",
        "annotation",
        "processed",
    ],
)
async def test_corrupt_new_store_never_falls_back_or_overwrites(disk, hass, mutation):
    s = disk()
    await s.async_load()
    s.model = warm()
    await s._save()
    path = Path(s.store().path)
    raw = json.loads(path.read_text())
    if mutation == "envelope":
        raw["version"] = 99
    elif mutation == "schema":
        raw["data"]["schema"] = 99
    elif mutation == "counter":
        raw["data"]["evidence"]["models"][MODEL]["scored"] = -1
    elif mutation == "histogram":
        raw["data"]["evidence"]["models"][MODEL]["errors"] = [0]
    elif mutation == "pending":
        raw["data"]["evidence"]["pending"] = None
    elif mutation == "annotation":
        raw["data"]["evidence"]["annotations"] = [{}]
    elif mutation == "processed":
        raw["data"]["evidence"]["processed"] += 1
    content = "{" if mutation == "json" else json.dumps(raw)
    path.write_text(content)
    # Even a valid legacy store must not hide corruption in a newer checkpoint.
    await _shadow.Store(hass, 1, s.legacy_key).async_save(ShadowModel().dump())
    fresh = disk()
    await fresh.async_load()
    await fresh.async_stop()
    assert not fresh.healthy and not fresh.summary()["checkpoint_verified"]
    assert path.read_text() == content


async def test_real_swallowed_write_failure_recovery_is_once_only(disk):
    s = disk()
    await s.async_load()
    s.model = warm()
    await s._save()
    path = Path(s.store().path)
    checkpoint = path.read_bytes()
    s.model.step(sample(70, alarm=True))
    with patch.object(storage, "write_utf8_file_atomic", side_effect=WriteError):
        await s._save()
    assert not s.healthy and s.model.predicted is None
    assert path.read_bytes() == checkpoint
    # The uncommitted score is not claimed durable. Recover the real checkpoint;
    # its pending prediction is censored, never matched across unknown downtime.
    fresh = disk()
    await fresh.async_load()
    assert fresh.healthy and len(fresh.model.ledger) == 1
    assert fresh.summary()["valid_scored_predictions"] == 0
    assert fresh.summary()["invalidated_predictions"] == 1
    again = disk()
    await again.async_load()
    assert again.model.ledger == fresh.model.ledger


async def test_store_failure_does_not_break_real_coordinator(hass, loaded, stove):
    before = stove.get_data.await_count
    with patch.object(
        loaded.shadow,
        "store",
        return_value=SimpleNamespace(
            async_save=AsyncMock(side_effect=OSError("private")),
        ),
    ):
        loaded.shadow.model = warm()
        await loaded.shadow._save()
    assert not loaded.shadow.healthy and loaded.analytics.healthy
    await loaded.async_refresh()
    assert loaded.last_update_success and loaded.analytics.healthy
    assert stove.get_data.await_count == before + 1


@pytest.mark.parametrize("partial", [True, False])
def test_session_no_prediction_and_negative_restart(partial):
    e, m = Evidence(), ShadowModel()
    a, _ = adapter_inputs()
    a.state.current.coverage_uncertain = partial
    for n in range(243):
        step(e, m, sample(n), a)
    assert m.ledger[-1]["outcome"] == "no_request_within_30m"
    before = deepcopy(e.data["models"])
    e, m = restore(e, m)
    assert e.data["models"] == before
    assert e.data["models"][MODEL]["negative"] == 1
    assert e.data["active"]["censored"]


def test_model_buckets_remain_separate_and_future_schema_fails_closed():
    e, m = Evidence(), ShadowModel()
    e.data["models"]["archived-model"] = deepcopy(e.data["models"][MODEL])
    e.data["models"]["archived-model"]["utility"] = cohort()
    saved = deepcopy(e.data["models"]["archived-model"])
    e, m = restore(e, m)
    assert e.data["models"]["archived-model"] == saved
    assert len(e.summary(m)["per_model"]) == 2
    p = m.dump()
    p["model_version"] = "future-model"
    with pytest.raises(ValueError):
        ShadowModel.restore(p)


async def test_final_ha_shutdown_flush_survives_process_replacement(disk, hass):
    from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
    from homeassistant.core import CoreState

    s = disk()
    await s.async_load()
    s.model = warm()
    await s._save()
    before = Path(s.store().path).read_bytes()
    hass.state = CoreState.stopping
    await s.async_stop()
    assert s.healthy
    assert Path(s.store().path).read_bytes() == before
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    payload = json.loads(Path(s.store().path).read_text())["data"]
    assert payload["engine"]["pending"] is None
    assert payload["engine"]["ledger"][-1]["outcome"] == "shutdown"
    assert payload["evidence"]["models"][MODEL]["invalidated"] == 1
    hass.state = CoreState.running
    fresh = disk()
    await fresh.async_load()
    assert fresh.healthy
    assert fresh.model.dump() == payload["engine"]
    assert fresh.summary()["invalidated_predictions"] == 1


async def test_migration_failure_keeps_old_and_retries_only_on_new_load(disk, hass):
    s = disk()
    legacy = _shadow.Store(hass, 1, s.legacy_key, atomic_writes=True)
    await legacy.async_save(warm().dump())
    before = Path(legacy.path).read_bytes()
    with patch.object(
        storage, "write_utf8_file_atomic", side_effect=WriteError
    ) as write:
        await s.async_load()
        assert write.call_count == 1
        await s.async_load()
        assert write.call_count == 1
    assert not s.healthy
    assert Path(legacy.path).read_bytes() == before
    fresh = disk()
    await fresh.async_load()
    assert fresh.healthy and fresh.summary()["invalidated_predictions"] == 1
    assert Path(legacy.path).read_bytes() == before


def test_pre_calibration_session_never_certifies_utility():
    from custom_components.hwam_stove._shadow_model import CALIBRATED_AT

    a, _ = adapter_inputs()
    a.state.current.first_observed = CALIBRATED_AT.isoformat()
    e, m = Evidence(), ShadowModel()
    step(e, m, sample(0, session=CALIBRATED_AT.isoformat()), a)
    assert e.data["active"]["censored"]
    assert e.data["models"][MODEL]["utility"]["requests"] == 0


def test_gap_brackets_are_bounded_and_reason_counter_saturation_explicit():
    e, m = Evidence(), ShadowModel()
    a, _ = adapter_inputs()
    for n in range(100):
        e.gap("failed_read")
        m.gap("failed_read")
        step(e, m, sample(n), a)
    assert len(e.data["gaps"]) == 32
    m._reason_counts["failed_read"] = 65535
    e.collect(m)
    e, m = restore(e, m)
    assert e.abstention(m)["reason_saturation_observed"]
    assert e.abstention(m)["reason_transition_counts"]["failed_read"] == 65535
