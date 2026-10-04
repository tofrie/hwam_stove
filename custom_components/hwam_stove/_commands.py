"""Internal handling of unconfirmed command results."""

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


def require_command_confirmation(result: bool) -> None:
    """Report missing confirmation without inferring whether execution occurred."""
    if result is False:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_not_confirmed",
        )
