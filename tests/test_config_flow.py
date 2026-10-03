"""Current UI and successful legacy import contracts, with real HA flows."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
import pytest

from pystove import pystove

from .helpers import DOMAIN, HOST

pytestmark = pytest.mark.contract


async def test_user_flow(hass, stove, stove_factory):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    with patch(
        "custom_components.hwam_stove.async_setup_entry", AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST, "name": "Test stove"})
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["title"] == "Test stove"
    assert result["data"] == {"host": HOST, "name": "Test stove"}
    stove_factory.assert_awaited_once_with(HOST)
    stove.destroy.assert_awaited_once_with()


async def test_duplicate_exact_host(hass, entry, stove_factory):
    result = await hass.config_entries.flow.async_init(DOMAIN,
        context={"source": SOURCE_USER}, data={"host": HOST, "name": "Other name"})
    assert result["type"] == "form"
    assert result["errors"] == {"base": "already_configured"}
    stove_factory.assert_not_called()


async def test_unknown_identity(hass, stove):
    stove.name = pystove.UNKNOWN
    result = await hass.config_entries.flow.async_init(DOMAIN,
        context={"source": SOURCE_USER}, data={"host": HOST, "name": "Test stove"})
    assert result["errors"] == {"base": "cannot_connect"}
    stove.destroy.assert_awaited_once_with()


async def test_yaml_schema_and_import(hass, stove_factory):
    from custom_components.hwam_stove import CONFIG_SCHEMA, async_setup

    config = CONFIG_SCHEMA({DOMAIN: {"legacy_stove": {
        "host": HOST, "monitored_variables": ["room_temperature"],
    }}})
    assert config[DOMAIN]["legacy_stove"]["monitored_variables"] == ["room_temperature"]
    default = CONFIG_SCHEMA({DOMAIN: {"x": {"host": HOST}}})
    assert default[DOMAIN]["x"]["monitored_variables"] == []
    with patch(
        "custom_components.hwam_stove.async_setup_entry", AsyncMock(return_value=True)
    ):
        assert await async_setup(hass, config)
        await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].source == SOURCE_IMPORT
    assert entries[0].data == {"name": "legacy_stove", "host": HOST}
    stove_factory.assert_awaited_once_with(HOST)
