# A02: observed heating sessions and refill requests

Base: `1a82c07c32b8157f62c25b1e5a01f050b178b40e`.
Dependency remains `saynwerk-pystove==0.3.0rc2`; integration version remains
`1.0.0rc1`. H01B remains OPEN. This work has only offline/simulated validation;
it has not been deployed or tested on a controller.

## Meaning and reducer

These are **observations of controller phase**, not measured combustion, fuel
loads or manufacturer-defined load cycles. They cannot prove continuity between
polls. No temperature threshold, firmware delay, door interpretation or fuel
mass is introduced.

`Ignition -> Burn` establishes a session whose start is the first observed
Ignition. Repeated Ignition retains that boundary. Burn/Glow transitions retain
the same session. Standby ends it once. Ignition returning to Standby without
Burn/Glow is an unconfirmed attempt and adds no completed record/count.
Ignition reaching Glow without an observed Burn is qualified as incomplete.
Startup in Burn/Glow has an unknown start. It can finish prospectively, but
never becomes a *fully observed* session retroactively. Ignition after Burn/Glow
qualifies the existing observation instead of inventing a new fire.

Only sessions with an observed ignition start, subsequent Burn, observed end,
and no detected coverage gap contribute to the complete-session total and last
complete duration. Ended partial sessions remain in the bounded ledger and a
separate `partial_sessions_excluded` summary. They do not replace the last
complete duration. The peak is the largest finite **observed** stove-temperature
value during non-Standby session observations; the final Standby sample is not
part of that maximum. No additional scaling or rounding is applied.

The pure reducer has explicit UTC/monotonic inputs. Request-start sequence
numbers reject duplicate/out-of-order completions; process-local ordering and
monotonic anchors are never restored. UTC clock rollback qualifies coverage;
elapsed calculations never use controller date/time or wall-clock differences.
Unknown phases qualify coverage rather than gaining undocumented semantics.

## Coordinator and lifecycle

The existing regular `get_data()` response feeds the observer once. Success with
identical data still feeds analytics despite `always_update=False`. New sensors
have their own removable listeners; old entities retain their equality behavior.
Entity callbacks and optimistic writes cannot feed the reducer.

M02 requested/debounced command readbacks are excluded. A confirmed-command
boundary invalidates an overlapping pre-command regular response. This does not
change command, debounce or H05 behavior. It does not assert that the next
regular response proves firmware freshness.

Regular read errors/cancellation qualify coverage and preserve the original
exception. A missed poll is detected when the next regular request starts later
than the previous completed observer callback plus its configured polling
interval and one second. The one second covers the inspected HA coordinator's
`int(loop.time()) + jitter + interval` scheduling range; it is not a firmware
delay and causes no wait. Longer scheduler/command interference is conservatively
qualified as a gap. No polling-disable or unavailable interval is filled in.

Reload/restart restores totals and the open observation with uncertain coverage.
It never invents ignition or adds elapsed time across downtime. The current
duration is unavailable for partial/gapped sessions; its compact attributes
retain accumulated observed elapsed time. A controller reboot has no proven
identity signal. A detected gap/reset-like transition is qualified, but an
unobserved reboot cannot be reliably distinguished from uninterrupted phases.

All lifecycle additions use public Store, bus, ConfigEntry, entity and coordinator
APIs/extension points. The existing coordinator update override is reused. The
public coordinator shutdown override flushes analytics only; it neither owns nor
destroys a Stove. Existing platform/client ownership and H01B safety remain.

## Refill requests

Only an observed `off -> on` edge increments the request count. Repeated `on`
does not. `on -> off` clears the active state. Initial `on`, recovery/reload `on`
and a change across an unknown interval do not invent an edge. An observed off
after recovery establishes a baseline for a later on edge. Missing/invalid alarm
values invalidate edge continuity. Counts exist globally, in the active session
and in calendar buckets.

These are **requests**, never actual refills. There is no door use, mass estimate,
notification delivery, reminder, acknowledgement or notification checkpoint.
The existing production automation is unchanged.

## Store and failure behavior

One integration-owned `Store` per ConfigEntry uses `atomic_writes=True`:
`hwam_stove.analytics.<entry_id>`. Store envelope version/minor is 1/1. Payload
schema is 1 with an explicit pure `migrate()` hook; no predecessor schema exists.
Unknown envelopes/payload versions and malformed/inconsistent records fail
closed. Only the integration's own file is inspected through public `Store.path`
to reject unsupported envelopes before HA can rewrite a minor version.

