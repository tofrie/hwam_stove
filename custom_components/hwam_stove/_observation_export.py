"""Explicit local response-only export, separate from redacted diagnostics."""

from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .const import DATA_STOVES, DOMAIN


@callback
def register_export(hass):
    if hass.services.has_service(DOMAIN, "export_observations"):
        return

    async def export(call):
        stoves = hass.data.get(DOMAIN, {}).get(DATA_STOVES, {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            stoves.get(entry_id)
            if entry_id
            else (next(iter(stoves.values())) if len(stoves) == 1 else None)
        )
        if coordinator is None:
            raise HomeAssistantError("Select exactly one loaded HWAM config entry")
        try:
            return coordinator.observations.export()
        except ValueError as error:
            raise HomeAssistantError("Observation recording unavailable") from error

    hass.services.async_register(
        DOMAIN,
        "export_observations",
        export,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
        supports_response=SupportsResponse.ONLY,
    )
