"""M07: HA-local clock fields, preserved instants and unchanged command behavior."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from aiohttp import ClientConnectionError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest

from custom_components.hwam_stove import _clock
from pystove import pystove

from .command_cases import SYNC_LOCAL_TIME, SYNC_UTC_TIME
from .helpers import COMMANDS, DOMAIN, entity_id_for
from .test_h04_commands import assert_unconfirmed, no_readback

pytestmark = pytest.mark.contract
BERLIN = ZoneInfo("Europe/Berlin")

# Literal expected local fields/offset/fold, including both sides of each DST
# transition. No expected value is calculated by the implementation under test.
CASES = [
    ("summer", datetime(2026, 7, 1, 10, tzinfo=UTC), "2026-07-01T12:00:00+02:00", 0),
    ("winter", datetime(2026, 1, 1, 10, tzinfo=UTC), "2026-01-01T11:00:00+01:00", 0),
    ("new_york", datetime(2026, 7, 1, 8, tzinfo=ZoneInfo("America/New_York")),
     "2026-07-01T14:00:00+02:00", 0),
    ("positive",
     datetime(2026, 7, 1, 10, tzinfo=timezone(timedelta(hours=5, minutes=30))),
     "2026-07-01T06:30:00+02:00", 0),
    ("negative", datetime(2026, 7, 1, 8, tzinfo=timezone(timedelta(hours=-4))),
     "2026-07-01T14:00:00+02:00", 0),
    ("spring_before", datetime(2026, 3, 29, 0, 59, 59, tzinfo=UTC),
     "2026-03-29T01:59:59+01:00", 0),
    ("spring_after", datetime(2026, 3, 29, 1, tzinfo=UTC),
     "2026-03-29T03:00:00+02:00", 0),
    ("autumn_before", datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC),
     "2026-10-25T02:59:59+02:00", 0),
    ("autumn_after", datetime(2026, 10, 25, 1, tzinfo=UTC),
     "2026-10-25T02:00:00+01:00", 1),
    ("ambiguous_first", datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0),
     "2026-10-25T02:30:00+02:00", 0),
    ("ambiguous_second", datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1),
     "2026-10-25T02:30:00+01:00", 1),
    # An imaginary aware wall time still carries an offset/instant. Normalize
    # through UTC instead of returning impossible HA-local clock fields.
    ("nonexistent_first", datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN, fold=0),
     "2026-03-29T03:30:00+02:00", 0),
    ("nonexistent_second", datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN, fold=1),
     "2026-03-29T01:30:00+01:00", 0),
    ("year_rollover", datetime(2026, 12, 31, 23, 59, 58, tzinfo=UTC),
     "2027-01-01T00:59:58+01:00", 0),
]


def assert_local(sent, expected, fold=0):
    assert sent.isoformat() == expected
    assert sent.tzinfo == BERLIN
    assert sent.fold == fold


async def test_M07_clock_uses_ha_local_time(entities, stove):
    """The former strict xfail is now an ordinary regression assertion."""
    await entities["datetime", "date_time"].async_set_value(
        datetime(2024, 7, 1, 10, tzinfo=UTC)
    )
    stove.set_time.assert_awaited_once()
    sent = stove.set_time.call_args.args[0]
    assert_local(sent, "2024-07-01T12:00:00+02:00")


@pytest.mark.parametrize("name,value,expected,fold", CASES, ids=[c[0] for c in CASES])
async def test_M07_aware_matrix(name, value, expected, fold, entities, loaded, stove):
    entity = entities["datetime", "date_time"]
    previous = entity.native_value
    with no_readback(loaded, stove):
        await entity.async_set_value(value)
    sent = stove.set_time.call_args.args[0]
    assert_local(sent, expected, fold)
    assert sent.astimezone(UTC) == value.astimezone(UTC)
    stove.assert_only_command("set_time", sent)
    assert entity.native_value == previous  # read path is unchanged


NAIVE_CASES = [
    (datetime(2026, 7, 1, 12), "2026-07-01T12:00:00+02:00", 0),
    (datetime(2026, 1, 1, 12), "2026-01-01T12:00:00+01:00", 0),
    (datetime(2026, 10, 25, 2, 30), "2026-10-25T02:30:00+02:00", 0),
    (datetime(2026, 3, 29, 2, 30), "2026-03-29T03:30:00+02:00", 0),
]


@pytest.mark.parametrize("value,expected,fold", NAIVE_CASES)
async def test_M07_direct_naive_uses_ha_convention(
    value, expected, fold, entities, loaded, stove
):
    with no_readback(loaded, stove):
        await entities["datetime", "date_time"].async_set_value(value)
    sent = stove.set_time.call_args.args[0]
    assert_local(sent, expected, fold)
    stove.assert_only_command("set_time", sent)


@pytest.fixture
async def clock_service_runtime(hass, entry):
    """Enable the optional datetime entity through HA's actual registry."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    er.async_get(hass).async_get_or_create(
        "datetime", DOMAIN, f"{entry.entry_id}-date_time",
        config_entry=entry, disabled_by=None,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield hass.data[DOMAIN]["stoves"][entry.entry_id]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def clock_service(hass, path, value=None):
    platform, key, service, data = (
        ("button", "sync_clock", "press", {}) if path == "sync" else
        ("datetime", "date_time", "set_value", {"datetime": value.isoformat()})
    )
    await hass.services.async_call(platform, service, {
        "entity_id": entity_id_for(hass, platform, key), **data,
    }, blocking=True)


