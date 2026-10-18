"""Prospective, bounded comparison of two forecasts; never a predictor input."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from math import isfinite

from ._shadow_model import MODEL, timestamp

POLICY = "a043-observed-horizons-v1"
NATIVE = "hwam-countdown-glow-observed-utc-v1"
SOURCES = (NATIVE, MODEL)
HORIZONS = (120, 90, 60, 45, 30, 20, 15, 10, 5)
WINDOWS = (60, 30, 15)
MAX_SAMPLES = 1024
MAX_LEDGER = 64
MAX_CHANGES = 64
MAX_AGE = 11.0  # Active cadence 10s + existing HA scheduler rounding allowance.
MATERIAL_CHANGE = 60.0  # Benchmark revision reporting, NOT a model threshold.
STATUSES = ("valid", "unavailable", "invalidated", "insufficient_data")


def finite(value):
    return type(value) in (int, float) and isfinite(value)


def metric():
    return {"n": 0, "sum": 0.0, "absolute_sum": 0.0, "min": None, "max": None}


def add_metric(m, value):
    m["n"] += 1
    m["sum"] += value
    m["absolute_sum"] += abs(value)
    m["min"] = value if m["min"] is None else min(m["min"], value)
    m["max"] = value if m["max"] is None else max(m["max"], value)


def score_stats():
    return {
        "states": dict.fromkeys(STATUSES, 0),
        "errors": metric(),
        "eligible_states": dict.fromkeys(STATUSES, 0),
        "forecast_available": 0,
        "eligible_forecast_available": 0,
        "paired": 0,
        "sessions": [],
        "dates": [],
        "paired_errors": metric(),
        "eligible_errors": metric(),
        "eligible_sessions": [],
        "eligible_dates": [],
    }


def source_stats():
    return {
        "forecast_cycles": 0,
        "gap_censored_cycles": 0,
        "left_censored_lead_cycles": 0,
        "lead_minutes": metric(),
        "revision_count": metric(),
        "movement_minutes": metric(),
        "horizons": {str(h): score_stats() for h in HORIZONS},
        "final": score_stats(),
        "stability": {
            str(w): {
                "states": dict.fromkeys(STATUSES, 0),
                "movement_minutes": metric(),
                "observations": 0,
                "available_samples": 0,
            }
            for w in WINDOWS
        },
    }


def source_cycle():
    return {
        "first": None,
        "last": None,
        "material_anchor": None,
        "revisions": [],
        "revision_count": 0,
        "revision_records_evicted": 0,
        "minimum": None,
        "maximum": None,
        "periods": [],
        "period_records_evicted": 0,
        "available_samples": 0,
        "unavailable_samples": 0,
        "issued": None,
        "issuances": [],
        "issuance_records_evicted": 0,
    }


def witnesses(item, session, prefix=""):
    item = {"sessions": item[prefix + "sessions"], "dates": item[prefix + "dates"]}
    if session not in item["sessions"] and len(item["sessions"]) < 20:
        item["sessions"].append(session)
    day = timestamp(session).date().isoformat()
    if day not in item["dates"] and len(item["dates"]) < 10:
        item["dates"].append(day)


def meaningful(item):
    return (
        item["paired"] >= 30
        and len(item["sessions"]) >= 20
        and len(item["dates"]) >= 10
    )


def summary_metric(m, show=True):
    return {
        "samples": m["n"],
        "mean": m["sum"] / m["n"] if m["n"] and show else None,
        "mean_absolute": m["absolute_sum"] / m["n"] if m["n"] and show else None,
        "min": m["min"] if show else None,
        "max": m["max"] if show else None,
    }


def score(
    state, *, forecast=None, observed=None, actual=None, reason=None, issuance=None
):
    return {
        "state": state,
        "issuance_at": issuance,
        "forecast_at": forecast,
        "observed_at": observed,
        "signed_error_minutes": (forecast - actual) / 60 if state == "valid" else None,
        "reason": reason,
    }


def at_horizon(cycle, target, source, actual):
    # Outcome selects a historical query time, never a forecast fitting input.
    index = SOURCES.index(source) + 1
    rows = cycle["samples"]
    row = next((r for r in reversed(rows) if r[0] <= target), None)
    if row is None:
        state = "invalidated" if cycle["start_kind"] == "gap" else "insufficient_data"
        return score(
            state,
            reason="gap_or_left_truncation"
            if state == "invalidated"
            else "no_prior_observation",
        )
    if target - row[0] > MAX_AGE:
        return score("invalidated", observed=row[0], reason="stale_observation")
    if row[index] is None:
        return score("unavailable", observed=row[0], reason="no_valid_forecast")
    return score(
        "valid",
        forecast=row[index],
        observed=row[0],
        actual=actual,
        issuance=row[3] if source == MODEL else None,
    )


def stability(cycle, actual, minutes, source):
    first = at_horizon(cycle, actual - minutes * 60, source, actual)
    rows = [r for r in cycle["samples"] if actual - minutes * 60 <= r[0] < actual]
    if first["state"] in {"invalidated", "insufficient_data"}:
        return {
            "state": first["state"],
            "range_minutes": None,
            "valid_samples": 0,
            "observations": len(rows),
            "material_revisions": 0,
        }
    index = SOURCES.index(source) + 1
    values = [r[index] for r in rows if r[index] is not None]
    revisions, anchor = 0, None
    for value in values:
        if anchor is None:
            anchor = value
        elif abs(value - anchor) >= MATERIAL_CHANGE:
            revisions += 1
            anchor = value
    state = (
        "valid"
        if len(values) >= 2
        else "insufficient_data"
        if values
        else "unavailable"
    )
    return {
        "state": state,
        "range_minutes": (max(values) - min(values)) / 60 if state == "valid" else None,
        "valid_samples": len(values),
        "observations": len(rows),
        "material_revisions": revisions,
    }


class Benchmark:
    """Immutable completed comparisons plus a bounded trailing forecast buffer."""

    def __init__(self):
        self.data = {
            "schema": 1,
            "policy": POLICY,
            "source_ids": list(SOURCES),
            "active": None,
            "ledger": [],
            "evicted": 0,
            "cycles_observed": 0,
            "actual_requests_observed": 0,
            "eligible_requests": 0,
            "sessions": [],
            "dates": [],
            "gap_censored_cycles": 0,
            "gap_before": False,
            "sources": {source: source_stats() for source in SOURCES},
        }
        self._previous = None
        self._sequence = -1

    def gap(self, reason):
        if self.data["active"]:
            self._finish(None, "gap", reason=reason)
        self.data["gap_before"] = True
        self._previous = None

    def _start(self, s, kind, qualified):
        self.data["cycles_observed"] += 1
        self.data["active"] = {
            "ordinal": self.data["cycles_observed"],
            "session": s.session,
            "start": s.at.timestamp(),
            "start_kind": kind,
            "qualified": qualified and kind in {"ignition", "alarm_cleared"},
            "sources": {source: source_cycle() for source in SOURCES},
            "samples": [],
            "samples_evicted": 0,
        }
        self.data["gap_before"] = False

    @staticmethod
    def native(s, data):
        # Same availability as the existing native timestamp entity. The duration
        # remains present in other phases but is not a valid timestamp forecast.
        if s.phase != "Glow":
            return None, "wait_until_glow"
        duration = data.get("time_to_new_fire_wood")
        if type(duration) is not timedelta or duration.total_seconds() < 0:
            return None, "invalid_or_missing_duration"
        try:
            result = (s.at + duration).timestamp()
        except (OverflowError, ValueError):
            return None, "invalid_or_missing_duration"
        return result, "available"

    def observe(self, s, data, model, analytics):
        if s.sequence <= self._sequence:
            return
        self._sequence = s.sequence
        old = self._previous
        if old:
            delta = (s.at - old.at).total_seconds()
            if delta <= 0 or abs(delta - (s.monotonic - old.monotonic)) > 1:
                self.gap("clock_discontinuity")
                old = None
            elif delta > MAX_AGE and old.phase != "Standby":
                # Conservative completed-observation age, including read latency.
                self.gap("observation_age")
                old = None
        if (
            not analytics.healthy
            or not analytics.online
            or type(s.alarm) is not bool
            or s.phase not in {"Ignition", "Burn", "Glow", "Standby"}
        ):
            self.gap("missing_observation")
            return
        active = self.data["active"]
        if active and active["session"] != s.session:
            self._finish(None, "session_end")
            old = None
        current = analytics.state.current
        qualified = bool(current and current.start and not current.coverage_uncertain)
        if s.alarm:
            if old and old.session == s.session and old.alarm is False:
                self.data["actual_requests_observed"] += 1
                if self.data["active"]:
                    self.data["active"]["qualified"] &= qualified
                    self._finish(
                        s.at.timestamp(),
                        "request",
                        previous=old.at.timestamp(),
                        model=model,
                    )
            # Do not read a forecast from the request observation to score itself.
            self._previous = s
            return
        if s.phase == "Standby" or not s.session:
            if self.data["active"]:
                self._finish(None, "session_end")
            self._previous = s
            return
        if self.data["active"] is None:
            kind = (
                "gap"
                if self.data["gap_before"]
                else "alarm_cleared"
                if old and old.alarm and old.session == s.session
                else "ignition"
                if s.phase == "Ignition"
                else "left_censored"
            )
            self._start(s, kind, qualified)
        cycle = self.data["active"]
        cycle["qualified"] &= qualified
        native, native_reason = self.native(s, data)
        pending = model.pending
        predicted = (
            timestamp(pending["predicted_request_at"]).timestamp() if pending else None
        )
        forecasts = (native, predicted)
        now = s.at.timestamp()
        for source, value, reason in zip(
            SOURCES,
            forecasts,
            (native_reason, "pending_evaluation" if pending else model.reason),
            strict=True,
        ):
            track = cycle["sources"][source]
            available = value is not None
            track["available_samples" if available else "unavailable_samples"] += 1
            periods = track["periods"]
            if (
                periods
                and periods[-1]["available"] == available
                and periods[-1]["reason"] == reason
            ):
                periods[-1]["last_observed"] = now
            else:
                periods.append(
                    {
                        "first_observed": now,
                        "last_observed": now,
                        "available": available,
                        "reason": reason,
                    }
                )
                if len(periods) > MAX_CHANGES:
                    periods.pop(1)  # Keep first observation and latest periods.
                    track["period_records_evicted"] += 1
            if not available:
                continue
            record = {"observed_at": now, "forecast_at": value}
            if track["first"] is None:
                track["first"] = record.copy()
                track["material_anchor"] = value
                self.data["sources"][source]["forecast_cycles"] += 1
            elif abs(value - track["material_anchor"]) >= MATERIAL_CHANGE:
                track["revision_count"] += 1
                track["material_anchor"] = value
                track["revisions"].append(record.copy())
                if len(track["revisions"]) > MAX_CHANGES:
                    track["revisions"].pop(0)
                    track["revision_records_evicted"] += 1
            if source == MODEL:
                issuance = {
                    key: pending[key]
                    for key in (
                        "prediction_at",
                        "predicted_request_at",
                        "model_version",
                        "confidence",
                    )
                }
                if not track["issuances"] or track["issuances"][-1] != issuance:
                    track["issuances"].append(issuance)
                    if track["issued"] is None:
                        track["issued"] = issuance.copy()
                    if len(track["issuances"]) > MAX_CHANGES:
                        track["issuances"].pop(0)
                        track["issuance_records_evicted"] += 1
            track["last"] = record
            track["minimum"] = (
                value if track["minimum"] is None else min(track["minimum"], value)
            )
            track["maximum"] = (
                value if track["maximum"] is None else max(track["maximum"], value)
            )
        cycle["samples"].append(
            [
                now,
                *forecasts,
                timestamp(pending["prediction_at"]).timestamp() if pending else None,
            ]
        )
        floor = now - 120 * 60 - MAX_AGE
        while cycle["samples"] and (
            cycle["samples"][0][0] < floor or len(cycle["samples"]) > MAX_SAMPLES
        ):
            cycle["samples"].pop(0)
            cycle["samples_evicted"] += 1
        self._previous = s

    def _finish(self, actual, outcome, *, reason=None, previous=None, model=None):
        cycle = self.data["active"]
        record = {
            key: deepcopy(value) for key, value in cycle.items() if key != "samples"
        }
        record.update(
            outcome=outcome,
            actual_request_at=actual,
            request_after=previous,
            reason=reason,
            source_ids=list(SOURCES),
            policy=POLICY,
            results={},
        )
        if outcome == "gap":
            self.data["gap_censored_cycles"] += 1
        for source in SOURCES:
            track = cycle["sources"][source]
            aggregate = self.data["sources"][source]
            if outcome == "gap" and track["first"]:
                aggregate["gap_censored_cycles"] += 1
            if actual is None:
                continue
            first = track["first"]
            lead = (actual - first["observed_at"]) / 60 if first else None
            if lead is not None:
                add_metric(aggregate["lead_minutes"], lead)
                if cycle["start_kind"] in {"gap", "left_censored"}:
                    aggregate["left_censored_lead_cycles"] += 1
                add_metric(aggregate["revision_count"], track["revision_count"])
                add_metric(
                    aggregate["movement_minutes"],
                    (track["maximum"] - track["minimum"]) / 60,
                )
            horizons = {
                str(h): at_horizon(cycle, actual - h * 60, source, actual)
                for h in HORIZONS
            }
            # Final means last completed observation strictly BEFORE the edge.
            final = at_horizon(cycle, actual, source, actual)
            if source == MODEL:
                for result in [*horizons.values(), final]:
                    if result["state"] != "valid":
                        continue
                    accepted = model is not None and any(
                        row["prediction_at"] is not None
                        and timestamp(row["prediction_at"]).timestamp()
                        == result["issuance_at"]
                        and timestamp(row["predicted_request_at"]).timestamp()
                        == result["forecast_at"]
                        and row["observed_request_at"]
                        == datetime.fromtimestamp(actual, UTC).isoformat()
                        and row["outcome"] == "request_observed"
                        and not row["gap_invalid"]
                        for row in model.ledger
                    )
                    if not accepted:
                        result.update(
                            state="invalidated",
                            signed_error_minutes=None,
                            reason="frozen_model_did_not_score_request",
                        )
            windows = {str(w): stability(cycle, actual, w, source) for w in WINDOWS}
            record["results"][source] = {
                "first_observed_lead_minutes": lead,
                "first_availability_left_censored": cycle["start_kind"]
                in {"gap", "left_censored"},
                "horizons": horizons,
                "final": final,
                "stability": windows,
            }
            for key, result in [*horizons.items(), ("final", final)]:
                accumulator = (
                    aggregate["final"] if key == "final" else aggregate["horizons"][key]
                )
                accumulator["states"][result["state"]] += 1
                if result["forecast_at"] is not None:
                    accumulator["forecast_available"] += 1
                if cycle["qualified"]:
                    accumulator["eligible_states"][result["state"]] += 1
                    if result["forecast_at"] is not None:
                        accumulator["eligible_forecast_available"] += 1
                if result["state"] == "valid":
                    add_metric(accumulator["errors"], result["signed_error_minutes"])
                    if cycle["qualified"]:
                        add_metric(
                            accumulator["eligible_errors"],
                            result["signed_error_minutes"],
                        )
                        witnesses(accumulator, cycle["session"], "eligible_")
            for w, result in windows.items():
                aggregate["stability"][w]["states"][result["state"]] += 1
                aggregate["stability"][w]["observations"] += result["observations"]
                aggregate["stability"][w]["available_samples"] += result[
                    "valid_samples"
                ]
                if result["state"] == "valid":
                    add_metric(
                        aggregate["stability"][w]["movement_minutes"],
                        result["range_minutes"],
                    )
        if actual is not None and cycle["qualified"]:
            self.data["eligible_requests"] += 1
            witnesses(self.data, cycle["session"])
            for key in [*(str(h) for h in HORIZONS), "final"]:
                results = [
                    record["results"][s]["final"]
                    if key == "final"
                    else record["results"][s]["horizons"][key]
                    for s in SOURCES
                ]
                if all(r["state"] == "valid" for r in results):
                    for source in SOURCES:
                        aggregate = self.data["sources"][source]
                        item = (
                            aggregate["final"]
                            if key == "final"
                            else aggregate["horizons"][key]
                        )
                        item["paired"] += 1
                        result = (
                            record["results"][source]["final"]
                            if key == "final"
                            else record["results"][source]["horizons"][key]
                        )
                        add_metric(
                            item["paired_errors"], result["signed_error_minutes"]
                        )
                        witnesses(item, cycle["session"])
        self.data["ledger"].append(record)
        if len(self.data["ledger"]) > MAX_LEDGER:
            self.data["ledger"].pop(0)
            self.data["evicted"] += 1
        self.data["active"] = None

    def dump(self):
        return deepcopy(self.data)

    @classmethod
    def restore(cls, payload):
        result = cls()
        validate(payload)
        result.data = deepcopy(payload)
        # Never restore edge anchors or match a pre-restart forecast to a later
        # request that might belong to an unobserved intervening request cycle.
        result.gap("restart")
        return result

    def summary(self):
        result = {
            key: self.data[key]
            for key in (
                "schema",
                "policy",
                "cycles_observed",
                "actual_requests_observed",
                "gap_censored_cycles",
            )
        }
        result.update(
            preliminary=not (
                self.data["eligible_requests"] >= 30
                and len(self.data["sessions"]) >= 20
                and len(self.data["dates"]) >= 10
            ),
            eligible_request_cycles=self.data["eligible_requests"],
            sessions_capped_at_20=len(self.data["sessions"]),
            dates_capped_at_10=len(self.data["dates"]),
            minimum_eligible_cycles=30,
            conclusion="no_source_ranking",
            minimum_paired_cycles=30,
            minimum_sessions=20,
            minimum_utc_dates=10,
            retained_cycles=len(self.data["ledger"]),
            sources={},
        )
        for source, aggregate in self.data["sources"].items():

            def summarize(item):
                ready = meaningful(item)
                individual_ready = (
                    item["eligible_errors"]["n"] >= 30
                    and len(item["eligible_sessions"]) >= 20
                    and len(item["eligible_dates"]) >= 10
                )
                total = sum(item["states"].values())
                eligible_total = sum(item["eligible_states"].values())
                return {
                    "states": item["states"].copy(),
                    "scoring_coverage": item["states"]["valid"] / total
                    if total
                    else None,
                    "forecast_coverage": item["forecast_available"] / total
                    if total
                    else None,
                    "eligible_states": item["eligible_states"].copy(),
                    "eligible_forecast_coverage": item["eligible_forecast_available"]
                    / eligible_total
                    if eligible_total
                    else None,
                    "paired_samples": item["paired"],
                    "sessions_capped_at_20": len(item["sessions"]),
                    "dates_capped_at_10": len(item["dates"]),
                    "paired_evidence_reached": ready,
                    "valid_error_samples": item["errors"]["n"],
                    "eligible_sessions_capped_at_20": len(item["eligible_sessions"]),
                    "eligible_dates_capped_at_10": len(item["eligible_dates"]),
                    "source_evidence_reached": individual_ready,
                    "signed_error_minutes": summary_metric(
                        item["eligible_errors"], individual_ready
                    ),
                    "paired_signed_error_minutes": summary_metric(
                        item["paired_errors"], ready
                    ),
                }

            result["sources"][source] = {
                "forecast_cycles": aggregate["forecast_cycles"],
                "gap_censored_cycles": aggregate["gap_censored_cycles"],
                "left_censored_lead_cycles": aggregate["left_censored_lead_cycles"],
                "first_observed_lead_minutes": summary_metric(
                    aggregate["lead_minutes"]
                ),
                "material_revisions": summary_metric(aggregate["revision_count"]),
                "maximum_movement_minutes": summary_metric(
                    aggregate["movement_minutes"]
                ),
                "horizons": {
                    h: summarize(item) for h, item in aggregate["horizons"].items()
                },
                "final": summarize(aggregate["final"]),
                "stability": {
                    w: {
                        "states": item["states"].copy(),
                        "observations": item["observations"],
                        "available_samples": item["available_samples"],
                        "observed_range_minutes": summary_metric(
                            item["movement_minutes"]
                        ),
                    }
                    for w, item in aggregate["stability"].items()
                },
            }
        return result


def require(condition):
    if not condition:
        raise ValueError("Invalid benchmark checkpoint")


def integer(value):
    require(type(value) is int and 0 <= value < 2**63)


def check_metric(item):
    require(type(item) is dict and set(item) == set(metric()))
    integer(item["n"])
    require(
        finite(item["sum"])
        and finite(item["absolute_sum"])
        and item["absolute_sum"] >= 0
    )
    require(all(v is None or finite(v) for v in (item["min"], item["max"])))
    require((item["min"] is None) == (item["n"] == 0) == (item["max"] is None))
    if item["n"]:
        require(item["min"] <= item["max"])
    else:
        require(item["sum"] == item["absolute_sum"] == 0)


def check_states(item):
    require(type(item) is dict and set(item) == set(STATUSES))
    for number in item.values():
        integer(number)


def check_score_stats(item):
    require(type(item) is dict and set(item) == set(score_stats()))
    check_states(item["states"])
    check_states(item["eligible_states"])
    integer(item["forecast_available"])
    integer(item["eligible_forecast_available"])
    require(
        item["states"]["valid"]
        <= item["forecast_available"]
        <= sum(item["states"].values())
    )
    require(item["eligible_forecast_available"] <= item["forecast_available"])
    require(
        item["eligible_states"]["valid"]
        <= item["eligible_forecast_available"]
        <= sum(item["eligible_states"].values())
    )
    require(all(item["eligible_states"][k] <= item["states"][k] for k in STATUSES))
    check_metric(item["errors"])
    check_metric(item["eligible_errors"])
    require(item["eligible_errors"]["n"] == item["eligible_states"]["valid"])
    check_metric(item["paired_errors"])
    integer(item["paired"])
    require(
        item["paired_errors"]["n"]
        == item["paired"]
        <= item["eligible_errors"]["n"]
        <= item["errors"]["n"]
    )
    require(item["errors"]["n"] == item["states"]["valid"] >= item["paired"])
    for key, maximum in (
        ("sessions", 20),
        ("dates", 10),
        ("eligible_sessions", 20),
        ("eligible_dates", 10),
    ):
        require(
            type(item[key]) is list
            and len(item[key]) <= maximum
            and len(set(item[key])) == len(item[key])
        )
        for value in item[key]:
            timestamp(value if key.endswith("sessions") else value + "T00:00:00+00:00")


def check_cycle(item, active):
    keys = {
        "ordinal",
        "session",
        "start",
        "start_kind",
        "qualified",
        "sources",
        "samples_evicted",
    }
    keys |= (
        {"samples"}
        if active
        else {
            "outcome",
            "actual_request_at",
            "request_after",
            "reason",
            "source_ids",
            "policy",
            "results",
        }
    )
    require(type(item) is dict and set(item) == keys)
    integer(item["ordinal"])
    integer(item["samples_evicted"])
    timestamp(item["session"])
    require(finite(item["start"]) and type(item["qualified"]) is bool)
    require(item["start_kind"] in {"ignition", "alarm_cleared", "gap", "left_censored"})
    require(type(item["sources"]) is dict and set(item["sources"]) == set(SOURCES))
    for source, track in item["sources"].items():
        require(type(track) is dict and set(track) == set(source_cycle()))
        for key in (
            "revision_count",
            "revision_records_evicted",
            "period_records_evicted",
            "available_samples",
            "unavailable_samples",
        ):
            integer(track[key])
        for key in ("material_anchor", "minimum", "maximum"):
            require(track[key] is None or finite(track[key]))
        require(
            type(track["revisions"]) is list and len(track["revisions"]) <= MAX_CHANGES
        )
        for row in [track["first"], track["last"], *track["revisions"]]:
            if row is not None:
                require(
                    type(row) is dict and set(row) == {"observed_at", "forecast_at"}
                )
                require(all(finite(v) for v in row.values()))
        require(type(track["periods"]) is list and len(track["periods"]) <= MAX_CHANGES)
        for row in track["periods"]:
            require(
                type(row) is dict
                and set(row)
                == {"first_observed", "last_observed", "available", "reason"}
            )
            require(finite(row["first_observed"]) and finite(row["last_observed"]))
            require(
                type(row["available"]) is bool
                and type(row["reason"]) is str
                and len(row["reason"]) <= 80
            )
        integer(track["issuance_records_evicted"])
        require(
            type(track["issuances"]) is list and len(track["issuances"]) <= MAX_CHANGES
        )
        for issued in [track["issued"], *track["issuances"]]:
            if issued is None:
                continue
            require(
                source == MODEL
                and set(issued)
                == {
                    "prediction_at",
                    "predicted_request_at",
                    "model_version",
                    "confidence",
                }
            )
            timestamp(issued["prediction_at"])
            timestamp(issued["predicted_request_at"])
            require(
                issued["model_version"] == MODEL
                and issued["confidence"] == "experimental_uncalibrated"
            )
    if active:
        require(type(item["samples"]) is list and len(item["samples"]) <= MAX_SAMPLES)
        last = None
        for row in item["samples"]:
            require(type(row) is list and len(row) == 4 and finite(row[0]))
            require(all(v is None or finite(v) for v in row[1:]))
            require(last is None or row[0] > last)
            last = row[0]
    else:
        require(item["outcome"] in {"request", "gap", "session_end"})
        require(item["source_ids"] == list(SOURCES) and item["policy"] == POLICY)
        require(
            item["reason"] is None
            or (type(item["reason"]) is str and len(item["reason"]) <= 80)
        )
        require(item["actual_request_at"] is None or finite(item["actual_request_at"]))
        require(item["request_after"] is None or finite(item["request_after"]))
        require(
            type(item["results"]) is dict
            and set(item["results"])
            == (set(SOURCES) if item["outcome"] == "request" else set())
        )
        if item["outcome"] == "request":
            require(
                finite(item["actual_request_at"])
                and finite(item["request_after"])
                and item["request_after"] < item["actual_request_at"]
            )
        for result in item["results"].values():
            require(
                set(result)
                == {
                    "first_observed_lead_minutes",
                    "first_availability_left_censored",
                    "horizons",
                    "final",
                    "stability",
                }
            )
            require(
                result["first_observed_lead_minutes"] is None
                or finite(result["first_observed_lead_minutes"])
            )
            require(type(result["first_availability_left_censored"]) is bool)
            require(
                set(result["horizons"]) == {str(h) for h in HORIZONS}
                and set(result["stability"]) == {str(w) for w in WINDOWS}
            )
            for row in [*result["horizons"].values(), result["final"]]:
                require(
                    set(row) == set(score("unavailable")) and row["state"] in STATUSES
                )
                for key in (
                    "forecast_at",
                    "observed_at",
                    "signed_error_minutes",
                    "issuance_at",
                ):
                    require(row[key] is None or finite(row[key]))
                if row["state"] == "valid":
                    require(finite(row["forecast_at"]) and finite(row["observed_at"]))
                    require(
                        row["signed_error_minutes"]
                        == (row["forecast_at"] - item["actual_request_at"]) / 60
                    )
                else:
                    require(row["signed_error_minutes"] is None)
            for row in result["stability"].values():
                require(
                    set(row)
                    == {
                        "state",
                        "range_minutes",
                        "valid_samples",
                        "observations",
                        "material_revisions",
                    }
                )
                require(
                    row["state"] in STATUSES
                    and (row["range_minutes"] is None or finite(row["range_minutes"]))
                )
                for key in ("valid_samples", "observations", "material_revisions"):
                    integer(row[key])


def validate(payload):
    require(type(payload) is dict and set(payload) == set(Benchmark().data))
    require(
        type(payload["schema"]) is int
        and payload["schema"] == 1
        and payload["policy"] == POLICY
    )
    require(
        payload["source_ids"] == list(SOURCES) and type(payload["gap_before"]) is bool
    )
    for key in (
        "eligible_requests",
        "evicted",
        "cycles_observed",
        "actual_requests_observed",
        "gap_censored_cycles",
    ):
        integer(payload[key])
    require(payload["eligible_requests"] <= payload["actual_requests_observed"])
    for key, maximum in (("sessions", 20), ("dates", 10)):
        require(type(payload[key]) is list and len(payload[key]) <= maximum)
        require(len(set(payload[key])) == len(payload[key]))
        for value in payload[key]:
            timestamp(value if key == "sessions" else value + "T00:00:00+00:00")
    require(type(payload["ledger"]) is list and len(payload["ledger"]) <= MAX_LEDGER)
    require(
        payload["cycles_observed"]
        == payload["evicted"] + len(payload["ledger"]) + (payload["active"] is not None)
    )
    for item in payload["ledger"]:
        check_cycle(item, False)
    if payload["active"] is not None:
        check_cycle(payload["active"], True)
    require(
        type(payload["sources"]) is dict and set(payload["sources"]) == set(SOURCES)
    )
    for aggregate in payload["sources"].values():
        require(set(aggregate) == set(source_stats()))
        integer(aggregate["forecast_cycles"])
        integer(aggregate["left_censored_lead_cycles"])
        integer(aggregate["gap_censored_cycles"])
        for key in ("lead_minutes", "revision_count", "movement_minutes"):
            check_metric(aggregate[key])
        require(set(aggregate["horizons"]) == {str(h) for h in HORIZONS})
        for row in [*aggregate["horizons"].values(), aggregate["final"]]:
            check_score_stats(row)
        require(set(aggregate["stability"]) == {str(w) for w in WINDOWS})
        for row in aggregate["stability"].values():
            require(
                set(row)
                == {"states", "movement_minutes", "observations", "available_samples"}
            )
            integer(row["observations"])
            integer(row["available_samples"])
            require(row["available_samples"] <= row["observations"])
            check_states(row["states"])
            check_metric(row["movement_minutes"])
