"""Cached diagnostics: privacy, HA serialization and an explicit zero-I/O budget."""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, time, timedelta
from enum import IntEnum, StrEnum
import inspect
import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import aiohttp
from homeassistant.components import diagnostics as ha_diagnostics, http
from homeassistant.helpers.json import ExtendedJSONEncoder
from homeassistant.setup import async_setup_component
import pytest

from custom_components.hwam_stove.diagnostics import async_get_config_entry_diagnostics
from pystove import Stove, pystove

from .helpers import COMMANDS, DOMAIN, status_data
from .test_pystove_boundary import real_loaded, real_transport

__all__ = ["real_loaded", "real_transport"]
pytestmark = pytest.mark.contract


class Phase(StrEnum):
    BURN = "Burn"


class Flag(IntEnum):
    OFF = 0
    ON = 1


class Explosive:
    """A runtime object must never be serialized or converted to free text."""

    def __str__(self):
        raise AssertionError("Unexpected runtime object conversion")

    __repr__ = __str__


@contextmanager
def no_diagnostics_io(monkeypatch, coordinator=None):
    """Even swallowed or un-awaited attempts at I/O/refresh fail this contract."""
    spies = []

    def forbid(patches, obj, name):
        spy = Mock(side_effect=AssertionError(f"Diagnostics called {name}"))
        patches.setattr(obj, name, spy)
        spies.append(spy)

    with monkeypatch.context() as patches:
        for name, _ in inspect.getmembers(Stove, predicate=callable):
            if not name.startswith("__"):
                forbid(patches, Stove, name)
        if coordinator is not None:
            for name in (
                "async_refresh", "async_request_refresh", "async_set_updated_data",
                "_async_update_data", "async_shutdown",
            ):
                forbid(patches, coordinator, name)
            if type(coordinator.stove) is not Stove:
                for name in (*COMMANDS, "get_data", "destroy"):
                    forbid(patches, coordinator.stove, name)
        forbid(patches, aiohttp.ClientSession, "_request")
        for name in (
            "socket", "create_connection", "getaddrinfo", "gethostbyname",
            "gethostbyname_ex", "gethostbyaddr", "getnameinfo",
        ):
            forbid(patches, socket, name)
        yield
    for spy in spies:
        spy.assert_not_called()


async def inert_diagnostics(hass, entry, monkeypatch, coordinator=None):
    with no_diagnostics_io(monkeypatch, coordinator):
        result = await async_get_config_entry_diagnostics(hass, entry)
    # Test plain JSON as well as HA's production encoder; NaN/Infinity forbidden.
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert json.loads(json.dumps(result, cls=ExtendedJSONEncoder)) == result
    return result


async def test_complete_allowlisted_snapshot(hass, entry, loaded, stove, monkeypatch):
    stove.series = "HWAM 4500"
    stove.algo_version = "HW.4500.221202"
    before = deepcopy(loaded.data)
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result == {
        "schema_version": 1,
        "integration": {
            "version": "1.0.0rc1", "saynwerk_pystove_version": "0.3.0rc1",
            "config_entry_version": 2, "config_entry_minor_version": 1,
            "entry_state": "loaded", "runtime_present": True,
            "cache_present": True, "last_update_success": True,
            "effective_update_interval_seconds": 10.0,
        },
        "stove": {
            "model": "4500", "firmware_version": "1.2.8",
            "remote_version": "4.5.9", "phase": "Burn",
            "operation_mode": "Normal", "algorithm": 7,
            "identification_algorithm": "HW.4500.221202", "updating": False,
        },
        "status": {
            "room_temperature": 21, "stove_temperature": 273, "oxygen_level": 20,
            "valve1_position": 10, "valve2_position": 20, "valve3_position": 30,
            "burn_level": 3, "time_since_remote_msg": 31,
            "night_lowering": "Day", "refill_alarm": True,
            "maintenance_alarms": [], "safety_alarms": [],
        },
    }
    assert loaded.data == before


