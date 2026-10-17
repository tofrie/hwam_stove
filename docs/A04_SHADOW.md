# A04 shadow mode — private 1.0.0rc4

Base a9cca8440d9aa5a1b0003d6e63a9af84e99d78a6 (A05); dependency stays
saynwerk-pystove==0.3.0rc3. H01B remains OPEN. No notification, control action,
additional request, polling change, library change or factual analytics migration.
Push Brennholz v2 and Alexa are not accessed or changed.

## Frozen model, not a validated recommendation

Model `a04-phase-matched-r1-v1` uses the **phase_matched** candidate from
`a04-offline-v1`. Calibration is frozen to capture-04's FIRST request only:
2026-10-11T09:25:31.563Z, sequence 489, Burn, 229 degrees in existing pystove units.
At least ten minutes of observations supply the same actual-UTC-time OLS cooling
slope. Predict crossing of 229 only in Burn while cooling; clamp negative ETA to
zero; abstain outside the original 0–30-minute range. No new fit, grid search,
online calibration or use of the second event. Before the calibration cutoff the
model abstains, including in historical replay.

Frozen replay source SHA256:
`b9d11dfc006cec89f359fc1b202d74c4940b2020a15fdb822a1eceba35733d98`.
Evidence archive SHA256:
`5b708ebc769ae8165ff180955072965f53c0e00be05d7a63b0df2cfbedd9fa44`.
An offline comparison against the original engine checked all 367 post-calibration
ready outputs across 1,309 preserved rows: exact formula parity, no evidence edits.
Neither that session nor its two events are independent validation.

Selection is a conservative scope decision (abstain outside calibrated phase),
NOT a winner selected for accuracy. Original phase-matched replay produced zero
numerical forecasts. Useful performance is unestablished; this model may abstain
frequently or prove useless. The phase gate is not a firmware law. Confidence is
always `experimental_uncalibrated`, never a probability or an assurance to add wood.

## Runtime observation and display policy (also versioned)

Only accepted regular coordinator reads enter the model. No entity callback,
M02 command readback, duplicate token or stale pre-command response contributes.
A02's qualified session identity/coverage is reused; its Store/counters are never
written by shadow mode. Session IDs in the private shadow Store are A02 observation
start timestamps, not device/network/config-entry identifiers.

Temperature, oxygen and all three valves must be finite; processed phase, strict
physical-door state and request state must be known. Oxygen/valves and session/
previous-request ages are not given invented predictive coefficients: the frozen
selected formula uses temperature/slope/phase only. The recent maximum is implicit
in the retained temperature window, not a second model. Raw phase is unavailable
in the protected mapping and is not requested. Door-open abstains and invalidates
that cycle's prediction; it never implies wood addition. A new request cycle begins
only after observed alarm clearing (or a new qualified session).

These adapter policies are deliberately stricter than raw offline replay:
- No forecasts for partial/coverage-uncertain A02 sessions, missing door/status,
  unavailable reads, command boundaries, unsupported phase or insufficient history.
- After phase changes, require a fresh ten-minute Burn history. No interpolation.
- Gaps use the existing A02 scheduled-read cadence (10s active / 60s Standby) plus
  HA's one-second scheduler rounding allowance, measured from previous completion
  to next start. The prototype's fixed 11s capture marker is NOT a new policy.
- UTC/monotonic discontinuities invalidate continuity; DST is not a discontinuity.
- Every successful poll is consumed once, including equal 25-key responses;
  shadow has its own state listener, no second coordinator/transport/poller.

One entity: **Saynwerk Nachlegeprognose**, timestamp (DE/EN/NL translations), same
stove device, key `shadow_refill_request`. Exactly one new identity: 48 -> 49.
A02/A02.1, physical door and door-too-long PROBLEM alarm identities remain unchanged.

The FIRST available prediction of each cycle is frozen, rounded to the nearest
UTC minute, and checkpointed before it is exposed. Subsequent regular observations
update whole minutes remaining; they do not replace that timestamp. States are
`insufficient_data`, `unknown`, `prediction_available`; only the last has an
available HA timestamp. At a passed predicted deadline the display becomes unknown
on the next regular observation; evaluation may still await the future request.
Continuous observation for more than 30 minutes without a request closes the
prediction as `no_request_within_30m`. No further forecast in that cycle. This is
an intentionally fixed forecast experiment, not a continuously retuned forecast.
Like the existing sensors it is polled, not continuous sensing; short transitions
can be missed. There is no assertion about the exact physical request onset.

## Separate bounded evaluation Store

`hwam_stove.shadow.<entry_id>` uses HA Store envelope 1, payload schema 1. It is
separate from the unchanged A02.1 schema-2 Store. Retain at most 200 closed cycle
rows, one pending prediction, and 200 distinct session observation IDs. Eviction
is counted; diagnostics report RETAINED evidence, not lifetime validation totals.
No per-poll samples, learned parameters, restored display or feature history are
persisted. No predictions or factual request counters are backfilled from Recorder.

