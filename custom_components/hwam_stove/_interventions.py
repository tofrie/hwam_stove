"""Bounded observations of possible interventions, never physical refill counts."""

from copy import deepcopy
from datetime import timedelta
from math import isfinite

from ._shadow_model import stamp, timestamp

POLICY = "a06-intervention-observations-v1"
LIMIT = 64
FOLLOWUP = 600  # Metadata window bound, NOT a firmware/wood-addition threshold.
KINDS = {
    "observed_request",
    "door_intervention",
    "glow_to_burn_unsampled",
    "coverage_gap",
    "native_forecast_disappeared",
    "model_expiry",
}
CATEGORIES = {"observed_refill_request", "possible_intervention", "unknown"}
REASONS = {
    "request_edge",
    "door_sequence",
    "request_during_intervention",
    "followup_window_ended",
    "session_end",
    "another_door_open",
    "unsampled_transition",
    "forecast_disappeared",
    "failed_read",
    "sampling_gap",
    "shutdown",
    "restart",
    "command_boundary",
    "clock_discontinuity",
    "missing_observation",
    "storage_failure",
    "prediction_expiry",
}


def number(value):
    return (
        value
        if type(value) in (int, float) and isfinite(value) and abs(value) < 1e9
        else None
    )


def context(s, data):
    duration = data.get("time_to_new_fire_wood")
    return {
        "phase": s.phase,
        "door_open": s.door,
        "refill_alarm": s.alarm,
        "stove_temperature": number(s.temperature),
        "oxygen_level": number(s.oxygen),
        "valves": [number(v) for v in s.valves],
        "native_forecast_available": s.phase == "Glow"
        and type(duration) is timedelta
        and duration.total_seconds() >= 0,
    }


class Interventions:
    def __init__(self):
        self.data = {
            "schema": 1,
            "policy": POLICY,
            "ledger": [],
            "pending": None,
            "counts": dict.fromkeys(sorted(KINDS), 0),
            "evicted": 0,
        }
        self.previous = None
        self.previous_context = None
        self.sequence = -1

    def _record(self, row):
        self.data["counts"][row["kind"]] += 1
        self.data["ledger"].append(deepcopy(row))
        if len(self.data["ledger"]) > LIMIT:
            self.data["ledger"].pop(0)
            self.data["evicted"] += 1

    def _event(self, s, ctx, kind, category, reason):
        old = self.previous
        return {
            "kind": kind,
            "category": category,
            "reason": reason,
            "policy": POLICY,
            "session_id": s.session,
            "after": stamp(old.at) if old else None,
            "observed_at": stamp(s.at),
            "end_observed_at": stamp(s.at),
            "coverage": "sampled" if old else "left_censored",
            "before": deepcopy(self.previous_context),
            "after_context": deepcopy(ctx),
            "door_closed_at": None,
            "glow_open_close_burn": False,
            "wood_added": "unknown",
            "opening_phase": s.phase if kind == "door_intervention" else None,
        }

    def _finish(self, reason, *, uncertain=False):
        row = self.data["pending"]
        if row is None:
            return
        row["reason"] = reason
        if uncertain:
            row["coverage"] = "incomplete"
            row["category"] = "unknown"
        self._record(row)
        self.data["pending"] = None

    def gap(self, reason):
        reason = reason if reason in REASONS else "missing_observation"
        self._finish(reason, uncertain=True)
        if self.previous is not None:
            row = self._event(
                self.previous, self.previous_context, "coverage_gap", "unknown", reason
            )
            row["after"] = stamp(self.previous.at)
            row["coverage"] = "incomplete"
            self._record(row)
        self.previous = self.previous_context = None

    def observe(self, s, data):
        """Return a conservative comparison boundary, never a stove action."""
        if s.sequence <= self.sequence:
            return None
        self.sequence = s.sequence
        old = self.previous
        if old:
            delta = (s.at - old.at).total_seconds()
            if delta <= 0 or abs(delta - (s.monotonic - old.monotonic)) > 1:
                self.gap("clock_discontinuity")
                old = None
            elif delta > (61 if old.phase == "Standby" else 11):
                self.gap("sampling_gap")
                old = None
            elif old.session != s.session:
                self.gap("session_end")
                old = None
        if (
            s.phase not in {"Ignition", "Burn", "Glow", "Standby"}
            or type(s.alarm) is not bool
            or type(s.door) is not bool
        ):
            self.gap("missing_observation")
            return "incomplete_intervention_window"
        ctx = context(s, data)
        boundary = None
        pending = self.data["pending"]
        if (
            pending
            and (s.at - timestamp(pending["observed_at"])).total_seconds() > FOLLOWUP
        ):
            self._finish("followup_window_ended")
            pending = None
        if s.alarm and old and old.alarm is False:
            self._record(
                self._event(
                    s,
                    ctx,
                    "observed_request",
                    "observed_refill_request",
                    "request_edge",
                )
            )
            if pending:
                pending["end_observed_at"], pending["after_context"] = stamp(s.at), ctx
                self._finish("request_during_intervention")
                pending = None
        if s.door and (old is None or old.door is False):
            self._finish("another_door_open")
            self.data["pending"] = pending = self._event(
                s, ctx, "door_intervention", "possible_intervention", "door_sequence"
            )
            boundary = "possible_door_intervention"
        if pending:
            pending["end_observed_at"], pending["after_context"] = stamp(s.at), ctx
            if not s.door and old and old.door:
                pending["door_closed_at"] = stamp(s.at)
            if pending["door_closed_at"] and s.phase == "Burn" and not s.door:
                before = pending["before"] or {}
                pending["glow_open_close_burn"] = (
                    before.get("phase") == "Glow" and pending["opening_phase"] == "Glow"
                )
                self._finish("door_sequence")
                boundary = "possible_early_intervention"
            elif s.phase == "Standby":
                self._finish("session_end")
        elif old and old.phase == "Glow" and s.phase == "Burn":
            self._record(
                self._event(
                    s,
                    ctx,
                    "glow_to_burn_unsampled",
                    "possible_intervention",
                    "unsampled_transition",
                )
            )
            boundary = "possible_unsampled_intervention"
        if (
            self.previous_context
            and self.previous_context["native_forecast_available"]
            and not ctx["native_forecast_available"]
        ):
            self._record(
                self._event(
                    s,
                    ctx,
                    "native_forecast_disappeared",
                    "unknown",
                    "forecast_disappeared",
                )
            )
        self.previous, self.previous_context = s, ctx
        return boundary

    def model_expired(self, s, data):
        self._record(
            self._event(
                s, context(s, data), "model_expiry", "unknown", "prediction_expiry"
            )
        )

    def dump(self):
        return deepcopy(self.data)

    @classmethod
    def restore(cls, payload):
        validate(payload)
        result = cls()
        result.data = deepcopy(payload)
        result.gap(
            "restart"
        )  # Retain candidate evidence, never resume its edge anchors.
        return result

    def summary(self):
        pending = self.data["pending"]
        return {
            "schema": 1,
            "policy": POLICY,
            "mode": "observation_only",
            "counts": self.data["counts"].copy(),
            "retained_events": len(self.data["ledger"]),
            "evicted_events": self.data["evicted"],
            "pending_candidate": pending is not None,
            "probable_physical_additions": None,
            "physical_classifier_validated": False,
            "wood_consumption_estimated": False,
            "followup_window_seconds": FOLLOWUP,
            "missing_alarm_cause": "not_established",
        }


