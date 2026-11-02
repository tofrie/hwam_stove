"""Fixed, private A07 observation schema; never arbitrary controller objects."""

from copy import deepcopy
from datetime import timedelta
from math import isfinite

from ._shadow_model import MODEL, timestamp

SCHEMA = 1
POLICY = "a072-regular-observations-v1"
LIMIT = 20000
DAYS = 7
CHECKPOINT_SECONDS = 1800
COLUMNS = (
    "sequence",
    "utc",
    "kind",
    "phase",
    "stove_temperature",
    "oxygen_level",
    "valve1_position",
    "valve2_position",
    "valve3_position",
    "door_open",
    "refill_alarm",
    "hwam_minutes",
    "session",
    "cycle",
    "segment",
    "interval_seconds",
    "quality",
    "reason",
    "session_qualification",
)
PHASES = {"Ignition", "Burn", "Glow", "Standby"}
GAPS = {"restart", "shutdown", "failed_read", "cancelled_read", "command_boundary"}
QUALITIES = {
    "baseline",
    "continuous",
    "edge_uncertain",
    "short_gap",
    "hard_gap",
    "partial",
    "gap",
}
REASONS = GAPS | {
    "regular",
    "session_change",
    "clock_discontinuity",
    "prolonged_outage",
    "sampling_gap",
    "missing_inputs",
}
QUALIFICATIONS = {"known_start", "partial", "no_session", "analytics_unhealthy"}


def number(value):
    return (
        value
        if type(value) in (int, float) and isfinite(value) and abs(value) <= 1e9
        else None
    )


def flag(value):
    return bool(value) if type(value) in (bool, int) and value in (0, 1) else None


def duration(value):
    return (
        number(value.total_seconds() / 60)
        if type(value) is timedelta and value.total_seconds() >= 0
        else None
    )


def empty():
    return dict(
        schema=SCHEMA,
        policy=POLICY,
        rows=[],
        next_sequence=1,
        session_serial=0,
        cycle_serial=0,
        segment_serial=0,
        session_anchor=None,
        evicted=0,
        checkpoint_at=None,
        last_prediction=None,
    )


def validate_prediction(value):
    if value is None:
        return
    if (
        type(value) is not dict
        or set(value) != {"issued_at", "predicted_at", "model"}
        or value["model"] != MODEL
    ):
        raise ValueError("Invalid last prediction")
    timestamp(value["issued_at"])
    timestamp(value["predicted_at"])


def last_prediction(shadow):
    """Read only immutable issuance fields, including after benchmark restart."""
    candidates = [shadow.model.pending] if shadow.model.pending else []
    candidates += list(reversed(shadow.model.ledger))
    for row in candidates:
        if row.get("prediction_at") is not None:
            result = dict(
                issued_at=row["prediction_at"],
                predicted_at=row["predicted_request_at"],
                model=row["model_version"],
            )
            validate_prediction(result)
            return result
    return None


def validate(payload):
    if (
        type(payload) is not dict
        or set(payload) != set(empty())
        or type(payload["schema"]) is not int
        or payload["schema"] != SCHEMA
        or payload["policy"] != POLICY
    ):
        raise ValueError("Unsupported observations schema")
    for key in (
        "next_sequence",
        "session_serial",
        "cycle_serial",
        "segment_serial",
        "evicted",
    ):
        if type(payload[key]) is not int or not 0 <= payload[key] <= 2**53:
            raise ValueError("Invalid observation counter")
    for key in ("checkpoint_at", "session_anchor"):
        if payload[key] is not None:
            timestamp(payload[key])
    validate_prediction(payload["last_prediction"])
    rows = payload["rows"]
    if type(rows) is not list or len(rows) > LIMIT:
        raise ValueError("Invalid observation inventory")
    if payload["next_sequence"] - 1 != len(rows) + payload["evicted"]:
        raise ValueError("Inconsistent observation inventory")
    previous = 0
    for row in rows:
        if type(row) is not list or len(row) != len(COLUMNS):
            raise ValueError("Invalid observation row")
        seq, at, kind, phase = row[:4]
        if type(seq) is not int or not previous < seq < payload["next_sequence"]:
            raise ValueError("Invalid observation sequence")
        previous = seq
        timestamp(at)
        if kind not in {"observation", "gap"} or phase not in PHASES | {None}:
            raise ValueError("Invalid observation kind/phase")
        for i in (4, 5, 6, 7, 8, 11, 15):
            if row[i] is not None and number(row[i]) is None:
                raise ValueError("Invalid measurement")
        if any(row[i] is not None and row[i] < 0 for i in (11, 15)):
            raise ValueError("Invalid duration")
        if any(row[i] is not None and type(row[i]) is not bool for i in (9, 10)):
            raise ValueError("Invalid edge state")
        for i, key in (
            (12, "session_serial"),
            (13, "cycle_serial"),
            (14, "segment_serial"),
        ):
            if row[i] is not None and (
                type(row[i]) is not int or not 1 <= row[i] <= payload[key]
            ):
                raise ValueError("Invalid observation reference")
        if (
            row[16] not in QUALITIES
            or row[17] not in REASONS
            or row[18] not in QUALIFICATIONS | {None}
        ):
            raise ValueError("Invalid observation quality")
        if kind == "gap" and (
            any(v is not None for v in row[3:16])
            or row[16] != "gap"
            or row[17] not in GAPS
            or row[18] is not None
        ):
            raise ValueError("Invented gap measurement")
        if kind == "observation" and (row[16] == "gap" or row[18] is None):
            raise ValueError("Invalid observation quality")
    return deepcopy(payload)
