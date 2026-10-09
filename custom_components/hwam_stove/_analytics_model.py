"""Pure observed-session reducer. No controller, HA, clock or storage access."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import isfinite
from zoneinfo import ZoneInfo

SCHEMA = 2
LEDGER_LIMIT = 100
DAY_LIMIT = 400
MONTH_LIMIT = 24
PHASES = {"Ignition", "Burn", "Glow", "Standby"}


@dataclass
class Session:
    """An observation interval, never a measured fuel load."""

    first_observed: str
    start: str | None
    last_observed: str
    established: bool = False
    coverage_uncertain: bool = False
    elapsed: float = 0
    peak: float | None = None
    requests: int = 0
    end: str | None = None
    complete: bool = False
    completion_timezone: str | None = None


@dataclass
class State:
    """Bounded persistent state plus process-local ordering/clock anchors."""

    schema: int = SCHEMA
    phase: str | None = None
    last_observed: str | None = None
    current: Session | None = None
    ledger: list[Session] = field(default_factory=list)
    last_complete: Session | None = None
    completed: int = 0
    partial_completed: int = 0
    requests: int = 0
    request_active: bool | None = None
    request_edge_known: bool = False
    days: dict[str, dict[str, int]] = field(default_factory=dict)
    months: dict[str, dict[str, int]] = field(default_factory=dict)
    request_retention_floor: str | None = None
    sequence: int = -1
    monotonic: float | None = None


@dataclass(frozen=True)
class Observation:
    """One accepted regular response; sequence assigned before its request."""

    sequence: int
    utc: datetime
    monotonic: float
    phase: str
    refill: bool | None
    temperature: float | None
    timezone: str


def gap(state: State) -> State:
    """Forget continuity, not authoritative totals or an active observation."""
    result = deepcopy(state)
    result.monotonic = None
    result.request_edge_known = False
    if result.current:
        result.current.coverage_uncertain = True
    return result


def _calendar(state: State, observation: Observation, key: str) -> None:
    local = observation.utc.astimezone(ZoneInfo(observation.timezone))
    for buckets, label, limit in (
        (state.days, local.strftime("%Y-%m-%d"), DAY_LIMIT),
        (state.months, local.strftime("%Y-%m"), MONTH_LIMIT),
    ):
        row = buckets.setdefault(label, {"completed": 0, "requests": 0})
        row[key] += 1
        for old in sorted(buckets)[:-limit]:
            if buckets is state.days and buckets[old]["requests"]:
                state.request_retention_floor = max(
                    state.request_retention_floor or old, old
                )
            del buckets[old]


def reduce(state: State, observation: Observation) -> State:
    """Consume once in request order, without inferring unobserved transitions."""
    if observation.sequence <= state.sequence:
        return state
    result = deepcopy(state)
    result.sequence = observation.sequence
    if observation.phase not in PHASES:
        return gap(result)
    stamp = observation.utc.astimezone(UTC).isoformat()
    # Wall-clock corrections qualify boundaries; elapsed time never uses them.
    if (result.last_observed and stamp < result.last_observed) or (
        result.monotonic is not None and observation.monotonic < result.monotonic
    ):
        result = gap(result)
    old_phase = result.phase
    session = result.current
    if session and result.monotonic is not None:
        session.elapsed += max(0, observation.monotonic - result.monotonic)
    if session is None and observation.phase != "Standby":
        session = result.current = Session(
            first_observed=stamp,
            start=stamp if observation.phase == "Ignition" else None,
            last_observed=stamp,
            established=observation.phase in {"Burn", "Glow"},
        )
    if session:
        session.last_observed = stamp
        # Ignition after activity is ambiguous (including controller restarts).
        if observation.phase == "Ignition" and old_phase in {"Burn", "Glow"}:
            session.coverage_uncertain = True
        if observation.phase == "Burn":
            session.established = True
        if observation.phase == "Glow" and not session.established:
            session.established = True
            session.coverage_uncertain = True  # Burn was not observed.
        temperature = observation.temperature
        if (type(temperature) in {int, float} and isfinite(temperature)
                and observation.phase != "Standby"):
            session.peak = temperature if session.peak is None else max(
                session.peak, temperature
            )
    if observation.refill is None:
        result.request_edge_known = False
    else:
        if (result.request_edge_known and result.request_active is False
                and observation.refill is True):
            result.requests += 1
            _calendar(result, observation, "requests")
            if session:
                session.requests += 1
        result.request_active = observation.refill
        result.request_edge_known = True
    if observation.phase == "Standby" and session:
        if session.established:
            session.end = stamp
            session.completion_timezone = observation.timezone
            session.complete = (
                session.start is not None and not session.coverage_uncertain
            )
            if session.complete:
                result.completed += 1
                result.last_complete = deepcopy(session)
                _calendar(result, observation, "completed")
            else:
                result.partial_completed += 1
            result.ledger = [*result.ledger, session][-LEDGER_LIMIT:]
        result.current = None
    result.phase = observation.phase
    result.last_observed = stamp
    result.monotonic = observation.monotonic
    return result


def dump(state: State) -> dict:
    """Serialize only the explicit model, excluding process-local anchors."""
    result = asdict(state)
    del result["monotonic"]
    del result["sequence"]
    return result


def migrate(payload: dict) -> dict:
    """Preserve factual schema-1 data; qualify unknowable pruned request history."""
    if (type(payload) is not dict or type(payload.get("schema")) is not int
            or payload["schema"] not in (1, SCHEMA)):
        raise ValueError("Unsupported analytics schema")
    result = deepcopy(payload)
    if result["schema"] == 1:
        if set(result) != set(dump(State())) - {"request_retention_floor"}:
            raise ValueError("Invalid legacy analytics fields")
        retained = sum(row["requests"] for row in result["days"].values())
        # Schema 1 recorded no eviction watermark. If requests were lost, do not
        # certify any retained legacy day: clock rollback could have recreated
        # an evicted bucket. The latest retained date is a conservative floor.
        result["request_retention_floor"] = (
            max(result["days"], default="9999-12-31")
            if retained < result["requests"] else None
        )
        result["schema"] = SCHEMA
    return result


def restore(payload: dict) -> State:
    """Validate completely before accepting totals; never repair by zeroing."""
    data = migrate(payload)
    if set(data) != set(dump(State())):
        raise ValueError("Invalid analytics fields")

    def timestamp(value):
        if value is not None and (
            type(value) is not str or datetime.fromisoformat(value).utcoffset()
            != UTC.utcoffset(None)
        ):
            raise ValueError("Invalid UTC boundary")

    def integer(value):
        if type(value) is not int or value < 0:
            raise ValueError("Invalid counter")

    def session(value):
        if value is None:
            return None
        if type(value) is not dict or set(value) != set(asdict(
            Session("", None, "")
        )):
            raise ValueError("Invalid session")
        record = Session(**value)
        for item in (record.first_observed, record.last_observed):
            if item is None:
                raise ValueError("Missing observation boundary")
        for item in (record.first_observed, record.last_observed,
                     record.start, record.end):
            timestamp(item)
        for item in (record.established, record.coverage_uncertain, record.complete):
            if type(item) is not bool:
                raise ValueError("Invalid quality flag")
        for item in (record.elapsed, record.peak):
            if item is not None and (type(item) not in {int, float}
                                     or not isfinite(item)):
                raise ValueError("Invalid measurement")
        if record.elapsed is None or record.elapsed < 0:
            raise ValueError("Invalid duration")
        integer(record.requests)
        if record.completion_timezone is not None:
            ZoneInfo(record.completion_timezone)
        if record.complete and (not record.established or record.start is None
                                or record.end is None or record.coverage_uncertain):
            raise ValueError("Invalid complete session")
        return record

    timestamp(data["last_observed"])
    if data["phase"] is not None and data["phase"] not in PHASES:
        raise ValueError("Invalid phase")
    if data["request_active"] is not None and type(data["request_active"]) is not bool:
        raise ValueError("Invalid request state")
    if type(data["request_edge_known"]) is not bool:
        raise ValueError("Invalid edge state")
    for key in ("completed", "partial_completed", "requests"):
        integer(data[key])
    for key, limit, fmt in (("days", DAY_LIMIT, "%Y-%m-%d"),
                            ("months", MONTH_LIMIT, "%Y-%m")):
        buckets = data[key]
        if type(buckets) is not dict or len(buckets) > limit:
            raise ValueError("Invalid calendar")
        for label, counts in buckets.items():
            if datetime.strptime(label, fmt).strftime(fmt) != label or (
                type(counts) is not dict or set(counts) != {"completed", "requests"}
            ):
                raise ValueError("Invalid calendar bucket")
            for count in counts.values():
                integer(count)
    if type(data["ledger"]) is not list or len(data["ledger"]) > LEDGER_LIMIT:
        raise ValueError("Invalid ledger")
    data["ledger"] = [session(row) for row in data["ledger"]]
    if any(row is None or row.end is None for row in data["ledger"]):
        raise ValueError("Unfinished ledger record")
    data["current"] = session(data["current"])
    data["last_complete"] = session(data["last_complete"])
    if data["current"] and data["current"].end is not None:
        raise ValueError("Finished current record")
    if data["last_complete"] and not data["last_complete"].complete:
        raise ValueError("Invalid last complete record")
    if bool(data["completed"]) != (data["last_complete"] is not None):
        raise ValueError("Inconsistent lifetime total")
    if (sum(row.complete for row in data["ledger"]) > data["completed"]
            or sum(not row.complete for row in data["ledger"])
            > data["partial_completed"]):
        raise ValueError("Ledger exceeds lifetime totals")
    for buckets in (data["days"], data["months"]):
        for key in ("completed", "requests"):
            if sum(row[key] for row in buckets.values()) > data[key]:
                raise ValueError("Calendar exceeds lifetime total")
    floor = data["request_retention_floor"]
    if floor is not None and (
        type(floor) is not str
        or datetime.strptime(floor, "%Y-%m-%d").strftime("%Y-%m-%d") != floor
    ):
        raise ValueError("Invalid request retention boundary")
    if (floor is None and sum(r["requests"] for r in data["days"].values())
            != data["requests"]):
        raise ValueError("Missing request retention boundary")
    return gap(State(**data))
