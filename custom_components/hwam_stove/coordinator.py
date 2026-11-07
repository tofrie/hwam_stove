"""HWAM Stove Update Coordinator."""

import asyncio
from datetime import timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pystove import pystove

from .const import DOMAIN, StoveDeviceIdentifier

_LOGGER = logging.getLogger(__name__)

# The stove's WiFi module regularly answers a single poll with a malformed
# "HTTP Host timed out" response or an empty body, and succeeds on the next
# request. Retry once, and keep the last known data for a few failed polls
# before marking all entities unavailable.
RETRY_DELAY = 2  # seconds
MAX_CONSECUTIVE_FAILURES = 3


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
        super().__init__(
            hass,
            _LOGGER,
            name=f"HWAM Stove {config_entry.data[CONF_NAME]}",
            update_interval=timedelta(seconds=10),
            always_update=False,
        )
        self.hass = hass
        self.name = config_entry.data[CONF_NAME]
        self.stove = stove
        self._consecutive_failures = 0

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Update stove info."""
        try:
            data = await self._async_fetch_with_retry()
        except UpdateFailed:
            self._consecutive_failures += 1
            if (
                self.data is not None
                and self._consecutive_failures < MAX_CONSECUTIVE_FAILURES
            ):
                _LOGGER.debug(
                    "Stove poll failed (%d in a row), keeping last known data",
                    self._consecutive_failures,
                )
                return self.data
            raise
        self._consecutive_failures = 0

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
        return data

    async def _async_fetch_with_retry(self) -> dict[str, Any]:
        """Fetch stove data, retrying once on a transient failure."""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                data = await self.stove.get_data()
            except (aiohttp.ClientError, TimeoutError) as err:
                last_error = err
            else:
                if data is not None:
                    return data
                last_error = None
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
        if last_error is not None:
            raise UpdateFailed(f"Error communicating with stove: {last_error}") from last_error
        raise UpdateFailed("Got empty response")
