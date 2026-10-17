"""Causal, frozen A04 shadow forecast; no HA, transport, training or clock I/O."""

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from statistics import mean, median

MODEL = "a04-phase-matched-r1-v1"
SCHEMA = 1
LIMIT = 200  # Closed prediction cycles; never an unbounded sample/event log.
CALIBRATED_AT = datetime(2026, 10, 11, 9, 25, 31, 563000, tzinfo=UTC)
TARGET = 229  # Frozen capture-04 R1, sequence 489; never learned online.
CONFIDENCE = "experimental_uncalibrated"
WINDOW = 600
HORIZON = 1800
REASONS = {
    "startup",
    "restart",
    "shutdown",
    "failed_read",
    "command_boundary",
    "sampling_gap",
    "missing_observations",
    "partial_session",
    "session_end",
    "session_change",
    "clock_discontinuity",
    "door_open",
    "unseen_phase",
    "insufficient_history",
    "before_calibration",
    "not_cooling",
    "beyond_range",
    "request_active",
    "prediction_expired",
    "storage_failure",
    "available",
    "request_observed",
    "no_prediction",
    "no_request_within_30m",
    "cycle_spent",
}


def finite(value):
    return type(value) in (int, float) and isfinite(value)


def stamp(at):
    return at.astimezone(UTC).isoformat()


def timestamp(value):
    if type(value) is not str:
        raise ValueError("Invalid shadow timestamp")
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(0):
        raise ValueError("Non-UTC shadow timestamp")
    return result


@dataclass(frozen=True)
class Sample:
    sequence: int
    at: datetime
    monotonic: float
    session: str | None
    qualified: bool
    phase: str | None
    alarm: bool | None
    door: bool | None
    temperature: float | None
    oxygen: float | None
    valves: tuple
    session_elapsed: float | None
    hwam_minutes: float | None = None

    def usable(self):
        return (
            self.phase in {"Ignition", "Burn", "Glow", "Standby"}
            and type(self.alarm) is bool
            and type(self.door) is bool
            and all(finite(x) for x in (self.temperature, self.oxygen, *self.valves))
            and len(self.valves) == 3
        )


def crossing(rows):
    """Exact frozen phase-matched candidate's OLS/crossing, before UI rounding."""
    current = rows[-1]
    if current.phase != "Burn":
        return None, "unseen_phase"
    times = [(s.at - rows[0].at).total_seconds() / 60 for s in rows]
    temperatures = [s.temperature for s in rows]
    mt, mv = mean(times), mean(temperatures)
    denominator = sum((t - mt) ** 2 for t in times)
    if not denominator:
        return None, "insufficient_history"
    slope = (
        sum((t - mt) * (v - mv) for t, v in zip(times, temperatures, strict=True))
        / denominator
    )
    if slope >= 0:
        return None, "not_cooling"
    eta = max(0.0, (current.temperature - TARGET) / -slope)
    return (None, "beyond_range") if eta > 30 else (eta, "available")


