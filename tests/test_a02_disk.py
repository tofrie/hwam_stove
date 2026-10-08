"""Exercise real atomic HA Store I/O outside HA's normal in-memory test fixture."""

from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import CoreState
from homeassistant.helpers import storage
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
import pytest

from custom_components.hwam_stove._analytics import Analytics
from custom_components.hwam_stove._analytics_model import dump

from .test_a02_model import START, run

# Save real methods before HA's autouse mock_storage patches them. These test-only
# adapters bypass that fixture, not the production public-API implementation.
REAL_LOAD = Store._async_load
REAL_WRITE = Store._async_write_data


@pytest.fixture
def disk(hass, tmp_path, monkeypatch):
    class DiskStore(Store):
        _async_load = REAL_LOAD
        _async_write_data = REAL_WRITE

        @property
        def path(self):
            return str(tmp_path / self.key)

    monkeypatch.setattr(Analytics, "_store", lambda a: DiskStore(
        hass, 1, a.key, atomic_writes=True,
    ))
    a = Analytics(hass, "disk-test", clock=lambda: 10, utcnow=lambda: START)
    return a


async def test_real_atomic_write_and_verified_public_read(disk):
    a = disk
    await a.async_load()
    with patch.object(storage, "write_utf8_file_atomic",
                      wraps=storage.write_utf8_file_atomic) as atomic:
        await a.async_observe(a.read_started(), {
            "phase": "Ignition", "refill_alarm": 0, "stove_temperature": 1,
        })
    assert a.healthy
    atomic.assert_called_once()
    stored = json.loads(Path(a.store.path).read_text())
    assert stored["version"] == 1
    assert stored["data"] == dump(a.state)


async def test_real_swallowed_writeerror_detected_without_reset(disk):
    a = disk
    a.state = run(["Ignition", "Burn", "Standby"])
    await a.store.async_save(dump(a.state))
    original = Path(a.store.path).read_bytes()
    await a.async_load()
    with patch.object(storage, "write_utf8_file_atomic", side_effect=WriteError):
        await a.async_observe(a.read_started(), {
            "phase": "Ignition", "refill_alarm": 0, "stove_temperature": 1,
        })
    assert not a.healthy
    assert a.state.completed == 1
    assert Path(a.store.path).read_bytes() == original


@pytest.mark.parametrize("kind", [
    "invalid_json", "empty", "version", "minor_version", "payload",
])
async def test_disk_corruption_unknown_version_and_next_restart(disk, hass, kind):
    a = disk
    value = {"version": 1, "minor_version": 1, "key": a.key,
             "data": dump(run(["Ignition", "Burn", "Standby"]))}
    if kind == "version":
        value["version"] = 99
    elif kind == "minor_version":
        value["minor_version"] = 99
    elif kind == "payload":
        value["data"]["schema"] = 99
    text = ("{" if kind == "invalid_json" else "{}" if kind == "empty"
            else json.dumps(value))
    path = Path(a.store.path)
    path.write_text(text)
    await a.async_load()
    assert not a.healthy
    await a.async_stop()
    if kind == "invalid_json":
        assert not path.exists()
        quarantined, = path.parent.glob(path.name + ".corrupt.*")
        assert quarantined.read_text() == text
    else:
        assert path.read_text() == text
    again = Analytics(hass, "disk-test")
    await again.async_load()
    assert not again.healthy


async def test_ha_final_write_is_pending_not_falsely_reported_failed(
    disk, hass, caplog,
):
    a = disk
    await a.async_load()
    a.state = run(["Ignition", "Burn", "Glow"])
    await a._save()
    original = Path(a.store.path).read_bytes()
    a.state.current.peak = 300
    hass.state = CoreState.stopping
    await a.async_stop()
    assert a.healthy
    assert Path(a.store.path).read_bytes() == original
    assert not a.online
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    assert json.loads(Path(a.store.path).read_text())["data"]["current"]["peak"] == 300
    assert "Heating analytics unavailable" not in caplog.text


async def test_prior_complete_duration_survives_partial_session(disk):
    a = disk
    a.state = run(["Ignition", "Burn", "Standby"])
    await a.store.async_save(dump(a.state))
    await a.async_load()
    a.utcnow = lambda: START + timedelta(days=1)
    await a.async_observe(a.read_started(), {
        "phase": "Burn", "refill_alarm": 1, "stove_temperature": 500,
    })
    await a.async_observe(a.read_started(), {
        "phase": "Standby", "refill_alarm": 0,
    })
    assert a.state.completed == 1
    assert a.state.last_complete.elapsed == 20
    assert a.state.partial_completed == 1
    assert a.state.requests == 0
