"""OpenTherm Gateway config flow."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, current_task, shield
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
import voluptuous as vol

from pystove import pystove

from ._host import host_key, normalize_host
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class HWAMStoveConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """HWAM Stove Config Flow."""

    VERSION = 2

    _pending_host: str | None = None
    _host_owner: Task | None = None

    def is_matching(self, other_flow: HWAMStoveConfigFlow) -> bool:
        """Match only active address reservations; never persist a host unique ID."""
        return (
            self._pending_host is not None
            and self._pending_host == other_flow._pending_host
            and other_flow._host_owner is not None
            and not other_flow._host_owner.done()
        )

    def _release_host(self) -> None:
        """Release this attempt, including failed/cancelled entry registration."""
        if self._host_owner is not None:
            self._host_owner.remove_done_callback(self._host_owner_done)
        self._host_owner = None
        self._pending_host = None

    def _host_owner_done(self, task: Task) -> None:
        if self._host_owner is task:
            self._release_host()

    def _host_configured(self, host: str) -> bool:
        return any(
            host_key(entry.data.get(CONF_HOST)) == (True, host)
            for entry in self._async_current_entries()
        )

    async def async_step_init(
        self, info: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle config flow initiation."""
        if info:
            name = info[CONF_NAME]
            try:
                host = normalize_host(info[CONF_HOST])
            except ValueError:
                return self._show_form({CONF_HOST: "invalid_host"})
            if self._host_configured(host):
                return self.async_abort(reason="already_configured")

            # Reserve synchronously before the first await. Public HA flow matching
            # covers both user and import flows, even while they are initializing.
            self._pending_host = host
            self._host_owner = current_task()
            if self.hass.config_entries.flow.async_has_matching_flow(self):
                self._release_host()
                return self.async_abort(reason="already_in_progress")
            self._host_owner.add_done_callback(self._host_owner_done)

            async def test_connection() -> None:
                """Try to connect to the OpenTherm Gateway."""
                stove = await pystove.Stove.create(host)
                try:
                    status = (
                        stove.name != pystove.UNKNOWN  # type: ignore[attr-defined]
                        and stove.stove_ip != pystove.UNKNOWN  # type: ignore[attr-defined]
                    )
                    if not status:
                        raise ConnectionError
                except (Exception, CancelledError):
                    try:
                        await self._async_destroy_stove(stove)
                    except (Exception, CancelledError):
                        _LOGGER.exception("Failed to close temporary config-flow Stove")
                    raise
                else:
                    # A close failure must prevent successful entry creation.
                    await self._async_destroy_stove(stove)

            creating = False
            try:
                try:
                    await test_connection()
                except ConnectionError:
                    return self._show_form({"base": "cannot_connect"})
                # Preserve an entry added during connection validation too.
                if self._host_configured(host):
                    return self.async_abort(reason="already_configured")
                result = self._create_entry(name, host)
                creating = True
                return result
            finally:
                # Success must retain the reservation until HA adds the entry:
                # async_finish_flow can await before registration. The owner task
                # callback also handles an exception/cancellation in that handoff.
                if not creating:
                    self._release_host()

        return self._show_form()

    async def _async_destroy_stove(self, stove: pystove.Stove) -> None:
        """Finish one owned close operation before propagating caller cancellation."""
        async def close() -> BaseException | None:
            # Return errors to the owner: cancelled shield waiters must not report
            # an already-handled close error as an unhandled background exception.
            try:
                await stove.destroy()
            except (Exception, CancelledError) as error:
                return error
            return None

        close_task = create_task(close())
        cancellation = None
        while not close_task.done():
            try:
                await shield(close_task)
            except CancelledError as error:
                if cancellation is None:
                    cancellation = error
        close_error = close_task.result()
        if close_error is not None:
            if cancellation is None:
                raise close_error
            _LOGGER.error(
                "Failed to close temporary Stove during cancellation",
                exc_info=(type(close_error), close_error, close_error.__traceback__),
            )
        if cancellation is not None:
            raise cancellation

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual initiation of the config flow."""
        return await self.async_step_init(user_input)

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import an OpenTherm Gateway device as a config entry.

        This flow is triggered by `async_setup` for configured devices.
        """
        formatted_config = {
            CONF_NAME: import_data[CONF_NAME],
            CONF_HOST: import_data[CONF_HOST],
        }
        return await self.async_step_init(info=formatted_config)

    def _show_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        """Show the config flow form with possible errors."""
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_HOST): str,
                }
            ),
            errors=errors or {},
        )

    def _create_entry(self, name: str, host: str) -> ConfigFlowResult:
        """Create entry for the HWAM Stove."""
        return self.async_create_entry(
            title=name, data={CONF_HOST: host, CONF_NAME: name}
        )
