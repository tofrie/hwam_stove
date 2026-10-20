# A06 — private rc8 resilience and observation-only interventions

Base: private rc7 `b8b5c2490756fb55553e2185841167b4e37e65ab`.
Dependency remains `saynwerk-pystove==0.3.0rc3`. There are still 49 entities.
H01B remains OPEN. No controller or production access was used for development.

## Established causes and limits

The pre-change audit is in A06_AUDIT.md. Failed HA reads previously made all
coordinator entities unavailable although their last data remained cached. An
A04.3 gap count is not a network-error counter: response latency, missing inputs,
shutdown and scheduling gaps can also censor evidence. rc3 can collapse both
transport failure and invalid JSON into an empty status result. Diagnostics
therefore report `empty_or_invalid_status`, not an invented Wi-Fi cause.
No evidence establishes a connection between 5 GHz DFS and the stove's 2.4 GHz
connection, or establishes a firmware defect behind a missing refill request.

## Display freshness

Only the six existing measurement sensors retain values: room/stove temperature,
oxygen, and the three valve positions. Values and native types are unchanged.

- Fresh: a successful response, within the normal 10-second active / 60-second
  Standby interval plus the existing 1-second scheduling allowance.
- Stale: immediately after any read failure/cancellation, or when that successful
  observation becomes overdue. Keep its original value until age reaches 120 s.
- Unavailable: no accepted observation, stopped runtime, or age >= 120 s.

The 120-second cap allows two normal Standby intervals; it is a display policy,
not a controller-latency or combustion assumption. Door, safety/refill alarms,
commands and other live coordinator entities use strict freshness without the
measurement grace period. The shadow timestamp is also unavailable while stale.
Historical analytics entities retain their existing factual availability policy.

Local HA timers only notify availability; they never read the controller. Equal
successful responses advance the genuine observation timestamp without inventing
measurement changes. Staleness/availability attribute transitions are real UI
changes; repeated failures with identical retained state emit no state change.
Retained values do not create new temperature observations or event edges in A02
or A04. HA Recorder may naturally hold the last displayed measurement during this
bounded grace period; rc8 does not alter Recorder or claim these are fresh samples.

Connection diagnostics expose observation UTC, age, freshness, last-read health,
expected interval and restricted error categories. Failure counts are for this
runtime only. No exception text, response bodies or network identifiers are added.
Display caches and their age anchors are never restored after restart.

## Recovery and causal observations

The next successful ordinary read replaces display values. Failed reads clear
causal anchors, not session totals, historical peaks, ledgers or request counts.
No lost phase, alarm or door transition is replayed/interpolated. Coverage-uncertain
A02 sessions remain qualified; A04 can regain its separate fresh window according
to A04.2. No gap threshold was relaxed to improve benchmark yield.

Adapters additionally test completed-observation age: an on-time request whose
response is delayed cannot count the unobserved waiting period as combustion time.
The frozen `_shadow_model.py` formula/thresholds and `_analytics_model.py` reducer
remain byte-identical to rc7.

## Intervention evidence, not physical refill accounting

Policy `a06-intervention-observations-v1` retains at most 64 event records plus one
pending candidate and fixed cumulative counters. Event context is allowlisted:
phase, door/alarm booleans, temperature, oxygen, valves and forecast availability;
records include observed interval endpoints, local session identifier and coverage.
The 600-second candidate follow-up limit bounds metadata only. It is not a rule
about how quickly wood must affect combustion. Completed records are immutable.

- Observed false→true alarm: an observed refill REQUEST, with wood action unknown.
- Sampled door sequence, including Glow→open→closed→Burn: POSSIBLE intervention.
- Glow→Burn without sampled door evidence: POSSIBLE unsampled intervention.
- Temperature recovery alone: no refill event.
- Missing observations, unknown door, restart or outage: incomplete/unknown;
  pending sequences cannot bridge the gap.