def require(value):
    if not value:
        raise ValueError("Invalid intervention checkpoint")


def validate(payload):
    require(type(payload) is dict and set(payload) == set(Interventions().data))
    require(payload["schema"] == 1 and payload["policy"] == POLICY)
    require(type(payload["ledger"]) is list and len(payload["ledger"]) <= LIMIT)
    require(type(payload["evicted"]) is int and payload["evicted"] >= 0)
    require(type(payload["counts"]) is dict and set(payload["counts"]) == KINDS)
    require(all(type(v) is int and v >= 0 for v in payload["counts"].values()))
    require(
        sum(payload["counts"].values()) == len(payload["ledger"]) + payload["evicted"]
    )
    require(
        payload["pending"] is None
        or (type(payload["pending"]) is dict and bool(payload["pending"]))
    )
    keys = {
        "kind",
        "category",
        "reason",
        "policy",
        "session_id",
        "after",
        "observed_at",
        "end_observed_at",
        "coverage",
        "before",
        "after_context",
        "door_closed_at",
        "glow_open_close_burn",
        "wood_added",
        "opening_phase",
    }
    for row in [
        *payload["ledger"],
        *([payload["pending"]] if payload["pending"] else []),
    ]:
        require(type(row) is dict and set(row) == keys)
        require(
            row["kind"] in KINDS
            and row["category"] in CATEGORIES
            and row["reason"] in REASONS
        )
        require(row["policy"] == POLICY and row["wood_added"] == "unknown")
        require(row["opening_phase"] in {None, "Ignition", "Burn", "Glow", "Standby"})
        require(row["coverage"] in {"sampled", "left_censored", "incomplete"})
        require(type(row["glow_open_close_burn"]) is bool)
        for key in (
            "session_id",
            "after",
            "observed_at",
            "end_observed_at",
            "door_closed_at",
        ):
            if row[key] is not None:
                timestamp(row[key])
        for ctx in (row["before"], row["after_context"]):
            if ctx is None:
                continue
            require(
                type(ctx) is dict
                and set(ctx)
                == {
                    "phase",
                    "door_open",
                    "refill_alarm",
                    "stove_temperature",
                    "oxygen_level",
                    "valves",
                    "native_forecast_available",
                }
            )
            require(ctx["phase"] in {"Ignition", "Burn", "Glow", "Standby"})
            require(
                all(
                    type(ctx[k]) is bool
                    for k in ("door_open", "refill_alarm", "native_forecast_available")
                )
            )
            require(type(ctx["valves"]) is list and len(ctx["valves"]) == 3)
            require(
                all(
                    v is None or number(v) == v
                    for v in [
                        ctx["stove_temperature"],
                        ctx["oxygen_level"],
                        *ctx["valves"],
                    ]
                )
            )
