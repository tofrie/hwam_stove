# A06.2 — persisted evidence gap validation (rc10)

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

Only `_shadow_evidence.py` and the manifest version change under runtime.
Schema 5, Evidence policy/schema, model formula, thresholds, checkpoint mechanics,
benchmark policies, A02 counters, controller requests and dependency are unchanged.
There is no migration, new recovery architecture or counter reconstruction.

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
6. Predictions resume only when the existing coverage/cycle/model conditions
   permit. A timestamp remaining unavailable is not itself a persistence failure.
   Do not bridge downtime or recreate historical predictions.

STOP if there is another Store-validation failure, identity change, historical
counter loss or new integration error. Do not attempt automatic recovery.

## Rollback limitations

A code-only rollback consists of stopping Core, restoring the previously saved
integration directory, and starting Core, with the same dependency and untouched
Stores. rc9 still rejects `prolonged_outage`; its Shadow component may become
unhealthy again. Downgrading code is therefore not a persistence repair.

No Store rollback is needed for rc10: there is no schema change. Do not delete
or replace `.storage` to make a downgrade appear healthy. A separately authorized
full-backup restore can discard all newer HA/A02 observations and settings;
never restore `.storage` while HA is running. Preserve forensic originals.
