"""Cached observed request statistics and annual calendar options; no I/O."""

from datetime import date
import re
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change

from ._analytics_sensor import AnalyticsSensor

SEASON_START = "heating_season_start"
SEASON_END = "heating_season_end"
DEFAULT_START = "09-01"
DEFAULT_END = "05-31"
DESCRIPTIONS = tuple(SensorEntityDescription(
    key=key, translation_key=key, icon="mdi:bell-outline",
) for key in (
    "refill_requests_current_session", "refill_requests_today",
    "refill_requests_season",
))


def month_day(value: str) -> tuple[int, int]:
    """Recurring annual boundary: a real MM-DD in every year (no February 29)."""
    if type(value) is not str or not re.fullmatch(r"[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Invalid annual boundary")
    month, day = map(int, value.split("-"))
    date(2001, month, day)
    return month, day


def boundaries(options) -> tuple[str, str]:
    start = options.get(SEASON_START, DEFAULT_START)
    end = options.get(SEASON_END, DEFAULT_END)
    month_day(start)
    month_day(end)
    return start, end


def season(today: date, start: str, end: str) -> tuple[date, date]:
    """Most recently started annual season, including its closed off-season total."""
    first, last = month_day(start), month_day(end)
    year = today.year - ((today.month, today.day) < first)
    return date(year, *first), date(year + (last < first), *last)


def requests_between(state, start: date, end: date) -> int | None:
    """None rather than a lower bound masquerading as an exact period total."""
    if (state.request_retention_floor
            and start.isoformat() <= state.request_retention_floor):
        return None
    return sum(row["requests"] for label, row in state.days.items()
               if start.isoformat() <= label <= end.isoformat())


class RequestStatisticsSensor(AnalyticsSensor):
    """Reuse analytics listeners/identity; calendar updates never refresh the stove."""

    def __init__(self, coordinator, description):
        super().__init__(coordinator, description)
        self._entry = coordinator.config_entry

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        async def options_changed(_hass, _entry):
            self.async_write_ha_state()

        self.async_on_remove(self._entry.add_update_listener(options_changed))
        if self.entity_description.key != "refill_requests_current_session":
            self.async_on_remove(async_track_time_change(
                self.hass, self._midnight, hour=0, minute=0, second=0,
            ))

    @callback
    def _midnight(self, _now):
        self.async_write_ha_state()

    @property
    def available(self):
        return super().available and self.native_value is not None

    def _range(self):
        today = self.analytics.utcnow().astimezone(
            ZoneInfo(self.hass.config.time_zone)
        ).date()
        if self.entity_description.key == "refill_requests_today":
            return today, today
        return season(today, *boundaries(self._entry.options))

    @property
    def native_value(self):
        state = self.analytics.state
        if self.entity_description.key == "refill_requests_current_session":
            return state.current.requests if state.current else None
        try:
            start, end = self._range()
        except (ValueError, TypeError):
            return None
        return requests_between(state, start, end)

    @property
    def extra_state_attributes(self):
        state = self.analytics.state
        attrs = super().extra_state_attributes
        attrs["counts_observed_requests_only"] = True
        if self.entity_description.key == "refill_requests_current_session":
            attrs.update({
                "start_boundary_known": bool(state.current and state.current.start),
                "coverage_uncertain": bool(
                    state.current and state.current.coverage_uncertain
                ),
            })
        else:
            attrs["retained_history_sufficient"] = self.native_value is not None
            try:
                start, end = self._range()
                attrs.update(period_start=start.isoformat(), period_end=end.isoformat())
            except (ValueError, TypeError):
                pass
        return attrs
