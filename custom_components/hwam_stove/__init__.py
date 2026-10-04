"""
Support for Hwam SmartControl stoves.

For more details about this component, please refer to the documentation at
https://github.com/mvn23/hwam_stove
"""

from asyncio import CancelledError, Lock
import logging

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, ConfigEntryNotReady
from homeassistant.const import CONF_HOST, CONF_MONITORED_VARIABLES, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from pystove import Stove

from .const import DATA_STOVES, DOMAIN
from .coordinator import StoveCoordinator
from .migration import async_migrate_registry_entry

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                cv.string: vol.Schema(
                    {
                        vol.Required(CONF_HOST): cv.string,
                        vol.Optional(CONF_NAME): cv.string,
                        vol.Optional(CONF_MONITORED_VARIABLES, default=[]): vol.All(
                            cv.ensure_list, [cv.string]
                        ),
                    }
                ),
            },
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate release registry identities before setting up any platforms."""
    if config_entry.version == 2:
        return True
    if config_entry.version != 1:
        _LOGGER.error("Unsupported HWAM config entry version: %s", config_entry.version)
        return False
    return async_migrate_registry_entry(hass, config_entry)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the HWAM Stove component from a config entry."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {DATA_STOVES: {}}

    try:
        stove = await Stove.create(config_entry.data[CONF_HOST])
    except TimeoutError as e:
        raise ConfigEntryNotReady() from e

    # H01A: exclusive client ownership ends when platform forwarding begins.
    stove_hub = None
    try:
        stove_hub = StoveCoordinator(hass, stove, config_entry)
        hass.data[DOMAIN][DATA_STOVES][config_entry.entry_id] = stove_hub
        await stove_hub.async_config_entry_first_refresh()
    except (Exception, CancelledError):
        # The awaited refresh has ended and no platform has received the client.
        # Cleanup failures must not replace the original setup exception.
        try:
            if stove_hub is not None:
                await stove_hub.async_shutdown()
        except (Exception, CancelledError):
            _LOGGER.exception("Failed to stop coordinator during pre-forward cleanup")
        try:
            domain_data = hass.data[DOMAIN]
            stoves = domain_data.get(DATA_STOVES, {})
            if stove_hub is not None and stoves.get(config_entry.entry_id) is stove_hub:
                stoves.pop(config_entry.entry_id)
            if not stoves:
                domain_data.pop(DATA_STOVES, None)
            if not domain_data:
                hass.data.pop(DOMAIN)
        except (Exception, CancelledError):
            _LOGGER.exception(
                "Failed to remove runtime data during pre-forward cleanup"
            )
        try:
            await stove.destroy()
        except (Exception, CancelledError):
            _LOGGER.exception("Failed to close Stove during pre-forward cleanup")
        raise

    # H01B remains open: forwarding may leave tasks/entities using this client.
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


_YAML_IMPORT_LOCK = f"{DOMAIN}_yaml_import_lock"
_YAML_ISSUE = "deprecated_import_from_configuration_yaml"


def _async_yaml_issue(hass: HomeAssistant, devices: dict) -> None:
    """Report entry presence, never promise complete legacy/runtime migration."""
    if not devices:
        ir.async_delete_issue(hass, DOMAIN, _YAML_ISSUE)
        return
    hosts = {device[CONF_HOST] for device in devices.values()}
    existing = {entry.data.get(CONF_HOST)
                for entry in hass.config_entries.async_entries(DOMAIN)}
    ir.async_create_issue(
        hass, DOMAIN, _YAML_ISSUE,
        is_fixable=False, is_persistent=False, severity=ir.IssueSeverity.WARNING,
        translation_key=_YAML_ISSUE,
        translation_placeholders={
            "configured": str(len(hosts & existing)),
            "total": str(len(hosts)),
            "missing": str(len(hosts - existing)),
            "duplicates": str(len(devices) - len(hosts)),
        },
    )


async def _async_import_yaml(hass: HomeAssistant, devices: dict) -> None:
    """Import each distinct exact host once per batch; preserve existing entries."""
    # Separate from the entry runtime dictionary: unloading a stove must not
    # invalidate a running YAML batch. This lock is in-memory, not stored config.
    lock = hass.data.setdefault(_YAML_IMPORT_LOCK, Lock())
    async with lock:
        seen = set()
        try:
            for name, device in devices.items():
                host = device[CONF_HOST]
                if host in seen:
                    continue
                seen.add(host)
                if any(entry.data.get(CONF_HOST) == host for entry in
                       hass.config_entries.async_entries(DOMAIN)):
                    continue
                # Historical import uses the YAML mapping key, not optional name.
                # Never mutate HA's shared YAML configuration or persist selections.
                data = {**device, CONF_NAME: name}
                try:
                    await hass.config_entries.flow.async_init(
                        DOMAIN, context={"source": SOURCE_IMPORT}, data=data
                    )
                except Exception:
                    # One failed import must not suppress unrelated controllers.
                    # Cancellation propagates after owned-flow cleanup below.
                    _LOGGER.exception("HWAM YAML import failed; entry may be missing")
                finally:
                    # HA can retain a form or an initializing flow after an error.
                    # Select ONLY this call's init-data object, including unfinished
                    # flows. Never abort an unrelated user/discovery/import flow.
                    flows = hass.config_entries.flow.async_progress_by_init_data_type(
                        dict, lambda candidate, owned=data: candidate is owned,
                        include_uninitialized=True,
                    )
                    for flow in flows:
                        hass.config_entries.flow.async_abort(flow["flow_id"])
        finally:
            _async_yaml_issue(hass, devices)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Schedule individual legacy imports without blocking HA component setup."""
    devices = {name: dict(device) for name, device in config.get(DOMAIN, {}).items()}
    _async_yaml_issue(hass, devices)
    if devices:
        # Flow completion sets up the new entry; awaiting it here can deadlock
        # against HA's component-setup barrier. HA owns/tracks the batch task.
        hass.async_create_task(_async_import_yaml(hass, devices))
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload the HWAM Stove component from a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    ):
        stove_hub = hass.data[DOMAIN][DATA_STOVES][config_entry.entry_id]
        await stove_hub.stove.destroy()

        hass.data[DOMAIN][DATA_STOVES].pop(config_entry.entry_id)
        if hass.data[DOMAIN][DATA_STOVES] == {}:
            hass.data[DOMAIN].pop(DATA_STOVES)
            # DATA_STOVES is the only key in hass.data[DOMAIN] at the moment...
            hass.data.pop(DOMAIN)

    return unload_ok
