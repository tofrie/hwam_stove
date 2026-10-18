"""rc6 cycle audit: deterministic offline behavior, no runtime corrections."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.hwam_stove._shadow import Shadow
from custom_components.hwam_stove._shadow_model import ShadowModel
from custom_components.hwam_stove._shadow_sensor import ShadowSensor

from .test_a04_shadow import START, sample, warm
from .test_a041_diagnostics import adapter_inputs


def feed(model, start, end, *, cooling_start=None, **changes):
    for n in range(start, end + 1):
        values = changes.copy()
        if cooling_start is not None:
            values["temperature"] = 259 - (n - cooling_start) / 6
        model.step(sample(n, **values))


def negative():
    model = warm()
    feed(model, 61, 241)
    assert model.reason == "cycle_spent"
    assert model.ledger[-1]["outcome"] == "no_request_within_30m"
    return model


def test_cycle_is_consumed_once_and_not_reissued_for_a_better_window():
    m = ShadowModel()
    feed(m, 0, 59)
    assert not m.spent and m.pending is None
    m.step(sample(60))
    issued = deepcopy(m.pending)
    assert m.spent and m.state == "prediction_available"
    feed(m, 61, 120, temperature=240)
    m.step(sample(120, temperature=500))  # Same token is not another observation.
    assert m.pending == issued and not m.ledger
    assert m.revision == 2  # One session plus one issued forecast.


def test_display_expiry_is_not_evaluation_closure_or_new_cycle():
    m = warm()
    issued = deepcopy(m.pending)
    feed(m, 61, 180)
    assert m.predicted == sample(180).at  # At the predicted timestamp, still visible.
    m.step(sample(181))
    assert m.reason == "prediction_expired" and m.predicted is None
    assert m.pending == issued and not m.ledger
    feed(m, 182, 240)
    assert m.pending == issued  # Exactly 30 minutes since issuance.
    m.step(sample(241))
    assert m.pending is None and m.reason == "cycle_spent"
    assert m.ledger[-1]["outcome"] == "no_request_within_30m"
    assert m.ledger[-1]["error_minutes"] is None
    feed(m, 242, 480, cooling_start=242)
    assert len(m.ledger) == 1 and m.pending is None
    assert m.reason == "cycle_spent"
    assert m.abstention_diagnostics()["history_span_seconds"] == 610


def test_request_after_display_expiry_scores_original_prediction():
    m = warm()
    issued = deepcopy(m.pending)
    feed(m, 61, 190)
    assert m.reason == "prediction_expired"
    m.step(sample(191, alarm=True))
    assert m.reason == "request_active" and m.spent
    assert m.ledger[0]["outcome"] == "request_observed"
    assert m.ledger[0]["predicted_request_at"] == issued["predicted_request_at"]
    assert m.ledger[0]["error_minutes"] < 0


def test_request_after_negative_does_not_rewrite_negative_or_double_count_cycle():
    m = negative()
    closed = deepcopy(m.ledger)
    m.step(sample(242, alarm=True))
    assert m.reason == "request_active" and m.ledger == closed
    feed(m, 243, 250, alarm=True)
    assert m.spent and m.pending is None and m.ledger == closed
    m.step(sample(251, temperature=259))
    assert not m.spent and not m.cycle_recorded
    assert m.reason == "insufficient_history"
    feed(m, 252, 310, cooling_start=251)
    assert m.pending is None
    m.step(sample(311, temperature=249))
    assert m.pending and m.spent
    assert m.pending["session_id"] == closed[0]["session_id"]
    assert m.ledger == closed


@pytest.mark.parametrize("already_issued", [False, True])
def test_door_close_is_not_a_new_request_cycle(already_issued):
    m = warm() if already_issued else ShadowModel()
    m.step(sample(61, door=True))
    assert m.reason == "door_open" and m.spent
    feed(m, 62, 123, cooling_start=62)
    assert m.reason == "cycle_spent" and m.pending is None
    assert len(m.history) == 62
    assert len(m.ledger) == int(already_issued)
    m.step(sample(124, alarm=True))
    m.step(sample(125, temperature=259))
    assert not m.spent
    feed(m, 126, 185, cooling_start=125)
    assert m.state == "prediction_available"


def test_glow_and_reheating_alone_do_not_rearm_a_consumed_request_cycle():
    m = negative()
    feed(m, 242, 300, phase="Glow")
    assert m.reason == "unseen_phase" and m.spent
    feed(m, 301, 361, cooling_start=301)
    assert m.reason == "cycle_spent" and m.pending is None
    # The subsequent observed alarm episode provides the missing causal boundary.
    m.step(sample(362, phase="Glow", alarm=True))
    m.step(sample(363, phase="Glow", alarm=False))
    assert not m.spent and m.reason == "unseen_phase"
    feed(m, 364, 424, cooling_start=364)
    assert m.state == "prediction_available"


def test_burn_glow_request_burn_preserves_censoring_and_starts_next_cycle():
    m = warm()
    m.step(sample(61, phase="Glow"))
    assert m.ledger[0]["outcome"] == "unseen_phase"
    assert m.pending is None and m.spent
    m.step(sample(62, phase="Glow", alarm=True))
    assert len(m.ledger) == 1 and m.reason == "request_active"
    m.step(sample(63, phase="Glow", alarm=False))
    assert not m.spent and not m.cycle_recorded
    feed(m, 64, 124, cooling_start=64)
    assert m.pending is not None
    m.step(sample(125, alarm=True))
    assert [r["outcome"] for r in m.ledger] == ["unseen_phase", "request_observed"]


def test_multiple_refill_cycles_in_same_session_without_restart():
    m = ShadowModel()
    predictions = []
    for start in (0, 72, 144):
        feed(m, start, start + 60, cooling_start=start)
        predictions.append(deepcopy(m.pending))
        feed(m, start + 61, start + 69, cooling_start=start)
        assert m.pending == predictions[-1]
        m.step(sample(start + 70, alarm=True))
        m.step(sample(start + 71, alarm=True))
    assert len({p["prediction_at"] for p in predictions}) == 3
    assert len({p["session_id"] for p in predictions}) == 1
    assert len(m.ledger) == 3
    assert all(r["outcome"] == "request_observed" for r in m.ledger)


@pytest.mark.parametrize(
    "point", ["before", "issued", "expired", "negative", "requested"]
)
@pytest.mark.parametrize("kind", ["restart", "gap"])
def test_restart_and_gap_never_restore_spent_lockout(point, kind):
    m = ShadowModel()
    end = {
        "before": 30,
        "issued": 60,
        "expired": 190,
        "negative": 241,
        "requested": 70,
    }[point]
    feed(m, 0, end, alarm=False)
    if point == "requested":
        m.step(sample(end + 1, alarm=True))
    pending = deepcopy(m.pending)
    historical = deepcopy(m.ledger)
    if kind == "restart":
        m = ShadowModel.restore(m.dump())
    else:
        m.gap("sampling_gap")
    assert not m.spent and not m.cycle_recorded and m.previous is None
    assert not m.history and m.pending is None
    assert m.ledger[: len(historical)] == historical
    if pending:
        assert m.ledger[-1]["prediction_at"] == pending["prediction_at"]
        assert m.ledger[-1]["gap_invalid"] and m.ledger[-1]["error_minutes"] is None
    count = len(m.ledger)
    # A false first observation is an anchor, never an inferred alarm-clearing edge.
    feed(m, 500, 559, cooling_start=500)
    assert m.pending is None
    m.step(sample(560, temperature=249))
    assert m.state == "prediction_available" and m.spent
    assert len(m.ledger) == count


def test_restart_while_alarm_true_waits_for_observed_clearing():
    m = warm()
    m.step(sample(70, alarm=True))
    saved = deepcopy(m.ledger)
    m = ShadowModel.restore(m.dump())
    feed(m, 500, 560, alarm=True)
    assert m.reason == "request_active" and m.pending is None
    assert m.ledger == saved
    feed(m, 561, 621, cooling_start=561)
    assert m.state == "prediction_available" and m.ledger == saved


def test_new_observed_session_clears_previous_spent_cycle():
    m = negative()
    m.step(sample(242, phase="Standby", session=None, qualified=False))
    assert not m.spent
    next_id = sample(243).at.isoformat()
    m.step(sample(243, phase="Ignition", session=next_id))
    feed(m, 244, 304, cooling_start=244, session=next_id)
    assert m.pending["session_id"] == next_id
    assert m.ledger[0]["session_id"] != next_id


async def test_reproduce_reported_counters_then_next_cycle_in_partial_session(hass):
    tick = [0]
    args = {
        "clock": lambda: tick[0],
        "utcnow": lambda: START + timedelta(seconds=tick[0]),
    }
    a, data = adapter_inputs()
    s = Shadow(hass, "cycle-audit", **args)
    await s.async_load()

    async def observe(n, *, alarm=False, temperature=300, door=False, phase="Burn"):
        # A valid 10s cadence with 1ms completion overhead reproduces the
        # reported 61 retained samples / rounded 600s span. No new policy.
        tick[0] = n * 10.001
        data.update(refill_alarm=alarm, stove_temperature=temperature, phase=phase)
        await s.async_observe(n, s.read_started(), a, data, door)

    # Two real observed edges, insufficient history for either forecast.
    for n in range(5):
        await observe(n, alarm=n in (1, 3))
    assert s.summary()["requests_without_prediction"] == 2
    await s.async_stop()
    s = Shadow(hass, "cycle-audit", **args)
    await s.async_load()
    a.state.current.coverage_uncertain = True
    for n in range(100, 403):
        await observe(n, temperature=259 - (n - 100) / 6)
    d = s.abstention_diagnostics()
    totals = s.summary()
    assert s.healthy and totals["checkpoint_verified"]
    assert totals["active_session_present"] and totals["active_session_censored"]
    assert s.session_qualification == "session_coverage_uncertain"
    assert s.observation_disposition == "accepted_regular_observation"
    assert d["state"] == "unknown" and d["reason"] == "cycle_spent"
    assert d["history_samples"] == 61 and d["history_span_seconds"] == 600
    assert d["missing_inputs"] == []
    assert totals["valid_scored_predictions"] == 0
    assert totals["invalidated_predictions"] == 0
    assert totals["requests_without_prediction"] == 2
    assert totals["retained_cycles"] == 3
    assert totals["negative_windows_without_request"] == 1
    assert totals["per_model"]["a04-phase-matched-r1-v1"]["issued"] == 1
    before = deepcopy(s.model.ledger)
    checkpoint = await s.store().async_load()
    await observe(403, alarm=True, phase="Glow")
    assert s.model.reason == "request_active"
    await observe(404, alarm=False, phase="Glow")
    for n in range(405, 466):
        await observe(n, temperature=259 - (n - 405) / 6)
    assert s.model.state == "prediction_available"
    assert s.model.ledger == before and a.state.current.coverage_uncertain
    assert s.summary()["evidence_sessions"] == 1
    assert s.summary()["per_model"]["a04-phase-matched-r1-v1"]["issued"] == 2
    assert s.evidence.data["active"]["censored"]

    # Same aggregate counters can also precede a different runtime blocker:
    # restore AFTER that negative record, then observe a door-only event.
    s = Shadow(hass, "alternate-cycle-audit", **args)
    await s.store().async_save(checkpoint)
    await s.async_load()
    assert not s.model.spent
    await observe(500, door=True)
    for n in range(501, 562):
        await observe(n)
    assert s.model.reason == "cycle_spent" and s.model.pending is None
    assert s.abstention_diagnostics()["history_samples"] == 61
    assert s.abstention_diagnostics()["history_span_seconds"] == 600
    for key in (
        "valid_scored_predictions",
        "invalidated_predictions",
        "requests_without_prediction",
        "retained_cycles",
        "negative_windows_without_request",
    ):
        assert s.summary()[key] == totals[key]


@pytest.mark.parametrize(
    "point", ["before", "issued", "expired", "negative", "requested"]
)
async def test_real_schema2_reload_rearms_partial_session_after_fresh_window(
    hass, point
):
    tick = [0]
    args = {
        "clock": lambda: tick[0],
        "utcnow": lambda: START + timedelta(seconds=tick[0]),
    }
    a, data = adapter_inputs()
    s = Shadow(hass, point, **args)
    await s.async_load()
    end = {
        "before": 30,
        "issued": 60,
        "expired": 190,
        "negative": 241,
        "requested": 70,
    }[point]
    for n in range(end + 1):
        tick[0] = n * 10
        data.update(
            stove_temperature=259 - n / 6,
            refill_alarm=point == "requested" and n == end,
        )
        await s.async_observe(n, s.read_started(), a, data, False)
    old = deepcopy(s.model.ledger)
    pending = deepcopy(s.model.pending)
    await s.async_stop()
    s = Shadow(hass, point, **args)
    await s.async_load()
    assert not s.model.spent and not s.model.cycle_recorded
    assert s.model.ledger[: len(old)] == old
    if pending:
        assert s.model.ledger[-1]["prediction_at"] == pending["prediction_at"]
        assert s.model.ledger[-1]["error_minutes"] is None
    a.state.current.coverage_uncertain = True
    for n in range(500, 561):
        tick[0] = n * 10
        data.update(stove_temperature=259 - (n - 500) / 6, refill_alarm=False)
        await s.async_observe(n, s.read_started(), a, data, False)
        if n < 560:
            assert s.model.pending is None
    assert s.healthy and s.model.state == "prediction_available"
    assert s.summary()["evidence_sessions"] == 1
    assert a.state.current.coverage_uncertain


def test_timestamp_unavailable_while_abstaining_reasons_remain_attributes():
    model = negative()
    shadow = SimpleNamespace(
        ready=True, healthy=True, closed=False, model=model, summary=model.summary
    )
    sensor = ShadowSensor(
        SimpleNamespace(
            shadow=shadow,
            config_entry=SimpleNamespace(entry_id="test-entry"),
        )
    )
    assert not sensor.available and sensor.native_value is None
    assert sensor.extra_state_attributes["reason"] == "cycle_spent"
    assert sensor.extra_state_attributes["prediction_state"] == "unknown"
    model.step(sample(242, alarm=True))
    feed(model, 243, 303, cooling_start=243)
    assert sensor.available and sensor.native_value == model.predicted


def test_phase_changes_without_issued_prediction_do_not_consume_cycle():
    m = ShadowModel()
    m.step(sample(0, phase="Ignition"))
    feed(m, 1, 20)
    feed(m, 21, 30, phase="Glow")
    assert not m.spent and m.pending is None and not m.ledger
    feed(m, 31, 91, cooling_start=31)
    assert m.pending and m.state == "prediction_available"


@pytest.mark.parametrize("door_open_at_clearing", [False, True])
def test_alarm_clear_door_order_exposes_original_conservative_policy(
    door_open_at_clearing,
):
    m = negative()
    m.step(sample(242, alarm=True, door=True))
    assert m.reason == "request_active"  # Alarm handling precedes door veto.
    m.step(sample(243, door=door_open_at_clearing, temperature=259))
    if door_open_at_clearing:
        assert m.reason == "door_open" and m.spent
    else:
        assert m.reason == "insufficient_history" and not m.spent
    feed(m, 244, 304, cooling_start=243)
    assert m.reason == ("cycle_spent" if door_open_at_clearing else "available")
    # With the door closed at clearing, a new forecast was actually issued.
    assert (m.pending is None) is door_open_at_clearing
    assert len(m.ledger) == 1
    # A subsequent unvetoed alarm-clear boundary still re-arms in the same session.
    m.step(sample(305, alarm=True))
    feed(m, 306, 366, cooling_start=306)
    assert m.state == "prediction_available"