`probable_physical_additions` remains null and `physical_classifier_validated`
false. No probable/confirmed physical-wood classifier is enabled. The existing
request counters never become physical-refill counters. No mass is estimated.
The user-reported early additions justify conservative censoring but are not
new measured events. Door-only negative examples remain essential.

## Forecast comparison and historical separation

Current benchmark policy: `a06-intervention-censoring-v2`. A possible door or
unsampled Glow→Burn intervention ends the current comparison without changing
its issued forecasts or fabricating a request timestamp. No new comparison starts
while the door is observed open. An alarm edge and door transition sampled together
do not prove which occurred first: preserve the observed request, do not invent an
earlier intervention. Following boundaries, existing left-censoring rules apply.

Censor counters separate possible intervention, communication/missing observation,
and incomplete window. Ledger reasons distinguish sampled door from unsampled
transition. Observed request records, native forecast disappearance and fully
observed model expiry are recorded separately. Censored rows have no success/error
scores. A no-request expiry is an observed outcome, not a measured physical refill
or proof of a controller fault. Short unsampled door/alarm transitions remain unknown.
Existing A04 model-specific evidence retains its original evaluation contract;
the new comparative policy does not relabel historical scores.

Diagnostics provide bounded summaries only, never the event/forecast ledger.
Missing-alarm cause remains `not_established`.

## Atomic persistence and rc7 migration

New outer schema 4 uses `hwam_stove.shadow_v4.<entry_id>`; intervention schema 1,
current benchmark schema 2. Read rc7 `shadow_v3` only if v4 is absent. Preserve the
A04 engine/evaluation history, safely invalidate pending restart comparisons, and
archive the rc7 benchmark under `a043-observed-horizons-v1`. Its old results and
metrics remain separately visible in `forecast_benchmark.rc7_archive`; new policy
metrics start prospectively in `forecast_benchmark.current`. This is not a reset
or pooling of incompatible evaluation histories. The original v3 file is untouched.
Older supported checkpoints keep their existing migration path. Corrupt storage
fails closed; it is not silently replaced with an empty history.

Atomic saves use the existing cancellation-safe ownership mechanism. Candidate
records/counters survive reload and restart; pending interval anchors do not.
Storage failure affects analytics health, not controller commands. A02/A02.1 Store
format, session/counter history and all registry identities remain unchanged.

## Private installation and rollback

1. Save the current integration directory, a full HA backup and existing analytics/
   shadow storage. Keep the earlier private rc7 ZIP. Disable HACS overwrite/update
   for this fork during validation. Do not delete/recreate the ConfigEntry.
2. Verify the delivered rc8 ZIP SHA256. Replace the **whole** existing
   `/config/custom_components/hwam_stove` directory with the ZIP's directory;
   restart HA manually. No dependency change or extra package install is needed.
3. Confirm rc8/library rc3, the same 49 entity identities, normal status recovery,
   analytics/shadow health, schema 4 and the archived/current benchmark summaries.
   Short failures should retain only six measurements with stale attributes;
   no stove action is required to validate the UI.
4. On unexpected setup/storage/identity errors, stop testing. Restore the whole
   rc7 directory and restart HA. rc7 reads its untouched v3 checkpoint; it cannot
   consume later v4 progress. Preserve v4 for investigation and eventual return.
   Never downgrade schema contents by hand or restore `.storage` while HA runs.
   For a full data rollback, stop Core and restore the matching pre-upgrade backup;
   this necessarily loses later observations. Do not delete evidence merely to
   clear an error.

## Remaining evidence needed

No autonomous classification stronger than POSSIBLE is justified yet. Obtain
multiple independent normal-use early additions and door-only non-additions,
with coverage-qualified existing observations, plus alarm/no-alarm sessions.
Do not induce events or add polling for this purpose. Network diagnosis needs
separate correlated evidence before assigning a Wi-Fi/firmware cause. No claim
of improved forecast accuracy or solved H01B is made.
