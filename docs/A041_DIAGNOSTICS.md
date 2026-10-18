# A04.1 — why the frozen shadow model abstains

Integration 1.0.0rc5, based on rc4 commit
`9f2e4c9df7158287d27e25b480e0677b777e694c`.
Dependency remains `saynwerk-pystove==0.3.0rc3`.
Model remains **a04-phase-matched-r1-v1**, including all its thresholds,
qualification guards, first-forecast freezing and scoring rules.
No requests, entities, commands, notifications or polling changes.
All 49 entity identities remain unchanged. H01B remains OPEN.

## Cached diagnostic schema

The integration-generated diagnostics `data.shadow` now includes:

- `abstention.state`, `reason`, `reason_category` — the currently selected model
  state and reason. This is the first applicable guard in the existing decision
  order, not an assertion that every later guard would pass.
- `abstention.reason_transition_counts` — fixed 21-key map, saturating at 65,535.
  Counts entries into reasons, **not polls, elapsed durations or request events**.
  Consecutive identical reasons do not increment. A transient gap followed by
  another reason in the same observation is counted. Available forecasts end an
  abstention run. Duplicate model callbacks do not increment.
- `counter_scope=current_runtime`, `counter_limit=65535` — these counters start
  with each Shadow instance/load and reset on restart/reload. They cannot explain
  pre-upgrade history. They are intentionally not persisted; diagnostic collection
  causes no disk writes or changes to existing evaluation records.
- `last_coverage_boundary` — last explicit invalidation/coverage guard, retained
  even if a later reason becomes current. A historic boundary does not by itself
  mean the current read failed; transition counts retain earlier boundary types.
- `last_unpredicted_request_prior_reason` — reason immediately before the most
  recent newly observed request recorded as `no_prediction` in this runtime.
  This is contemporaneous context, not a retrospective claim about the entire
  cycle. It starts null and does not backfill the old ledger.
- `history_samples`, `history_span_seconds`, `required_history_seconds=600` —
  bounded history coverage only, not temperatures or raw samples.
- `missing_inputs` — fixed field names only when required inputs are missing or
  invalid: phase, refill_alarm, door_open, stove_temperature, oxygen_level,
  valve_positions. No offending values are exposed.
- `coordinator_input_state` — runtime_missing / cache_missing / update_failed /
  last_update_successful / unknown. This reports cached HA state, not a new
  freshness test. Model reason `failed_read` records the read invalidation
  independently of the cached HA flag. There is no new
  wall-clock stale timeout or polling path.
- `observation_disposition` — last adapter decision: not_started, accepted regular
  observation, closed, not_ready, unhealthy, duplicate_or_older_observation or
  pre_boundary_read. Rejected callbacks do not silently become model observations.
- `last_observed_session_qualification` — not_observed, qualified,
  analytics_unhealthy, analytics_offline, no_current_session,
  session_start_unobserved or session_coverage_uncertain. This explains why a
  healthy analytics subsystem can still have an unsuitable current session.

No identifiers, dates, raw history, ledger, response bodies or exception text
are added. The existing HA-owned download wrapper is unchanged.

## Complete abstention-path audit

| Code | Exact existing condition / interpretation |
|---|---|
| startup | New shadow instance; no usable observation yet. |
| restart | Existing Store loaded; prior predictions cannot span the restart. |
| shutdown | Existing shutdown boundary invalidates the current forecast. |
| failed_read | Existing failure/cancellation boundary invalidates coverage. |
| command_boundary | Existing command boundary; command readbacks are not prediction evidence. |
| sampling_gap | Next read starts beyond previous completion + existing 10s active / 60s Standby interval + 1s allowance. |
| clock_discontinuity | Naive UTC input, nonpositive elapsed time, or UTC/monotonic elapsed discrepancy greater than the existing 1s limit. |
| session_change | Different observed session; transient boundary before evaluating the new sample. |
| missing_observations | A required input fails the existing `Sample.usable()` contract. Unknown phase text is included here. |
| partial_session | Session absent/unqualified outside Standby, including uncertain coverage or unseen session start. |
| session_end | Unqualified/absent session with Standby. |
| door_open | Observed open door; existing cycle suppression applies, without assuming wood addition. |
| unseen_phase | Usable phase is not Burn (e.g. Ignition or Glow); model has no calibrated phase match. |
| insufficient_history | Fewer than 600s of qualifying retained history, or zero OLS time variance. |
| before_calibration | Historical replay timestamp precedes the fixed calibration availability date; irrelevant to correctly dated current production. |
| not_cooling | Frozen trailing-window OLS temperature slope is zero or positive. |
| beyond_range | Frozen temperature-crossing estimate is more than 30 minutes away. |
| request_active | Refill alarm already active; no forecast of an already observed request. |
| prediction_expired | Immutable forecast time passed, but its evaluation may still await a future request. |
| cycle_spent | This cycle cannot issue another forecast, including after the door closes or a completed negative prediction window. Wait for the existing new-cycle rules. |
| storage_failure | Optional shadow storage/runtime processing degraded; no prediction is served. Existing error handling is unchanged. |

