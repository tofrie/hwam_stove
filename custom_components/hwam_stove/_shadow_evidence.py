"""Bounded durable A04 evidence, separate from the frozen prediction engine.

No training, clock I/O or controller access. Schema 2 retains schema-1 engine
records verbatim and attributes aggregates to their original model. Utility
statistics use only prospectively observed complete sessions, never legacy rows.
"""

from copy import deepcopy
from math import ceil

from ._shadow_model import (
    ABSTENTION_REASONS,
    CALIBRATED_AT,
    LIMIT,
    MODEL,
    ShadowModel,
    stamp,
    timestamp,
)

# Persisted evidence gaps have a separate vocabulary from frozen model reasons.
EVIDENCE_GAP_REASONS = frozenset(ABSTENTION_REASONS) | {"prolonged_outage"}

SCHEMA = 2
POLICY = "a042-continuous-window-v1"
# Tenths of a minute, [0, .1), ... [59.9, 60), [60, infinity).
BINS = 601
COUNTERS = (
    "issued",
    "scored",
    "invalidated",
    "without_prediction",
    "negative",
    "evidence_sessions",
    "evaluated_sessions",
    "completed_qualified_sessions",
)
UTILITY = ("sessions", "requests", "scored", "negative", "early", "late")


def cohort():
    return {**dict.fromkeys(UTILITY, 0), "errors": [0] * BINS, "dates": []}


def stats():
    return {**dict.fromkeys(COUNTERS, 0), "errors": [0] * BINS, "utility": cohort()}


