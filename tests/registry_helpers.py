"""Historical identities transcribed from the pinned 1.0.0b2 release.

No old runtime is executed and no migration is supplied by this fixture.
"""

import json
from pathlib import Path

from homeassistant.helpers import device_registry as dr, entity_registry as er

from .helpers import DOMAIN
from .test_entities import ROWS

HISTORICAL = json.loads(
    (Path(__file__).parent / "fixtures/historical_registry.json").read_text()
)


def seed_historical(hass, entry):
    hass.config_entries.async_update_entry(entry, data=HISTORICAL["entry_data"])
    devices = {}
    for kind in ("stove", "remote"):
        devices[kind] = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"test_stove-{kind}")},
            manufacturer="HWAM", name=f"Historical {kind}",
        )
    ids = set()
    for row in ROWS:
        entity = er.async_get(hass).async_get_or_create(
            row["platform"], DOMAIN, f'test_stove-{row["key"]}',
            config_entry=entry, device_id=devices[row["device"]].id,
            suggested_object_id=f'original_{row["key"]}',
        )
        ids.add(entity.entity_id)
    return ids, {device.id for device in devices.values()}