The payload contains phase/UTC observation boundaries, current/last-complete
session, quality/start-boundary flags, covered elapsed seconds, sampled peak,
request state/edge continuity/counts, lifetime complete/partial totals and:

- Last 100 completed observation records, including qualified partial records.
- At most 400 daily and 24 monthly count buckets.
- Last complete duration retained even when that record leaves the ledger.

No host, raw response, device/entity identity or other library data is stored.
The ConfigEntry ID is only the internal storage key. Registry and O01 diagnostics
are not used as storage. Recorder can be absent or purged without losing totals.

Phase/request transitions, finalization and explicit gaps checkpoint immediately.
Other successful observations checkpoint after 60 monotonic seconds since the
last verified write. This is a disk policy, not a request interval. Up to less
than 60 seconds of *uncheckpointed observations* (elapsed/peak changes) may be
lost in a crash; when observations stop, wall-clock checkpoint age can be longer.
No unobserved time is reconstructed. Reload normally flushes remaining state.

Each ordinary save is verified using a fresh public Store load because HA can
log a write error without raising it. Cancellation waits for owned disk work to
settle, including repeated cancellation, before propagating. A failed read/write
or verification disables analytics, preserves the existing file/totals and never
breaks stove control. There is no automatic storage retry/reset. Reload can retry
loading repaired storage. A corrupt-file quarantine prevents a later missing
file from being silently treated as a new zero-total installation.

When HA is stopping, Store defers saves to HA's final-write event. That pending
write is not called durable or incorrectly reported as failed; HA owns its final
flush/error reporting. Atomic writes protect whole checkpoints, not against disk
loss. Backups remain the recovery source. Restoring an older backup also restores
its older analytics totals. No speculative reconciliation is performed.

## Calendar and four entities

UTC instants are grouped using HA's configured timezone at the observation.
Session completion belongs to its observed end date/month; request counts belong
to their observed edges. Midnight/month/DST never split a session or reset the
lifetime count. Old buckets retain their original grouping if HA's timezone is
later changed; they are not retrospectively reinterpreted.

Four new stable keys use the existing `<entry_id>-<key>` convention and stove
device association, with DE/EN/NL names:

| Key | Native value / availability |
| --- | --- |
| `observed_session_duration` | Seconds since observed ignition, from monotonic intervals; unknown before Burn/while idle, unavailable for unknown start/gaps/offline |
| `last_complete_session_duration` | Last fully observed completed duration in seconds; unknown until one exists |
| `completed_observed_sessions` | Lifetime fully observed completions; compact today/month, excluded partial count and observed request count |
| `last_session_observed_peak` | Last ended observation's sampled °C maximum, with completeness/request attributes |

All four become unavailable on storage failure. Historical values/totals remain
available during a controller outage, with coverage status, because their stored
facts remain valid. Durations use DURATION/seconds and the peak TEMPERATURE/°C.
No new state class is assigned: neither historical peaks nor session durations
are instantaneous MEASUREMENT. The initial observed-count sensor deliberately
uses integration-owned aggregates rather than adding Recorder sum semantics.
Existing six O02 measurement sensors and all original 40 identities are unchanged.
There is no migration for the four additions. No ledger is placed in attributes.

## Verification and next boundary

Deterministic tests inject observations, UTC and monotonic clocks. Event barriers
exercise cancellation/races without sleeps. They cover transitions, abandoned
attempts, partial startup, duplicate/out-of-order/equal reads, M02 overlap,
unavailability/recovery, reload in every phase, request edges, missed polls,
calendar/DST, clock changes, sampled peaks, checkpoint/crash boundaries, real
atomic disk I/O, swallowed write errors, corruption, unknown versions and HA's
final-write path. Both complete HA gates retain HTTP/DNS/socket blocking and the
real-library boundary contract. Exact run results are in A02_GATE_EVIDENCE.json.

Actual wood-addition detection remains unimplemented. Next A03 should validate
normal-use phase/request/door transitions against independently observed actions,
under a separate read-only authorization, before defining any probable-refill
classifier. No stove command or forced test fire is needed for this work.
