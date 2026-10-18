# A04.3 prospective forecast benchmark — private rc7

Base: `01c2ca462eeff68c2f9a2036619ec616ca015147` (rc6).
No controller access was used. User-reported 1–2 hour HWAM lead time is not a
measurement in this benchmark. Previously recorded rc6 results are preserved,
but never backfilled into benchmark statistics.

## Source semantics

Installed `saynwerk-pystove==0.3.0rc3` derives `time_to_new_fire_wood` from the
raw `new_fire_wood_hours/minutes` as a timedelta. It derives
`new_fire_wood_estimate` by adding exactly that duration to the controller's
naive date/time. They are two representations of ONE forecast, not independent
signals. The existing HA timestamp entity exposes it only in **Glow**; the
duration entity does not have that phase gate. This is an existing integration
availability contract, not proof of undocumented firmware validity outside Glow.

Benchmark source `hwam-countdown-glow-observed-utc-v1` uses Glow + an actual
nonnegative timedelta. Zero is accepted. Missing, wrong-type, negative or
unrepresentable durations are unavailable; no new firmware sentinel is invented.
Prediction UTC = accepted observation UTC + duration. This deliberately avoids
controller-clock skew/DST and is recorded as the source's semantics. It need
not equal the controller-clock-based UI timestamp when that clock is wrong.
Neither native entity nor library parsing changes.

Saynwerk source is the unchanged `a04-phase-matched-r1-v1`. The frozen model
file is byte-identical to rc6 (SHA256
`ac6dd276fe9012a10bb9f707a68fc2dd40a5557c197d6e9ef11a23f89a3fc334`).
Each horizon retains its exact Saynwerk issuance identity; a model-only missing-input
boundary may produce a later, distinct issuance inside the same request segment.
The old issuance stays immutable/censored and cannot borrow the newer score.
The benchmark reads immutable issued metadata; it never changes the model or
feeds native forecasts into its formula. A pending prediction whose displayed
timestamp expired can still be evaluated under the existing model contract.
A prediction the model censored/closed without scoring the actual request is
NOT resurrected by the benchmark: its previously available horizon comparisons
become invalidated, with no accuracy error assigned.

**Phase asymmetry matters:** Saynwerk currently predicts in Burn and closes a
pending prediction on a subsequent non-request Glow observation. Native timestamp
availability requires Glow. Matched forecasts at the same horizon may therefore
be absent structurally. Separate coverage/lead/accuracy remain useful, but their
conditional errors must not be presented as a matched head-to-head victory.

## Causal policy `a043-observed-horizons-v1`

Only accepted normal coordinator observations are consumed, after the unchanged
model step. Command readbacks, duplicate callbacks and pre-boundary reads do not
enter via a second path. No new status read, polling loop, command, notification
or entity. The 49 existing identities and the library's 25-key contract remain.

A benchmark segment begins on an observed false alarm during a heating session.
It ends at the next continuously observed false→true request, session end or
evidence boundary. Startup/recovery with true is not a request edge. True→false
allows another segment. Ignition-start or observed alarm-clear segments in an
A02 session with known start and no uncertain coverage qualify for independent
comparison counts. Other segments remain explicitly qualified as partial/gap/
left-censored and contribute descriptive records, not the evidence threshold.
This does not change the frozen predictor's own cycle/spent state machine.

At a future observed request at UTC T, query stored observations at
T−120/90/60/45/30/20/15/10/5 minutes. Select the LAST completed observation at or
before the target, never the nearest future observation. Maximum age **11 s**
(active 10 s polling + 1 s existing scheduling allowance); never carry an older
valid forecast over a newer unavailable observation. Final score likewise uses
only the last observation strictly BEFORE the actual request. Request-time
forecast data cannot score its own event. This is retrospective evaluation of
prospectively stored forecasts, not retrospective reconstruction or tuning.

The benchmark also splits on completed-observation gaps >11 s (read latency is
included), or UTC/monotonic discontinuity >1 s. This is deliberately conservative
and can censor a benchmark segment while the unchanged predictor remains usable.
There is no polling change or new grace period for the predictor. A request is
known to have appeared between the preceding false observation and T; errors
are relative to observed T, not an invented exact controller transition time.

Per source: first observed availability/lead, first/latest valid forecast,
availability periods, revisions of ≥60 s relative to the last material revision,
full observed min/max forecast movement, horizon/final errors and final
60/30/15-minute observed ranges. The 60 s revision threshold is reporting only,
not model tuning. Stability needs ≥2 available observations; its available and
total observation counts accompany it. A narrow range during sparse availability
is not evidence of continuous stability. Left-censored first availability is
counted separately; its lead is only first **observed** lead.

Error sign = (forecast UTC − observed request UTC) / 60: negative early,
positive late. Statistics retain counts, signed/absolute sums and extrema;
mean absolute error is not a median. No fake zeros for unavailable/insufficient/
invalidated cases. A missed request is never manufactured.

