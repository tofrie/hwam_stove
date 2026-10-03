"""A simulated public Stove contract, independent of controller transport."""

from copy import deepcopy
from datetime import datetime, time, timedelta
from unittest.mock import create_autospec

from pystove import Stove

DOMAIN = "hwam_stove"
HOST = "stove.invalid"
ENTRY_ID = "01K00000000000000000000000"
COMMANDS = (
    "set_burn_level", "set_night_lowering", "set_night_lowering_hours",
    "set_remote_refill_alarm", "set_time", "start",
)


def status_data():
    """Literal processed 0.3a1 status; synthetic values, no real identity."""
    return {
        "algorithm": 7, "burn_level": 3, "maintenance_alarms": [],
        "message_id": 42,
        "new_fire_wood_estimate": datetime(2024, 3, 1, 1, 5, 57),
        "night_begin_time": time(22, 15), "night_end_time": time(6, 30),
        "night_lowering": "Day", "operation_mode": "Normal",
        "oxygen_level": 20, "phase": "Burn", "refill_alarm": 1,
        "remote_refill_alarm": 0, "remote_version": "4.5.9",
        "room_temperature": 21, "safety_alarms": [], "stove_temperature": 273,
        "time_since_remote_msg": 31, "date_time": datetime(2024, 2, 29, 23, 58, 57),
        "time_to_new_fire_wood": timedelta(hours=1, minutes=7), "updating": 0,
        "valve1_position": 10, "valve2_position": 20, "valve3_position": 30,
        "firmware_version": "1.2.8",
    }


class SimulatedStove:
    """Record each call; never infer success or mutate status automatically.

    Methods use the installed 0.3a1 signatures. Tests explicitly arrange readback,
    False or exceptions; this does not invent controller application semantics.
    """

    def __init__(self):
        self.name = "Simulated stove"
        self.stove_ip = HOST
        self.stove_mdns = "stove.invalid"
        self.series = "Synthetic series"
        self.data = status_data()
        signatures = Stove()  # Construction alone has no I/O; never call create here.
        self.destroy = create_autospec(signatures.destroy)
        self.get_data = create_autospec(signatures.get_data, side_effect=self._read)
        for name in COMMANDS:
            method = create_autospec(getattr(signatures, name), return_value=True)
            setattr(self, name, method)

    async def _read(self):
        return deepcopy(self.data)

    def reset_commands(self):
        for name in COMMANDS:
            getattr(self, name).reset_mock()

    def assert_only_command(self, name, *args, **kwargs):
        getattr(self, name).assert_awaited_once_with(*args, **kwargs)
        for other in COMMANDS:
            if other != name:
                getattr(self, other).assert_not_called()


def registry_entries(hass, entry_id=ENTRY_ID):
    from homeassistant.helpers import entity_registry as er

    return er.async_entries_for_config_entry(er.async_get(hass), entry_id)


def entity_id_for(hass, platform, key, entry_id=ENTRY_ID):
    from homeassistant.helpers import entity_registry as er

    result = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry_id}-{key}"
    )
    assert result is not None
    return result
