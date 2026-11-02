"""Bounded local recording of existing reads, with batched atomic checkpoints."""

import asyncio
from collections import deque
from copy import deepcopy
from datetime import timedelta
import json
import logging
from pathlib import Path
from time import monotonic

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt

from ._analytics import _settle
from ._observation_data import (
    CHECKPOINT_SECONDS,
    COLUMNS,
    DAYS,
    LIMIT,
    PHASES,
    duration,
    empty,
    flag,
    last_prediction,
    number,
    validate,
)
from ._shadow_model import stamp, timestamp

_LOGGER = logging.getLogger(__name__)


class Observations:
    def __init__(self, hass, entry_id, *, clock=monotonic, utcnow=dt.utcnow):
        self.hass, self.clock, self.utcnow = hass, clock, utcnow
        self.store = Store(
            hass, 1, f"hwam_stove.observations_v1.{entry_id}", atomic_writes=True
        )
        self.data = empty()
        self.rows = deque()
        self.ready = self.closed = self.verified = False
        self.healthy = True
        self.dirty = False
        self.checkpoint_sequence = 0
        self.previous = None
        self._last_token = -1
        self._generation = 0
        self._last_gap = None
        self._cancel = None
        self._task = None
        self._lock = asyncio.Lock()

    def _degrade(self, error):
        self.healthy = self.verified = False
        if self._cancel:
            self._cancel()
            self._cancel = None
        _LOGGER.error("Observation recording stopped: %s", type(error).__name__)

    async def async_load(self):
        async with self._lock:
            if self.ready or self.closed:
                return
            try:
                # Validate the complete envelope BEFORE HA can quarantine/migrate it.
                # Unknown/corrupt files are retained unchanged; never reset silently.
                def inspect():
                    path = Path(self.store.path)
                    if not path.exists():
                        if any(path.parent.glob(path.name + ".corrupt.*")):
                            raise ValueError(
                                "Missing observations with quarantined evidence"
                            )
                        return None
                    envelope = json.loads(path.read_bytes())
                    if (
                        type(envelope) is not dict
                        or envelope.get("version") != 1
                        or envelope.get("minor_version", 1) != 1
                        or envelope.get("key") != self.store.key
                    ):
                        raise ValueError("Unsupported observation envelope")
                    return validate(envelope["data"])

                payload = await self.hass.async_add_executor_job(inspect)
                if payload is not None:
                    loaded = await self.store.async_load()
                    if loaded != payload:
                        raise ValueError("Observation read changed")
                    self.data = payload
                    self.rows = deque(self.data.pop("rows"))
                    self.verified = True
                    self.checkpoint_sequence = self.data["next_sequence"] - 1
                self.ready = True
                self.boundary("restart")
            except Exception as error:
                self._degrade(error)
            self.ready = True

    def read_started(self):
        return self._generation

    @callback
    def _changed(self):
        self.dirty = True
        if (
            not self.closed
            and self.healthy
            and self._cancel is None
            and self._task is None
        ):
            self._cancel = async_call_later(self.hass, CHECKPOINT_SECONDS, self._due)

    @callback
    def _due(self, _now):
        self._cancel = None
        if not self.closed and self.healthy:
            self._task = self.hass.async_create_background_task(
                self._checkpoint_task(),
                "HWAM observation checkpoint",
            )

    async def _checkpoint_task(self):
        try:
            await self.async_checkpoint()
        finally:
            self._task = None
            if self.dirty and self.healthy and not self.closed:
                self._changed()

    def _append(self, row, at):
        row[0] = self.data["next_sequence"]
        self.data["next_sequence"] += 1
        self.rows.append(row)
        cutoff = at - timedelta(days=DAYS)
        while self.rows and (
            len(self.rows) > LIMIT or timestamp(self.rows[0][1]) < cutoff
        ):
            self.rows.popleft()
            self.data["evicted"] += 1
        self._changed()

    @callback
    def boundary(self, reason):
        self._generation += 1
        if (
            not self.ready
            or not self.healthy
            or self.closed
            or reason == self._last_gap
        ):
            return
        self._last_gap = reason
        self.previous = None
        at = self.utcnow()
        row = [None] * len(COLUMNS)
        row[:3] = [0, stamp(at), "gap"]
        row[16:18] = ["gap", reason]
        self._append(row, at)

    @callback
    def observe(self, sequence, token, data, door, at, clock, analytics, shadow):
        try:
            self._observe(sequence, token, data, door, at, clock, analytics, shadow)
        except Exception as error:
            self._degrade(error)

    def _observe(self, sequence, token, data, door, at, clock, analytics, shadow):
        if (
            not self.ready
            or not self.healthy
            or self.closed
            or sequence <= self._last_token
            or token != self._generation
        ):
            return
        self._last_token = sequence
        prediction = last_prediction(shadow)
        if prediction and (
            self.data["last_prediction"] is None
            or prediction["issued_at"] > self.data["last_prediction"]["issued_at"]
        ):
            self.data["last_prediction"] = prediction
        current = analytics.state.current
        anchor = current.first_observed if current else None
        qualification = (
            "analytics_unhealthy"
            if not analytics.healthy
            else "no_session"
            if current is None
            else "partial"
            if current.start is None or current.coverage_uncertain
            else "known_start"
        )
        session_changed = anchor != self.data["session_anchor"]
        if session_changed:
            self.data["session_anchor"] = anchor
            if anchor is not None:
                self.data["session_serial"] += 1
        raw_phase = data.get("phase")
        phase = raw_phase if type(raw_phase) is str and raw_phase in PHASES else None
        alarm = flag(data.get("refill_alarm"))
        door = door if type(door) is bool else None
        numbers = [
            number(data.get(k))
            for k in (
                "stove_temperature",
                "oxygen_level",
                "valve1_position",
                "valve2_position",
                "valve3_position",
            )
        ]
        quality, reason, delta = "baseline", "regular", None
        old = self.previous
        if old:
            delta = (at - old["at"]).total_seconds()
            elapsed = clock - old["clock"]
            limit = 61 if old["phase"] == "Standby" else 30
            edge = 61 if old["phase"] == "Standby" else 11
            if delta <= 0 or elapsed <= 0 or abs(delta - elapsed) > 1:
                quality, reason, delta = "hard_gap", "clock_discontinuity", None
            elif delta > limit:
                quality, reason = "hard_gap", "prolonged_outage"
            elif delta > edge:
                quality, reason = (
                    ("short_gap" if delta > 15 else "edge_uncertain"),
                    "sampling_gap",
                )
            else:
                quality = "continuous"
        if old and not old.get("usable", False) and quality == "continuous":
            quality, reason = "baseline", "missing_inputs"
        if session_changed:
            quality, reason = "baseline", "session_change"
        if (
            phase is None
            or door is None
            or alarm is None
            or any(v is None for v in numbers)
        ):
            quality, reason = "partial", "missing_inputs"
        continuous = quality == "continuous"
        if not continuous:
            self.data["segment_serial"] += 1
            self.data["cycle_serial"] += 1
        elif old["alarm"] is True and alarm is False:
            self.data["cycle_serial"] += 1
        row = [
            0,
            stamp(at),
            "observation",
            phase,
            *numbers,
            door,
            alarm,
            duration(data.get("time_to_new_fire_wood")),
            self.data["session_serial"] if anchor is not None else None,
            self.data["cycle_serial"],
            self.data["segment_serial"],
            delta,
            quality,
            reason,
            qualification,
        ]
        self._append(row, at)
        self.previous = dict(
            at=at,
            clock=clock,
            phase=phase,
            alarm=alarm,
            usable=quality != "partial",
        )
        self._last_gap = None

    def _payload(self):
        return {**deepcopy(self.data), "rows": deepcopy(list(self.rows))}

    async def async_checkpoint(self):
        async with self._lock:
            if not self.ready or not self.healthy or not self.dirty:
                return
            if self._cancel:
                self._cancel()
                self._cancel = None
            payload = self._payload()
            payload["checkpoint_at"] = stamp(self.utcnow())
            try:
                await self.hass.async_add_executor_job(validate, payload)

                async def write():
                    await self.store.async_save(payload)
                    if await self.store.async_load() != payload:
                        raise OSError("Observation checkpoint mismatch")

                await _settle(write())
                self.data["checkpoint_at"] = payload["checkpoint_at"]
                self.verified = True
                self.checkpoint_sequence = payload["next_sequence"] - 1
                self.dirty = self.data["next_sequence"] != payload["next_sequence"]
            except asyncio.CancelledError:
                self._degrade(asyncio.CancelledError())
                raise
            except Exception as error:
                self._degrade(error)

    async def async_stop(self):
        if self.closed:
            return
        self.boundary("shutdown")
        self.closed = True
        if self._cancel:
            self._cancel()
            self._cancel = None
        task = self._task

        async def finish():
            if task:
                await task
            await self.async_checkpoint()

        await _settle(finish())

    def summary(self):
        return dict(
            schema_version=1,
            healthy=self.healthy,
            ready=self.ready,
            checkpoint_verified=self.verified,
            buffered_changes=self.dirty,
            checkpoint_sequence=self.checkpoint_sequence,
            retained_records=len(self.rows),
            evicted_records=self.data["evicted"],
            max_records=LIMIT,
            max_days=DAYS,
            checkpoint_seconds=CHECKPOINT_SECONDS,
        )

    def export(self):
        if not self.ready or not self.healthy:
            raise ValueError("Observation recording unavailable")
        return dict(
            schema_version=1,
            policy=self.data["policy"],
            columns=list(COLUMNS),
            units=dict(
                stove_temperature="°C",
                oxygen_level="%",
                valves="%",
                hwam_minutes="min",
                interval_seconds="s",
            ),
            metadata=self.summary(),
            checkpoint_at=self.data["checkpoint_at"],
            reference_semantics={
                "session": (
                    "Local A02 observed-session alias; qualification is explicit"
                ),
                "cycle": (
                    "Observation window; resets at lost edges or request clearing; "
                    "never a count of wood additions"
                ),
                "segment": "Edge-continuous observation segment",
            },
            rows=deepcopy(list(self.rows)),
        )
