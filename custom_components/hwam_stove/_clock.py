"""Use HA-local wall-clock fields for the stove's timezone-free clock protocol."""

from datetime import datetime

from homeassistant.util.dt import as_local, as_utc, utcnow


def stove_local_time(value: datetime | None = None) -> datetime:
    """Preserve the instant; interpret naive inputs in HA's configured zone.

    The UTC round trip also normalizes imaginary local representations at a DST
    gap. Offset/fold selection for naive inputs follows HA/zoneinfo conventions.
    """
    return as_local(as_utc(utcnow() if value is None else value))
