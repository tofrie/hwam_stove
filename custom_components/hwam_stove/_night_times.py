"""Per-stove coordination of paired night-time commands and existing reads."""

from asyncio import Lock
from datetime import time

from homeassistant.exceptions import HomeAssistantError

from pystove import Stove

from .const import DOMAIN


class NightTimeCommands:
    """Keep only the night-time pair and its command/read ordering."""

    def __init__(self, stove: Stove) -> None:
        self._stove = stove
        self._lock = Lock()
        self._last_read: tuple[time, time] | None = None
        self._confirmed_pair: tuple[time, time] | None = None
        self._uncertain = True
        self._generation = 0
        self._command_active = False

    def read_started(self) -> int | None:
        """A read starting during a command cannot establish its outcome."""
        return None if self._command_active else self._generation

    def read_finished(
        self, generation: int | None, start: time | None, end: time | None
    ) -> None:
        """Accept a successful read only if no command overlapped it."""
        if (
            generation is None
            or generation != self._generation
            or self._command_active
            or not isinstance(start, time)
            or not isinstance(end, time)
        ):
            return
        self._last_read = (start, end)
        self._confirmed_pair = None
        self._uncertain = False

    async def async_set_value(self, value: time, *, set_start: bool) -> bool:
        """Send one paired command, retaining uncertainty until a suitable read."""
        async with self._lock:
            pair = self._confirmed_pair or self._last_read
            if self._uncertain or pair is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="night_times_not_synchronized",
                )
            pair = (value, pair[1]) if set_start else (pair[0], value)

            # No suspension between acquiring a safe pair and starting the call.
            # Cancellation while waiting for the lock does not reach this point.
            self._generation += 1
            self._command_active = True
            self._uncertain = True
            self._confirmed_pair = None
            try:
                success = await self._stove.set_night_lowering_hours(
                    start=pair[0], end=pair[1]
                )
                if success is True:
                    self._confirmed_pair = pair
                    self._uncertain = False
                # The entity retains H04's False -> command_not_confirmed error.
                return success
            finally:
                # Errors/cancellation preserve uncertainty and propagate as-is.
                self._command_active = False
