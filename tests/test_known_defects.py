"""Desired behavior only. Narrow strict xfails are not compatibility promises."""

from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from .command_cases import CASES, invoke
from .helpers import DOMAIN, HOST

pytestmark = pytest.mark.known_defect


class MissingAuditBehavior(AssertionError):
    """Only the final, specific audit expectation may produce an expected failure."""


def require_behavior(condition, message):
    if not condition:
        raise MissingAuditBehavior(message)


def defect(audit_id, reason):
    return pytest.mark.xfail(strict=True, raises=MissingAuditBehavior,
                             reason=f"{audit_id}: {reason}")


@defect("H03", "post-create identity validation has no guaranteed cleanup")
async def test_H03_flow_closes_after_validation_exception(hass, stove):
    from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

    flow = HWAMStoveConfigFlow()
    flow.hass = hass
    del stove.name  # Inject a failure after the public factory has returned.
    with pytest.raises(AttributeError):
        await flow.async_step_user({"name": "Test", "host": HOST})
    closed = stove.destroy.await_count
    try:
        require_behavior(closed == 1, "Flow client must close after validation fails")
    finally:
        if not closed:
            await stove.destroy()


@defect("H04", "False gives no HA error although command success is unconfirmed")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_H04_unconfirmed_command_raises(case, entities, stove):
    getattr(stove, case.method).return_value = False
    reported = False
    try:
        await invoke(case, entities)
    except HomeAssistantError:
        reported = True
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )
    require_behavior(reported, "HA must report unconfirmed success without retrying")


@defect("H05", "second night-time edit uses a stale cached companion value")
@pytest.mark.parametrize("first", ["begin", "end"])
async def test_H05_night_edits_preserve_each_other(first, entities, stove):
    async def applied(*, start, end):
        # Explicit scenario: an acknowledged edit becomes visible on next read.
        # No default simulator behavior or real firmware semantics are inferred.
        stove.data["night_begin_time"] = start
        stove.data["night_end_time"] = end
        return True

    stove.set_night_lowering_hours.side_effect = applied
    begin = entities["time", "night_begin_time"]
    end = entities["time", "night_end_time"]
    edits = [(begin, time(21)), (end, time(7))]
    if first == "end":
        edits.reverse()
    for entity, value in edits:
        await entity.async_set_value(value)
    assert stove.set_night_lowering_hours.await_count == 2
    last = stove.set_night_lowering_hours.call_args.kwargs
    require_behavior(last == {"start": time(21), "end": time(7)},
                     "The second edit must retain the first confirmed change")


@defect("M02", "successful commands do not request coordinator readback")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_M02_confirmed_command_refreshes(case, entities, hass, stove):
    before = stove.get_data.await_count
    await invoke(case, entities)
    await hass.async_block_till_done()
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )
    require_behavior(
        stove.get_data.await_count > before, "Read status after confirmed success"
    )


@defect("M04", "an existing entry suppresses import of all additional YAML devices")
async def test_M04_import_additional_yaml_device(hass, entry):
    from custom_components.hwam_stove import async_setup

    with patch.object(hass.config_entries.flow, "async_init", AsyncMock()) as flow:
        await async_setup(hass, {DOMAIN: {"second": {"host": "second.invalid"}}})
        await hass.async_block_till_done()
    require_behavior(flow.await_count == 1, "Import a distinct additional YAML device")


@defect("M07", "clock setter does not convert an aware UTC input to HA local wall time")
async def test_M07_clock_uses_ha_local_time(entities, stove):
    await entities["datetime", "date_time"].async_set_value(
        datetime(2024, 7, 1, 10, tzinfo=UTC)
    )
    stove.set_time.assert_awaited_once()
    sent = stove.set_time.call_args.args[0]
    require_behavior(sent.hour == 12, "10:00 UTC in July is 12:00 Europe/Berlin")


@defect("M08", "timedelta.seconds drops complete days")
async def test_M08_duration_includes_days(entities, loaded):
    loaded.data["time_to_new_fire_wood"] = timedelta(days=1, hours=2)
    sensor = entities["sensor", "time_to_new_fire_wood"]
    sensor._handle_coordinator_update()
    require_behavior(sensor.native_value == 93600, "Duration must include all 26 hours")
