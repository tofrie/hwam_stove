# A06.2 — persistence validation and freshness thread safety (rc10)

Base: rc9 `a0545331d77c48a2333012b7079e22091df1c38e`.
Integration: `1.0.0rc10`; dependency remains `saynwerk-pystove==0.3.0rc3`.
49 entity identities and all entity descriptions remain unchanged. H01B is OPEN.

## Change

A06.1 legitimately records `prolonged_outage` in an Evidence gap. The rc9 loader
validated Evidence gaps against the frozen model's abstention vocabulary, which
does not contain that value. A previously successful write/readback could thus
fail on the next load with `ValueError("Invalid gap reason")`.

`EVIDENCE_GAP_REASONS` retains the existing accepted reasons and adds exactly
`prolonged_outage`. Only the persisted gap validator uses this separate set.
The model abstention inventory is not extended. Unknown reasons, invalid
structures and inconsistent counters continue to be rejected.

Only `_shadow_evidence.py`, `_freshness.py` and the manifest version change
under runtime (compared with rc9).
Schema 5, Evidence policy/schema, model formula, thresholds, checkpoint mechanics,
benchmark policies, A02 counters, controller requests and dependency are unchanged.
There is no migration, new recovery architecture or counter reconstruction.

## Confirmed freshness callback defect

The scheduler already uses HA `async_call_later`, which schedules a loop timer.
The target `Freshness._expire` was an ordinary synchronous method without
`@callback`. In HA 2026.10.4, `get_hassjob_callable_job_type` classifies that target
as `HassJobType.Executor`; the timer's `_run_async_call_action` dispatches it to
HA's executor. The chain is:

`async_call_later -> HA timer dispatcher -> executor(_expire) -> notify ->
StoveCoordinator.async_update_listeners -> entity _handle_coordinator_update ->
async_write_ha_state`.

The central fix imports HA `callback` and decorates `_expire`. HA then executes
that same short, nonblocking callback directly on its event loop. No thread,
threadsafe wrapper, executor I/O change or per-platform workaround is added.
The library read was and remains awaited async I/O. No synchronous controller
operation is introduced on the loop.

Actual HA timer-dispatch tests fail on the former implementation for both active
and standby intervals (the recorded thread differs), then pass with the fix.
Tests also trace real coordinator listeners and Entity state writes across
sensor, binary_sensor, number, switch and time, and simulate an awaited executor
result returning to loop processing. Tests use captured actual loop handles and
injected ages, not sleeps or mocked HA job classification.

Every successful observation cancels the preceding two local deadlines before
scheduling new ones. Failed reads keep the existing deadlines; they do not
multiply them. The ConfigEntry registers coordinator shutdown through HA;
`async_shutdown` and the HA-stop listener call `_async_stop_models`, which closes
freshness and cancels both deadlines. The closed guard prevents notifications
from a late dispatched callback. Repeated unload/setup tests confirm no remaining
freshness handles, entity writes, reads or accumulated coordinator listeners.
Normal reloads do not accumulate timers/listeners. A separate deterministic
in-flight read test reproduced re-arming after unload: `Freshness.accept` could
create two timers after `close`. It now returns False when already closed,
without accepting the late sample or scheduling callbacks. No live freshness
threshold changes. The test verifies the actual unload/read-completion race
leaves no timer or listener. The reported daily warning count
cannot be reconstructed from code alone, but a single unsafe notification fans
out to the existing entity listeners.

The expiry deadlines (interval + 1.001 seconds and 120 seconds), all A06.1
15/30-second gap policy, strict edge freshness, request cadence and model
semantics are unchanged. Timer notifications alter display availability only;
tests assert no new analytics/model observations or controller calls.
H01B partial-platform-setup limitations remain OPEN.

## Preservation verification

Unmodified private forensic copies were verified independently before this fix.
They are not included in this repository, its synthetic fixtures or the ZIP.
The actual corrected loader is exercised only against disposable copies in a
network-blocked HA fixture. Initial load plus three subsequent loads preserve
all cumulative prediction aggregates, closed ledger records, annotations and
rc7/rc8 archives, with successful checkpoint verification. Original forensic
hashes are checked before and after each test. The separate A02 schema-2 data
is validated without mutation. Historical stores are inspected without migration.

Public regression fixtures cover open/closed supported gaps, rejection of
unsupported reasons, unchanged model vocabulary, real generated outage -> disk
checkpoint -> repeated load, exactly-once pending invalidation, unrelated
aggregate corruption and an exact rc9 runtime diff. No production access or
controller communication is used during implementation/testing.

This combined rc10 supersedes the earlier persistence-only rc10 package; use
only the latest ZIP checksum and commit. No second release/version is created.

See `A062_VERIFICATION.json` for the recorded environments and results.

## Manual installation — only after separate authorization

1. Make a fresh private HA backup before installation and retain the current
   integration directory outside `custom_components`. Preserve every Shadow
   version and A02 analytics Store. Earlier forensic copies are evidence, not
   necessarily the latest A02 checkpoint. Prevent HACS overwriting this private
   integration during validation. Do not remove/recreate the ConfigEntry.
2. Verify the installation ZIP SHA256 and its per-file manifest on your Mac.
   Extract to a staging folder. It contains only `custom_components/hwam_stove/`.
3. In an explicitly authorized maintenance window, stop HA Core before replacing
   code, replace only that integration directory with the extracted directory,
   then start HA Core. Do not copy anything into `.storage`, delete Stores, or
   change dependencies. Do not restart Proxmox/HAOS merely for this code change.
4. Check version `1.0.0rc10`, unchanged `saynwerk-pystove==0.3.0rc3`, the same
   ConfigEntry/devices and all 49 entity identities. Normal stove values should
   resume. Do not issue stove commands for verification.
5. Check cached diagnostics: Shadow `healthy`, `persistence_healthy` and
   `checkpoint_verified` true; historical aggregates loaded rather than zero.
   Compare counters with the pre-installation checkpoint, allowing only normal
   subsequent observations. A02 may have advanced since the forensic snapshot;
   its earlier value is not a target to restore.
6. Check that freshness expiry/reconnection no longer logs an off-loop
   `async_write_ha_state` warning. Use normal observation only, without forcing
   controller disconnects or requests.
7. Predictions resume only when the existing coverage/cycle/model conditions
   permit. A timestamp remaining unavailable is not itself a persistence failure.
   Do not bridge downtime or recreate historical predictions.

STOP if there is another Store-validation failure, identity change, historical
counter loss or new integration error. Do not attempt automatic recovery.

## Rollback limitations

A code-only rollback consists of stopping Core, restoring the previously saved
integration directory, and starting Core, with the same dependency and untouched
Stores. rc9 still rejects `prolonged_outage`; its Shadow component may become
unhealthy again, and the off-loop freshness callback defect also returns.
Downgrading code is therefore not a persistence or thread-safety repair.

No Store rollback is needed for rc10: there is no schema change. Do not delete
or replace `.storage` to make a downgrade appear healthy. A separately authorized
full-backup restore can discard all newer HA/A02 observations and settings;
never restore `.storage` while HA is running. Preserve forensic originals.
