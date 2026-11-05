"""A07 consumes real read boundaries; private copies only, no transport."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.helpers import storage
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
import pytest

from custom_components.hwam_stove import _observations
from custom_components.hwam_stove._observation_data import COLUMNS, LIMIT, validate
from custom_components.hwam_stove._observations import Observations
from custom_components.hwam_stove._shadow_model import ShadowModel
from custom_components.hwam_stove._shadow_sensor import ShadowSensor

from .helpers import COMMANDS, entity_id_for, registry_entries, status_data
from .test_a04_shadow import START, sample, warm
from .test_a042_persistence import REAL_LOAD, REAL_WRITE


@pytest.fixture
def recorder(hass, tmp_path, monkeypatch):
    class DiskStore(Store):
        _async_load = REAL_LOAD
        _async_write_data = REAL_WRITE

        @property
        def path(self):
            return str(tmp_path / self.key)

    monkeypatch.setattr(_observations, "Store", DiskStore)
    now = [0.0]
    instances = []

    def make():
        obj = Observations(
            hass,
            "synthetic",
            clock=lambda: now[0],
            utcnow=lambda: START + timedelta(seconds=now[0]),
        )
        instances.append(obj)
        return obj

    yield make, now
    for obj in instances:
        obj.closed = True
        if obj._cancel:
            obj._cancel()


def capture(obj, now, n, *, data=None, door=False, shadow=None, analytics=None):
    analytics = analytics or SimpleNamespace(
        healthy=True,
        state=SimpleNamespace(
            current=SimpleNamespace(
                first_observed=START.isoformat(),
                start=START.isoformat(),
                coverage_uncertain=False,
            )
        ),
    )
    shadow = shadow or SimpleNamespace(model=ShadowModel())
    obj.observe(
        n,
        obj.read_started(),
        data or status_data(),
        door,
        START + timedelta(seconds=now[0]),
        now[0],
        analytics,
        shadow,
    )
    return dict(zip(COLUMNS, obj.rows[-1], strict=True))


async def test_normal_equal_polls_export_and_command_exclusion(
    hass, export_context, loaded, stove
):
    recorder = loaded.observations
    initial = len(recorder.rows)
    for _ in range(3):
        await loaded.async_refresh()
    assert len(recorder.rows) == initial + 3
    requests = stove.get_data.await_count
    observations = sum(r[2] == "observation" for r in recorder.rows)
    await loaded.async_refresh_after_command()
    assert sum(r[2] == "observation" for r in recorder.rows) == observations
    assert stove.get_data.await_count <= requests + 1
    before = stove.get_data.await_count
    snapshot = deepcopy(loaded.shadow.model.dump())
    analytics = deepcopy(loaded.analytics.state)
    result = await hass.services.async_call(
        "hwam_stove",
        "export_observations",
        {},
        blocking=True,
        return_response=True,
        context=export_context,
    )
    assert result["records"] == len(recorder.rows)
    assert len(json.dumps(result)) < 1500
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert recorder.rows
    assert (
        loaded.shadow.model.dump() == snapshot and loaded.analytics.state == analytics
    )
    assert stove.get_data.await_count == before
    assert len(registry_entries(hass)) == 49
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()


@pytest.mark.parametrize(
    "delay,quality",
    [
        (10, "continuous"),
        (11, "continuous"),
        (12, "edge_uncertain"),
        (20, "short_gap"),
        (30, "short_gap"),
        (31, "hard_gap"),
    ],
)
async def test_gap_quality_without_interpolation(recorder, delay, quality):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1)
    now[0] = delay
    row = capture(obj, now, 2)
    assert row["quality"] == quality and row["interval_seconds"] == delay
    assert len(obj.rows) == 3  # restart + exactly two genuine observations
    assert row["cycle"] == (1 if quality == "continuous" else 2)
    validate(obj._payload())


@pytest.mark.parametrize(
    "door,alarm",
    [(None, None), (None, 1), (True, False), (False, 0), ("secret", "secret")],
)
async def test_unknown_edge_values_are_not_false(recorder, door, alarm):
    make, now = recorder
    obj = make()
    await obj.async_load()
    row = capture(
        obj,
        now,
        1,
        door=door,
        data=status_data()
        | {
            "refill_alarm": alarm,
            "ssid": "SECRET",
            "host": "SECRET",
            "body": "SECRET",
        },
    )
    assert row["door_open"] is (door if type(door) is bool else None)
    assert row["refill_alarm"] is (bool(alarm) if type(alarm) in (bool, int) else None)
    assert "SECRET" not in json.dumps(obj.export())
    validate(obj._payload())


async def test_request_cycle_is_not_a_wood_event(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    cycle = capture(obj, now, 1, data=status_data() | {"refill_alarm": False})["cycle"]
    for n, (door, alarm) in enumerate(
        [(True, False), (False, False), (False, True), (False, True), (False, False)], 2
    ):
        now[0] += 10
        row = capture(
            obj, now, n, door=door, data=status_data() | {"refill_alarm": alarm}
        )
        assert row["cycle"] == cycle + (n == 6)
    assert "wood_added" not in json.dumps(obj.export())


async def test_duplicates_boundary_and_cancellation_do_not_create_samples(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1)
    size = len(obj.rows)
    capture(obj, now, 1)
    assert len(obj.rows) == size
    token = obj.read_started()
    obj.boundary("command_boundary")
    obj.observe(2, token, status_data(), False, START, 0, None, None)
    assert len(obj.rows) == size + 1 and obj.rows[-1][2] == "gap"
    obj.boundary("failed_read")
    obj.boundary("failed_read")
    assert len(obj.rows) == size + 2
    assert all(x is None for x in obj.rows[-1][3:16])


async def test_actual_disk_checkpoint_restart_and_immutable_rows(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1, shadow=SimpleNamespace(model=warm()))
    await obj.async_checkpoint()
    assert obj.verified and not obj.dirty
    original = deepcopy(list(obj.rows))
    last = deepcopy(obj.data["last_prediction"])
    now[0] += 10
    capture(obj, now, 2)  # uncheckpointed; simulate process loss, never write this row
    obj.closed = True
    obj._cancel()
    obj._cancel = None
    for _ in range(3):
        restored = make()
        await restored.async_load()
        assert restored.healthy and restored.verified
        assert list(restored.rows)[: len(original)] == original
        assert restored.rows[-1][17] == "restart"
        assert restored.previous is None
        assert restored.data["last_prediction"] == last
        await restored.async_stop()
    assert json.loads(Path(restored.store.path).read_bytes())["data"]["schema"] == 1


async def test_batched_deadline_loop_thread_and_unload(recorder, hass, monkeypatch):
    make, now = recorder
    obj = make()
    loop_thread = threading.get_ident()
    seen = []
    original = obj.async_checkpoint

    async def checkpoint():
        seen.append(threading.get_ident())
        await original()

    monkeypatch.setattr(obj, "async_checkpoint", checkpoint)
    await obj.async_load()
    cancel = obj._cancel
    for i in range(100):
        now[0] = i * 10
        capture(obj, now, i)
        assert obj._cancel == cancel
    assert not Path(obj.store.path).exists()  # no write for 100 polls
    handle = cancel.__self__
    handle._run()  # Actual HA job classification/dispatch, not a direct callback.
    handle.cancel()
    await obj._task
    assert seen == [loop_thread] and obj.verified
    now[0] += 10
    capture(obj, now, 101)
    await obj.async_stop()
    assert obj._cancel is None and obj._task is None
    before = deepcopy(obj.export())
    obj._due(START)
    capture(obj, now, 102)
    assert obj.export() == before


async def test_failed_atomic_write_keeps_checkpoint_bytes(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1)
    await obj.async_checkpoint()
    path = Path(obj.store.path)
    before = path.read_bytes()
    now[0] += 10
    capture(obj, now, 2)
    with patch.object(storage, "write_utf8_file_atomic", side_effect=WriteError):
        await obj.async_checkpoint()
    assert not obj.healthy and path.read_bytes() == before
    with pytest.raises(ValueError):
        obj.export()
    await obj.async_stop()
    assert path.read_bytes() == before


@pytest.mark.parametrize("corruption", [b"{broken", b'{"version":99}', b"{}"])
async def test_corrupt_store_never_quarantined_overwritten_or_reset(
    recorder, corruption
):
    make, _ = recorder
    obj = make()
    path = Path(obj.store.path)
    path.write_bytes(corruption)
    await obj.async_load()
    assert not obj.healthy
    await obj.async_stop()
    assert path.read_bytes() == corruption
    assert not list(path.parent.glob(path.name + ".corrupt.*"))


async def test_retention_bound_and_age(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    for i in range(LIMIT + 12):
        now[0] = i * 10
        capture(obj, now, i)
    assert len(obj.rows) == LIMIT
    assert obj.data["evicted"] == 13
    validate(obj._payload())
    now[0] += 8 * 86400
    capture(obj, now, LIMIT + 13)
    assert len(obj.rows) == 1
    assert obj.rows[-1][16] == "hard_gap"


@pytest.mark.parametrize(
    "state,reason,status",
    [
        ("active", "available", "active"),
        ("expired", "prediction_expired", "expired"),
        ("door", "door_open", "superseded"),
        ("phase", "unseen_phase", "superseded"),
        ("restart", "restart", "unknown"),
        ("request", "request_active", "request_active"),
    ],
)
async def test_timestamp_semantics_and_last_immutable_issuance(
    hass,
    loaded,
    state,
    reason,
    status,
):
    model = warm()
    original = deepcopy(model.pending)
    loaded.shadow.model = model
    loaded.freshness.clock = lambda: 0
    loaded.freshness.accept(sample(60).at, 0, 10)
    if state == "expired":
        model.step(sample(181))
    elif state == "door":
        model.step(sample(61, door=True))
    elif state == "phase":
        model.step(sample(61, phase="Glow"))
    elif state == "restart":
        loaded.shadow.model = model = ShadowModel.restore(model.dump())
    elif state == "request":
        model.step(sample(61, alarm=True))
    loaded.async_update_listeners()
    await hass.async_block_till_done()
    entity = hass.states.get(entity_id_for(hass, "sensor", "shadow_refill_request"))
    assert entity.attributes["benchmark_status"] == status
    assert entity.attributes["benchmark_reason"] == reason
    assert entity.attributes["last_prediction_at"] == original["predicted_request_at"]
    assert entity.attributes["prediction_issued_at"] == original["prediction_at"]
    assert entity.state == "unknown"  # No regular observation after injection.
    assert entity.attributes["device_class"] == "timestamp"


async def test_last_prediction_survives_ledger_eviction_without_revival(hass, loaded):
    loaded.shadow.model = warm()
    retained = dict(
        issued_at=loaded.shadow.model.pending["prediction_at"],
        predicted_at=loaded.shadow.model.pending["predicted_request_at"],
        model=loaded.shadow.model.pending["model_version"],
    )
    loaded.observations.data["last_prediction"] = retained
    loaded.shadow.model = ShadowModel()
    entity = ShadowSensor(loaded)
    assert entity.available and entity.native_value is None
    assert (
        entity.extra_state_attributes["last_prediction_at"] == retained["predicted_at"]
    )


async def test_checkpoint_readback_mismatch_fails_closed(recorder, monkeypatch):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1)

    async def mismatched():
        return {}

    monkeypatch.setattr(obj.store, "async_load", mismatched)
    await obj.async_checkpoint()
    assert not obj.healthy and not obj.verified
    before = Path(obj.store.path).read_bytes()
    await obj.async_stop()
    assert Path(obj.store.path).read_bytes() == before


async def test_append_during_checkpoint_and_repeated_stop_cancellation(
    recorder, monkeypatch
):
    from asyncio import Event, create_task

    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1)
    entered, release = Event(), Event()
    original = obj.store.async_save
    count = 0

    async def held(payload):
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            await release.wait()
        await original(payload)

    monkeypatch.setattr(obj.store, "async_save", held)
    obj._cancel()
    obj._due(START)
    await entered.wait()
    now[0] = 10
    capture(obj, now, 2)
    stop = create_task(obj.async_stop())
    # Deterministically wait for the coroutine to close its observation boundary.
    while not obj.closed:
        await asyncio.sleep(0)
    stop.cancel()
    await asyncio.sleep(0)
    stop.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await stop
    assert count == 2
    saved = json.loads(Path(obj.store.path).read_bytes())["data"]
    assert saved["rows"] == list(obj.rows)
    assert saved["rows"][-1][17] == "shutdown"
    assert obj.verified and not obj.dirty and obj._cancel is None and obj._task is None


async def test_unknown_to_known_is_not_a_continuous_edge(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1, door=None)
    now[0] = 10
    row = capture(obj, now, 2)
    assert row["quality"] == "baseline" and row["reason"] == "missing_inputs"


async def test_model_and_analytics_are_read_only_and_capacity_size(recorder, tmp_path):
    make, now = recorder
    obj = make()
    await obj.async_load()
    model = warm()
    before = deepcopy(model.dump())
    for n in range(20):
        now[0] = 10 * n
        capture(obj, now, n, shadow=SimpleNamespace(model=model))
    assert model.dump() == before
    # Measure real HA serialization at maximum retention, not a guessed byte size.
    prototype = deepcopy(obj.rows[-1])
    obj.rows.clear()
    for n in range(LIMIT):
        row = prototype.copy()
        row[0] = n + 1
        row[1] = (START + timedelta(seconds=10 * n)).isoformat()
        obj.rows.append(row)
    obj.data["next_sequence"] = LIMIT + 1
    obj.data["evicted"] = 0
    await obj.async_checkpoint()
    assert obj.verified
    size = Path(obj.store.path).stat().st_size
    export_bytes = len(json.dumps(obj.export(), separators=(",", ":")).encode())
    assert size < 12_000_000 and export_bytes < 8_000_000
    print(f"A07 CAPACITY: store={size} bytes; compact_export={export_bytes} bytes")


async def test_export_multiple_entries_requires_selection(hass, export_context, loaded):
    from homeassistant.exceptions import HomeAssistantError

    stoves = hass.data["hwam_stove"]["stoves"]
    stoves["synthetic-other"] = loaded
    try:
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "hwam_stove",
                "export_observations",
                {},
                blocking=True,
                return_response=True,
                context=export_context,
            )
        result = await hass.services.async_call(
            "hwam_stove",
            "export_observations",
            {"config_entry_id": loaded.config_entry.entry_id},
            blocking=True,
            return_response=True,
            context=export_context,
        )
        assert loaded.config_entry.entry_id not in json.dumps(result)
    finally:
        stoves.pop("synthetic-other")


async def test_schema_inventory_conflict_rejected(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1)
    payload = obj._payload()
    payload["next_sequence"] += 1
    with pytest.raises(ValueError, match="Inconsistent observation inventory"):
        validate(payload)


async def test_standby_cadence_and_clock_gap(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    capture(obj, now, 1, data=status_data() | {"phase": "Standby"})
    now[0] = 60
    assert capture(obj, now, 2)["quality"] == "continuous"
    now[0] = 59
    assert capture(obj, now, 3)["reason"] == "clock_discontinuity"


async def test_unknown_native_forecast_and_unhealthy_analytics_still_record(recorder):
    make, now = recorder
    obj = make()
    await obj.async_load()
    a = SimpleNamespace(healthy=False, state=SimpleNamespace(current=None))
    result = capture(
        obj,
        now,
        1,
        analytics=a,
        data=status_data()
        | {
            "time_to_new_fire_wood": "SECRET",
            "oxygen_level": float("nan"),
        },
    )
    assert result["hwam_minutes"] is None and result["oxygen_level"] is None
    assert (
        result["session"] is None
        and result["session_qualification"] == "analytics_unhealthy"
    )
    assert "SECRET" not in json.dumps(obj.export())
    validate(obj._payload())


async def test_expired_prediction_on_restart_keeps_metadata_not_timestamp(hass, loaded):
    loaded.shadow.model = ShadowModel.restore(warm().dump())
    loaded.freshness.clock = lambda: 0
    loaded.freshness.accept(sample(300).at, 0, 10)
    sensor = ShadowSensor(loaded)
    assert sensor.available and sensor.native_value is None
    assert sensor.extra_state_attributes["benchmark_status"] == "expired"
    assert sensor.extra_state_attributes["last_prediction_outcome"] == "restart"


async def test_real_reload_flushes_store_and_keeps_registry(
    hass, entry, recorder, loaded
):
    before = [
        (e.id, e.entity_id, e.unique_id, e.device_id) for e in registry_entries(hass)
    ]
    old = loaded.observations
    await old.async_checkpoint()
    rows = deepcopy(list(old.rows))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = hass.data["hwam_stove"]["stoves"][entry.entry_id].observations
    assert old.closed and old._cancel is None and old._task is None
    assert new is not old
    assert list(new.rows)[: len(rows)] == rows
    assert any(r[17] == "shutdown" for r in new.rows)
    assert sum(r[17] == "restart" for r in new.rows) == 2
    assert before == [
        (e.id, e.entity_id, e.unique_id, e.device_id) for e in registry_entries(hass)
    ]
