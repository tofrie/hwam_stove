# A07.1 / A07.2 — private rc11

Base: combined rc10 `0471b8c72f481f5a560897dc2b7ffdc457867c32`.
Dependency remains `saynwerk-pystove==0.3.0rc3`. Same 49 entity identities.
No new forecast model, command, notification, request or polling path. H01B OPEN.

## Timestamp presentation

The existing `shadow_refill_request` entity retains its identity and timestamp
class. An active, fresh, healthy A04 prediction supplies its native timestamp.
Otherwise the loaded entity has `native_value=None` (HA **unknown**). This is
intentional: HA omits the explanatory extra attributes when an entity is unavailable.
An unloaded/not-ready entity is still unavailable. An expired or invalid timestamp
is never presented as the current forecast merely to keep a dashboard populated.

Compact attributes retain the original `last_prediction_at`, `prediction_issued_at`,
`model_version`, `status`, `reason`, `data_age_seconds`, `data_freshness`, and last
benchmark outcome. Existing experimental confidence/evaluation attributes remain.
`status` distinguishes active, expired, superseded, stale, unknown and request_active.
DE/EN/NL translations name the additional fields and status values. The native
sensor value may say Unknown; open its attributes to see the last original time
and why it is not current. A dashboard attribute row can display that time explicitly
as **Letzter Prognosezeitpunkt**; never label it a current instruction to add wood.

Issuance fields come from immutable A04 pending/ledger rows. A single bounded
copy is also retained in the new A07 Store so later ledger eviction does not erase
the last display record. Restore never makes this record an active prediction.
A04's cycle_spent, formula, thresholds, validation, restart invalidation, counters
and schema 5 remain unchanged. No old prediction or score is rewritten.

## Separate observation Store

HA Store envelope 1, payload schema 1, policy `a072-regular-observations-v1`.
Key: `hwam_stove.observations_v1.<config_entry_id>`. The key is local storage routing;
no config-entry/device/entity/network identifiers appear in exported records.
No A02/A04 file format, counters or migration rules change.

Rows are compact arrays with an explicit exported `columns` list:

- sequence, UTC timestamp, kind (`observation` or `gap`);
- processed phase, native stove temperature, oxygen, three valve positions;
- door and refill request (`true` / `false` / `null` for unknown);
- controller forecast as minutes, when the already-returned duration is valid;
- local session/cycle/segment references, observed interval seconds;
- quality, reason and session qualification.

Only existing successful **regular** coordinator reads create observations,
including equal-valued responses with a new read token. M02 command readbacks,
duplicate tokens, stale pre-command completions and display callbacks do not.
No library access is made by the recorder, its checkpoint callback or export.
Unknown/malformed optional inputs remain null; no interpolation, substituted
values, door events or wood additions are generated. Raw bodies, arbitrary extra
fields, host/IP/SSID/MAC, user names and credentials are not retained.

Door/refill transitions need strict known edges; a local cycle reference is an
**observation window**, not a physical refill counter. It changes after lost edge
continuity or an observed request clearing. Session references alias A02 observed
sessions and explicitly retain partial/unknown qualification. Phase and door
changes are recorded facts, not labels asserting wood addition.

The existing cadence is used without downsampling: normally 10 s while active,
60 s in Standby. Interval metadata distinguishes <=11 s edge-continuous active
reads, >11–15 s edge uncertainty, >15–30 s short gaps, and longer gaps. Standby
uses its existing 61 s allowance. Failed/cancelled reads, command boundaries,
shutdown and restart add bounded gap markers, not measurements. Repeated identical
failure markers are coalesced. Following recovery/restart starts a new baseline;
unknown events during downtime are never reconstructed. Clock discontinuities
are explicit. The A06.1 numerical/display policy is not changed by recording.

## Retention, resource cost and crash behavior

Retain at most **7 days or 20,000 records**, whichever limit is reached first.
Gap markers count toward the limit. Oldest records are evicted with an explicit
cumulative eviction counter; the first retained day/session may therefore be partial.
Do not treat a retention boundary as an observed session start. Export regularly
before eviction if a longer offline corpus is wanted.

At 8 active hours plus 16 Standby hours daily, about 3,840 measurement rows/day
fit roughly 5.2 days (slightly less with markers). Continuous 10 s observations
fit about 55.6 hours; predominantly Standby reaches the 7-day bound first.

