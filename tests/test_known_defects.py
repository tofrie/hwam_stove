"""Desired behavior only. Narrow strict xfails are not compatibility promises."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from .command_cases import CASES, invoke
from .helpers import DOMAIN

pytestmark = pytest.mark.known_defect


class MissingAuditBehavior(AssertionError):
    """Only the final, specific audit expectation may produce an expected failure."""


def require_behavior(condition, message):
    if not condition:
        raise MissingAuditBehavior(message)


def defect(audit_id, reason):
    return pytest.mark.xfail(strict=True, raises=MissingAuditBehavior,
                             reason=f"{audit_id}: {reason}")


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
