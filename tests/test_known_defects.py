"""Desired behavior only. Narrow strict xfails are not compatibility promises."""

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