## Store, bounds and recovery

Outer shadow schema **3**, benchmark schema **1**, new namespace
`hwam_stove.shadow_v3.<entry_id>`; existing rc6 `shadow_v2` and rc5 `shadow` files
are read-only migration sources. Existing engine/evidence records and cumulative
progress migrate unchanged except the existing once-only restart censoring of a
pending forecast. Benchmark starts empty/prospective. Corrupt new checkpoints
fail closed; they cannot silently fall back to older data. Atomic write and
readback verification retain the rc6 ownership/cancellation behavior.

Retain at most 64 completed benchmark segments, 64 immutable Saynwerk issuance records, 64 revisions and 64 availability
periods per source per segment (first period retained), and 1,024 active forecast
observations limited to the last 120 min + 11 s. No raw status history, bodies,
network identifiers, temperatures or door history are retained. Evictions are
counted. Immutable issued Saynwerk metadata, policy and source IDs accompany the
record. Cumulative aggregates survive record eviction. Session/date witness sets
are capped at 20/10; diagnostics expose only counts, never those identifiers or
the ledger. Future source/model semantics require explicit schema migration;
unknown policy/source checkpoints fail closed rather than rewriting history.

Restart/gap preserves completed scores and the incomplete segment's previously
observed forecasts, but closes that segment as censored with no invented actual
outcome. A request could have occurred during the gap, so those old forecasts
cannot safely be attached to an arbitrary later request. Fresh continuous
post-gap observations can yield valid later horizons in a new segment; a horizon
inside/before the gap remains invalidated. A02 partial-session semantics remain
unchanged. Benchmark errors never reclassify historical Saynwerk evaluation.

## Diagnostics and evidence threshold

New cached-only `forecast_benchmark` section: schema/policy, observed cycles and
requests, eligible cycles, bounded independent-session/date counts, per-source
forecast/left-censored/gap counts, first observed lead, revisions, movement,
horizon and final validity/coverage, gated signed/absolute errors, and stability.
No ledger in diagnostics or entity attributes; no added entity or timestamp state.

Forecast coverage is the fraction of completed request segments with a fresh
available forecast at that horizon. Scoring coverage separately counts forecasts
that can validly be scored; a later-censored Saynwerk forecast still existed but
is not an accuracy sample. Eligible-session forecast coverage is also reported
separately, with all/eligible unavailable, insufficient and invalidated counts. Gap-censored segments without a known request outcome are separately counted
and cannot enter a made-up request denominator.

Conservative evidence gate: **30 eligible request cycles, ≥20 independently
observed heating sessions, ≥10 UTC session-start dates** before the global
`preliminary` flag clears for coverage review. Each source/horizon accuracy
summary separately needs 30 valid eligible scores from ≥20 sessions/10 dates.
Paired accuracy uses only the same valid eligible events for both sources and
requires its own 30/20/10 gate; no mixing of unmatched errors into paired means.
Many requests from one heating session cannot meet these gates. Thresholds are a
minimum diversity check, not statistical significance or proof of superiority.
Descriptive counts/lead/ranges may be inspected earlier, clearly preliminary.
`conclusion` always remains `no_source_ranking`; no automatic winner is declared.

## A04.4 decision criteria

First review independent coverage, source-specific errors, censoring/availability
and phase-dependent selection bias. If there is no matched support, do not claim
one model is more accurate on the same cases. A hybrid may be proposed only after
sufficient prospective source/horizon evidence and useful complementary coverage
exist, with a NEW frozen policy and a held-out future evaluation plan. A04.3 does
not build, tune or route a hybrid, and never selects combustion-control actions.

## Private installation / rollback

1. Back up the current integration directory and HA configuration, including all
   A02 and shadow Store files. Keep a VM/HA backup for exact rollback.
2. Replace only `/config/custom_components/hwam_stove/` with the ZIP's directory;
   retain the existing ConfigEntry and all registries. Keep HACS from replacing
   this private test. Library remains `saynwerk-pystove==0.3.0rc3`.
3. Restart HA manually; verify rc7, 49 entities with unchanged identities, normal
   sensors and cached diagnostics showing shadow schema3 / benchmark schema1,
   healthy verified persistence. Counters begin prospectively; no old benchmark
   forecasts are synthesized. No command or special stove use is required.
4. For code rollback, stop HA and restore the previous rc6 integration directory;
   retain both v2/v3 Store files. rc6 reads untouched v2 evidence from migration
   time; rc7-only progress is retained in v3 but not visible in rc6. Restart HA.
   Never edit/restore `.storage` while HA runs. Do not delete v3 to force a
   re-migration: divergent v2/v3 progress requires explicit reconciliation before
   returning to rc7. A complete HA/VM backup restore is the exact full-state
   rollback (it also reverts later production state).
