"""Desired behavior only. Narrow strict xfails are not compatibility promises."""

from unittest.mock import AsyncMock, patch

import pytest

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


@defect("M04", "an existing entry suppresses import of all additional YAML devices")
async def test_M04_import_additional_yaml_device(hass, entry):
    from custom_components.hwam_stove import async_setup

    with patch.object(hass.config_entries.flow, "async_init", AsyncMock()) as flow:
        await async_setup(hass, {DOMAIN: {"second": {"host": "second.invalid"}}})
        await hass.async_block_till_done()
    require_behavior(flow.await_count == 1, "Import a distinct additional YAML device")
