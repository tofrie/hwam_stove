"""A02.1 factual request aggregates, public options and cached HA states."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.storage import Store
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hwam_stove._analytics import Analytics
from custom_components.hwam_stove._analytics_model import (
    DAY_LIMIT,
    State,
    dump,
    gap,
    migrate,
    reduce,
    restore,
)
from custom_components.hwam_stove._request_statistics import (
    DESCRIPTIONS,
    SEASON_END,
    SEASON_START,
    month_day,
    requests_between,
    season,
)
from custom_components.hwam_stove.diagnostics import async_get_config_entry_diagnostics

from . import test_a02_analytics as a02_helpers
from .helpers import COMMANDS, DOMAIN, entity_id_for, registry_entries
from .test_a02_analytics import observe
from .test_a02_model import START, run, sample
from .test_m06_reconfigure import identities
from .test_o02_statistics import live_sensor

KEYS = [d.key for d in DESCRIPTIONS]


@pytest.fixture(autouse=True)
def clock(monkeypatch, stove):
    stove.data["refill_alarm"] = 0
    return a02_helpers.clock.__wrapped__(monkeypatch)


def legacy(state):
    payload = dump(state)
    payload.pop("request_retention_floor")
    payload["schema"] = 1
    return payload


@pytest.mark.parametrize("phase", ["Ignition", "Burn", "Glow", "Standby"])
@pytest.mark.parametrize("alarms,count", [
    ([False, True, True, False], 1), ([True, True], 0),
    ([False, True, False, True], 2), ([False, None, True], 0),
])
def test_edges_and_duplicate_callbacks_in_all_phases(phase, alarms, count):
    state = State()
    for i, alarm in enumerate(alarms):
        observation = sample(i, phase, refill=alarm)
        state = reduce(state, observation)
        assert reduce(state, observation) is state
    assert state.requests == count
    assert requests_between(state, START.date(), START.date()) == count
    assert state.current is None if phase == "Standby" else (
        state.current.requests == count
    )


@pytest.mark.parametrize("boundary", [gap, lambda s: restore(dump(s))])
def test_recovery_or_restart_requires_new_observed_false(boundary):
    state = reduce(run(["Burn"]), sample(1, "Burn", refill=True))
    state = boundary(state)
    state = reduce(state, sample(2, "Burn", refill=True))
    assert state.requests == 1
    state = reduce(state, sample(3, "Burn", refill=False))
    state = reduce(state, sample(4, "Burn", refill=True))
    assert state.requests == 2


@pytest.mark.parametrize("utc,day", [
    ("2026-05-31T21:59:59+00:00", "2026-05-31"),
    ("2026-05-31T22:00:00+00:00", "2026-06-01"),
    ("2026-08-31T22:00:00+00:00", "2026-09-01"),
    ("2026-03-29T00:59:59+00:00", "2026-03-29"),
    ("2026-03-29T01:00:00+00:00", "2026-03-29"),
    ("2026-10-25T00:30:00+00:00", "2026-10-25"),
    ("2026-10-25T01:30:00+00:00", "2026-10-25"),
])
def test_request_calendar_midnight_and_dst(utc, day):
    instant = datetime.fromisoformat(utc)
    state = reduce(State(), sample(0, "Burn", start=instant - timedelta(seconds=1)))
    state = reduce(state, sample(1, "Burn", start=instant, seconds=0, refill=True))
    assert state.requests == 1
    assert state.days == {day: {"completed": 0, "requests": 1}}


def test_calendar_uses_observation_timezone_not_fixed_country():
    start = datetime.fromisoformat("2026-10-01T00:30:00+00:00")
    state = reduce(State(), replace(sample(0, "Burn", start=start),
                                   timezone="America/New_York"))
    state = reduce(state, replace(sample(1, "Burn", start=start, refill=True),
                                 timezone="America/New_York"))
    assert set(state.days) == {"2026-09-30"}


@pytest.mark.parametrize("today,start,end,expected", [
    ("2026-09-01", "09-01", "05-31", ("2026-09-01", "2027-05-31")),
    ("2027-05-31", "09-01", "05-31", ("2026-09-01", "2027-05-31")),
    ("2027-06-01", "09-01", "05-31", ("2026-09-01", "2027-05-31")),
    ("2027-08-31", "09-01", "05-31", ("2026-09-01", "2027-05-31")),
    ("2027-09-01", "09-01", "05-31", ("2027-09-01", "2028-05-31")),
    ("2026-10-01", "01-01", "12-31", ("2026-01-01", "2026-12-31")),
    ("2026-02-28", "02-28", "02-28", ("2026-02-28", "2026-02-28")),
])
def test_inclusive_seasons_and_offseason_completed_total(today, start, end, expected):
    period = season(date.fromisoformat(today), start, end)
    assert tuple(d.isoformat() for d in period) == expected


@pytest.mark.parametrize("value", ["02-29", "02-30", "13-01", "00-01", "9-1", "", None])
def test_invalid_or_nonannual_boundary(value):
    with pytest.raises(ValueError):
        month_day(value)


def test_schema_one_preserves_every_existing_field_before_continuity_gap():
    state = run(["Ignition", "Burn"])
    state = reduce(state, sample(2, "Burn", refill=True, temp=500))
    state = run(["Standby", "Burn"], state, offset=3)
    payload = legacy(state)
    before = deepcopy(payload)
    converted = migrate(payload)
    assert payload == before
    assert converted.pop("request_retention_floor") is None
    assert converted.pop("schema") == 2
    assert converted == {k: v for k, v in before.items() if k != "schema"}
    restored = restore(before)
    assert restored.completed == 1 and restored.requests == 1
    assert restored.last_complete.peak == 500
    assert restored.ledger == state.ledger
    assert restored.current.first_observed == state.current.first_observed
    assert restored.current.coverage_uncertain


def test_bounded_daily_retention_never_certifies_pruned_history():
    state = State()
    for i in range(DAY_LIMIT + 2):
        start = START + timedelta(days=i)
        state = reduce(state, sample(i * 2, "Standby", start=start, seconds=0))
        state = reduce(state, sample(i * 2 + 1, "Standby", start=start,
                                     seconds=1, refill=True))
    assert len(state.days) == DAY_LIMIT
    assert state.requests == DAY_LIMIT + 2
    floor = (START.date() + timedelta(days=1)).isoformat()
    assert state.request_retention_floor == floor
    restored = restore(dump(state))
    assert requests_between(restored, START.date(), START.date()) is None
    end = START.date() + timedelta(days=DAY_LIMIT + 1)
    assert requests_between(restored, end - timedelta(days=365), end) == 366
    # Schema 1 lacks a watermark; a recreated bucket cannot prove completeness.
    old = restore(legacy(state))
    assert old.requests == state.requests
    assert old.request_retention_floor == end.isoformat()
    assert requests_between(old, end, end) is None
    assert requests_between(old, end + timedelta(days=1), end + timedelta(days=1)) == 0


def test_schema_one_empty_retention_with_nonzero_lifetime_is_unknown():
    payload = legacy(State(requests=5))
    state = restore(payload)
    assert state.requests == 5
    assert state.request_retention_floor == "9999-12-31"
    assert requests_between(state, START.date(), START.date()) is None


async def test_store_migration_then_restart_true_no_duplicate(hass, clock):
    a = Analytics(hass, "synthetic")
    state = reduce(run(["Ignition"]), sample(1, "Burn", refill=True))
    await a.store.async_save(legacy(state))
    await a.async_load()
    assert a.healthy and a.state.requests == 1
    await observe(a, clock, "Burn", alarm=True)
    await a.async_stop()
    saved = await Store(hass, 1, a.key).async_load()
    assert saved["schema"] == 2 and saved["requests"] == 1
    assert saved["current"]["requests"] == 1
    b = Analytics(hass, "synthetic")
    await b.async_load()
    await observe(b, clock, "Burn", alarm=True)
    assert b.state.requests == 1
    await observe(b, clock, "Burn", alarm=False)
    await observe(b, clock, "Burn", alarm=True)
    assert b.state.requests == 2
    await b.async_stop()


async def test_three_entities_session_qualification_and_no_new_requests(
    hass, entry, loaded, stove, clock,
):
    assert len(registry_entries(hass, entry.entry_id)) == 47
    sensors = [live_sensor(hass, k) for k in KEYS]
    before = stove.get_data.await_count
    assert sensors[0].native_value == 0
    assert sensors[0].extra_state_attributes["start_boundary_known"] is False
    await observe(loaded.analytics, clock, "Burn", alarm=True)
    assert [s.native_value for s in sensors] == [1, 1, 1]
    await observe(loaded.analytics, clock, "Standby", alarm=True)
    assert not sensors[0].available and sensors[0].native_value is None
    await observe(loaded.analytics, clock, "Ignition", alarm=False)
    assert [s.native_value for s in sensors] == [0, 1, 1]
    assert sensors[0].extra_state_attributes["start_boundary_known"]
    assert stove.get_data.await_count == before
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()
    for sensor in sensors:
        assert sensor.state_class is None
        assert sensor.native_unit_of_measurement is None


async def test_public_options_recompute_without_reload_or_network(
    hass, entry, loaded, stove, stove_factory, clock,
):
    a = loaded.analytics
    a.state.days = {"2026-08-31": {"completed": 0, "requests": 3},
                    "2026-09-01": {"completed": 0, "requests": 2}}
    a.state.requests = 5
    a.state.months = {}
    current = live_sensor(hass, KEYS[2])
    assert current.native_value == 2
    before = identities(hass, entry)
    entry_data = deepcopy(dict(entry.data))
    reads, creates = stove.get_data.await_count, stove_factory.await_count
    hass.config_entries.async_update_entry(entry, options={"unrelated": "keep"})
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    assert flow["data_schema"]({}) == {SEASON_START: "09-01", SEASON_END: "05-31"}
    failed = await hass.config_entries.options.async_configure(flow["flow_id"], {
        SEASON_START: "02-29", SEASON_END: "05-31",
    })
    assert failed["errors"] == {SEASON_START: "invalid_season_boundary"}
    assert entry.options == {"unrelated": "keep"}
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {
        SEASON_START: "08-01", SEASON_END: "12-31",
    })
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["unrelated"] == "keep"
    assert current.native_value == 5
    assert hass.states.get(current.entity_id).state == "5"
    assert identities(hass, entry) == before and entry.data == entry_data
    assert stove.get_data.await_count == reads
    assert stove_factory.await_count == creates
    stove.destroy.assert_not_called()
    diagnostic = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostic["analytics"] == {
        "schema_version": 2, "healthy": True, "ready": True,
        "season_start": "08-01", "season_end": "12-31",
    }
    assert "ledger" not in str(diagnostic)


@pytest.mark.parametrize("instant", [
    "2026-03-28T23:00:00+00:00", "2026-03-29T22:00:00+00:00",
    "2026-10-24T22:00:00+00:00", "2026-10-25T23:00:00+00:00",
    "2026-08-31T22:00:00+00:00", "2026-05-31T22:00:00+00:00",
])
async def test_public_midnight_listener_rolls_over_without_read(
    hass, entry, stove, clock, freezer, instant,
):
    midnight = datetime.fromisoformat(instant)
    freezer.move_to(midnight - timedelta(seconds=5))
    clock[0] = (midnight - timedelta(seconds=5) - START).total_seconds()
    await hass.config.async_set_time_zone("Europe/Berlin")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub = hass.data[DOMAIN]["stoves"][entry.entry_id]
    await observe(hub.analytics, clock, "Burn", alarm=True, advance=1)
    sensor = live_sensor(hass, KEYS[1])
    assert hass.states.get(sensor.entity_id).state == "1"
    count = stove.get_data.await_count
    clock[0] = (midnight - START).total_seconds()
    freezer.move_to(midnight)
    async_fire_time_changed(hass, midnight)
    await hass.async_block_till_done()
    assert hass.states.get(sensor.entity_id).state == "0"
    assert stove.get_data.await_count == count
    assert hub.analytics.state.requests == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_checkpoint_disables_all_three_not_factual_zero(
    hass, loaded, stove, clock,
):
    with patch.object(loaded.analytics.store, "async_save", side_effect=OSError):
        await observe(loaded.analytics, clock, "Burn", alarm=True)
    assert not loaded.analytics.healthy
    for key in KEYS:
        entity_id = entity_id_for(hass, "sensor", key)
        assert hass.states.get(entity_id).state == "unavailable"


async def test_readback_and_repeated_listener_do_not_count_request(
    hass, loaded, stove, clock,
):
    before = loaded.analytics.state.requests
    stove.data["refill_alarm"] = True
    await loaded.async_refresh_after_command()
    loaded.async_update_listeners()
    loaded.async_update_listeners()
    assert loaded.analytics.state.requests == before
    clock[0] += 10
    await loaded.async_refresh()  # A normal read observes the real false->true edge.
    assert loaded.analytics.state.requests == before + 1
    stove.data["refill_alarm"] = False
    clock[0] += 10
    await loaded.async_refresh()
    stove.data["refill_alarm"] = True
    clock[0] += 10
    await loaded.async_refresh()
    assert loaded.analytics.state.requests == before + 2


async def test_unavailable_true_recovery_and_public_reload_keep_counters(
    hass, entry, loaded, stove, clock,
):
    ids = identities(hass, entry)
    clock[0] += 10
    stove.data["refill_alarm"] = 1
    await loaded.async_refresh()
    assert loaded.analytics.state.requests == 1
    clock[0] += 10
    with patch.object(stove.get_data, "side_effect", lambda: None):
        await loaded.async_refresh()
    assert not loaded.last_update_success
    await observe(loaded.analytics, clock, "Burn", alarm=True)
    assert loaded.analytics.state.requests == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    replacement = hass.data[DOMAIN]["stoves"][entry.entry_id]
    assert replacement.analytics.state.requests == 1
    assert replacement.analytics.state.current.requests == 1
    assert replacement.analytics.state.current.coverage_uncertain
    assert identities(hass, entry) == ids
    assert len(registry_entries(hass, entry.entry_id)) == 47
    assert stove.destroy.await_count == 1


async def test_uncertain_retention_options_never_reset_or_report_lower_bound(
    hass, entry, loaded, clock,
):
    state = loaded.analytics.state
    state.requests = 7
    state.days = {"2026-10-01": {"completed": 0, "requests": 2}}
    state.request_retention_floor = "2026-09-01"
    sensor = live_sensor(hass, KEYS[2])
    assert not sensor.available
    assert sensor.native_value is None
    stored = dump(state)
    hass.config_entries.async_update_entry(entry, options={
        SEASON_START: "10-01", SEASON_END: "05-31",
    })
    await hass.async_block_till_done()
    assert sensor.available and sensor.native_value == 2
    assert dump(state) == stored  # Reconfiguration changes the query only.


def test_dst_repeated_hour_events_are_two_edges_on_one_calendar_day():
    state = State()
    for i, utc in enumerate(("2026-10-25T00:30:00+00:00",
                             "2026-10-25T01:30:00+00:00")):
        instant = datetime.fromisoformat(utc)
        state = reduce(state, replace(sample(i * 2, "Burn"),
                                      utc=instant - timedelta(seconds=1)))
        state = reduce(state, replace(sample(i * 2 + 1, "Burn", refill=True),
                                      utc=instant))
    assert state.requests == 2
    assert state.days == {"2026-10-25": {"completed": 0, "requests": 2}}


def test_inclusive_request_query_does_not_count_outside_season():
    state = State(days={
        day: {"completed": 0, "requests": value}
        for day, value in (("2026-08-31", 8), ("2026-09-01", 1),
                           ("2027-05-31", 2), ("2027-06-01", 9))
    }, requests=20)
    for current in (date(2027, 5, 31), date(2027, 6, 1)):
        assert requests_between(state, *season(current, "09-01", "05-31")) == 3


def test_approved_a02_observation_and_identity_contract_unchanged():
    """Cached queries preserve the A02 reducer, protocol and existing entities."""
    import ast
    import json
    from pathlib import Path
    import subprocess

    root = Path(__file__).resolve().parents[1]
    base = "41934e8cb3e17f558e8606793e0cc0860c4d1bae"
    prefix = "custom_components/hwam_stove/"

    def prior(name):
        return subprocess.check_output(["git", "show", f"{base}:{prefix}{name}"],
                                       cwd=root)

    def current(name):
        return (root / prefix / name).read_bytes()

    for name in ("coordinator.py", "_analytics.py", "_analytics_sensor.py",
                 "entity.py", "migration.py", "number.py", "switch.py",
                 "button.py", "binary_sensor.py", "time.py", "datetime.py"):
        assert current(name) == prior(name), name
    name = "_analytics_model.py"
    for function in ("reduce", "gap", "dump"):
        before = next(n for n in ast.parse(prior(name)).body
                      if isinstance(n, ast.FunctionDef) and n.name == function)
        after = next(n for n in ast.parse(current(name)).body
                     if isinstance(n, ast.FunctionDef) and n.name == function)
        assert ast.dump(before) == ast.dump(after)
    old, new = json.loads(prior("manifest.json")), json.loads(current("manifest.json"))
    assert new.pop("version") == "1.0.0rc2"
    assert old.pop("version") == "1.0.0rc1"
    assert old == new
    assert new["requirements"] == ["saynwerk-pystove==0.3.0rc2"]
    for lang in ("de", "en", "nl"):
        name = f"translations/{lang}.json"
        old, new = json.loads(prior(name)), json.loads(current(name))
        new.pop("options")
        for key in KEYS:
            new["entity"]["sensor"].pop(key)
        assert old == new