async def test_offline_cache_and_real_recovery(hass, entry, loaded, stove, monkeypatch):
    before = deepcopy(loaded.data)
    stove.get_data.side_effect = TimeoutError("private network failure")
    await loaded.async_refresh()
    assert not loaded.last_update_success
    offline = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert offline["integration"]["last_update_success"] is False
    assert offline["integration"]["cache_present"] is True
    assert offline["stove"]["phase"] == before["phase"]
    assert loaded.data == before  # Last successful cache, never claimed current.

    stove.get_data.side_effect = None
    recovered = status_data() | {"phase": "Standby", "room_temperature": 18}
    stove.get_data.return_value = recovered
    await loaded.async_refresh()
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["integration"]["last_update_success"] is True
    assert result["integration"]["effective_update_interval_seconds"] == 60.0
    assert result["stove"]["phase"] == "Standby"
    assert result["status"]["room_temperature"] == 18


async def test_none_update_does_not_fetch_for_diagnostics(
    hass, entry, loaded, stove, monkeypatch,
):
    stove.get_data.side_effect = None
    stove.get_data.return_value = None
    await loaded.async_refresh()
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["integration"]["last_update_success"] is False
    assert result["integration"]["cache_present"] is True


@pytest.mark.parametrize("cache", [None, {}, {"phase": "Standby"}, Explosive()])
async def test_missing_partial_cache(hass, entry, loaded, monkeypatch, cache):
    loaded.data = cache
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["integration"]["cache_present"] is (type(cache) is dict)
    assert result["stove"]["phase"] == (
        "Standby" if type(cache) is dict and cache else None
    )
    assert all(value is None for value in result["status"].values())


@pytest.mark.parametrize("domain_data", [None, {}, {"stoves": {}}, {"stoves": None}])
async def test_absent_runtime_before_setup(hass, entry, monkeypatch, domain_data):
    if domain_data is not None:
        hass.data[DOMAIN] = domain_data
    result = await inert_diagnostics(hass, entry, monkeypatch)
    assert result["integration"] == {
        "version": None, "saynwerk_pystove_version": "0.3.0rc1",
        "config_entry_version": 1, "config_entry_minor_version": 1,
        "entry_state": "not_loaded", "runtime_present": False,
        "cache_present": False, "last_update_success": None,
        "effective_update_interval_seconds": None,
    }
    assert all(value is None for value in result["stove"].values())
    assert all(value is None for value in result["status"].values())


async def test_after_unload(hass, entry, stove, monkeypatch):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    result = await inert_diagnostics(hass, entry, monkeypatch)
    assert result["integration"]["entry_state"] == "not_loaded"
    assert result["integration"]["runtime_present"] is False
    assert result["integration"]["version"] == "1.0.0rc1"
    stove.destroy.assert_awaited_once()


async def test_optional_identification_missing(hass, entry, loaded, monkeypatch):
    del loaded.stove.series
    loaded.data.pop("firmware_version")
    loaded.data.pop("remote_version")
    loaded.data.pop("algorithm")
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    for key in (
        "model", "firmware_version", "remote_version", "algorithm",
        "identification_algorithm",
    ):
        assert result["stove"][key] is None


async def test_enum_and_alarm_serialization(hass, entry, loaded, monkeypatch):
    alarm = pystove.MAINTENANCE_ALARMS[0]
    safety = pystove.SAFETY_ALARMS[0]
    alarm_enum = StrEnum("Alarm", {"VALUE": alarm})
    loaded.data.update({
        "phase": Phase.BURN, "updating": Flag.ON, "refill_alarm": Flag.OFF,
        "burn_level": Flag.ON, "maintenance_alarms": [alarm_enum.VALUE],
        "safety_alarms": [safety],
    })
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["stove"]["phase"] == "Burn"
    assert result["stove"]["updating"] is True
    assert result["status"]["refill_alarm"] is False
    assert result["status"]["burn_level"] == 1
    assert result["status"]["maintenance_alarms"] == [alarm]
    assert result["status"]["safety_alarms"] == [safety]
    result["status"]["maintenance_alarms"].clear()
    assert loaded.data["maintenance_alarms"] == [alarm_enum.VALUE]


@pytest.mark.parametrize("bad_value", [
    "198.51.100.42", True, float("nan"), float("inf"), float("-inf"),
    datetime(2025, 1, 1), time(22), timedelta(seconds=12), Explosive(), [], {},
])
async def test_numbers_cannot_serialize_arbitrary_values(
    hass, entry, loaded, monkeypatch, bad_value,
):
    loaded.data["room_temperature"] = bad_value
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["status"]["room_temperature"] is None


