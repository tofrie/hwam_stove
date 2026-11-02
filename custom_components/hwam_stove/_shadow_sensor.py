"""One optional experimental timestamp; no command or notification path."""

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.helpers.device_registry import DeviceInfo

from ._observation_data import last_prediction
from ._shadow_model import CONFIDENCE, MODEL, timestamp
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
        self.async_on_remove(self.shadow.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self):
        # HA omits extra attributes for unavailable entities. A loaded timestamp
        # entity may be unknown (None) while explaining its last invalid forecast.
        return self.shadow.ready and not self.shadow.closed

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
        return self.shadow.model.predicted if self._active() else None

    @property
    def extra_state_attributes(self):
        m = self.shadow.model
        summary = self.shadow.summary()
        last = self._last_prediction()
        freshness = self.coordinator.freshness
        age = freshness.age() if hasattr(freshness, "age") else None
        result = {
            "status": self._display_status(last),
            "last_prediction_at": last["predicted_at"] if last else None,
            "prediction_issued_at": last["issued_at"] if last else None,
            "data_age_seconds": round(age, 1) if age is not None else None,
            "data_freshness": freshness.state(),
            "last_prediction_outcome": next(
                (
                    r["outcome"]
                    for r in reversed(m.ledger)
                    if last and r["prediction_at"] == last["issued_at"]
                ),
                None,
            ),
            "prediction_state": m.state,
            "reason": m.reason,
            "minutes_remaining": m.minutes if self._active() else None,
            "confidence": CONFIDENCE if self._active() else "unknown",
            "model_version": MODEL,
            "validated_cycles": summary["valid_scored_predictions"],
        }
        if summary["median_absolute_error_minutes"] is not None:
            result["median_absolute_error_minutes"] = summary[
                "median_absolute_error_minutes"
            ]
        return result
