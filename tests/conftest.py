"""Use real HA lifecycle/registries with a strictly offline Stove boundary."""

from pathlib import Path
import socket
from unittest.mock import AsyncMock

import aiohttp
from homeassistant.const import CONF_HOST, CONF_NAME
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from pystove import Stove

from .command_cases import SYNC_UTC_TIME
from .dependency_contract import verify_installed
from .helpers import DOMAIN, ENTRY_ID, HOST, SimulatedStove


def pytest_addoption(parser):
    parser.addoption(
        "--pystove-scenario",
        choices=("baseline", "candidate", "release", "published"),
        default="published", help="Verify one exact approved test artifact",
    )


@pytest.fixture(scope="session", autouse=True)
def installed_pystove(pytestconfig):
    return verify_installed(pytestconfig.getoption("--pystove-scenario"))


@pytest.fixture(autouse=True)
def ha_clock_now(monkeypatch):
    """Freeze only the integration's HA utcnow binding, not HA timers or OS TZ."""
    from custom_components.hwam_stove import _clock

    monkeypatch.setattr(_clock, "utcnow", lambda: SYNC_UTC_TIME)


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations, monkeypatch):
    """Allow HA to discover this checkout, not a fake integration module."""
    import custom_components

    monkeypatch.setattr(custom_components, "__path__", [
        str(Path(__file__).resolve().parents[1] / "custom_components")
    ])


@pytest.fixture(autouse=True)
def no_controller_network(monkeypatch):
    """Fail before HTTP or DNS; pytest-socket also blocks all IP sockets."""
    attempts = []

    async def no_http(*args, **kwargs):
        attempts.append("HTTP")
        raise AssertionError("Network forbidden: HTTP")

    def no_dns(*args, **kwargs):
        attempts.append("DNS")
        raise AssertionError("Network forbidden: DNS")

    monkeypatch.setattr(aiohttp.ClientSession, "_request", no_http)
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    yield attempts
    assert attempts == [], "Unexpected attempted HTTP/DNS access"


@pytest.fixture(autouse=True)
def stove():
    return SimulatedStove()


@pytest.fixture(autouse=True)
def stove_factory(monkeypatch, stove):
    factory = AsyncMock(return_value=stove)
    monkeypatch.setattr(Stove, "create", factory)
    return factory


@pytest.fixture
def entry(hass):
    config_entry = MockConfigEntry(
        domain=DOMAIN, title="Test stove", version=1, entry_id=ENTRY_ID,
        data={CONF_HOST: HOST, CONF_NAME: "Test stove"},
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
async def loaded(hass, entry):
    await hass.config.async_set_time_zone("Europe/Berlin")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield hass.data[DOMAIN]["stoves"][entry.entry_id]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def entities(hass, entry, loaded):
    """Instantiate all descriptions, including disabled ones, through platform setup.

    State-write sinks are mocked for these additional, unregistered instances.
    The loaded fixture independently tests normal HA registration/state handling.
    """
    from importlib import import_module
    from unittest.mock import Mock

    result = {}
    for platform in (
        "sensor", "binary_sensor", "number", "switch", "button", "time", "datetime"
    ):
        captured = []
        module = import_module(f"custom_components.hwam_stove.{platform}")
        await module.async_setup_entry(hass, entry, captured.extend)
        for entity in captured:
            entity.hass = hass
            entity.async_write_ha_state = Mock()
            entity.async_schedule_update_ha_state = Mock()
            if hasattr(entity, "_handle_coordinator_update"):
                entity._handle_coordinator_update()
            result[platform, entity.entity_description.key] = entity
    return result