A fully populated real HA serialized test file is about **6.43 MB**, with compact
JSON export about **2.73 MB**. A dirty buffer schedules one checkpoint after
**1,800 seconds**. New samples do not postpone that deadline or create more timers.
At capacity, two full checkpoints/hour are about **309 MB/day** of file data if
recording runs 24 hours, plus flushes on unload/shutdown. Filesystem write
amplification and HA backups are additional and device-dependent. This avoids
per-poll disk writes (~360/hour active). No additional database or event pipeline.

Checkpoints use HA atomic writes and readback equality verification. Validation
of large snapshots and file I/O use HA's existing executor; callbacks, collection
and entity notifications stay on the event loop. No new thread is created.
Concurrent arrivals remain dirty for the next checkpoint. Shutdown cancels the
timer, settles in-flight writing and flushes remaining data, including under
repeated caller cancellation. Late reads cannot append after close.

A process crash can lose uncheckpointed observations (normally the last <=30
minutes of collection, plus an in-flight write), never earlier confirmed records
inside the retention window. Such a gap is explicit on restart. No observation
is inferred from elapsed time. Retained records are immutable; automatic retention
is the only intentional removal. The checkpoint atomically replaces the bounded
file, rather than modifying old row values.

Corrupt/unknown Stores fail closed and remain unchanged; collection stops with a
redacted error category and diagnostics health=false. No reset, quarantine,
reconstruction or automatic retry is attempted by this feature. Existing stove
operation and independent A02/A04 behavior continue. After a new installation,
checkpoint_verified may legitimately be false until the first checkpoint; dirty
in-memory observations already exist. `checkpoint_sequence` identifies the last
durable record; later exported rows are explicitly still buffered.

## Local export

In HA Developer Tools → Actions, run only the new export action:

```yaml
action: hwam_stove.export_observations
data: {}
```

It requires a response and returns a JSON snapshot. Copy/save the response as a
local `.json` file. With multiple loaded HWAM entries, choose the intended entry
in the `config_entry_id` selector; no such identifier is included in the response.
The action reads memory only: it does not contact the controller, force a status
refresh, write an export file, upload anything, or force an extra checkpoint.
It rejects missing/ambiguous entries and unhealthy recording rather than returning
an apparently complete data set. Exported arrays are independent copies.

The export contains columns, units, policy/schema, bounded rows, retention/health
metadata, checkpoint timestamp and reference semantics. Preserve these metadata
along with the rows. `sequence` identifies retained committed records; uncommitted
exports can overlap differently after a crash, so do not deduplicate by sequence
alone without timestamp/kind/checkpoint provenance.

Ordinary O01 diagnostics show only recorder health, counts, limits and checkpoint
state; they never include the rows. Explicit exports contain private household
activity times despite omitting network/account identifiers. Keep them local and
share only deliberately. No automatic export/cloud transport is configured.

## Manual installation and rollback

No production changes were made by development. After separate manual authorization:

1. Make a fresh HA backup and preserve the current integration directory outside
   `custom_components`. Prevent HACS overwriting the private fork. Do not recreate
   the HWAM ConfigEntry.
2. Verify the ZIP SHA256, stop HA Core, replace only
   `custom_components/hwam_stove/` with the ZIP contents, then start Core.
   Do not replace `.storage` or change the installed pystove distribution.
3. Confirm rc11, dependency rc3, same two devices and 49 entities/identities.
   Normal values and A02/A04 health should recover. Historical counts remain
   loaded; normal shutdown/restart invalidations retain their existing semantics.
4. Inspect the prediction's attributes when its state is Unknown. A previous time
   must remain labeled last/expired/invalid, not become a new forecast.
5. Observe recorder ready/healthy and, after the first 30-minute checkpoint,
   checkpoint_verified. Export using the action above. Do not induce stove events.
6. Stop on a new storage/identity/lifecycle error. Preserve evidence; do not reset
   any counters or replace Stores to conceal an error.

Rollback: stop Core, restore the saved **combined rc10** integration directory,
then start Core, keeping dependency rc3. Leave the new observations Store intact
for evidence/reinstallation; rc10 does not consume it. A02/A04 require no migration
or rollback. rc10's older prediction display returns. Do not restore `.storage`
while HA is running. A full-backup restore can discard newer data and is a separate
action, not required for this code rollback.

## Remaining model evidence

This release gathers evidence; it does not establish prediction accuracy.
Independent normal-use days need complete pre-request windows, negative cooling
windows, observed request/door coverage and declared gap quality. Early wood
additions require separate user-reported provenance; door or Glow→Burn alone is
insufficient. Freeze and evaluate any later model on separate days before claiming
a dynamic or 45–120-minute request ETA. The original A04 benchmark stays frozen.
