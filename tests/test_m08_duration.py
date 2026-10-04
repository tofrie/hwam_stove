"""M08: signed total duration with the existing integer-second precision."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.helpers import entity_platform, entity_registry as er
import pytest

from pystove import pystove

from .helpers import COMMANDS, DOMAIN, ENTRY_ID, entity_id_for, status_data

pytestmark = pytest.mark.contract
KEY = pystove.DATA_TIME_TO_NEW_FIREWOOD


def duration_sensor(hass):
    target = entity_id_for(hass, "sensor", KEY)
    for platform in entity_platform.async_get_platforms(hass, DOMAIN):
        if target in platform.entities:
            return platform.entities[target]
    raise AssertionError(f"Duration sensor missing: {target}")


async def test_M08_duration_includes_days(entities, loaded):
    """The former strict-xfail is now an ordinary regression."""
    loaded.data["time_to_new_fire_wood"] = timedelta(days=1, hours=2)
    sensor = entities["sensor", "time_to_new_fire_wood"]
    sensor._handle_coordinator_update()
    assert sensor.native_value == 93600, "Duration must include all 26 hours"
    assert type(sensor.native_value) is int


DURATIONS = [
    pytest.param(timedelta(0), 0, id="zero"),
    pytest.param(timedelta(seconds=1), 1, id="one-second"),
    pytest.param(timedelta(seconds=59), 59, id="59-seconds"),
    pytest.param(timedelta(minutes=1), 60, id="one-minute"),
    pytest.param(timedelta(hours=23, minutes=59, seconds=59), 86399,
                 id="before-one-day"),
    pytest.param(timedelta(hours=24), 86400, id="one-day"),
    pytest.param(timedelta(hours=26), 93600, id="26-hours"),
    pytest.param(timedelta(days=3, hours=4, minutes=5, seconds=6), 273906,
                 id="multiple-days"),
    pytest.param(timedelta(seconds=-1), -1, id="negative-second"),
    pytest.param(timedelta(hours=-2), -7200, id="negative-two-hours"),
    pytest.param(timedelta(days=-1), -86400, id="negative-day"),
    pytest.param(timedelta(days=-1, hours=2), -79200,
                 id="negative-day-positive-hours"),
    # Preserve the existing omission of the normalized microseconds component.
    # For negative fractions this is floor, not int(total_seconds()) truncation.
    pytest.param(timedelta(microseconds=1), 0, id="positive-microsecond"),
    pytest.param(timedelta(seconds=1, microseconds=999999), 1,
                 id="positive-fraction"),
    pytest.param(timedelta(hours=26, microseconds=999999), 93600,
                 id="days-and-fraction"),
    pytest.param(timedelta(microseconds=-1), -1, id="negative-microsecond"),
    pytest.param(timedelta(seconds=-1, microseconds=-500000), -2,
                 id="negative-fraction"),
    pytest.param(timedelta(days=-1, hours=2, microseconds=1), -79200,
                 id="negative-day-positive-fraction"),
]


@pytest.mark.parametrize("duration,expected", DURATIONS)
async def test_M08_duration_matrix_in_real_ha(loaded, hass, stove, duration, expected):
    """Exercise the real coordinator callback, native type and HA unit conversion."""
    sensor = duration_sensor(hass)
    registered_before = er.async_get(hass).async_get(sensor.entity_id)
    reads = stove.get_data.await_count
    stove.data[KEY] = duration
    with patch.object(loaded, "async_request_refresh",
                      wraps=loaded.async_request_refresh) as requested:
        await loaded.async_refresh()  # one explicit simulated ordinary read
        await hass.async_block_till_done()
        requested.assert_not_called()
    assert sensor.native_value == expected
    assert type(sensor.native_value) is int
    assert sensor.available
    assert loaded.data[KEY] == duration  # Do not rewrite the timedelta source.
    assert isinstance(loaded.data[KEY], timedelta)
    assert stove.get_data.await_count == reads + 1
    for method in COMMANDS:
        getattr(stove, method).assert_not_called()

    # HA uses the existing suggested hours for this registered sensor. Native
    # seconds are integers; HA's existing display-unit conversion is a float.
    state = hass.states.get(sensor.entity_id)
    assert state.attributes["unit_of_measurement"] == "h"
    assert float(state.state) == pytest.approx(expected / 3600, abs=1e-12)
    assert er.async_get(hass).async_get(sensor.entity_id) == registered_before


async def test_M08_normal_fixture_and_sensor_contract(loaded, hass, stove):
    """The existing 67-minute fixture and description remain unchanged."""
    sensor = duration_sensor(hass)
    description = sensor.entity_description
    assert loaded.data == status_data()
    assert loaded.data[KEY] == timedelta(hours=1, minutes=7)
    assert sensor.native_value == 4020
    assert type(sensor.native_value) is int
    assert sensor.unique_id == f"{ENTRY_ID}-{KEY}"
    assert sensor.device_info["identifiers"] == {(DOMAIN, f"{ENTRY_ID}-stove")}
    assert description.translation_key == "time_to_new_firewood"
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.device_class == SensorDeviceClass.DURATION
    assert sensor.native_unit_of_measurement == UnitOfTime.SECONDS
    assert description.suggested_unit_of_measurement == UnitOfTime.HOURS
    assert sensor.suggested_display_precision == 2
    assert description.entity_registry_enabled_default is True
    assert description.icon is None
    assert sensor.state_class is None  # O02 remains outside this scope.
    assert stove.get_data.await_count == 1