@pytest.mark.parametrize("value,expected,fold", [c[1:] for c in CASES] + NAIVE_CASES)
async def test_M07_real_datetime_service(
    value, expected, fold, clock_service_runtime, hass, stove
):
    before = hass.states.get(entity_id_for(hass, "datetime", "date_time"))
    with no_readback(clock_service_runtime, stove):
        await clock_service(hass, "datetime", value)
        await hass.async_block_till_done()
    sent = stove.set_time.call_args.args[0]
    assert_local(sent, expected, fold)
    stove.assert_only_command("set_time", sent)
    assert hass.states.get(before.entity_id) == before


@pytest.mark.parametrize("name,value,expected,fold", CASES, ids=[c[0] for c in CASES])
async def test_M07_sync_ha_now_matrix(
    name, value, expected, fold, clock_service_runtime, hass, stove, monkeypatch
):
    now = Mock(return_value=value.astimezone(UTC))
    monkeypatch.setattr(_clock, "utcnow", now)
    with no_readback(clock_service_runtime, stove):
        await clock_service(hass, "sync")
    now.assert_called_once_with()
    sent = stove.set_time.call_args.args[0]
    assert_local(sent, expected, fold)
    stove.assert_only_command("set_time", sent)
    # HA still records a press attempt separately from the time written to HWAM.
    state = hass.states.get(entity_id_for(hass, "button", "sync_clock"))
    assert dt_util.parse_datetime(state.state) is not None


@pytest.mark.parametrize("path", ["datetime", "sync"])
async def test_M07_uses_current_ha_zone(
    path, entities, loaded, hass, stove, monkeypatch
):
    """Resolve configured timezone for each action, never cache Europe/Berlin."""
    fixed = datetime(2026, 7, 1, 10, 20, 30, tzinfo=UTC)
    monkeypatch.setattr(_clock, "utcnow", lambda: fixed)
    for zone, expected in [
        ("Europe/Berlin", "2026-07-01T12:20:30+02:00"),
        ("Asia/Kathmandu", "2026-07-01T16:05:30+05:45"),
        ("America/Los_Angeles", "2026-07-01T03:20:30-07:00"),
        ("UTC", "2026-07-01T10:20:30+00:00"),
    ]:
        await hass.config.async_set_time_zone(zone)
        stove.reset_commands()
        with no_readback(loaded, stove):
            if path == "sync":
                await entities["button", "sync_clock"].async_press()
            else:
                await entities["datetime", "date_time"].async_set_value(fixed)
        sent = stove.set_time.call_args.args[0]
        assert sent.isoformat() == expected
        assert sent.tzinfo == dt_util.get_default_time_zone()
        assert sent.astimezone(UTC) == fixed
        stove.assert_only_command("set_time", sent)


@pytest.mark.parametrize("path", ["datetime", "sync"])
@pytest.mark.parametrize("confirmed", [True, False])
async def test_M07_service_confirmation(
    path, confirmed, clock_service_runtime, hass, stove
):
    stove.set_time.return_value = confirmed
    with no_readback(clock_service_runtime, stove):
        if confirmed:
            await clock_service(hass, path, SYNC_UTC_TIME)
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await clock_service(hass, path, SYNC_UTC_TIME)
            assert_unconfirmed(raised.value)
    stove.assert_only_command("set_time", SYNC_LOCAL_TIME)


@pytest.mark.parametrize("path", ["datetime", "sync"])
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, RuntimeError,
                                        ClientConnectionError, TimeoutError])
async def test_M07_exception_identity(path, error_type, entities, loaded, stove):
    failure = error_type("original clock failure")
    stove.set_time.side_effect = failure
    with no_readback(loaded, stove), pytest.raises(error_type) as raised:
        if path == "sync":
            await entities["button", "sync_clock"].async_press()
        else:
            await entities["datetime", "date_time"].async_set_value(SYNC_UTC_TIME)
    assert raised.value is failure
    stove.assert_only_command("set_time", SYNC_LOCAL_TIME)


@pytest.mark.parametrize("path", ["datetime", "sync"])
async def test_M07_task_cancellation(path, entities, loaded, stove):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def pending(value):
        entered.set()
        try:
            await release.wait()
        finally:
            finished.set()

    stove.set_time.side_effect = pending
    action = (entities["button", "sync_clock"].async_press() if path == "sync" else
              entities["datetime", "date_time"].async_set_value(SYNC_UTC_TIME))
    with no_readback(loaded, stove):
        task = asyncio.create_task(action)
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            task.cancel("M07 cancellation")
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    stove.assert_only_command("set_time", SYNC_LOCAL_TIME)


