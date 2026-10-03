"""Current installation and historical registry representations."""

from homeassistant.helpers import device_registry as dr
import pytest

from .helpers import DOMAIN, ENTRY_ID, registry_entries
from .registry_helpers import HISTORICAL, seed_historical
from .test_entities import ROWS

pytestmark = pytest.mark.contract


async def test_current_devices(loaded, hass):
    registry = dr.async_get(hass)
    for kind in ("stove", "remote"):
        device = registry.async_get_device_by_identifier(
            (DOMAIN, f"{ENTRY_ID}-{kind}"), ENTRY_ID
        )
        assert device is not None
        assert device.config_entry_id == ENTRY_ID
        assert device.manufacturer == "HWAM"
        assert device.sw_version == ("1.2.8" if kind == "stove" else "4.5.9")
        if kind == "stove":
            assert device.model == "Synthetic series"


async def test_current_reload_preserves_ids(loaded, hass, entry):
    before = {(r.entity_id, r.unique_id, r.device_id) for r in registry_entries(hass)}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    after = {(r.entity_id, r.unique_id, r.device_id) for r in registry_entries(hass)}
    assert after == before


async def test_historical_registry_shape(hass, entry):
    entity_ids, device_ids = seed_historical(hass, entry)
    assert len(entity_ids) == 40
    assert len(device_ids) == 2
    assert {r.unique_id for r in registry_entries(hass)} == {
        "test_stove-" + row["key"] for row in ROWS
    }
    identifiers = {
        next(iter(dr.async_get(hass).async_get(d).identifiers))[1] for d in device_ids
    }
    assert identifiers == set(HISTORICAL["device_identifiers"])


def test_historical_and_current_schemes_are_distinct():
    assert set(HISTORICAL["device_identifiers"]).isdisjoint(
        HISTORICAL["current_device_identifiers"]
    )
    assert (
        HISTORICAL["entity_unique_id_prefix"]
        != HISTORICAL["current_entity_unique_id_prefix"]
    )
