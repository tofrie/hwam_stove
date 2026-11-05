"""Live presentation of independent benchmark issuances or observed HWAM minutes."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.helpers.storage import Store

from ._analytics import _settle
from ._observation_data import flag
from ._shadow_model import MODEL, finite, stamp, timestamp

HWAM = "a08-hwam-observed-countdown-v1"
# Display policy, not a firmware limit or an accuracy estimate.
MAX_MINUTES = 180
CHECKPOINT_SECONDS = 300
_LOGGER = logging.getLogger(__name__)


class LiveState:
    def __init__(self):
        self.last = None
        self.status, self.reason = "uncertain", "startup"
        self.previous = self.anchor = self.native_minutes = None
        self.window_start = self.observed_at = None

    def boundary(self, reason):
        self.previous = self.anchor = self.native_minutes = self.window_start = None
        self.status, self.reason = "stale", reason

    def _wait(self, reason, status="uncertain"):
        self.anchor = self.native_minutes = self.window_start = None
        self.status, self.reason = status, reason

    def step(self, at, clock, phase, door, alarm, minutes, issuance=None):
        """Consume one regular read. Compare both clocks for discontinuities."""
        old = self.previous
        self.previous = (at, clock, phase)
        self.observed_at = at
        alarm = flag(alarm)
        if alarm is True:
            self._wait("request_active", "request_active")
            return
        if door is not False or alarm is None:
            self._wait("door_open" if door is True else "missing_edge_input")
            return
        if old:
            delta = (at - old[0]).total_seconds()
            if delta <= 0 or abs(delta - (clock - old[1])) > 1:
                self._wait("clock_discontinuity")
                return
            if delta > (61 if old[2] == "Standby" else 11):
                self._wait("sampling_gap")
                return
        if phase not in {"Burn", "Glow"}:
            self._wait("inactive_phase")
            return
        if old and old[2] != phase:
            self._wait("phase_change")
            return
        if self.window_start is None:
            self.window_start = at

        native = self._native(at, minutes, phase)
        if issuance is not None:
            issued, eta = issuance
            if self.window_start <= issued <= at < eta:
                self._accept("saynwerk", issued, eta, "frozen_benchmark_available")
                return
        if native is not None:
            self._accept("hwam", *native, reason="observed_countdown")
            return
        self.status = "uncertain"
        if self.anchor and self.anchor[1] <= at:
            self.status, self.reason = "expired", "countdown_elapsed"

    def _native(self, at, minutes, phase):
        if (
            not finite(minutes)
            or not 0 < minutes <= MAX_MINUTES
            or minutes != int(minutes)
            or (phase == "Burn" and minutes == 120)
        ):
            self.native_minutes = self.anchor = None
            self.reason = (
                "ambiguous_burn_plateau"
                if phase == "Burn" and minutes == 120
                else "invalid_countdown"
            )
            return None
        eta = at + timedelta(minutes=minutes)
        previous = self.native_minutes
        self.native_minutes = (minutes, eta)
        if self.anchor and previous and minutes == previous[0]:
            # An unchanged integer duration must never continuously push the ETA out.
            return self.anchor if self.anchor[1] > at else None
        if self.anchor and abs((eta - self.anchor[1]).total_seconds()) > 300:
            self.anchor = None
            self.reason = "countdown_jump_unconfirmed"
            return None
        # Confirm an initial or abruptly revised duration with a second regular read.
        if self.anchor is None and (
            previous is None or abs((eta - previous[1]).total_seconds()) > 120
        ):
            self.reason = "awaiting_countdown_confirmation"
            return None
        if self.anchor is None and previous and previous[0] == minutes:
            # Anchor to the first of the two agreeing observations.
            eta = previous[1]
            at = eta - timedelta(minutes=minutes)
            # A gap alone cannot revise a retained ETA for an identical duration.
            if (
                self.last
                and self.last["source"] == "hwam"
                and self.last["minutes"] == minutes
            ):
                self.reason = "awaiting_changed_countdown_after_boundary"
                return None
        rounded = (eta + timedelta(seconds=30)).replace(second=0, microsecond=0)
        self.anchor = (at, rounded)
        return self.anchor

    def _accept(self, source, issued, eta, reason):
        self.last = {
            "source": source,
            "model": MODEL if source == "saynwerk" else HWAM,
            "seen": stamp(self.observed_at),
            "calculated": stamp(issued),
            "eta": stamp(eta),
            "minutes": self.native_minutes[0] if source == "hwam" else None,
        }
        self.status, self.reason = "active", reason

    def display(self, now, freshness):
        status, reason = self.status, self.reason
        age = (now - self.observed_at).total_seconds() if self.observed_at else None
        if (freshness != "fresh" and status != "stale") or (
            age is not None
            and (age < 0 or age > 11)
            and status in {"active", "request_active"}
        ):
            status, reason = "stale", "regular_observation_stale"
        eta = timestamp(self.last["eta"]) if self.last else None
        if (
            eta
            and eta <= now
            and (
                status in {"active", "stale"}
                or reason
                in {
                    "invalid_countdown",
                    "awaiting_countdown_confirmation",
                    "awaiting_changed_countdown_after_boundary",
                    "ambiguous_burn_plateau",
                }
            )
        ):
            status, reason = "expired", "forecast_elapsed"
        return eta if status == "active" else None, status, reason


class LiveForecast:
    """One private atomic checkpoint of the last display record, never a score."""

    def __init__(self, hass, entry_id):
        self.state = LiveState()
        self.store = Store(
            hass, 1, f"hwam_stove.live_v1.{entry_id}", atomic_writes=True
        )
        self.loaded = self.closed = self.storage_failed = False
        self.saved = self.saved_clock = None
        self.lock = asyncio.Lock()

    async def load(self):
        if self.loaded:
            return
        async with self.lock:
            if self.loaded:
                return
            try:
                last = await self.store.async_load()
                if last is not None:
                    if (
                        type(last) is not dict
                        or set(last)
                        != {"source", "model", "seen", "calculated", "eta", "minutes"}
                        or last["source"] not in {"hwam", "saynwerk"}
                        or last["model"]
                        != (MODEL if last["source"] == "saynwerk" else HWAM)
                        or timestamp(last["seen"]) < timestamp(last["calculated"])
                        or (
                            last["source"] == "hwam"
                            and not (
                                finite(last["minutes"])
                                and 0 < last["minutes"] <= MAX_MINUTES
                                and last["minutes"] == int(last["minutes"])
                            )
                        )
                        or (
                            last["source"] == "saynwerk" and last["minutes"] is not None
                        )
                        or not timedelta(0)
                        < timestamp(last["eta"]) - timestamp(last["calculated"])
                        <= timedelta(minutes=MAX_MINUTES, seconds=30)
                    ):
                        raise ValueError("Invalid live checkpoint")
                    self.state.last = self.saved = last
                    self.state.observed_at = timestamp(last["seen"])
                    self.state.boundary("restored_checkpoint")
            except Exception as err:
                self.storage_failed = True
                _LOGGER.error(
                    "Live forecast checkpoint unavailable: %s", type(err).__name__
                )
            self.loaded = True

    async def checkpoint(self, clock, *, final=False):
        async with self.lock:
            if (
                self.closed
                or self.storage_failed
                or self.state.last is None
                or self.state.last == self.saved
            ):
                return
            if (
                not final
                and self.saved_clock is not None
                and clock - self.saved_clock < CHECKPOINT_SECONDS
            ):
                return
            record = self.state.last
            try:

                async def write():
                    await self.store.async_save(record)
                    if await self.store.async_load() != record:
                        raise OSError("Live checkpoint readback mismatch")

                await _settle(write())
            except Exception as err:
                self.storage_failed = True
                _LOGGER.error("Live forecast checkpoint failed: %s", type(err).__name__)
            else:
                self.saved, self.saved_clock = record, clock

    async def stop(self):
        try:
            await self.checkpoint(0, final=True)
        finally:
            self.closed = True
