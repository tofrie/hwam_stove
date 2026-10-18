"""Optional shadow Store and accepted-regular-observation adapter; no Stove API."""

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
from time import monotonic

from homeassistant.core import CoreState, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt

from ._analytics import _settle
from ._forecast_benchmark import Benchmark
from ._shadow_evidence import Evidence
from ._shadow_model import Sample, ShadowModel

_LOGGER = logging.getLogger(__name__)


class Shadow:
    def __init__(self, hass, entry_id, *, clock=monotonic, utcnow=dt.utcnow):
        self.hass, self.clock, self.utcnow = hass, clock, utcnow
        self.key = f"hwam_stove.shadow_v3.{entry_id}"
        self.rc6_key = f"hwam_stove.shadow_v2.{entry_id}"
        self.legacy_key = f"hwam_stove.shadow.{entry_id}"
        self.evidence = Evidence()
        self.benchmark = Benchmark()
        self.model = ShadowModel()
        self.ready, self.healthy, self.closed = False, True, False
        self._lock = asyncio.Lock()
        self._listeners = []
        self._saved = None
        self._sequence = -1
        self._ready_at = None
        self._interval = 60
        self._generation = 0
        self.observation_disposition = "not_started"
        self.session_qualification = "not_observed"

    def store(self):
        return Store(self.hass, 1, self.key, atomic_writes=True)

    def summary(self):
        return {
            **self.evidence.summary(self.model),
            "schema_version": 3,
            "persistence_healthy": self.healthy,
            "checkpoint_verified": self._saved is not None and self.healthy,
        }

    def abstention_diagnostics(self):
        return self.evidence.abstention(self.model)

    def gap(self, reason):
        self.benchmark.gap(reason)
        self.evidence.gap(reason)
        self.model.gap(reason)
        if self.healthy:
            self.evidence.collect(self.model)

    def degrade(self, error):
        self.healthy = False
        self.gap("storage_failure")
        _LOGGER.error("Shadow prediction unavailable: %s", type(error).__name__)

    async def async_load(self):
        async with self._lock:
            if self.ready or self.closed:
                return
            try:

                async def read(store):
                    path = Path(store.path)

                    def existed():
                        if path.exists():
                            envelope = json.loads(path.read_text(encoding="utf-8"))
                            if (
                                type(envelope) is not dict
                                or envelope.get("version") != 1
                                or envelope.get("minor_version", 1) != 1
                            ):
                                raise ValueError("Unsupported shadow envelope")
                        return path.exists() or any(
                            path.parent.glob(path.name + ".corrupt.*")
                        )

                    previous = await self.hass.async_add_executor_job(existed)
                    payload = await store.async_load()
                    if payload is None and previous:
                        raise ValueError("Unreadable shadow evidence")
                    return payload

                payload = await read(self.store())
                if payload is None:
                    payload = await read(Store(self.hass, 1, self.rc6_key))
                if payload is None:
                    # Leave both rc5 and rc6 evidence byte-for-byte intact.
                    payload = await read(Store(self.hass, 1, self.legacy_key))
                if payload is not None:
                    if type(payload) is not dict:
                        raise ValueError("Invalid shadow state")
                    if payload.get("schema") == 1:
                        self.evidence = Evidence.migrate(payload)
                        self.model = ShadowModel.restore(payload)
                    elif payload.get("schema") in (2, 3) and set(payload) == {
                        "schema",
                        "engine",
                        "evidence",
                    } | ({"benchmark"} if payload["schema"] == 3 else set()):
                        if payload["schema"] == 3:
                            self.benchmark = Benchmark.restore(payload["benchmark"])
                        self.model = ShadowModel.restore(payload["engine"])
                        self.evidence = Evidence.restore(
                            payload["evidence"], payload["engine"]
                        )
                    else:
                        raise ValueError("Unsupported shadow schema")
                    self.evidence.gap("restart")
                    # restore already invalidated any pending row exactly once.
                    self.evidence.collect(self.model)
                await self._save()
            except Exception as error:
                self.degrade(error)
            self.ready = True

    @callback
    def async_add_listener(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def notify(self):
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.error("Shadow listener failed")

    @callback
    def boundary(self, reason="command_boundary"):
        self._generation += 1
        self._ready_at = None
        self.gap(reason)
        self.notify()

    def read_started(self):
        return self._generation, self.clock()

    async def _save(self):
        if not self.healthy:
            return
        try:
            payload = self.evidence.dump(self.model)
            payload.update(schema=3, benchmark=self.benchmark.dump())
            if payload == self._saved:
                return

            async def write():
                await self.store().async_save(payload)
                if self.hass.state == CoreState.stopping:
                    return False
                if await self.store().async_load() != payload:
                    raise OSError("Shadow checkpoint failed")
                return True

            if await _settle(write()):
                self._saved = payload
        except asyncio.CancelledError:
            self.gap("failed_read")
            self.healthy = False
            self.notify()
            raise
        except Exception as error:
            self.degrade(error)

    async def async_gap(self, reason="failed_read"):
        async with self._lock:
            if self.closed:
                return
            self.boundary(reason)
            if self.ready:
                await self._save()

    async def async_observe(self, sequence, token, analytics, data, door):
        async with self._lock:
            if self.closed or not self.ready or not self.healthy:
                self.observation_disposition = (
                    "closed"
                    if self.closed
                    else "not_ready"
                    if not self.ready
                    else "unhealthy"
                )
                return
            if sequence <= self._sequence:
                self.observation_disposition = "duplicate_or_older_observation"
                return
            self._sequence = sequence
            generation, started = token
            if generation != self._generation:
                self.observation_disposition = "pre_boundary_read"
                await self._save()  # Boundary already invalidated the prediction.
                return
            self.observation_disposition = "accepted_regular_observation"
            try:
                if (
                    self._ready_at is not None
                    and started > self._ready_at + self._interval + 1
                ):
                    self.gap("sampling_gap")
                # Same scheduling allowance as A02; no capture-specific 11s policy.
                state = analytics.state
                current = state.current
                qualified = (
                    analytics.healthy
                    and analytics.online
                    and current is not None
                    and current.start is not None
                )
                self.session_qualification = (
                    "analytics_unhealthy"
                    if not analytics.healthy
                    else "analytics_offline"
                    if not analytics.online
                    else "no_current_session"
                    if current is None
                    else "session_start_unobserved"
                    if current.start is None
                    else "session_coverage_uncertain"
                    if current.coverage_uncertain
                    else "qualified"
                )
                alarm = data.get("refill_alarm")
                alarm = (
                    bool(alarm)
                    if type(alarm) in (bool, int) and alarm in (0, 1)
                    else None
                )
                forecast = data.get("time_to_new_fire_wood")
                forecast = (
                    forecast.total_seconds() / 60
                    if type(forecast) is timedelta
                    else None
                )
                observation = Sample(
                    sequence,
                    self.utcnow(),
                    self.clock(),
                    current.first_observed if current else None,
                    qualified,
                    data.get("phase"),
                    alarm,
                    door,
                    data.get("stove_temperature"),
                    data.get("oxygen_level"),
                    tuple(data.get(f"valve{i}_position") for i in (1, 2, 3)),
                    current.elapsed if current else None,
                    forecast,
                )
                self.evidence.observe(observation, analytics)
                self.model.step(observation)
                self.benchmark.observe(observation, data, self.model, analytics)
                # Atomic bounded evidence checkpoint; no transport I/O.
                await self._save()
                self._interval = 60 if data.get("phase") == "Standby" else 10
                self._ready_at = self.clock()
            except Exception as error:
                self.degrade(error)
            self.notify()

    async def async_stop(self, _event=None):
        async with self._lock:
            if self.closed:
                return
            self.closed = True
            self.gap("shutdown")
            if self.ready:
                await self._save()
            self.notify()