class ShadowModel:
    """One immutable first forecast per observed request cycle, bounded ledger."""

    def __init__(self):
        self.history = deque(maxlen=256)
        self.previous = None
        self.last_request = None
        self.pending = None
        self.ledger = []
        self.spent = False
        self.cycle_recorded = False
        self.state = "insufficient_data"
        self.reason = "startup"
        self.predicted = None
        self.minutes = None
        self.revision = 0
        self.evicted = 0
        self.sessions = []

    def unavailable(self, reason, insufficient=False):
        self.reason = reason
        self.state = "insufficient_data" if insufficient else "unknown"
        self.predicted = self.minutes = None

    def close(self, outcome, actual=None, after=None, gap=False):
        if self.pending is None:
            return
        row = deepcopy(self.pending)
        row.update(
            outcome=outcome,
            gap_invalid=gap,
            observed_request_at=stamp(actual) if actual else None,
            request_after=stamp(after) if after else None,
        )
        if outcome == "request_observed":
            assert actual > timestamp(row["prediction_at"])
            row["error_minutes"] = (
                timestamp(row["predicted_request_at"]) - actual
            ).total_seconds() / 60
        self.ledger.append(row)
        if len(self.ledger) > LIMIT:
            self.ledger.pop(0)
            self.evicted += 1
        self.pending = None
        self.cycle_recorded = True
        self.revision += 1

    def gap(self, reason):
        """Never retain a scoreable forecast or edge anchor across evidence loss."""
        self.close(reason, gap=True)
        self.history.clear()
        self.previous = self.last_request = None
        self.spent = False
        self.cycle_recorded = False
        self.unavailable(reason)

    def step(self, s):
        if s.at.utcoffset() is None:
            self.gap("clock_discontinuity")
            return
        s = replace(s, at=s.at.astimezone(UTC))
        old = self.previous
        if old and s.sequence <= old.sequence:
            return  # Repeated callbacks/read tokens are not new observations.
        if old:
            seconds = (s.at - old.at).total_seconds()
            elapsed = s.monotonic - old.monotonic
            if seconds <= 0 or elapsed <= 0 or abs(seconds - elapsed) > 1:
                self.gap("clock_discontinuity")
                old = None
            elif s.session != old.session:
                self.close("session_change", gap=True)
                self.history.clear()
                self.spent = False
                self.cycle_recorded = False
                self.last_request = None
                old = None
        self.previous = s
        if not s.usable():
            self.gap("missing_observations")
            return
        if not s.qualified or not s.session:
            self.gap("session_end" if s.phase == "Standby" else "partial_session")
            return
        if s.session not in self.sessions:
            self.sessions = [*self.sessions, s.session][-LIMIT:]
            self.revision += 1
        # Score only the next future observed false->true edge, never startup true.
        if s.alarm:
            if old and old.alarm is False:
                if self.pending is None and not self.cycle_recorded:
                    self.pending = {
                        "session_id": s.session,
                        "prediction_at": None,
                        "predicted_request_at": None,
                        "model_version": MODEL,
                        "confidence": "unknown",
                        "observed_request_at": None,
                        "request_after": None,
                        "error_minutes": None,
                        "gap_invalid": False,
                        "outcome": "pending",
                        "hwam_minutes_at_prediction": None,
                    }
                    self.close("no_prediction", s.at, old.at)
                else:
                    self.close("request_observed", s.at, old.at)
                self.last_request = s.at
            else:
                self.close("request_active", gap=True)
            self.spent = True
            self.history.clear()
            self.unavailable("request_active")
            return
        if old and old.alarm is True:
            self.spent = False
            self.cycle_recorded = False
            self.history.clear()
        if s.door:
            self.close("door_open", gap=False)
            self.spent = True
            self.history.clear()
            self.unavailable("door_open")
            return
        if (
            self.pending
            and (s.at - timestamp(self.pending["prediction_at"])).total_seconds()
            > HORIZON
        ):
            self.close("no_request_within_30m")
            self.unavailable("cycle_spent")
            return
        if s.phase != "Burn":
            self.close("unseen_phase", gap=False)
            self.history.clear()
            self.unavailable("unseen_phase")
            return
        self.history.append(s)
        while (
            len(self.history) > 1
            and (s.at - self.history[1].at).total_seconds() > WINDOW
        ):
            self.history.popleft()
        if self.pending:
            predicted = timestamp(self.pending["predicted_request_at"])
            if s.at > predicted:
                self.unavailable("prediction_expired")
            else:
                self.publish(predicted, s.at)
            return
        if self.spent:
            self.unavailable("cycle_spent")
            return
        if s.at < CALIBRATED_AT:
            self.unavailable("before_calibration", True)
            return
        if (s.at - self.history[0].at).total_seconds() < WINDOW:
            self.unavailable("insufficient_history", True)
            return
        rows = [x for x in self.history if (s.at - x.at).total_seconds() <= WINDOW]
        eta, reason = crossing(rows)
        if eta is None:
            self.unavailable(reason)
            return
        # Minute precision only; preserve this timestamp forever for evaluation.
        predicted = (s.at + timedelta(minutes=eta, seconds=30)).replace(
            second=0, microsecond=0
        )
        self.pending = {
            "session_id": s.session,
            "prediction_at": stamp(s.at),
            "predicted_request_at": stamp(predicted),
            "model_version": MODEL,
            "confidence": CONFIDENCE,
            "observed_request_at": None,
            "request_after": None,
            "error_minutes": None,
            "gap_invalid": False,
            "outcome": "pending",
            "hwam_minutes_at_prediction": (
                s.hwam_minutes
                if finite(s.hwam_minutes) and s.hwam_minutes >= 0
                else None
            ),
        }
        self.revision += 1
        self.spent = True
        self.publish(predicted, s.at)

    def publish(self, predicted, now):
        self.state, self.reason = "prediction_available", "available"
        self.predicted = predicted
        self.minutes = max(0, round((predicted - now).total_seconds() / 60))

    def summary(self):
        scored = [r for r in self.ledger if r["outcome"] == "request_observed"]
        sessions = {r["session_id"] for r in scored}
        days = {s[:10] for s in sessions}
        meaningful = len(scored) >= 30 and len(sessions) >= 20 and len(days) >= 10
        return {
            "model_version": MODEL,
            "schema_version": SCHEMA,
            "evidence_sessions": len(self.sessions),
            "requests_without_prediction": sum(
                r["outcome"] == "no_prediction" for r in self.ledger
            ),
            "retained_cycles": len(self.ledger),
            "retained_sessions": len({r["session_id"] for r in self.ledger}),
            "valid_scored_predictions": len(scored),
            "negative_windows_without_request": sum(
                r["outcome"] == "no_request_within_30m" for r in self.ledger
            ),
            "invalidated_predictions": sum(
                r["outcome"]
                not in {"request_observed", "no_prediction", "no_request_within_30m"}
                for r in self.ledger
            ),
            "evicted_cycles": self.evicted,
            "median_absolute_error_minutes": round(
                median(abs(r["error_minutes"]) for r in scored), 1
            )
            if meaningful
            else None,
        }

    def dump(self):
        return {
            "schema": SCHEMA,
            "model_version": MODEL,
            "ledger": deepcopy(self.ledger),
            "pending": deepcopy(self.pending),
            "evicted": self.evicted,
            "sessions": self.sessions.copy(),
        }

    @classmethod
    def restore(cls, payload):
        """Reject unknown/corrupt state; no migration or silent reset of evidence."""
        if (
            type(payload) is not dict
            or set(payload)
            != {"schema", "model_version", "ledger", "pending", "evicted", "sessions"}
            or type(payload["schema"]) is not int
            or payload["schema"] != SCHEMA
            or payload["model_version"] != MODEL
            or type(payload["evicted"]) is not int
            or payload["evicted"] < 0
            or type(payload["ledger"]) is not list
            or len(payload["ledger"]) > LIMIT
        ):
            raise ValueError("Unsupported shadow state")
        if (
            type(payload["sessions"]) is not list
            or len(payload["sessions"]) > LIMIT
            or len(set(payload["sessions"])) != len(payload["sessions"])
        ):
            raise ValueError("Invalid shadow sessions")
        for session in payload["sessions"]:
            timestamp(session)
        keys = {
            "session_id",
            "prediction_at",
            "predicted_request_at",
            "model_version",
            "confidence",
            "observed_request_at",
            "request_after",
            "error_minutes",
            "gap_invalid",
            "outcome",
            "hwam_minutes_at_prediction",
        }
        for row in [
            *payload["ledger"],
            *([payload["pending"]] if payload["pending"] is not None else []),
        ]:
            if type(row) is not dict or set(row) != keys:
                raise ValueError("Invalid shadow row")
            timestamp(row["session_id"])
            for key in ("prediction_at", "predicted_request_at"):
                if row["outcome"] == "no_prediction":
                    if row[key] is not None:
                        raise ValueError("Invented prediction")
                else:
                    timestamp(row[key])
            for key in ("observed_request_at", "request_after"):
                if row[key] is not None:
                    timestamp(row[key])
            if (
                row["model_version"] != MODEL
                or row["confidence"]
                != ("unknown" if row["outcome"] == "no_prediction" else CONFIDENCE)
                or type(row["gap_invalid"]) is not bool
                or row["outcome"] not in REASONS | {"pending"}
                or any(
                    row[k] is not None and not finite(row[k])
                    for k in ("error_minutes", "hwam_minutes_at_prediction")
                )
            ):
                raise ValueError("Invalid shadow metadata")
            if row["hwam_minutes_at_prediction"] is not None and (
                row["hwam_minutes_at_prediction"] < 0
            ):
                raise ValueError("Invalid native comparison")
            if row["outcome"] in {"request_observed", "no_prediction"}:
                if (
                    row["observed_request_at"] is None
                    or row["request_after"] is None
                    or timestamp(row["request_after"])
                    >= timestamp(row["observed_request_at"])
                ):
                    raise ValueError("Invalid observed request bracket")
            elif (
                row["observed_request_at"] is not None
                or row["request_after"] is not None
            ):
                raise ValueError("Invented cross-gap request association")
            scored = row["outcome"] == "request_observed"
            if scored:
                if (
                    row["gap_invalid"]
                    or row["observed_request_at"] is None
                    or row["request_after"] is None
                    or timestamp(row["observed_request_at"])
                    <= timestamp(row["prediction_at"])
                    or row["error_minutes"]
                    != (
                        timestamp(row["predicted_request_at"])
                        - timestamp(row["observed_request_at"])
                    ).total_seconds()
                    / 60
                ):
                    raise ValueError("Invalid shadow score")
            elif row["error_minutes"] is not None:
                raise ValueError("Unscored shadow error")
        if any(r["outcome"] == "pending" for r in payload["ledger"]) or (
            payload["pending"] is not None
            and payload["pending"]["outcome"] != "pending"
        ):
            raise ValueError("Invalid pending inventory")
        result = cls()
        result.ledger, result.pending = (
            deepcopy(payload["ledger"]),
            deepcopy(payload["pending"]),
        )
        result.evicted = payload["evicted"]
        result.sessions = payload["sessions"].copy()
        result.gap("restart")
        return result
