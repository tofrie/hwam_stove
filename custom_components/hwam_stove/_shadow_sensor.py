"""One optional experimental timestamp; no command or notification path."""

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ._live_forecast import HWAM
from ._observation_data import last_prediction
from ._shadow_model import CONFIDENCE, timestamp
from .const import DOMAIN, StoveDeviceIdentifier
from .entity import HWAMStoveBaseEntity


class ShadowSensor(HWAMStoveBaseEntity, SensorEntity):
    entity_description = SensorEntityDescription(
        key="shadow_refill_request",
        translation_key="shadow_refill_request",
        device_class=SensorDeviceClass.TIMESTAMP,
    )

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.shadow = coordinator.shadow
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}-shadow_refill_request"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{coordinator.config_entry.entry_id}-{StoveDeviceIdentifier.STOVE}",
                )
            }
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.live_signal, self._live_changed
            )
        )
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @callback
    def _live_changed(self):
        """Dispatch equal-read and command-boundary writes on the HA event loop."""
        self.async_write_ha_state()

    @property
    def available(self):
        # HA omits extra attributes for unavailable entities. A loaded timestamp
        # entity may be unknown (None) while explaining its last invalid forecast.
        return self.coordinator.live.loaded and not self.coordinator.live.closed

    def _active(self):
        s = self.shadow
        return (
            s.ready
            and s.healthy
            and not s.closed
            and s.model.state == "prediction_available"
            and self.coordinator.freshness.state() == "fresh"
            and not self._expired(s.model.predicted)
        )

    def _expired(self, predicted):
        freshness = self.coordinator.freshness
        at = getattr(freshness, "observed_at", None)
        if at is None:
            previous = self.shadow.model.previous
            at = previous.at if previous is not None else None
        age = freshness.age() if hasattr(freshness, "age") else 0
        return (
            predicted is not None
            and at is not None
            and predicted < at + timedelta(seconds=age or 0)
        )

    def _last_prediction(self):
        current = last_prediction(self.shadow)
        recording = getattr(self.coordinator, "observations", None)
        retained = (
            recording.data.get("last_prediction") if recording is not None else None
        )
        if retained and (
            current is None or retained["issued_at"] > current["issued_at"]
        ):
            return retained
        return current

    def _display_status(self, last):
        m = self.shadow.model
        if not self.shadow.healthy or self.shadow.closed:
            return "unknown"
        if m.reason == "request_active":
            return "request_active"
        if self._active():
            return "active"
        if last is None:
            return "unknown"
        latest = next(
            (r for r in reversed(m.ledger) if r["prediction_at"] == last["issued_at"]),
            None,
        )
        outcome = latest["outcome"] if latest else m.reason
        if outcome in {
            "door_open",
            "unseen_phase",
            "session_end",
            "session_change",
            "request_observed",
            "command_boundary",
        }:
            return "superseded"
        if m.reason == "prediction_expired" or self._expired(
            timestamp(last["predicted_at"])
        ):
            return "expired"
        if outcome in {
            "restart",
            "shutdown",
            "failed_read",
            "sampling_gap",
            "missing_observations",
            "clock_discontinuity",
        }:
            return "unknown"
        if self.coordinator.freshness.state() != "fresh":
            return "stale"
        return "unknown"

    @property
    def native_value(self):
        return self._live_display()[0]

    def _live_display(self):
        freshness = self.coordinator.freshness
        return self.coordinator.live.state.display(
            freshness.utcnow(), freshness.state()
        )

    @property
    def extra_state_attributes(self):
        m = self.shadow.model
        summary = self.shadow.summary()
        last = self._last_prediction()
        freshness = self.coordinator.freshness
        live = self.coordinator.live
        record = live.state.last or {}
        eta, status, reason = self._live_display()
        now = freshness.utcnow()
        source = record.get("source")
        result = dict(
            benchmark_status=self._display_status(last),
            benchmark_reason=m.reason,
            prediction_state=m.state,
            validated_cycles=summary["valid_scored_predictions"],
            last_prediction_outcome=next(
                (
                    r["outcome"]
                    for r in reversed(m.ledger)
                    if last and r["prediction_at"] == last["issued_at"]
                ),
                None,
            ),
            prediction_source=source,
            prediction_status=status,
            status=status,
            reason=reason,
            predicted_refill_at=record.get("eta"),
            last_calculated=record.get("calculated"),
            last_prediction_at=record.get("eta")
            or (last["predicted_at"] if last else None),
            prediction_issued_at=record.get("calculated")
            or (last["issued_at"] if last else None),
            data_age_seconds=(
                max(0, round((now - live.state.observed_at).total_seconds()))
                if live.state.observed_at
                else None
            ),
            data_freshness=freshness.state(),
            confidence=CONFIDENCE if eta else "unknown",
            model_version=record.get("model", HWAM),
            minutes_remaining=max(0, round((eta - now).total_seconds() / 60))
            if eta
            else None,
            live_storage_healthy=not live.storage_failed,
        )
        if summary["median_absolute_error_minutes"] is not None:
            result["benchmark_median_absolute_error_minutes"] = summary[
                "median_absolute_error_minutes"
            ]
        return result