def count(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Invalid evidence counter")


def check_cohort(value):
    if type(value) is not dict or set(value) != set(cohort()):
        raise ValueError("Invalid utility aggregate")
    for key in UTILITY:
        count(value[key])
    if type(value["errors"]) is not list or len(value["errors"]) != BINS:
        raise ValueError("Invalid error histogram")
    for number in value["errors"]:
        count(number)
    if sum(value["errors"]) != value["scored"] or value["scored"] > value["requests"]:
        raise ValueError("Inconsistent utility evidence")
    dates = value["dates"]
    if type(dates) is not list or len(dates) > 10 or len(set(dates)) != len(dates):
        raise ValueError("Invalid evidence dates")
    for day in dates:
        if type(day) is not str or len(day) != 10:
            raise ValueError("Invalid evidence date")
        timestamp(day + "T00:00:00+00:00")


def quantile_bounds(histogram, fraction):
    """Nearest-rank quantile; never label a histogram estimate an exact error."""
    total = sum(histogram)
    if not total:
        return None
    ranks = (
        [total // 2, total // 2 + 1]
        if fraction == 0.5 and total % 2 == 0
        else [ceil(total * fraction)]
    )
    bounds = []
    for rank in ranks:
        accumulated = 0
        for i, number in enumerate(histogram):
            accumulated += number
            if accumulated >= rank:
                bounds.append((i / 10, (i + 1) / 10 if i < BINS - 1 else None))
                break
    return [
        sum(b[0] for b in bounds) / len(bounds),
        sum(b[1] for b in bounds) / len(bounds)
        if all(b[1] is not None for b in bounds)
        else None,
    ]


class Evidence:
    """Cumulative outcomes plus a bounded, prospective utility cohort."""

    def __init__(self):
        self.data = {
            "policy": POLICY,
            "models": {MODEL: stats()},
            "processed": 0,
            "legacy_evicted_unknown": 0,
            "legacy_qualification_unknown": False,
            "legacy_reason_counts_unknown": False,
            "reason_saturation_observed": False,
            "reasons": dict.fromkeys(ABSTENTION_REASONS, 0),
            "last_session": None,
            "last_evaluated_session": None,
            "active": None,
            "last_observed": None,
            "last_alarm": None,
            "gaps": [],
            "open_gap": None,
            "annotations": [],
            "pending": None,
        }
        self._reason_seen = dict.fromkeys(ABSTENTION_REASONS, 0)
        self._previous = None
        self._continuous = None
        self._eligible_edge = False
        self._qualified_now = False

    @classmethod
    def migrate(cls, engine):
        # Validate before reading anything; restore() only mutates its own copy.
        ShadowModel.restore(engine)
        result = cls()
        result.data.update(
            processed=engine["evicted"],
            legacy_evicted_unknown=engine["evicted"],
            legacy_qualification_unknown=True,
            legacy_reason_counts_unknown=True,
        )
        # These are lower bounds from retained rc5 evidence, not lost history.
        result.data["models"][MODEL]["evidence_sessions"] = len(engine["sessions"])
        if engine["sessions"]:
            result.data["last_session"] = max(engine["sessions"], key=timestamp)
        # Use un-restored inventory: a pending prediction is still pending here.
        m = ShadowModel()
        m.ledger, m.pending = deepcopy(engine["ledger"]), deepcopy(engine["pending"])
        m.evicted = engine["evicted"]
        result.collect(m, legacy=True)
        return result

    @classmethod
    def restore(cls, data, engine):
        result = cls()
        if (
            type(data) is not dict
            or set(data) != set(result.data)
            or data["policy"] != POLICY
        ):
            raise ValueError("Unsupported evidence state")
        for key in ("processed", "legacy_evicted_unknown"):
            count(data[key])
        for key in (
            "legacy_qualification_unknown",
            "legacy_reason_counts_unknown",
            "reason_saturation_observed",
        ):
            if type(data[key]) is not bool:
                raise ValueError("Invalid legacy provenance")
        models = data["models"]
        if (
            type(models) is not dict
            or not 1 <= len(models) <= 16
            or MODEL not in models
        ):
            raise ValueError("Unsupported model inventory")
        for name, item in models.items():
            if type(name) is not str or not 1 <= len(name) <= 80:
                raise ValueError("Invalid model attribution")
            if type(item) is not dict or set(item) != set(stats()):
                raise ValueError("Invalid model totals")
            for key in COUNTERS:
                count(item[key])
            check_cohort(item["utility"])
            if (
                type(item["errors"]) is not list
                or len(item["errors"]) != BINS
                or sum(item["errors"]) != item["scored"]
            ):
                raise ValueError("Invalid cumulative errors")
            for number in item["errors"]:
                count(number)
        if type(data["reasons"]) is not dict or set(data["reasons"]) != set(
            ABSTENTION_REASONS
        ):
            raise ValueError("Invalid reason inventory")
        for value in data["reasons"].values():
            count(value)
        for key in ("last_session", "last_evaluated_session", "last_observed"):
            if data[key] is not None:
                timestamp(data[key])
        if data["last_alarm"] is not None and type(data["last_alarm"]) is not bool:
            raise ValueError("Invalid alarm anchor")
        active = data["active"]
        if active is not None:
            if type(active) is not dict or set(active) != {
                "session",
                "censored",
                "model",
                "utility",
            }:
                raise ValueError("Invalid active evidence session")
            timestamp(active["session"])
            if type(active["censored"]) is not bool or active["model"] not in models:
                raise ValueError("Invalid session qualification")
            check_cohort(active["utility"])
        gaps = data["gaps"]
        if type(gaps) is not list or len(gaps) > 32:
            raise ValueError("Invalid gap inventory")
        for gap in [
            *gaps,
            *([data["open_gap"]] if data["open_gap"] is not None else []),
        ]:
            if type(gap) is not dict or set(gap) != {"after", "through", "reason"}:
                raise ValueError("Invalid gap")
            if gap["reason"] not in EVIDENCE_GAP_REASONS:
                raise ValueError("Invalid gap reason")
            for key in ("after", "through"):
                if gap[key] is not None:
                    timestamp(gap[key])
        if data["open_gap"] and data["open_gap"]["through"] is not None:
            raise ValueError("Closed open gap")
        known_outcomes = sum(
            item[key]
            for item in models.values()
            for key in ("scored", "invalidated", "without_prediction", "negative")
        )
        if known_outcomes + data["legacy_evicted_unknown"] != data["processed"]:
            raise ValueError("Inconsistent cumulative inventory")
        annotations = data["annotations"]
        if type(annotations) is not list or len(annotations) != len(engine["ledger"]):
            raise ValueError("Invalid annotation inventory")
        if data["processed"] != len(engine["ledger"]) + engine["evicted"]:
            raise ValueError("Inconsistent processed inventory")
        for annotation in annotations:
            if type(annotation) is not dict or set(annotation) != {
                "status",
                "utility_eligible",
            }:
                raise ValueError("Invalid evidence annotation")
            if annotation["status"] not in {
                "legacy_unknown",
                "evaluated",
                "invalidated_due_to_gap",
                "censored",
            }:
                raise ValueError("Invalid evaluation status")
            if type(annotation["utility_eligible"]) is not bool:
                raise ValueError("Invalid eligibility")
        if (data["pending"] is None) != (engine["pending"] is None):
            raise ValueError("Inconsistent pending evidence")
        if (
            data["pending"] is not None
            and data["pending"] != engine["pending"]["prediction_at"]
        ):
            raise ValueError("Changed issued prediction")
        result.data = deepcopy(data)
        return result

    def gap(self, reason):
        # Last observed -> first future observation is an evidence gap, not a
        # claim about the actual time of a physical event or power outage.
        if self.data["open_gap"] is None:
            self.data["open_gap"] = {
                "after": self.data["last_observed"],
                "through": None,
                "reason": reason,
            }
        if self.data["active"]:
            self.data["active"]["censored"] = True
        self._previous = self._continuous = None
        self._eligible_edge = self._qualified_now = False

    def observe(self, s, analytics):
        """Record only accepted regular observations, before the engine step."""
        if self._previous and s.sequence <= self._previous.sequence:
            return
        old = self._previous
        self._eligible_edge = False
        if old and (
            (s.at - old.at).total_seconds() <= 0
            or abs((s.at - old.at).total_seconds() - (s.monotonic - old.monotonic)) > 1
        ):
            self.gap("clock_discontinuity")
            old = None
        active = self.data["active"]
        if active and active["session"] != s.session:
            old = self._previous = self._continuous = None
            completed = next(
                (
                    r
                    for r in getattr(analytics.state, "ledger", [])
                    if r.first_observed == active["session"]
                ),
                None,
            )
            if completed and completed.complete and not active["censored"]:
                totals = self.data["models"][active["model"]]
                totals["completed_qualified_sessions"] += 1
                u, buffer = totals["utility"], active["utility"]
                buffer["sessions"] = 1
                for key in UTILITY:
                    u[key] += buffer[key]
                u["errors"] = [
                    a + b for a, b in zip(u["errors"], buffer["errors"], strict=True)
                ]
                day = active["session"][:10]
                if day not in u["dates"] and len(u["dates"]) < 10:
                    u["dates"].append(day)
            self.data["active"] = None
        if s.session and self.data["active"] is None:
            last = self.data["last_session"]
            newer = last is None or timestamp(s.session) > timestamp(last)
            if newer:
                self.data["models"][MODEL]["evidence_sessions"] += 1
                self.data["last_session"] = s.session
            current = analytics.state.current
            self.data["active"] = {
                "session": s.session,
                "model": MODEL,
                "censored": not (
                    newer
                    and s.qualified
                    and not current.coverage_uncertain
                    and timestamp(s.session) > CALIBRATED_AT
                ),
                "utility": cohort(),
            }
        active = self.data["active"]
        current = analytics.state.current
        self._qualified_now = bool(
            s.usable()
            and s.qualified
            and current
            and not current.coverage_uncertain
            and timestamp(s.session) > CALIBRATED_AT
        )
        if not s.usable() or not s.qualified:
            self.gap("missing_observations" if not s.usable() else "partial_session")
            old = None
        if active and not self._qualified_now:
            active["censored"] = True
        if self.data["open_gap"] is not None:
            closed = {**self.data["open_gap"], "through": stamp(s.at)}
            self.data["gaps"] = [*self.data["gaps"], closed][-32:]
            self.data["open_gap"] = None
        if s.usable() and s.qualified:
            if self._continuous is None:
                self._continuous = s.at
            self._eligible_edge = bool(
                old
                and old.alarm is False
                and s.alarm is True
                and self._qualified_now
                and active
                and not active["censored"]
                and (s.at - self._continuous).total_seconds() >= 1800
            )
            if self._eligible_edge:
                active["utility"]["requests"] += 1
            self._previous = s
        self.data["last_observed"], self.data["last_alarm"] = stamp(s.at), s.alarm

    def collect(self, model, *, legacy=False):
        """Fold new immutable closed rows once; eviction never erases totals."""
        total = model.evicted + len(model.ledger)
        delta = total - self.data["processed"]
        if not 0 <= delta <= len(model.ledger):
            raise ValueError("Lost shadow checkpoint continuity")
        rows = model.ledger[-delta:] if delta else []
        active = self.data["active"]
        for row in rows:
            m = self.data["models"].setdefault(row["model_version"], stats())
            outcome = row["outcome"]
            if row["prediction_at"] and self.data["pending"] != row["prediction_at"]:
                m["issued"] += 1
            self.data["pending"] = None
            key = (
                "scored"
                if outcome == "request_observed"
                else "without_prediction"
                if outcome == "no_prediction"
                else "negative"
                if outcome == "no_request_within_30m"
                else "invalidated"
            )
            m[key] += 1
            if key == "scored":
                m["errors"][min(BINS - 1, int(abs(row["error_minutes"]) * 10))] += 1
            session = row["session_id"]
            last = self.data["last_evaluated_session"]
            if last is None or timestamp(session) > timestamp(last):
                m["evaluated_sessions"] += 1
                self.data["last_evaluated_session"] = session
            eligible = bool(
                not legacy
                and active
                and not active["censored"]
                and active["session"] == session
                and self._qualified_now
                and ((key == "scored" and self._eligible_edge) or key == "negative")
            )
            if eligible:
                u = active["utility"]
                u[key] += 1
                if key == "scored":
                    error = row["error_minutes"]
                    u["errors"][min(BINS - 1, int(abs(error) * 10))] += 1
                    if error:
                        u["early" if error < 0 else "late"] += 1
            self.data["annotations"].append(
                {
                    "status": "invalidated_due_to_gap"
                    if row["gap_invalid"]
                    else "legacy_unknown"
                    if legacy
                    else "evaluated"
                    if key in {"scored", "negative", "without_prediction"}
                    else "censored",
                    "utility_eligible": eligible,
                }
            )
        self.data["annotations"] = self.data["annotations"][-LIMIT:]
        self.data["processed"] = total
        if model.pending and self.data["pending"] != model.pending["prediction_at"]:
            self.data["models"][model.pending["model_version"]]["issued"] += 1
            self.data["pending"] = model.pending["prediction_at"]
        reasons = model.abstention_diagnostics()["reason_transition_counts"]
        for key, value in reasons.items():
            self.data["reasons"][key] += max(0, value - self._reason_seen[key])
        self.data["reason_saturation_observed"] |= any(
            value >= 65535 for value in reasons.values()
        )
        self._reason_seen = reasons.copy()

    def summary(self, model):
        totals = self.data["models"][MODEL]
        result = model.summary()
        result.update(
            schema_version=SCHEMA,
            evidence_sessions=totals["evidence_sessions"],
            evaluated_sessions=totals["evaluated_sessions"],
            completed_qualified_sessions=totals["completed_qualified_sessions"],
            valid_scored_predictions=totals["scored"],
            requests_without_prediction=totals["without_prediction"],
            negative_windows_without_request=totals["negative"],
            invalidated_predictions=totals["invalidated"],
            # Old rolling-ledger medians are not prospective utility estimates.
            median_absolute_error_minutes=None,
            legacy_evicted_outcomes_unknown=self.data["legacy_evicted_unknown"],
            legacy_qualification_unknown=self.data["legacy_qualification_unknown"],
            policy_version=POLICY,
            active_session_present=self.data["active"] is not None,
            active_session_censored=(
                self.data["active"]["censored"] if self.data["active"] else None
            ),
            retained_gap_brackets=len(self.data["gaps"]),
            per_model={
                name: self.utility_summary(item)
                for name, item in self.data["models"].items()
            },
        )
        return result

    @staticmethod
    def utility_summary(item):
        u = item["utility"]
        evaluated = u["scored"] + u["negative"]
        return {
            **{key: item[key] for key in COUNTERS},
            "all_scored_median_error_bounds_minutes": quantile_bounds(
                item["errors"], 0.5
            ),
            "all_scored_p90_error_bounds_minutes": quantile_bounds(item["errors"], 0.9),
            "qualified_sessions": u["sessions"],
            "qualified_dates_capped_at_10": len(u["dates"]),
            "eligible_request_cycles": u["requests"],
            "eligible_scored_predictions": u["scored"],
            "prediction_coverage": u["scored"] / u["requests"]
            if u["requests"]
            else None,
            "negative_forecast_fraction": u["negative"] / evaluated
            if evaluated
            else None,
            "median_absolute_error_bounds_minutes": quantile_bounds(u["errors"], 0.5),
            "p90_absolute_error_bounds_minutes": quantile_bounds(u["errors"], 0.9),
            "minimum_evidence_reached": u["sessions"] >= 20
            and len(u["dates"]) >= 10
            and u["requests"] >= 30,
            "early_predictions": u["early"],
            "late_predictions": u["late"],
        }

    def abstention(self, model):
        result = model.abstention_diagnostics()
        result.update(
            reason_transition_counts=self.data["reasons"].copy(),
            counter_scope="persistent_since_schema_2",
            counter_limit=2**63 - 1,
            per_runtime_counter_limit=65535,
            reason_saturation_observed=self.data["reason_saturation_observed"],
            historical_reason_counts_unknown=self.data["legacy_reason_counts_unknown"],
        )
        return result

    def dump(self, model):
        self.collect(model)
        return {
            "schema": SCHEMA,
            "engine": model.dump(),
            "evidence": deepcopy(self.data),
        }
