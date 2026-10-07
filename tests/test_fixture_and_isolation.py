"""Prove fixture provenance and that test transport cannot reach a controller."""

from importlib.metadata import version
import json
from pathlib import Path
import socket
import sys
from unittest.mock import AsyncMock

import aiohttp
import pytest
from pytest_homeassistant_custom_component.plugins import HASocketBlockedError
import pytest_socket

from pystove import Stove

from .helpers import HOST, status_data

pytestmark = pytest.mark.contract


def test_exact_environment(installed_pystove):
    assert sys.version_info[:3] == (3, 14, 6)
    ha = version("homeassistant")
    framework = {"2026.9.4": "0.13.367", "2026.10.0b0": "0.13.368"}[ha]
    for package, expected in {
        "homeassistant": ha, "pytest": "9.0.3",
        "pytest-homeassistant-custom-component": framework,
        installed_pystove.get("distribution", "pystove"): installed_pystove["version"],
    }.items():
        assert version(package) == expected


async def test_fixture_matches_released_parser(installed_pystove):
    distribution = installed_pystove.get("distribution", "pystove")
    assert version(distribution) == installed_pystove["version"]
    raw = json.loads((Path(__file__).parent / "fixtures/raw_status.json").read_text())
    parser = Stove()  # No session/factory; only the released pure conversion executes.
    parser.get_raw_data = AsyncMock(return_value=raw)
    assert await parser.get_data() == status_data()
    parser.get_raw_data.assert_awaited_once_with()


async def test_factory_is_always_simulated(stove, stove_factory, no_controller_network):
    assert await Stove.create(HOST) is stove
    stove_factory.assert_awaited_once_with(HOST)
    assert no_controller_network == []


async def test_http_guard(no_controller_network):
    async with aiohttp.ClientSession() as session:
        with pytest.raises(AssertionError, match="Network forbidden: HTTP"):
            await session.get("http://stove.invalid/get_stove_data")
    assert no_controller_network == ["HTTP"]
    # Acknowledge only this intentional guard probe.
    no_controller_network.remove("HTTP")


def test_dns_guard(no_controller_network):
    with pytest.raises(AssertionError, match="Network forbidden: DNS"):
        socket.getaddrinfo(HOST, 80)
    assert no_controller_network == ["DNS"]
    no_controller_network.remove("DNS")


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
def test_ip_socket_guard(family):
    with (
        pytest.warns(UserWarning, match="socket.socket"),
        pytest.raises(pytest_socket.SocketBlockedError) as blocked,
    ):
        socket.socket(family, socket.SOCK_STREAM)
    # The HA plugin fails even caught socket attempts. Acknowledge exactly the
    # expected sentinel from this self-test; never clear other recorded attempts.
    assert blocked.value in HASocketBlockedError.instances
    HASocketBlockedError.instances.remove(blocked.value)
