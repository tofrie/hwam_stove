"""Replay the controller fallback against private A07 exports without accessing HA."""

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.hwam_stove._live_forecast import LiveState
from custom_components.hwam_stove._shadow_model import crossing


def analyse(files):
    rows = {}
    sources = []
    for file in files:
        raw = file.read_bytes()
        d = json.loads(raw)
        assert (
            d["schema_version"] == 1
            and len(d["rows"]) == d["metadata"]["retained_records"]
        )
        for row in d["rows"]:
            r = dict(zip(d["columns"], row, strict=True))
            assert rows.setdefault(r["sequence"], r) == r, "Conflicting export overlap"
        sources.append([hashlib.sha256(raw).hexdigest(), len(d["rows"])])
    live = LiveState()
    previous = None
    windows, samples = deque(), []
    sessions, quality, reasons = defaultdict(list), Counter(), Counter()
    transitions, edges, doors = Counter(), [], []
    for key in sorted(rows):
        r = rows[key]
        at = datetime.fromisoformat(r["utc"])
        phase, alarm, door = r["phase"], r["refill_alarm"], r["door_open"]
        quality[r["quality"]] += 1
        if r["kind"] == "gap":
            live.boundary(r["reason"])
        else:
            live.step(at, at.timestamp(), phase, door, alarm, r["hwam_minutes"])
        native, status, reason = live.display(at, "fresh")
        active = phase in {"Glow", "Burn"} and alarm is False and door is False
        if active:
            reasons[reason] += 1
        if r["session"]:
            sessions[r["session"]].append(r)
        if previous and previous["phase"] and phase and previous["phase"] != phase:
            transitions[(previous["phase"], phase)] += 1
        if previous and door and previous["door_open"] is False:
            doors.append([r["utc"], r["quality"]])
        if not active or phase != "Burn" or r["quality"] != "continuous":
            windows.clear()
        crossing_ok = False
        if active and phase == "Burn":
            windows.append(
                SimpleNamespace(at=at, temperature=r["stove_temperature"], phase=phase)
            )
            while len(windows) > 1 and (at - windows[1].at).total_seconds() >= 600:
                windows.popleft()
            if (at - windows[0].at).total_seconds() >= 600:
                eta, why = crossing(list(windows))
                crossing_ok = eta is not None and eta > 0
        if previous and previous["refill_alarm"] is False and alarm is True:
            edge = {
                "session": r["session"],
                "utc": r["utc"],
                "quality": r["quality"],
                "temperature": r["stove_temperature"],
                "horizons": {},
            }
            # Only uninterrupted, closed-door, alarm-off pre-edge observations.
            eligible = []
            if r["quality"] == "continuous" and door is False:
                for t, other, prediction, independent in reversed(samples):
                    if (
                        other["session"] != r["session"]
                        or other["door_open"] is not False
                        or other["refill_alarm"] is not False
                    ):
                        break
                    eligible.append((t, prediction, independent))
                    if other["quality"] != "continuous":
                        break

            def result(t, pred, at=at):
                return {
                    "lead_min": round((at - t).total_seconds() / 60, 2),
                    "error_min": round((pred - at).total_seconds() / 60, 2),
                }

            qualified = [(t, p) for t, p, _ in eligible if p]
            if qualified:
                edge["first"] = result(*qualified[-1])
                edge["last"] = result(*qualified[0])
            for h in (5, 15, 30, 45, 60, 90, 120):
                point = next(
                    (
                        (t, p)
                        for t, p, _ in eligible
                        if (at - t).total_seconds() >= h * 60
                    ),
                    None,
                )
                if (
                    point
                    and (at - point[0]).total_seconds() <= h * 60 + 11
                    and point[1]
                ):
                    edge["horizons"][h] = result(*point)
            edge["independent_window_in_preceding_30min"] = any(
                ok for t, _, ok in eligible if (at - t).total_seconds() <= 1800
            )
            edges.append(edge)
        samples.append((at, r, native, crossing_ok))
        previous = r

    def span(rr, k):
        v = [r[k] for r in rr if r[k] is not None]
        return [min(v), max(v)]

    session_reports = {}
    for n, rr in sessions.items():
        session_reports[n] = {
            "start_phase": rr[0]["phase"],
            "last_phase": rr[-1]["phase"],
            "start_peak_end_C": [
                rr[0]["stove_temperature"],
                max(r["stove_temperature"] for r in rr),
                rr[-1]["stove_temperature"],
            ],
            "oxygen_range": span(rr, "oxygen_level"),
            "valve_ranges": [span(rr, f"valve{i}_position") for i in (1, 2, 3)],
            "qualifications": dict(Counter(r["session_qualification"] for r in rr)),
        }
    # Controls require five observed minutes with no edge, door opening or gap.
    # Adjacent controls overlap and are descriptive, not independent training labels.
    controls, clean_until = [], None
    for t, r, _, _ in reversed(samples):
        if (
            r["phase"] != "Burn"
            or r["door_open"] is not False
            or r["refill_alarm"] is not False
            or r["quality"] != "continuous"
        ):
            clean_until = None
            continue
        if clean_until is None:
            clean_until = t
        if (clean_until - t).total_seconds() >= 300:
            controls.append(r)
    edge_times = {edge["utc"] for edge in edges if edge["quality"] == "continuous"}
    positives = [r for r in rows.values() if r["utc"] in edge_times]
    variables = (
        "stove_temperature",
        "oxygen_level",
        "valve1_position",
        "valve2_position",
        "valve3_position",
    )
    # A second sensitivity check permits the existing A06 numerical window
    # to span short gaps. No missing door/alarm edge is scored across such a gap.
    numeric = deque()
    crossings = Counter()
    for t, r, _, _ in samples:
        if (
            r["phase"] != "Burn"
            or r["door_open"] is not False
            or r["refill_alarm"] is not False
        ):
            numeric.clear()
            continue
        if r["quality"] not in {"continuous", "edge_uncertain", "short_gap"}:
            numeric.clear()
        numeric.append(
            SimpleNamespace(at=t, phase="Burn", temperature=r["stove_temperature"])
        )
        while len(numeric) > 1 and (t - numeric[1].at).total_seconds() >= 600:
            numeric.popleft()
        if (t - numeric[0].at).total_seconds() >= 600:
            eta, why = crossing(list(numeric))
            crossings[why if eta != 0 else "already_below_target"] += 1
    return {
        "sources_sha256_and_rows": sources,
        "unique_rows": len(rows),
        "strict_5min_negative_control_samples": len(controls),
        "negative_control_ranges": {k: span(controls, k) for k in variables},
        "request_edge_ranges": {k: span(positives, k) for k in variables},
        "numerical_30s_gap_sensitivity": crossings,
        "quality": quality,
        "phase_counts": Counter(r["phase"] or "gap" for r in rows.values()),
        "transitions": {f"{a}->{b}": n for (a, b), n in transitions.items()},
        "sessions": session_reports,
        "door_openings": doors,
        "edges": edges,
        "active_closed_alarm_off_reasons": reasons,
        "native_live_minutes_range": span(
            [r for _, r, p, _ in samples if p], "hwam_minutes"
        ),
        "rolling_crossing_opportunities_samples": sum(ok for _, _, _, ok in samples),
        "burn_120": sum(
            r["phase"] == "Burn" and r["hwam_minutes"] == 120 for r in rows.values()
        ),
        "burn_max_minutes": max(
            r["hwam_minutes"] for r in rows.values() if r["phase"] == "Burn"
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", nargs="+", type=Path)
    print(json.dumps(analyse(parser.parse_args().exports), indent=2))
