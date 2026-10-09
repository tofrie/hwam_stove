# A02.1 — observed refill-request statistics (private validation)

Base: `41934e8cb3e17f558e8606793e0cc0860c4d1bae` (A02).
Integration version: `1.0.0rc2`. Dependency unchanged:
`saynwerk-pystove==0.3.0rc2`. H01B remains OPEN. No actual-refill classifier,
wood-addition count, fuel estimate or A03 conclusion is introduced.

## Counting and entities

The existing A02 reducer is unchanged. Only a regular, accepted observation of
`refill_alarm=false` followed by `true` counts one **observed refill request**.
Repeated true, startup/reload/recovery true, duplicate callbacks, rejected reads
and M02 readbacks do not themselves count. A later regular false→true observation
can count independently of a readback. Coverage gaps require a new observed false.

Three additional enabled sensors use the existing entry-ID/key identity pattern:

| Key | Meaning |
| --- | --- |
| `refill_requests_current_session` | Existing current session's request counter; unavailable without a session. New sessions start at zero. |
| `refill_requests_today` | Observed edges attributed to today's HA-local calendar date. |
| `refill_requests_season` | Observed edges inside the most recently started configured annual season. |

Partial sessions expose `start_boundary_known` and `coverage_uncertain`; an active
Burn/Glow startup never becomes a claimed complete session. Calendar zero means
zero **observed** requests, not that no unobserved request occurred. A Standby edge
counts in the calendar. Existing A02 ordering attributes an edge on the transition
to Standby to the ending session before closing it; no new session is created.
The 44 existing identities remain unchanged; the expected total is 47.
No sensor claims MEASUREMENT/TOTAL/TOTAL_INCREASING statistics semantics.

## Annual season options

Settings → Devices & services → HWAM entry → Configure exposes two validated
annual `MM-DD` strings, default `09-01` / `05-31`. A full date selector would add
an irrelevant year. Both dates are inclusive; the end belongs to the next year
when it precedes the start. Equal boundaries mean one day. February 29 is rejected
because an annual boundary must exist every year; no implicit leap-day policy.
During the off-season the most recently completed season's total remains visible.

The public OptionsFlow preserves unrelated options. An option update changes only
the cached query: no reload, client creation, controller read or timestamp rewrite.
The daily query follows HA's configured timezone, not a fixed German timezone.
Public HA local-midnight timers update cached sensor states, including DST days,
even without a new status observation. Timers/listeners are removed with entities.
Stored date buckets keep their original observation-time local date attribution;
changing HA's timezone cannot retrospectively re-label historical buckets.

## Persistence and migration

The existing atomic HA Store key/envelope is retained; the **payload schema** moves
from 1 to 2. No registry or ConfigEntry-version migration is involved. Schema-1
sessions, current counts, completed/partial totals, peaks, ledger, calendar rows
and existing coverage information are retained. Reload applies A02's existing
continuity-gap qualification and does not replay a true alarm as an edge.

No per-event ledger is added. Existing limits remain: 400 daily aggregate buckets,
24 monthly buckets, 100 session records. An annual period spans at most 366 dates,
so the ordinary current-season query fits inside retained daily aggregates.
Schema 2 adds a monotonic `request_retention_floor`: if a daily bucket with requests
is evicted, queries touching that date or earlier are unavailable rather than
silently undercounted. Removing zero-request buckets does not invalidate totals.
This also handles clock rollback without claiming a recreated bucket is complete.

Schema 1 recorded no eviction watermark. If its retained daily request sum equals
the lifetime total, the migration establishes complete request retention. Otherwise
it conservatively marks dates through the latest retained day as uncertain (or
all dates if there are no retained days). Existing lifetime/session totals are
preserved. New queries strictly after the boundary can become available; changing
options never resets counters or fills missing history. Invalid/unknown Stores
remain fail-closed under the existing A02 health policy. A failed checkpoint makes
analytics unavailable; cached memory is not advertised as durable success.

Migration is persisted at the next normal A02 checkpoint/final save. No extra
controller communication is needed. Compact cached diagnostics add payload schema,
ready/healthy flags and validated season boundaries; no ledger or identifiers.

## Private installation and rollback

Before replacing the integration, take a current HA backup including `.storage`
(registries, entry/options and `hwam_stove.analytics.*`) and the current integration.
Keep HACS from overwriting it. Replace the entire integration directory with the
supplied ZIP's `custom_components/hwam_stove/`; preserve the entry and restart HA.
Verify the same entry, same two devices, all previous 44 entities/customizations,
three new request sensors, normal values, diagnostics schema 2/healthy, and reload.
Do not induce stove actions for testing. Counts are prospective observations and
retained A02 facts; nothing is reconstructed from Recorder.

For rollback, restore the matching pre-upgrade HA backup (code, registries/options
and analytics Store together). Do not restore `.storage` while HA is running.
Old A02 cannot read schema 2: do not relabel the schema, delete the Store or copy
only old code over migrated analytics. Restoring a backup loses observations made
since that backup; retain a separate copy if analysis of that interval is needed.
Public distribution remains subject to the previously documented license blocker.
