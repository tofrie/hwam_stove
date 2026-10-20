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
from ._freshness import Freshness
from ._night_times import NightTimeCommands
from ._shadow import Shadow
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
        self.freshness = Freshness(hass, self.async_update_listeners)
        self._freshness_changed = False
        self.door_open: bool | None = None
        self._door_changed = False
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
        self.shadow = Shadow(hass, config_entry.entry_id)
        config_entry.async_on_unload(hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_stop_models,
        ))

    async def async_shutdown(self) -> None:
        """Retain HA shutdown behavior and finish only our analytics checkpoint."""
        await super().async_shutdown()
        if hasattr(self, "analytics"):
            await self._async_stop_models()

    async def _async_stop_models(self, _event=None):
        self.freshness.close()
        try:
            await self.analytics.async_stop()
        finally:
            await self.shadow.async_stop()

    async def async_refresh_after_command(self, *, reconcile: bool = False) -> None:
        """Request readback after confirmation, not a command-success verdict."""
        self.analytics.command_boundary()
        self.shadow.boundary()
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
        if self.last_update_success:
            notify_equal = (self._command_reconcile or self._door_changed
                            or self._freshness_changed)
            self._command_reconcile = False
            if (notify_equal and self._read_previous_success
                    and self.data == self._read_previous_data):
                # Parent already notifies changed data/recovery. Coalesce M02
                # reconciliation and door-only updates in the equal-data case.
                self.async_update_listeners()
        self._door_changed = False

    def _accepted_door(self, snapshot, previous_generation):
        """Require a fresh accepted library read, never a retained side cache."""
        if (type(snapshot) is dict and snapshot.get("valid") is True
                and type(snapshot.get("generation")) is int
                and type(previous_generation) is int
                and snapshot["generation"] == previous_generation + 1
                and type(snapshot.get("door_open")) is bool):
            return snapshot["door_open"]
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Invalidate the door on any failed/cancelled coordinator read."""
        previous_door = self.door_open
        try:
            return await self._async_read_data()
        except (Exception, CancelledError) as error:
            self.freshness.fail(error)
            self.shadow.boundary("failed_read")
            self.door_open = None
            self._door_changed = previous_door is not None
            if isinstance(error, CancelledError):
                # HA re-raises cancellation before its completion callback.
                self.async_update_listeners()
                self._door_changed = False
            raise

    async def _async_read_data(self) -> dict[str, Any]:
        """Update stove info using the unchanged coordinator read path."""
        await self.analytics.async_load()
        await self.shadow.async_load()
        regular = not self._command_read
        observation = self.analytics.read_started()
        shadow_token = self.shadow.read_started()
        self._read_previous_data = self.data
        self._read_previous_success = self.last_update_success
        read_generation = self.night_times.read_started()
        before = getattr(self.stove, "cached_status", None)
        generation = before.get("generation") if type(before) is dict else None
        previous_door = self.door_open
        self._door_changed = False
        try:
            data = await self.stove.get_data()
            # Snapshot immediately, before analytics/checkpoint awaits.
            snapshot = getattr(self.stove, "cached_status", None)
            door = self._accepted_door(snapshot, generation)
            if data is None:
                raise UpdateFailed("Got empty response")
            received_at = self.freshness.utcnow()
            received_clock = self.freshness.clock()
        except (Exception, CancelledError):
            if regular:
                try:
                    await self.analytics.async_gap(observation)
                    await self.shadow.async_gap()
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
            await self.shadow.async_observe(
                observation[0], shadow_token, self.analytics, data, door,
            )
        # No await between publishing the accepted door and returning its status.
        self._freshness_changed = self.freshness.accept(
            received_at, received_clock, self.update_interval.total_seconds(),
        )
        self.door_open = door
        self._door_changed = door != previous_door
        return data
