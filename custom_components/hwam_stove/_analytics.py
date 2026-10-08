"""Optional analytics persistence and listeners; never owns a Stove client."""

import asyncio
from collections.abc import Callable
from datetime import datetime
import json
import logging
from pathlib import Path
from time import monotonic

from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt

from ._analytics_model import Observation, State, dump, gap, reduce, restore

_LOGGER = logging.getLogger(__name__)
CHECKPOINT_SECONDS = 60  # Disk checkpoint policy, NOT a controller delay.


async def _settle(awaitable):
    """Finish owned disk work before propagating even repeated cancellation."""
    task = asyncio.create_task(awaitable)
    cancellation = None
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError as error:
            cancellation = error
    try:
        result = task.result()
    except BaseException:
        if cancellation is None:
            raise
    if cancellation is not None:
        raise cancellation
    return result


class Analytics:
    """Bounded checkpoints with separate listeners for equality-suppressed polls."""

    def __init__(self, hass: HomeAssistant, entry_id: str, *,
                 clock: Callable[[], float] = monotonic,
                 utcnow: Callable[[], datetime] = dt.utcnow) -> None:
        self.hass = hass
        self.clock = clock
        self.utcnow = utcnow
        self.key = f"hwam_stove.analytics.{entry_id}"
        self.store = self._store()
        self.state = State()
        self.ready = False
        self.healthy = True
        self.online = False
        self.closed = False
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._saved: dict | None = None
        self._checkpoint = self.clock()
        self._ready_at: float | None = None
        self._interval = 60.0
        self._sequence = 0
        self._generation = 0

    def _store(self) -> Store:
        # Store's envelope stays at 1; our payload has explicit schema migration.
        return Store(self.hass, 1, self.key, atomic_writes=True)

    def _degrade(self, error: Exception) -> None:
        self.healthy = False
        self.online = False
        # No arbitrary exception text, config identifiers or controller values.
        _LOGGER.error("Heating analytics unavailable: %s", type(error).__name__)

    async def async_load(self) -> None:
        """Load once; a corrupt/unknown store must never become a new zero total."""
        async with self._lock:
            if self.ready or self.closed:
                return
            try:
                path = Path(self.store.path)

                def existed():
                    if path.exists():
                        try:
                            envelope = json.loads(path.read_text(encoding="utf-8"))
                        except (ValueError, UnicodeError):
                            # Let HA quarantine invalid JSON through async_load.
                            return True
                        if not isinstance(envelope, dict) or (
                            envelope and (
                                envelope.get("version") != 1
                                or envelope.get("minor_version", 1) != 1
                            )
                        ):
                            # Inspect only our own file via Store's public path.
                            # Do not let HA's permissive minor-version migration
                            # rewrite an unknown envelope before payload validation.
                            raise ValueError("Unsupported analytics envelope")
                    return path.exists() or any(
                        path.parent.glob(path.name + ".corrupt.*")
                    )

                previous = await self.hass.async_add_executor_job(existed)
                payload = await self.store.async_load()
                if payload is None:
                    if previous:
                        raise ValueError("Existing analytics store is unreadable")
                else:
                    self.state = restore(payload)
                    self._saved = payload
            except Exception as error:
                self._degrade(error)
            self.ready = True

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.error("Heating analytics listener failed")

    @callback
    def command_boundary(self) -> None:
        """Reject an overlapping pre-command response, without interpreting it."""
        self._generation += 1

    def read_started(self) -> tuple[int, int, float]:
        self._sequence += 1
        return self._sequence, self._generation, self.clock()

    async def _save(self) -> None:
        payload = dump(self.state)
        if payload == self._saved or not self.healthy:
            return
        try:
            async def write_and_verify():
                await self.store.async_save(payload)
                if self.hass.state == CoreState.stopping:
                    # HA defers this public API to its final-write event. Do not
                    # misreport a pending final write as a failed/durable write.
                    return False
                # async_save can swallow a WriteError. A fresh public Store load
                # must observe the exact checkpoint before we call it durable.
                if await self._store().async_load() != payload:
                    raise OSError("Analytics checkpoint verification failed")
                return True

            if await _settle(write_and_verify()):
                self._saved = payload
                self._checkpoint = self.clock()
        except asyncio.CancelledError:
            self.healthy = False
            self.online = False
            raise
        except Exception as error:
            self._degrade(error)

    async def async_observe(self, token: tuple[int, int, float], data: dict) -> None:
        """Called once at the existing regular-read boundary, never by entities."""
        async with self._lock:
            sequence, generation, started = token
            if self.closed or not self.ready or not self.healthy:
                return
            if sequence <= self.state.sequence:
                return
            if generation != self._generation:
                self.state = gap(self.state)
                self.state.sequence = sequence
                self.online = False
                await self._save()
                self._notify()
                return
            # HA schedules int(loop.time()) + jitter + interval: allow its full
            # one-second rounding range, not any invented firmware grace period.
            missed = (self._ready_at is not None
                      and started > self._ready_at + self._interval + 1)
            if missed:
                self.state = gap(self.state)
            previous = self.state
            refill = data.get("refill_alarm")
            refill = (bool(refill) if type(refill) in {bool, int}
                      and refill in (0, 1) else None)
            self.state = reduce(previous, Observation(
                sequence, self.utcnow(), self.clock(), data.get("phase"),
                refill, data.get("stove_temperature"), self.hass.config.time_zone,
            ))
            self.online = self.state.monotonic is not None
            transition = (
                missed or previous.phase != self.state.phase
                or previous.request_active != self.state.request_active
                or previous.request_edge_known != self.state.request_edge_known
                or previous.completed != self.state.completed
                or previous.partial_completed != self.state.partial_completed
                or self._saved is None
            )
            if transition or self.clock() - self._checkpoint >= CHECKPOINT_SECONDS:
                await self._save()
            self._interval = 60 if self.state.phase == "Standby" else 10
            self._ready_at = self.clock()
            self._notify()

    async def async_gap(self, token: tuple[int, int, float]) -> None:
        """Failed regular read: qualify coverage and preserve its caller error."""
        async with self._lock:
            if self.closed or not self.ready or token[0] <= self.state.sequence:
                return
            self.state = gap(self.state)
            self.state.sequence = token[0]
            self.online = False
            await self._save()
            self._notify()

    async def async_stop(self, _event=None) -> None:
        """Flush once using no client resource; HA owns the awaited callback."""
        async with self._lock:
            if self.closed:
                return
            self.closed = True
            self.state = gap(self.state)
            self.online = False
            if self.ready:
                await self._save()
            self._notify()
