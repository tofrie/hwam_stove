# A04.2 — restart-safe shadow evidence (private rc6)

Base: `bd2a9368218efad7946b425dcc129d3e7d2dc881` (rc5).
Model: `a04-phase-matched-r1-v1`, byte-identical `_shadow_model.py`.
Integration: `1.0.0rc6`; library stays `saynwerk-pystove==0.3.0rc3`.
49 entity identities, commands, notifications and coordinator cadence unchanged.

## Audit and authorized correction

[A042_RC5_AUDIT.json](A042_RC5_AUDIT.json) records the pre-edit 66-requirement
SATISFIED / PARTIALLY SATISFIED / NOT SATISFIED audit. Offline rc5 proof: retained
scored rows survived restore, pending was censored once, all four alarm combinations
added zero edges and downtime added zero elapsed time. However, 200 later ledger
entries could erase the reported scored count, reason counters reset, and rc5
stored no utility eligibility certificates. A known-start A02 session stayed
coverage-uncertain after restart, blocking A04 for its entire remainder.

The user explicitly approved separate post-gap A04 window eligibility. Only the
adapter's permanent rejection of `current.coverage_uncertain` was removed. A02's
flag remains untouched; unknown ignition/start still blocks prediction. A04 needs
new continuous history and every existing frozen model guard. Its temperature,
OLS, phase, range, calibration, rounding and cycle logic are unchanged. No tuning.

## Schema and migration

HA public `Store`, envelope 1, atomic writes, payload schema 2:

- `engine`: exact schema-1 model payload, unchanged immutable original rows;
- `evidence`: policy version, per-model cumulative outcomes and error histograms,
  active observed-session identity, session cohort buffer, last observed UTC/alarm,
  bounded gap brackets, eligibility annotations and persistent reason counters.

New key: `hwam_stove.shadow_v2.<entry_id>`.
Legacy key: `hwam_stove.shadow.<entry_id>` is read only when no new Store exists.
Migration validates the complete old payload, imports retained records, folds
known totals, censors pending exactly once, and writes/reads back the new Store.
The legacy file is left **byte-identical**. A corrupt/unknown new Store never falls
back to old data or writes an empty replacement. Migration can retry on a subsequent
load only if there is no new checkpoint; it never automatically retries writes.
The A02/A02.1 Store is not modified by this work.

Legacy limits are explicit: evicted rc5 outcomes and volatile reason counts cannot
be recovered. Retained legacy error values and model attribution are preserved,
but absent 30-minute/full-session qualification is `legacy_unknown`, never invented.
Known legacy totals are lower bounds if rc5 already evicted records. Existing A02
completed totals are not retroactively certified as A04-qualified evidence.

Bounded inventory: 200 exact closed rows plus pending, 200 annotations, 32 gap
brackets, one active session buffer, at most 16 attributed model summaries, ten
UTC date witnesses per model and fixed-size error histograms. Cumulative counters
are nonnegative integers; reason counters expose possible process-local saturation.
Model buckets never mix. Unknown engine/model schemas fail closed: a later model
requires an explicit migration, preserving archived rows and attribution.

## Restart and evaluation semantics

The identity is the existing A02 `first_observed`, not a new restart identifier.
Its persisted high-water mark prevents duplicate session counting. Resume checks
against A02's current session; historical evidence remains independent of whether
that session is still active. Downtime is a bracket from the last checkpointed
observation to the first subsequent observation, not a claimed exact outage time.

No temperature history, monotonic clock, edge anchor or pending eligibility is
restored. The first post-gap alarm anchors the new window. false/false, true/true,
false/true and true/false across downtime all add **zero** observed request edges.
Pending original time, predicted time, model and confidence are kept. The sidecar
marks `invalidated_due_to_gap`; the engine retains its exact restart/shutdown/etc.
reason and a null error. Repeated reloads cannot invalidate that row twice.
After sufficient new history (normally the existing 600-second Burn window), A04
may predict again even though the A02 session remains partial. No intervention,
new read, extrapolation or clearing of A02 uncertainty is involved.

