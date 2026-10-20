"""One optional experimental timestamp; no command or notification path."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.helpers.device_registry import DeviceInfo

from ._shadow_model import CONFIDENCE, MODEL
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
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self):
        s = self.shadow
        return (
            s.ready
            and s.healthy
            and not s.closed
            and s.model.state == "prediction_available"
            and self.coordinator.freshness.state() == "fresh"
        )

    @property
    def native_value(self):
        return self.shadow.model.predicted if self.available else None

    @property
    def extra_state_attributes(self):
        m = self.shadow.model
        summary = self.shadow.summary()
        result = {
            "prediction_state": m.state,
            "reason": m.reason,
            "minutes_remaining": m.minutes,
            "confidence": CONFIDENCE if self.available else "unknown",
            "model_version": MODEL,
            "validated_cycles": summary["valid_scored_predictions"],
        }
        if summary["median_absolute_error_minutes"] is not None:
            result["median_absolute_error_minutes"] = summary[
                "median_absolute_error_minutes"
            ]
        return result
