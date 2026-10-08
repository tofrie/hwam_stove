"""Four derived sensors; no controller calls or Recorder dependency."""

from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.helpers.device_registry import DeviceInfo

from ._analytics import Analytics
from .const import DOMAIN, StoveDeviceIdentifier
from .entity import HWAMStoveBaseEntity

DESCRIPTIONS = (
    SensorEntityDescription(
        key="observed_session_duration", translation_key="observed_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS, suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="last_complete_session_duration",
        translation_key="last_complete_session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS, suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="completed_observed_sessions",
        translation_key="completed_observed_sessions",
        icon="mdi:counter",
    ),
    SensorEntityDescription(
        key="last_session_observed_peak", translation_key="last_session_observed_peak",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
)


class AnalyticsSensor(HWAMStoveBaseEntity, SensorEntity):
    """Listener to observation state, independent of coordinator data equality."""

    def __init__(self, coordinator, description):
        # Reuse the existing identity convention without altering old descriptions.
        # Device association is the stove; the helper expects this attribute.
        self.analytics: Analytics = coordinator.analytics
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}-{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(
            DOMAIN, f"{coordinator.config_entry.entry_id}-{StoveDeviceIdentifier.STOVE}"
        )})
        self.entity_description = description

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.analytics.async_add_listener(self.async_write_ha_state))

    @property
    def available(self):
        a = self.analytics
        if not (a.ready and a.healthy and not a.closed):
            return False
        if self.entity_description.key == "observed_session_duration":
            return a.online and (a.state.current is None or (
                a.state.current.start is not None
                and not a.state.current.coverage_uncertain
            ))
        return True

    @property
    def native_value(self):
        state = self.analytics.state
        match self.entity_description.key:
            case "observed_session_duration":
                current = state.current
                return current.elapsed if current and current.established else None
            case "last_complete_session_duration":
                return state.last_complete.elapsed if state.last_complete else None
            case "completed_observed_sessions":
                return state.completed
            case "last_session_observed_peak":
                return state.ledger[-1].peak if state.ledger else None
        return None

    @property
    def extra_state_attributes(self):
        a = self.analytics
        state = a.state
        current = state.current
        local = a.utcnow().astimezone(ZoneInfo(self.hass.config.time_zone))
        attrs = {
            "analytics_status": "degraded" if not a.healthy else (
                "observing" if a.online else "coverage_unknown"
            ),
        }
        match self.entity_description.key:
            case "observed_session_duration":
                attrs.update({
                    "start_boundary_known": (
                        current is not None and current.start is not None
                    ),
                    "coverage_uncertain": bool(current and current.coverage_uncertain),
                    "observed_elapsed_seconds": current.elapsed if current else None,
                    "refill_request_episodes": current.requests if current else 0,
                    "refill_request_active": state.request_active,
                    "request_edge_known": state.request_edge_known,
                })
            case "completed_observed_sessions":
                attrs.update({
                    "today": state.days.get(
                        local.strftime("%Y-%m-%d"), {}
                    ).get("completed", 0),
                    "month": state.months.get(
                        local.strftime("%Y-%m"), {}
                    ).get("completed", 0),
                    "partial_sessions_excluded": state.partial_completed,
                    "observed_refill_requests": state.requests,
                })
            case "last_session_observed_peak":
                last = state.ledger[-1] if state.ledger else None
                attrs["complete_observation"] = last.complete if last else None
                attrs["refill_request_episodes"] = last.requests if last else None
        return attrs

