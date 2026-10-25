"""Prospective numerical coverage policy; never reconstruct physical edges."""

from copy import deepcopy
from math import isfinite

POLICY = "a061-sparse-observations-v1"
NORMAL = 15.0
SHORT = 30.0
EDGE = 11.0
LIMIT = 65535
CAUSES = {
    "prolonged_outage",
    "clock_discontinuity",
    "restart",
    "shutdown",
    "command_boundary",
    "storage_failure",
    "missing_observations",
    "session_change",
    "phase_change",
    "sampling_gap",
}


class GapPolicy:
    def __init__(self):
        self.data = {
            "policy": POLICY,
            "normal_jitter_intervals": 0,
            "short_gaps": 0,
            "hard_gaps": 0,
            "edge_sensitive_gaps": 0,
            "longest_short_gap_seconds": 0.0,
            "numerical_history_preserved_across_short_gap": 0,
            "hard_gap_causes": dict.fromkeys(sorted(CAUSES), 0),
        }
        self.previous = None
        self.failed = False
        self.hard_reason = None

    def bump(self, key):
        self.data[key] = min(LIMIT, self.data[key] + 1)

    def hard(self, reason):
        reason = reason if reason in CAUSES else "missing_observations"
        if self.hard_reason != reason:
            self.bump("hard_gaps")
            causes = self.data["hard_gap_causes"]
            causes[reason] = min(LIMIT, causes[reason] + 1)
        self.previous, self.failed, self.hard_reason = None, False, reason

    def classify(self, s):
        if not s.usable():
            return "missing_observations", True
        old = self.previous
        if old is None:
            return "BASELINE", False
        seconds = (s.at - old.at).total_seconds()
        elapsed = s.monotonic - old.monotonic
        if seconds <= 0 or elapsed <= 0 or abs(seconds - elapsed) > 1:
            return "clock_discontinuity", True
        if old.session != s.session:
            return "session_change", True
        normal, short, edge = (
            (61, 61, 61) if old.phase == "Standby" else (NORMAL, SHORT, EDGE)
        )
        if seconds > short:
            return "prolonged_outage", True
        uncertain = self.failed or seconds > edge
        if uncertain:
            self.bump("edge_sensitive_gaps")
        if seconds <= normal:
            self.bump("normal_jitter_intervals")
            return "NORMAL_JITTER", uncertain
        self.bump("short_gaps")
        self.data["longest_short_gap_seconds"] = max(
            self.data["longest_short_gap_seconds"], seconds
        )
        return "SHORT_GAP", True

    def accept(self, s):
        self.previous, self.failed, self.hard_reason = s, False, None

    def dump(self):
        return deepcopy(self.data)

    def summary(self):
        return {
            **deepcopy(self.data),
            "normal_active_max_seconds": NORMAL,
            "short_active_max_seconds": SHORT,
            "strict_active_edge_max_seconds": EDGE,
            "standby_max_seconds": 61,
            "counter_limit": LIMIT,
            "scope": "prospective_persistent_policy",
            "edge_uncertainty_is_not_outage_proof": True,
        }

    @classmethod
    def restore(cls, payload):
        result = cls()
        if type(payload) is not dict or set(payload) != set(result.data):
            raise ValueError("Invalid gap policy checkpoint")
        if payload["policy"] != POLICY:
            raise ValueError("Unknown gap policy")
        for key in result.data:
            value = payload[key]
            if key in {"policy", "hard_gap_causes"}:
                continue
            if key == "longest_short_gap_seconds":
                valid = (
                    type(value) in (int, float)
                    and isfinite(value)
                    and 0 <= value <= SHORT
                )
            else:
                valid = type(value) is int and 0 <= value <= LIMIT
            if not valid:
                raise ValueError("Invalid gap counter")
        causes = payload["hard_gap_causes"]
        if (
            type(causes) is not dict
            or set(causes) != CAUSES
            or any(type(v) is not int or not 0 <= v <= LIMIT for v in causes.values())
        ):
            raise ValueError("Invalid hard-gap causes")
        result.data = deepcopy(payload)
        result.hard("restart")
        return result


def break_edges(model, reason):
    """Keep numerical rows, but never score an issued forecast across lost edges."""
    model.close(reason, gap=True)
    model.previous = model.last_request = None
    model._last_coverage_boundary = reason
    model.unavailable(reason)
    # Preserve spent/cycle_recorded: loss alone does not create a new opportunity.