Each row contains session ID, immutable prediction cutoff and predicted UTC time,
model/confidence, eventual first-observed request timestamp and last-false bracket,
signed error in minutes (prediction minus first observed request), invalidation
reason and coverage-gap flag. Observed requests without a forecast have an explicit
`no_prediction` row with no invented prediction. An initial already-true alarm is
not an edge. Only a strictly FUTURE false->true observation can score a prediction.
An invalidated cycle is never reassociated across a gap. Raw error uses the rounded
issued timestamp, never a later revised estimate. Predicted/request times remain
private household activity data in this local Store; no ledger is exposed in HA
attributes or diagnostics.

An optional same-cutoff native `time_to_new_fire_wood` value is retained as minutes
under `hwam_minutes_at_prediction`, preserving original duration conversion. This
supports a LATER paired comparison without another request. It is not a claim that
HWAM's conditional forecast has identical semantics or that Saynwerk is better.

Restore never resumes a pending forecast: close it as invalid `restart`, clear
edge/history anchors, and require new qualified observations. Corrupt/unknown schema
or model versions fail closed without resetting or overwriting evidence. Changing
the model later needs an explicit Store migration or new namespace, not reinterpretation
of old rows. Writes are atomic and verified; repeated cancellation settles owned
storage work. Write failure disables shadow only; no command retry or replacement
of normal measurements. Unload flushes and removes listeners. Abrupt power loss
cannot turn a persisted pending row into a valid resumed score.

Compact cached diagnostics: model/schema, retained session/cycle counts, scored,
invalidated and no-prediction counts, separate fully observed negative-window
counts, eviction count, health and gated error summary.
No IDs, timestamps, raw history or network identifiers are included. The UI gets
only model/state/reason, whole minutes remaining, uncalibrated confidence, scored
cycle count and (only at the threshold below) median absolute error.

## Prospective decision protocol — freeze before new sessions

No usefulness claim now. First review after **at least 20 separate qualified
observed heating sessions on at least 10 UTC dates**, with **at least 30 future
observed request cycles with continuous preceding 30-minute coverage**. Include
normal Burn and Glow requests and non-request/end-of-session cooling; no induced
stove events. Retain at least 30 minutes of gap-free follow-up for negative windows.
Exclude capture-04, partial sessions and censored windows from accuracy claims.
The local bounded ledger plus factual A02 evidence must support the denominators;
if records were evicted or coverage cannot be established, gather more evidence.

Predeclared private utility targets (engineering acceptance criteria, not measured
performance): forecast coverage >=50% of eligible request cycles; median absolute
error <=5 minutes; 90th-percentile absolute error <=10 minutes; <=20% of eligible
issued forecasts with no request in their full 30-minute follow-up. Report abstention,
phase-specific coverage, censored cycles and early/late errors separately; never
count correlated polls as trials. A model that emits nothing fails coverage; it
must not wait forever for enough successes to be declared good. Use session-level
summaries rather than pretending every request is independent. Failing a target
means NOT demonstrated useful; any changed model needs a new version and new
untouched future sessions.

HA displays median absolute error only with >=30 scored predictions spanning >=20
session IDs and >=10 UTC dates within retention. This reporting threshold alone is
NOT the usefulness verdict and does not certify full-session coverage. Do not enable
notifications/control based merely on crossing it. No kilograms/wood consumption.

## Private installation / rollback

ZIP contains only custom_components/hwam_stove, version 1.0.0rc4, published rc3
library requirement unchanged. Back up the current integration and HA data first;
prevent HACS overwrite. Replace integration files with HA stopped/restart once,
keep the same ConfigEntry. Expect 49 registry entities, same two devices and all
48 existing IDs. The forecast may remain unavailable: inspect its reason, never
force a stove action to obtain a prediction. Normal values/A02.1 counters must
continue unaffected. No deployment is performed during development.

Rollback to the saved rc3 integration and restart; retain the separate shadow
Store as evidence, never reset the factual analytics Store. Never edit/restore
.storage while HA is running. H01B remains OPEN; stop on unload failure.

## Offline verification — 2026-10-17

Python 3.14.6 / HA 2026.9.4 compatibility suite: 1,731 passed, 0 xfailed/XPASS.
Includes 47 focused shadow tests, 147 real-library-boundary, 35 B01 and 17 A05
checks, plus the existing A02/A02.1 and lifecycle regressions. Final scoped recheck
of shadow, cached diagnostics and runtime integrity: 104 passed after separating
negative windows from censored predictions in diagnostics. Ruff passes. Socket,
DNS and HTTP isolation remain enforced. No multi-version/release matrix run.
The exact frozen formula matched 367 original replay outputs; source evidence
archive unchanged. Existing 48 identities are retained and one new entity is
registered, including across reload. No new controller request path exists.
