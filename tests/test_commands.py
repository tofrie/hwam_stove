"""Successful command calls and no implicit integration-level retries."""

from asyncio import CancelledError

from homeassistant.exceptions import HomeAssistantError
import pytest

from .command_cases import CASES, invoke
from .helpers import entity_id_for

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_successful_command(case, entities, stove):
    await invoke(case, entities)
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("failure", [TimeoutError, CancelledError])
async def test_no_command_retry(case, failure, entities, stove):
    getattr(stove, case.method).side_effect = failure()
    expected = (TimeoutError, HomeAssistantError)
    if failure is CancelledError:
        expected = CancelledError
    with pytest.raises(expected):
        await invoke(case, entities)
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )


async def test_existing_optimistic_values(entities, stove):
    """Record supported success feedback; M02 separately requires later readback."""
    number = entities["number", "burn_level"]
    await number.async_set_native_value(4)
    assert number.native_value == 4
    number.async_write_ha_state.assert_called()
    switch = entities["switch", "remote_refill_alarm"]
    await switch.async_turn_on()
    assert switch.is_on is True
    switch.async_schedule_update_ha_state.assert_called_once_with()


@pytest.mark.parametrize("platform,key,service,data,method,args", [
    ("number", "burn_level", "set_value", {"value": 4}, "set_burn_level", (4,)),
    ("switch", "night_lowering", "turn_off", {}, "set_night_lowering", (False,)),
    ("button", "start", "press", {}, "start", ()),
])
async def test_real_ha_entity_service(
    loaded, hass, stove, platform, key, service, data, method, args
):
    await hass.services.async_call(platform, service,
        {"entity_id": entity_id_for(hass, platform, key), **data}, blocking=True)
    stove.assert_only_command(method, *args)
