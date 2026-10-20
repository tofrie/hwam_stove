"""Display-only cache age; never a status observation or request scheduler."""

from asyncio import CancelledError
from collections import Counter
from datetime import datetime
from json import JSONDecodeError
from time import monotonic

from aiohttp import ClientConnectionError, ClientPayloadError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt

RETAIN_SECONDS = 120
MEASUREMENTS = frozenset(
    {
        "room_temperature",
        "stove_temperature",
        "oxygen_level",
        "valve1_position",
        "valve2_position",
        "valve3_position",
    }
)


class Freshness:
    def __init__(self, hass, notify, *, clock=monotonic, utcnow=dt.utcnow):
        self.hass, self.notify = hass, notify
        self.clock, self.utcnow = clock, utcnow
        self.observed_at: datetime | None = None
        self.observed_clock = None
        self.interval = 60
        self.failed = False
        self.closed = False
        self.error = None
        self.failures = Counter()
        self._cancel = []

    def age(self):
        return (
            None
            if self.observed_clock is None
            else max(0, self.clock() - self.observed_clock)
        )

    def state(self):
        age = self.age()
        if self.closed or age is None or age >= RETAIN_SECONDS:
            return "unavailable"
        if self.failed or age > self.interval + 1:
            return "stale"
        return "fresh"

    def accept(self, at, clock, interval):
        changed = self.state() != "fresh"
        self.observed_at, self.observed_clock = at, clock
        self.interval, self.failed, self.error = interval, False, None
        self._clear_timers()
        # Local availability deadlines, no refresh, socket or Stove call.
        for seconds in (interval + 1.001, RETAIN_SECONDS):
            delay = max(0, seconds - self.age())
            self._cancel.append(async_call_later(self.hass, delay, self._expire))
        return changed

    def fail(self, error):
        self.failed = True
        self.error = (
            "cancelled"
            if isinstance(error, CancelledError)
            else "timeout"
            if isinstance(error, TimeoutError)
            else "connection"
            if isinstance(error, ClientConnectionError)
            else "payload"
            if isinstance(error, ClientPayloadError)
            else "invalid_json"
            if isinstance(error, JSONDecodeError)
            else "empty_or_invalid_status"
            if type(error).__name__ == "UpdateFailed"
            else "unexpected_error"
        )
        self.failures[self.error] += 1

    def _expire(self, _now=None):
        if not self.closed:
            self.notify()

    def _clear_timers(self):
        for cancel in self._cancel:
            cancel()
        self._cancel.clear()

    def close(self):
        self.closed = True
        self._clear_timers()

    def summary(self):
        return {
            "last_successful_status_observation": self.observed_at.isoformat()
            if self.observed_at
            else None,
            "data_age_seconds": self.age(),
            "display_state": self.state(),
            "last_read_failed": self.failed,
            "last_error_category": self.error,
            "failure_counts": dict(self.failures),
            "expected_poll_seconds": self.interval,
            "measurement_retention_seconds": RETAIN_SECONDS,
            "failures_are_transport_proof": False,
        }