All closed outcomes, issued/scored/invalidated/no-prediction/negative counts and
reason transitions accumulate beyond ledger eviction. Current-session evidence
is buffered separately for utility eligibility. Only a prospectively observed,
completed, uncensored A02-qualified session commits this buffer to the utility
cohort. A gap excludes that session from utility claims, without deleting its valid
pre-gap rows, scores or outcome totals. Completed-session progress never resets on
restart. An unfinished session is not prematurely called a complete qualified trial.

The unchanged decision protocol remains >=20 separate qualified sessions, >=10 UTC
dates, >=30 future request edges preceded by continuous 30-minute observations.
Every eligible request enters the denominator, including model abstentions. A
negative forecast requires its full gap-free 30-minute follow-up. No startup edge,
partial/calibration session or censored window certifies usefulness. Targets remain
coverage >=50%, median absolute error <=5 min, p90 <=10 min and negative forecasts
<=20%. Meeting sample counts alone does not demonstrate usefulness.

Exact per-row errors remain in the 200-row ledger. All-time error distributions
use fixed 0.1-minute absolute-error bins, up to 60 minutes plus overflow. Diagnostics
report quantile **bounds**, with a null upper bound for overflow; they never claim
an exact all-time median from a rolling sample. Median uses the middle rank(s),
p90 nearest rank. A target straddled by the bounds is undetermined. This is bounded
aggregate retention, not retuning the model or its acceptance thresholds. The
optional old rolling-median entity attribute is omitted rather than misrepresented;
`validated_cycles` now uses cumulative scored outcomes. Predictions themselves,
entity identities and timestamp values are unchanged.

## Failure and privacy

Each accepted regular observation checkpoints the bounded evidence, including the
last observation boundary; disk I/O increases, controller I/O does not. Owned
writes settle repeated cancellation. During HA shutdown, the public Store final-
write event flushes deferred data. Ordinary restarts/reloads/Core replacements
restore the last successful checkpoint and preserve cumulative validation progress.

On load/write/readback failure, prediction becomes unavailable; cached diagnostics
report `persistence_healthy=false` and `checkpoint_verified=false`. A02, normal
sensors and commands remain operational. Reload after repairing the storage issue
recovers the last real checkpoint. A write that did not succeed cannot be claimed
durable: sudden power/storage loss between observation and checkpoint may lose
that uncommitted observation, but cannot fabricate a score across the resulting gap.
No software guarantee can preserve data never successfully stored.

Diagnostics expose only bounded summary values, reasons and model versions, not
session IDs, timestamps, Store keys, gap endpoints, histories or ledgers. Local
Store data includes necessary timestamps/observed-session IDs; protect it as HA data.

## Private upgrade and rollback

1. Back up the installed integration and HA data, including both shadow Store keys
   and the A02 Store; prevent HACS overwrite. Do not remove/recreate the ConfigEntry.
2. Stop HA, replace only `custom_components/hwam_stove/` with the rc6 ZIP contents,
   then start HA. This development task does not perform those actions.
3. Expect the same 49 entities/devices, rc3 library, normal values and healthy
   analytics. Check cached diagnostics for schema 2, healthy verified persistence,
   cumulative counts and legacy qualification flags. Prediction may initially be
   unavailable. Do not induce stove events for validation.
4. A subsequent ordinary reload/restart should retain counts; pending may become
   invalidated once. A02 remains partial after a gap. H01B remains OPEN: stop on
   unload failure instead of attempting destructive recovery.
5. To roll back, stop HA and restore the backed-up rc5 integration files. Keep the
   rc6 `shadow_v2` Store and legacy Store as evidence; never delete/reset them.
   rc5 reads its untouched legacy snapshot and cannot display evidence accumulated
   by rc6. Do not merge/edit Stores or restore `.storage` while HA runs. On a later
   rc6 return, a valid v2 Store remains authoritative; data collected only during a
   rollback is not automatically merged or retroactively qualified.

## Verification

Focused HA 2026.10.1 / Python 3.14.6 only; deterministic clocks, no sleeps in the
new tests, IP sockets/DNS/HTTP blocked. Includes real atomic Store migration,
write-failure/readback detection, final-write lifecycle, corrupted/future schemas,
all alarm combinations, repeated restart stages, causal replay, post-gap resumption,
ledger eviction, prospective utility accumulation and unchanged frozen-engine proof.
See the accompanying A042 verification evidence for exact test totals and artifacts.
No production, hardware, notification, publishing or deployment access.