`available` is success, not an abstention counter. `request_observed`,
`no_prediction`, `no_request_within_30m` and `pending` are evaluation outcomes,
not additional model-abstention reasons. Session changes, clock corrections and
sampling gaps can be transient and otherwise disappear behind the next guard.

## What the supplied production diagnostics establish

**User-reported facts:** healthy shadow/analytics, one retained observed heating
session, two requests without a prediction, no valid scored or invalidated
predictions, and a successful last refresh.

**Confirmed from those facts and rc4 code:** two qualified observed false-to-true
request edges were recorded without a pending forecast for their cycles. They
are not two measured forecast errors. Last-update success does not prove that
the model had sufficient prior continuous coverage.

**Possible, not confirmed:** insufficient history, unqualified session coverage,
missing door/status input, non-Burn phase, non-cooling slope, crossing beyond
30 minutes, a door-induced spent cycle, or prior gaps/command boundaries. A gap
with no pending forecast does not increment `invalidated_predictions`. Earlier
partial coverage may recover into later eligible request observations; the two
counted edges themselves passed the existing qualification guard. Active alarms
also explain abstention *after* the requests. The old totals cannot identify the
specific cause of either missed forecast, or prove that no prediction ever existed.
No production observations were inspected during this implementation.

Current healthy state rules out an active storage-degraded state at that snapshot;
current successful refresh rules out a currently reported failed HA update, but
neither rules out earlier failures. Incorrect historical dates are not assumed.
Capture rc5 diagnostics during normal use to obtain actual reason evidence.

## Persistence and behavior preservation

Evaluation Store schema 1 and all payload fields are unchanged. No migration,
counter backfill, deletion or ledger reset is performed. Existing closed records
remain exact; a pending record on restart is invalidated exactly as rc4 already
did. A02/A02.1 stores and reducer, coordinator, sensor identities, library, model
math and thresholds are unchanged. New diagnostic state never enters model
decisions or `dump()`; no additional per-poll Store writes are introduced.

Tests compare all existing model statements against rc4 after removing only
diagnostic assignments/calls, and compare forecasts, state, history, complete
payloads and scores at every step of deterministic causal replay. Focused HA
2026.10.1 checks include all A04 regressions, persisted rc4 records, reload/49
identities, missing inputs, counter bounds, request suppression and zero I/O in
the diagnostics download. No multi-version/release matrix is required here.

Verified 2026-10-18: Python 3.14.6 / HA 2026.10.1 / test framework 0.13.372,
**633 passed, zero xfails/XPASS**. Included A04.1 54, A04 47, O01 56,
A02/A02.1 161, A05 17, real-library boundary 147, B01 35, plus entity/registry,
lifecycle/H01B safety, isolation and integrity checks. Ruff and `pip check`
passed. Published rc3 is the sole installed `pystove` import owner and matches
the existing approved source-hash contract. Tests block HTTP/DNS/IP sockets.
No production or controller access occurred.

## Private installation and rollback

1. Make a current HA backup and retain the installed rc4 integration directory.
   Keep HACS from overwriting this private fork.
2. Verify the supplied ZIP SHA256. Extract it outside `/config`, then replace
   only `/config/custom_components/hwam_stove/` with its matching ZIP directory.
   Keep the existing config entry and all `.storage` files untouched.
3. Restart HA once manually. Confirm version 1.0.0rc5, unchanged dependency rc3,
   the same 49 entities/device identities and normal values.
4. Download HWAM diagnostics during ordinary use and inspect `data.shadow`.
   Reasons/counters begin prospectively; an unknown forecast after restart is
   expected and does not reset factual statistics or evaluation history.
5. If necessary, restore the saved rc4 integration directory and restart HA.
   Store schema remains compatible. Do not delete the config entry or restore
   `.storage` while HA is running. No stove action is needed for validation.
