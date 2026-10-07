"""Allowlisted config-entry diagnostics from memory, without refresh or I/O."""

from datetime import timedelta
from enum import Enum
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import IntegrationNotLoaded, async_get_loaded_integration

from pystove import pystove
from pystove.version import __version__ as PYSTOVE_VERSION

from .const import DATA_STOVES, DOMAIN

_NUMERIC_STATUS = (
    "room_temperature", "stove_temperature", "oxygen_level",
    "valve1_position", "valve2_position", "valve3_position",
    "burn_level", "time_since_remote_msg",
)
_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,5}(?:(?:a|b|rc)[0-9]+)?")
_MODEL = re.compile(r"(?:HWAM[ ._-]?)?([1-9][0-9]{3})", re.IGNORECASE)
_ALGORITHM = re.compile(r"(?:HW\.[0-9]{4}|DS\.DSB[0-9]{1,2})\.[0-9]{6}")


def _scalar(value: Any) -> Any:
    """Unwrap one enum value; callers still enforce their exact field contract."""
    return value.value if isinstance(value, Enum) else value


def _number(value: Any) -> int | float | None:
    """Omit non-numbers, booleans, non-finite and unbounded diagnostic values."""
    value = _scalar(value)
    if type(value) in (int, float) and -1e12 <= value <= 1e12:
        return value
    return None


def _flag(value: Any) -> bool | None:
    value = _scalar(value)
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def _choice(value: Any, choices: list[str]) -> str | None:
    value = _scalar(value)
    return value if type(value) is str and value in choices else None


def _version(value: Any) -> str | None:
    value = _scalar(value)
    if type(value) is str and len(value) <= 32 and _VERSION.fullmatch(value):
        return value
    return None


def _model(value: Any) -> str | None:
    # Never return arbitrary XML text as a model. Unknown spellings are omitted.
    if type(value) is str and len(value) <= 16 and (match := _MODEL.fullmatch(value)):
        return match[1]
    return None


def _algorithm(value: Any) -> str | int | None:
    value = _scalar(value)
    if type(value) is int and 0 <= value <= 65535:
        return value
    if type(value) is str and len(value) <= 32 and _ALGORITHM.fullmatch(value):
        return value
    return None


def _alarms(value: Any, names: list[str]) -> list[str] | None:
    """Copy only known alarm names; unknown content is not a clear-alarm result."""
    if type(value) is not list or len(value) > 64:
        return None
    result = [_choice(item, names) for item in value]
    if any(item is None for item in result):
        return None
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, Any]:
    """Read one cached snapshot, including when runtime/status is unavailable.

    No awaits, tasks, registry reads, library calls or fallback initialization.
    This controls the integration's data, not HA's surrounding download envelope.
    """
    try:
        integration_version = async_get_loaded_integration(hass, DOMAIN).manifest.get(
            "version"
        )
    except IntegrationNotLoaded:
        integration_version = None

    domain_data = hass.data.get(DOMAIN)
    stoves = domain_data.get(DATA_STOVES) if type(domain_data) is dict else None
    coordinator = stoves.get(entry.entry_id) if type(stoves) is dict else None
    cached = getattr(coordinator, "data", None)
    data = cached if type(cached) is dict else {}
    stove = getattr(coordinator, "stove", None)
    optional = getattr(stove, "cached_diagnostics", None)
    optional = optional if type(optional) is dict else {}
    beeps = optional.get("remote_refill_beeps")
    interval = getattr(coordinator, "update_interval", None)

    return {
        "schema_version": 1,
        "integration": {
            "version": _version(integration_version),
            "saynwerk_pystove_version": _version(PYSTOVE_VERSION),
            "config_entry_version": entry.version,
            "config_entry_minor_version": entry.minor_version,
            "entry_state": entry.state.value,
            "runtime_present": coordinator is not None,
            "cache_present": type(cached) is dict,
            "last_update_success": _flag(
                getattr(coordinator, "last_update_success", None)
            ),
            "effective_update_interval_seconds": (
                interval.total_seconds() if type(interval) is timedelta else None
            ),
        },
        "stove": {
            "model": _model(getattr(stove, "series", None)),
            "firmware_version": _version(data.get("firmware_version")),
            "remote_version": _version(data.get("remote_version")),
            "wifi_version": _version(optional.get("wifi_version")),
            "phase": _choice(data.get("phase"), pystove.PHASE),
            "operation_mode": _choice(
                data.get("operation_mode"), pystove.OPERATION_MODES
            ),
            "algorithm": _algorithm(data.get("algorithm")),
            "identification_algorithm": _algorithm(
                getattr(stove, "algo_version", None)
            ),
            "updating": _flag(data.get("updating")),
        },
        "status": {
            **{key: _number(data.get(key)) for key in _NUMERIC_STATUS},
            "night_lowering": _choice(
                data.get("night_lowering"), pystove.NIGHT_LOWERING_STATES
            ),
            "refill_alarm": _flag(data.get("refill_alarm")),
            "remote_refill_beeps": (
                beeps if type(beeps) is int and 0 <= beeps <= 1e12 else None
            ),
            "maintenance_alarms": _alarms(
                data.get("maintenance_alarms"), pystove.MAINTENANCE_ALARMS
            ),
            "safety_alarms": _alarms(data.get("safety_alarms"), pystove.SAFETY_ALARMS),
        },
    }