@pytest.mark.parametrize("field", [
    "phase", "operation_mode", "algorithm", "firmware_version", "remote_version",
    "updating", "night_lowering", "refill_alarm", "maintenance_alarms",
    "safety_alarms",
])
async def test_expected_fields_reject_free_text(
    hass, entry, loaded, monkeypatch, field,
):
    loaded.data[field] = "PRIVATE-SSID-token@example.invalid"
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    group = "stove" if field in result["stove"] else "status"
    assert result[group][field] is None


@pytest.mark.parametrize("alarms", [["unknown private name"], [Explosive()], [1],
                                   {"secret": 1}, ("DoorAlarm",)])
async def test_unknown_alarm_is_not_reported_as_clear(
    hass, entry, loaded, monkeypatch, alarms,
):
    loaded.data["maintenance_alarms"] = alarms
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    assert result["status"]["maintenance_alarms"] is None


async def test_private_cache_config_and_exception_never_leave_snapshot(
    hass, entry, loaded, monkeypatch, caplog,
):
    secret = "PRIVATE-canary-token-198.51.100.42"
    hass.config_entries.async_update_entry(
        entry, data=dict(entry.data) | {"host": secret, "name": secret},
        options={"password": secret}, title=secret,
    )
    for name in ("name", "stove_ip", "stove_mdns", "ssid", "mac", "series",
                 "algo_version", "raw_data", "session"):
        monkeypatch.setattr(loaded.stove, name, secret, raising=False)
    loaded.data.update({
        "host": secret, "ssid": secret, "mac": secret, "name": secret,
        "unique_id": secret, "raw_response": secret, "new_protocol_field": secret,
        "date_time": datetime(2025, 5, 5, 12, 13, 14),
        "night_begin_time": time(23, 59), "internal": Explosive(),
    })
    loaded.last_exception = RuntimeError(secret)
    before = caplog.text
    result = await inert_diagnostics(hass, entry, monkeypatch, loaded)
    text = json.dumps(result)
    assert secret not in text
    assert entry.entry_id not in text
    assert result["stove"]["model"] is None
    assert result["stove"]["identification_algorithm"] is None
    assert not ({"date_time", "night_begin_time", "night_end_time",
                 "time_to_new_fire_wood", "new_fire_wood_estimate", "message_id"}
                & result["status"].keys())
    assert caplog.text == before


async def test_real_library_zero_calls_or_new_tasks(
    hass, entry, real_loaded, real_transport, monkeypatch,
):
    session, = real_transport.sessions
    calls = deepcopy(session.calls)
    tasks = asyncio.all_tasks()
    for _ in range(3):
        result = await inert_diagnostics(hass, entry, monkeypatch, real_loaded)
        assert result["stove"]["firmware_version"] == "1.2.8"
    assert session.calls == calls
    assert session.close_calls == 0
    assert len(real_transport.sessions) == 1
    assert real_transport.destroyed == []
    assert asyncio.all_tasks() == tasks


@pytest.mark.parametrize("state", ["normal", "no_cache", "partial", "unavailable"])
async def test_native_ha_download_serializes_without_network(
    hass, entry, loaded, monkeypatch, state,
):
    """Exercise native discovery, admin view and encoder without an HTTP server."""
    assert await async_setup_component(hass, "diagnostics", {})
    await hass.async_block_till_done()
    if state == "no_cache":
        loaded.data = None
    elif state == "partial":
        loaded.data = {"phase": "Burn"}
    elif state == "unavailable":
        loaded.last_update_success = False
    # A request object in memory: no HTTP client, local listener or socket.
    request = {"hass_user": SimpleNamespace(is_admin=True)}

    class Request(dict):
        app = {http.KEY_HASS: hass}

    with no_diagnostics_io(monkeypatch, loaded):
        response = await ha_diagnostics.DownloadDiagnosticsView().get(
            Request(request), "config_entry", entry.entry_id,
        )
    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["data"] == await inert_diagnostics(
        hass, entry, monkeypatch, loaded
    )
    assert entry.entry_id not in json.dumps(payload["data"])
    # HA owns this envelope/filename; an integration cannot redact it.
    assert entry.entry_id in response.headers["Content-Disposition"]
    assert "home_assistant" in payload
    assert "integration_manifest" in payload
