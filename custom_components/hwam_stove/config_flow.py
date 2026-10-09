"""OpenTherm Gateway config flow."""

from __future__ import annotations

from asyncio import CancelledError, Task, create_task, current_task, shield
import logging
from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.reload import async_integration_yaml_config
import voluptuous as vol

from pystove import pystove

from . import _YAML_HOSTS
from ._host import host_key, normalize_host
from ._request_statistics import (
    DEFAULT_END,
    DEFAULT_START,
    SEASON_END,
    SEASON_START,
    month_day,
)
from .const import CREATE_TRANSPORT_ERRORS, DATA_STOVES, DOMAIN, EXCLUDED_CREATE_ERRORS

_LOGGER = logging.getLogger(__name__)


class HWAMStoveConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """HWAM Stove Config Flow."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HeatingSeasonOptionsFlow()

    _pending_host: str | None = None
    _host_owner: Task | None = None

    def is_matching(self, other_flow: HWAMStoveConfigFlow) -> bool:
        """Match only active address reservations; never persist a host unique ID."""
        return (
            self._pending_host is not None
            and other_flow._host_owner is not None
            and not other_flow._host_owner.done()
            and (
                self._pending_host == other_flow._pending_host
                or (
                    self.context.get("source") == SOURCE_RECONFIGURE
                    and other_flow.context.get("source") == SOURCE_RECONFIGURE
                    and self.context.get("entry_id")
                    == other_flow.context.get("entry_id")
                )
            )
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

    def _host_configured(
        self, host: str, *, exclude_entry_id: str | None = None
    ) -> bool:
        return any(
            entry.entry_id != exclude_entry_id
            and host_key(entry.data.get(CONF_HOST)) == (True, host)
            for entry in self._async_current_entries()
        )

    def _reserve_host(self, host: str) -> bool:
        """Reserve synchronously through the shared M05 matching mechanism."""
        self._pending_host = host
        self._host_owner = current_task()
        if self.hass.config_entries.flow.async_has_matching_flow(self):
            self._release_host()
            return False
        self._host_owner.add_done_callback(self._host_owner_done)
        return True

    async def _async_test_connection(self, host: str) -> None:
        """Reuse the existing connection test and H03 client ownership unchanged."""
        try:
            stove = await pystove.Stove.create(host)
        except EXCLUDED_CREATE_ERRORS:
            raise
        except CREATE_TRANSPORT_ERRORS as err:
            # Reuse the existing cannot_connect result, retaining the cause.
            # No client was returned; initialization cleanup belongs to pystove.
            raise ConnectionError() from err
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
            if not self._reserve_host(host):
                return self.async_abort(reason="already_in_progress")

            creating = False
            try:
                try:
                    await self._async_test_connection(host)
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

    async def _async_yaml_host_guard(self, old_host: str) -> str | None:
        """Require removal of the old YAML address before releasing it.

        Neither loaded batches nor the next startup may re-import the old host.
        No address history or YAML-to-entry identity is persisted or inferred.
        """
        loaded = self.hass.data.get(_YAML_HOSTS)
        if loaded is None:
            return "yaml_not_checked"
        if host_key(old_host) in loaded:
            return "yaml_configuration"
        try:
            config = await async_integration_yaml_config(self.hass, DOMAIN)
        except HomeAssistantError:
            return "yaml_check_failed"
        if config is None:
            return "yaml_check_failed"
        if any(host_key(device[CONF_HOST]) == host_key(old_host)
               for device in config.get(DOMAIN, {}).values()):
            return "yaml_configuration"
        return None

    def _reconfigure_ready(self, entry: ConfigEntry) -> bool:
        """Do not make host changes trigger B01 or recover the open H01B case."""
        if (
            entry.version != self.VERSION
            or entry.minor_version != self.MINOR_VERSION
            or entry.state not in {
                ConfigEntryState.LOADED, ConfigEntryState.NOT_LOADED,
                ConfigEntryState.SETUP_RETRY, ConfigEntryState.SETUP_ERROR,
            }
        ):
            return False
        stoves = self.hass.data.get(DOMAIN, {}).get(DATA_STOVES, {})
        return entry.entry_id not in stoves or entry.state is ConfigEntryState.LOADED

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change only an existing entry's connection address, then ask HA to reload."""
        entry = self._get_reconfigure_entry()
        old_host = entry.data[CONF_HOST]
        if user_input is None:
            return self._show_reconfigure_form(old_host)
        try:
            host = normalize_host(user_input[CONF_HOST])
        except ValueError:
            return self._show_reconfigure_form(old_host, {CONF_HOST: "invalid_host"})
        if self._host_configured(host, exclude_entry_id=entry.entry_id):
            return self.async_abort(reason="already_configured")
        if not self._reserve_host(host):
            return self.async_abort(reason="already_in_progress")
        try:
            if host_key(old_host) == (True, host):
                # Keep the original stored spelling; no validation client or reload.
                return self.async_abort(reason="reconfigure_unchanged")
            if not self._reconfigure_ready(entry):
                return self.async_abort(reason="reconfigure_not_ready")
            if error := await self._async_yaml_host_guard(old_host):
                return self._show_reconfigure_form(host, {"base": error})
            try:
                await self._async_test_connection(host)
            except ConnectionError:
                return self._show_reconfigure_form(host, {"base": "cannot_connect"})
            # YAML may have been edited while validating. Check before the final
            # synchronous entry checks/update; the temporary client is closed now.
            if error := await self._async_yaml_host_guard(old_host):
                return self._show_reconfigure_form(host, {"base": error})
            if not any(flow["flow_id"] == self.flow_id for flow in
                       self.hass.config_entries.flow.async_progress_by_handler(
                           DOMAIN, include_uninitialized=True
                       )):
                return self.async_abort(reason="reconfigure_cancelled")
            if (
                self.hass.config_entries.async_get_entry(entry.entry_id) is not entry
                or entry.data[CONF_HOST] != old_host
            ):
                return self.async_abort(reason="reconfigure_entry_changed")
            if self._host_configured(host, exclude_entry_id=entry.entry_id):
                return self.async_abort(reason="already_configured")
            if not self._reconfigure_ready(entry):
                return self.async_abort(reason="reconfigure_not_ready")
            # HA updates synchronously and schedules reload. This is a saved host,
            # not a promise that reload succeeded. Never roll back registry/data.
            return self.async_update_reload_and_abort(
                entry, data_updates={CONF_HOST: host},
                reason="reconfigure_successful",
                reload_even_if_entry_is_unchanged=False,
            )
        finally:
            self._release_host()

    def _show_reconfigure_form(
        self, host: str, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
            errors=errors or {},
        )

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


class HeatingSeasonOptionsFlow(OptionsFlow):
    """Two annual boundaries, no client validation or integration reload."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            for key in (SEASON_START, SEASON_END):
                try:
                    month_day(user_input.get(key))
                except (ValueError, TypeError):
                    errors[key] = "invalid_season_boundary"
            if not errors:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options,
                    SEASON_START: user_input[SEASON_START],
                    SEASON_END: user_input[SEASON_END],
                })
        values = user_input if user_input is not None else self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(SEASON_START, default=values.get(
                SEASON_START, DEFAULT_START
            )): str,
            vol.Required(SEASON_END, default=values.get(SEASON_END, DEFAULT_END)): str,
        }), errors=errors)