async def test_M07_unavailable_sync_does_not_read_clock_or_send(
    clock_service_runtime, hass, stove, monkeypatch
):
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    await clock_service_runtime.async_refresh()
    now = Mock(side_effect=AssertionError("Unavailable button must not run"))
    monkeypatch.setattr(_clock, "utcnow", now)
    with no_readback(clock_service_runtime, stove):
        await clock_service(hass, "sync")
    now.assert_not_called()
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()


WIRE_CASES = [
    (datetime(2026, 7, 1, 10, 20, 30, 123456, tzinfo=UTC),
     '{"year":2026,"month":6,"day":1,"hours":12,"minutes":20,"seconds":30}'),
    (datetime(2026, 1, 1, 10, 20, 30, tzinfo=UTC),
     '{"year":2026,"month":0,"day":1,"hours":11,"minutes":20,"seconds":30}'),
    (datetime(2026, 12, 31, 23, 59, 58, tzinfo=UTC),
     '{"year":2027,"month":0,"day":1,"hours":0,"minutes":59,"seconds":58}'),
    (datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0),
     '{"year":2026,"month":9,"day":25,"hours":2,"minutes":30,"seconds":0}'),
    (datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1),
     '{"year":2026,"month":9,"day":25,"hours":2,"minutes":30,"seconds":0}'),
    (datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN),
     '{"year":2026,"month":2,"day":29,"hours":3,"minutes":30,"seconds":0}'),
]


@pytest.mark.parametrize("path", ["datetime", "sync"])
@pytest.mark.parametrize("value,payload", WIRE_CASES)
async def test_M07_actual_pystove_serialization(
    path, value, payload, entities, loaded, stove, monkeypatch, installed_pystove
):
    """Exercise published set_time AND _post with a fake HTTP session only."""
    distribution = installed_pystove.get("distribution", "pystove")
    assert version(distribution) == installed_pystove["version"]
    client = pystove.Stove()
    client.stove_host = "clock.invalid"
    response = AsyncMock()
    response.text.return_value = '{"response":"OK"}'
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = Mock()
    session.post.return_value = context
    client._session = session
    stove.set_time.side_effect = client.set_time
    monkeypatch.setattr(_clock, "utcnow", lambda: value.astimezone(UTC))
    # The library's OS-local fallback must never be consulted by either path.
    system_datetime = Mock()
    system_datetime.now.side_effect = AssertionError("OS clock fallback used")
    monkeypatch.setattr(pystove, "datetime", system_datetime)
    with no_readback(loaded, stove):
        if path == "sync":
            await entities["button", "sync_clock"].async_press()
        else:
            await entities["datetime", "date_time"].async_set_value(value)
    sent = stove.set_time.call_args.args[0]
    stove.assert_only_command("set_time", sent)
    session.post.assert_called_once_with("http://clock.invalid/set_time", data=payload)
    context.__aenter__.assert_awaited_once_with()
    context.__aexit__.assert_awaited_once_with(None, None, None)
    response.text.assert_awaited_once_with()
    system_datetime.now.assert_not_called()


@pytest.mark.parametrize("system_zone,system_wall", [
    ("UTC", "2026-07-01T10:20:30"),
    ("America/Los_Angeles", "2026-07-01T03:20:30"),
])
def test_M07_sync_with_different_process_timezone(system_zone, system_wall):
    """Real process TZ changes are confined to disposable, network-blocked children."""
    script = '''
import asyncio
from datetime import UTC, datetime
import json
import socket
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

attempts = []
def forbidden(*args, **kwargs):
    attempts.append("network")
    raise AssertionError("Network forbidden in timezone subprocess")
socket.getaddrinfo = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden

import aiohttp
async def no_http(*args, **kwargs):
    forbidden()
aiohttp.ClientSession._request = no_http

from homeassistant.util import dt as dt_util
from zoneinfo import ZoneInfo
from custom_components.hwam_stove import _clock
from custom_components.hwam_stove.button import BUTTON_DESCRIPTIONS

time.tzset()
fixed = datetime(2026, 7, 1, 10, 20, 30, tzinfo=UTC)
_clock.utcnow = lambda: fixed
dt_util.set_default_time_zone(ZoneInfo("Europe/Berlin"))
stove = SimpleNamespace(set_time=AsyncMock(return_value=True))
button = next(d for d in BUTTON_DESCRIPTIONS if d.key == "sync_clock")
assert asyncio.run(button.press_func(stove)) is True
stove.set_time.assert_awaited_once()
assert not attempts
print(json.dumps({
    "system": datetime.fromtimestamp(fixed.timestamp()).isoformat(),
    "sent": stove.set_time.call_args.args[0].isoformat(),
    "ha_zone": str(stove.set_time.call_args.args[0].tzinfo),
    "network_attempts": attempts,
}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "TZ": system_zone},
        check=True, capture_output=True, text=True, timeout=30,
    )
    assert json.loads(result.stdout) == {
        "system": system_wall,
        "sent": "2026-07-01T12:20:30+02:00",
        "ha_zone": "Europe/Berlin",
        "network_attempts": [],
    }
