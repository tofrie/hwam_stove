"""HWAM Stove Update Coordinator."""

from asyncio import CancelledError
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
    REQUEST_REFRESH_DEFAULT_IMMEDIATE,
    DataUpdateCoordinator,
    UpdateFailed,
)

from pystove import pystove

from ._analytics import Analytics
from ._night_times import NightTimeCommands
from .const import DOMAIN, StoveDeviceIdentifier

_LOGGER = logging.getLogger(__name__)


class StoveCoordinator(DataUpdateCoordinator):
    """Abstract description of a stove coordinator."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        stove: pystove.Stove,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        request_debouncer = Debouncer(
            hass, _LOGGER, cooldown=REQUEST_REFRESH_DEFAULT_COOLDOWN,
            immediate=REQUEST_REFRESH_DEFAULT_IMMEDIATE,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"HWAM Stove {config_entry.data[CONF_NAME]}",
            update_interval=timedelta(seconds=10),
            always_update=False,
            request_refresh_debouncer=request_debouncer,
        )
        # The coordinator installs its refresh function during construction.
        # Wrap only debounced requests, using the SAME coordinator refresh path
        # and lock. Scheduled/explicit regular reads keep their H05 contract.
        request_debouncer.function = self._async_command_refresh
        self._command_read = False
        self._command_reconcile = False
        self.hass = hass
        self.name = config_entry.data[CONF_NAME]
        self.stove = stove
        self.night_times = NightTimeCommands(stove)

        dev_reg = dr.async_get(hass)
        self.stove_device_entry = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={
                (DOMAIN, f"{config_entry.entry_id}-{StoveDeviceIdentifier.STOVE}")
            },
            manufacturer="HWAM",
            translation_key="hwam_stove_device",
        )
        self.remote_device_entry = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={
                (DOMAIN, f"{config_entry.entry_id}-{StoveDeviceIdentifier.REMOTE}")
            },
            manufacturer="HWAM",
            translation_key="hwam_remote_device",
        )
        self.analytics = Analytics(hass, config_entry.entry_id)
        config_entry.async_on_unload(hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self.analytics.async_stop,
        ))

    async def async_shutdown(self) -> None:
        """Retain HA shutdown behavior and finish only our analytics checkpoint."""
        await super().async_shutdown()
        if hasattr(self, "analytics"):
            await self.analytics.async_stop()

    async def async_refresh_after_command(self, *, reconcile: bool = False) -> None:
        """Request readback after confirmation, not a command-success verdict."""
        self.analytics.command_boundary()
        self._command_reconcile |= reconcile
        try:
            await self.async_request_refresh()
        except Exception:
            # Normal read failures are handled by DataUpdateCoordinator. Keep
            # unexpected refresh errors separate too; never retry the command.
            # Caller cancellation still propagates as cancellation of the action.
            _LOGGER.exception("Status refresh failed after confirmed command")

    async def _async_command_refresh(self) -> None:
        """Tag actual debounced reads, including deferred/coalesced executions."""
        self._command_read = True
        try:
            # Debouncer already owns the coordinator's refresh lock here;
            # async_refresh() would acquire that same lock a second time.
            await self._async_refresh()
        finally:
            self._command_read = False

    @callback
    def _async_refresh_finished(self) -> None:
        """Reconcile optimistic state even when identical data suppresses updates."""
        if self._command_reconcile and self.last_update_success:
            self._command_reconcile = False
            if self._read_previous_success and self.data == self._read_previous_data:
                # Changed data/recovery already notifies listeners in HA's parent
                # method. Only its equality-suppressed case needs this callback.
                self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        """Update stove info."""
        await self.analytics.async_load()
        regular = not self._command_read
        observation = self.analytics.read_started()
        self._read_previous_data = self.data
        self._read_previous_success = self.last_update_success
        read_generation = self.night_times.read_started()
        try:
            data = await self.stove.get_data()
            if data is None:
                raise UpdateFailed("Got empty response")
        except (Exception, CancelledError):
            if regular:
                try:
                    await self.analytics.async_gap(observation)
                except (Exception, CancelledError):
                    _LOGGER.error("Analytics gap checkpoint interrupted")
            raise

        self.update_interval = timedelta(
            seconds=10 if data[pystove.DATA_PHASE] != pystove.PHASE[5] else 60
        )

        dev_reg = dr.async_get(self.hass)
        dev_reg.async_update_device(
            self.stove_device_entry.id,
            model=self.stove.series,
            sw_version=data.get(pystove.DATA_FIRMWARE_VERSION),
        )
        dev_reg.async_update_device(
            self.remote_device_entry.id,
            sw_version=data.get(pystove.DATA_REMOTE_VERSION),
        )
        if not self._command_read:
            # An immediate read is an observation, not proof that firmware has
            # applied a command. Never use requested readback to overwrite the
            # confirmed command pair or clear H05 uncertainty. Regular reads
            # retain the existing generation/overlap validation unchanged.
            self.night_times.read_finished(
                read_generation,
                data.get(pystove.DATA_NIGHT_BEGIN_TIME),
                data.get(pystove.DATA_NIGHT_END_TIME),
            )
        if regular:
            await self.analytics.async_observe(observation, data)
        return data
