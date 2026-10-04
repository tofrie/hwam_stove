"""H04: unconfirmed execution is an action error, never a reason to retry."""

from asyncio import CancelledError
from contextlib import contextmanager
from copy import deepcopy
from string import Formatter
from unittest.mock import patch

from aiohttp import ClientConnectionError
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.util import dt as dt_util
import pytest

from .command_cases import CASES, invoke
from .helpers import DOMAIN, entity_id_for

pytestmark = pytest.mark.contract

MESSAGES = {
    "en": "The stove did not confirm the command. "
          "The command may already have been carried out.",
    "de": "Der Ofen hat den Befehl nicht bestätigt. "
          "Der Befehl kann bereits ausgeführt worden sein.",
    "nl": "De kachel heeft de opdracht niet bevestigd. "
          "De opdracht kan al zijn uitgevoerd.",
}


def assert_unconfirmed(error):
    """A translated action error must preserve uncertainty about execution."""
    assert type(error) is HomeAssistantError
    assert not isinstance(error, ServiceValidationError)
    assert error.translation_domain == DOMAIN
    assert error.translation_key == "command_not_confirmed"
    assert error.translation_placeholders is None
    # HA's exception formatter strips a final period from the cached message.
    assert str(error) == MESSAGES["en"].rstrip(".")
    for claim in ("not executed", "unchanged", "rejected by stove", "rolled back",
                  "command failed"):
        assert claim not in str(error).lower()


@contextmanager
def no_readback(coordinator, stove):
    """No refresh request, immediate read or inferred coordinator state change."""
    reads = stove.get_data.await_count
    data = deepcopy(coordinator.data)
    with (
        patch.object(coordinator, "async_request_refresh",
                     wraps=coordinator.async_request_refresh) as request,
        patch.object(coordinator, "async_refresh",
                     wraps=coordinator.async_refresh) as refresh,
    ):
        yield
        request.assert_not_called()
        refresh.assert_not_called()
    assert stove.get_data.await_count == reads
    assert coordinator.data == data


def prepare_direct_entity(case, entities):
    entity = entities[case.platform, case.key]
    if case.platform == "switch":
        # Both directions must attempt a different value, not a no-op assertion.
        entity._attr_is_on = not case.expected_args[0]
    entity.async_write_ha_state.reset_mock()
    entity.async_schedule_update_ha_state.reset_mock()
    return entity


def controller_value(case, entity):
    if case.platform == "switch":
        return entity.is_on
    if case.platform != "button":
        return entity.native_value
    return None  # A button has no controller value to optimistically change.


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("confirmed", [True, False], ids=["true", "false"])
async def test_H04_command_result(case, confirmed, entities, loaded, stove):
    """The ten former xfails now require an error; True keeps existing feedback."""
    entity = prepare_direct_entity(case, entities)
    before = controller_value(case, entity)
    getattr(stove, case.method).return_value = confirmed
    with no_readback(loaded, stove):
        if confirmed:
            await invoke(case, entities)
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await invoke(case, entities)
            assert_unconfirmed(raised.value)
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )
    if confirmed and case.platform == "number":
        assert entity.native_value == 4.0
        entity.async_write_ha_state.assert_called_once_with()
        entity.async_schedule_update_ha_state.assert_not_called()
    elif confirmed and case.platform == "switch":
        assert entity.is_on is case.expected_args[0]
        entity.async_schedule_update_ha_state.assert_called_once_with()
        entity.async_write_ha_state.assert_not_called()
    else:
        assert controller_value(case, entity) == before
        entity.async_write_ha_state.assert_not_called()
        entity.async_schedule_update_ha_state.assert_not_called()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("error_type", [
    CancelledError, RuntimeError, ClientConnectionError, TimeoutError,
])
async def test_H04_existing_exceptions_propagate(
    case, error_type, entities, loaded, stove
):
    entity = prepare_direct_entity(case, entities)
    before = controller_value(case, entity)
    failure = error_type("original command exception")
    getattr(stove, case.method).side_effect = failure
    with no_readback(loaded, stove), pytest.raises(error_type) as raised:
        await invoke(case, entities)
    assert raised.value is failure
    assert raised.value.args == ("original command exception",)
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )
    assert controller_value(case, entity) == before
    entity.async_write_ha_state.assert_not_called()
    entity.async_schedule_update_ha_state.assert_not_called()


@pytest.fixture
async def service_runtime(hass, entry, stove, case):
    """Use registered HA entities, including a user-enabled datetime entity."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    er.async_get(hass).async_get_or_create(
        "datetime", DOMAIN, f"{entry.entry_id}-date_time",
        config_entry=entry, disabled_by=None,
    )
    if case.platform == "switch":
        if case.key == "night_lowering":
            stove.data[case.key] = "Disabled" if case.expected_args[0] else "Day"
        else:
            stove.data[case.key] = int(not case.expected_args[0])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield hass.data[DOMAIN]["stoves"][entry.entry_id]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def service_parameters(case):
    if case.platform == "switch":
        return case.action.removeprefix("async_"), {}
    if case.platform == "button":
        return "press", {}
    if case.platform == "number":
        return "set_value", {"value": case.arguments[0]}
    field = "time" if case.platform == "time" else "datetime"
    return "set_value", {field: case.arguments[0].isoformat()}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("confirmed", [True, False], ids=["true", "false"])
async def test_H04_real_entity_service(
    case, confirmed, service_runtime, hass, stove
):
    """An HA button timestamp records an attempt, including an unconfirmed one."""
    entity_id = entity_id_for(hass, case.platform, case.key)
    before = hass.states.get(entity_id)
    assert before is not None
    assert before.state != "unavailable"
    service, data = service_parameters(case)
    getattr(stove, case.method).return_value = confirmed
    with no_readback(service_runtime, stove):
        if confirmed:
            await hass.services.async_call(case.platform, service,
                {"entity_id": entity_id, **data}, blocking=True)
        else:
            with pytest.raises(HomeAssistantError) as raised:
                await hass.services.async_call(case.platform, service,
                    {"entity_id": entity_id, **data}, blocking=True)
            assert_unconfirmed(raised.value)
        await hass.async_block_till_done()
    stove.assert_only_command(
        case.method, *case.expected_args, **dict(case.expected_kwargs)
    )
    after = hass.states.get(entity_id)
    if case.platform == "button":
        # HA's final _async_press_action writes the last-press timestamp BEFORE
        # invoking async_press. Keep that framework contract, even on error.
        assert before.state == "unknown"
        assert dt_util.parse_datetime(after.state) is not None
    elif confirmed and case.platform == "number":
        assert float(after.state) == 4
        assert after.state != before.state
    elif confirmed and case.platform == "switch":
        assert after.state == ("on" if case.expected_args[0] else "off")
        assert after.state != before.state
    else:
        assert after == before


@pytest.mark.parametrize("language", ["en", "de", "nl"])
async def test_H04_exception_translations(loaded, hass, language):
    """Validate real HA loading, exact uncertainty wording and placeholder parity."""
    strings = await translation.async_get_translations(
        hass, language, "exceptions", {DOMAIN}
    )
    key = f"component.{DOMAIN}.exceptions.command_not_confirmed.message"
    assert strings[key] == MESSAGES[language]
    placeholders = {field for _, field, _, _ in Formatter().parse(strings[key])
                    if field is not None}
    assert placeholders == set()
