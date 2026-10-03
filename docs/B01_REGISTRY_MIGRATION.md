# B01 registry migration

## Baseline and scope

Based on foundation commit `e6a0a4c3bea92c91aecc26067bace5cc2f3adeef`.
Manifest `1.0.0b2` and `pystove==0.3a1` remain unchanged. The test environment
remains Python 3.14.6 / Home Assistant 2026.10.0b0, with the foundation's locked
dependencies. This is not certification of a later HA release.

The authoritative historical fixture describes release `1.0.0b2`, commit
`5b7650a5435bffe94629cb523c36aff51d8159b6`. It stores `data["id"] = "test_stove"`.
Device identifiers are `(hwam_stove, test_stove-stove)` and
`(hwam_stove, test_stove-remote)`; entities use `test_stove-<description.key>`.
Master `2176600eece1c644f594a9608186bf395bb2488b` instead uses
`<config_entry.entry_id>-<suffix>`. It had no migration between these contracts.

No controller requests, dependency changes, metadata fixes, entity platform
changes, or other audit fixes are included. B01 is the only converted xfail.

## Migration contract

ConfigFlow VERSION changes from 1 to 2. HA calls `async_migrate_entry()` before
integration setup when the stored major version is older. This follows the
[official config flow migration mechanism](https://developers.home-assistant.io/docs/core/integration/config_flow/#config-entry-migration)
and the installed, pinned HA `config_entries.py` implementation. Version 2 is a
no-op; other unknown versions are rejected without downgrade. New UI/import flows
create version 2 entries using the current schema, with no legacy `id` added.

The existing ConfigEntry, entry_id, title, data, host, name, options and minor
version remain intact. Only its major version changes. The stored legacy `id`
is deliberately retained for recovery and unambiguous retry. Master-created v1
entries without `id` only receive version 2: their registries are not modified.
An invalid stored legacy id fails explicitly rather than guessing from a name.

`migration.py` contains a frozen allowlist for all 40 historical descriptions:
14 sensors, 18 binary sensors, 2 buttons, 2 switches, 1 number, 2 times and
1 datetime. Tests compare every `(entity domain, key)` against the independent
fixture. Domain is essential because sensor and switch share `night_lowering`.
New future entity descriptions must not silently expand this historical allowlist.

Only exact known old identities attached to this config entry are updated.
Unknown keys, other integration platforms and foreign config entries are left
alone. Missing entities/devices are allowed; migration itself creates none.
HA's subsequent normal setup may create missing current entities/devices.

Device updates use `async_update_device(new_identifiers=...)`, preserving registry
record IDs and any unrelated identifiers. Entity updates use
`async_update_entity(new_unique_id=...)`, preserving entity_id and device_id.
Names, areas, disabled/hidden states, options and other user metadata remain on
their original records. HA itself updates bookkeeping such as modified_at and
previous_unique_id; ordinary platform setup may add default registry options.

## Conflict and failure handling

All target identities are checked before any write. A separate legacy and current
record for one known identity is a conflict, even when both belong to this entry.
A current entity unique_id owned by another entry also blocks migration. No record
is deleted, merged or chosen as a winner. The same device carrying both identifier
aliases is safe: its legacy alias is removed. An ambiguous single device carrying
both stove and remote identities is rejected. Device lookup uses HA 2026.10's
config-entry-scoped API; another entry's devices are not adopted or altered.

Order: preflight every identity, update devices, update entities, mark version 2.
The callback does not await between validation and writes. Registry updates are
synchronous callbacks that schedule independent storage writes; there is no
transaction spanning config entries, device registry and entity registry.

An update exception returns failure and attempts compensating updates in reverse
order. Record IDs and associations are never destroyed. The old version remains
1 so a later attempt can resume a partially migrated state if compensation fails.
If a version write fails after changing the in-memory marker, reset the marker
before compensating registry updates. If that reset fails, retain the already
completed current registries: version 2 must not point at rolled-back legacy IDs.
HA reports unsuccessful migration rather than continuing this setup attempt.

Rollback also uses public registry APIs: modified_at/previous_unique_id are HA
bookkeeping and are not restored byte-for-byte. Unexpected persistence failure or
process termination across independent HA storage writes cannot be made crash
atomic by this integration. In particular, a prematurely persisted v2 marker
alongside older registry files would bypass a version-triggered retry. Consistent
HA backups/manual recovery remain necessary for that storage-failure case.
Conflicting installations require deliberate manual assessment; this migration
does not automatically deduplicate damage from an earlier unprotected upgrade.

## Verification

`tests/test_migration.py` exercises actual HA registries and the framework setup
path, with the simulated Stove boundary and existing HTTP/DNS/socket isolation.
It covers complete and partial registries; neither/one/both devices; disabled and
renamed entities; areas, names, hidden state and options; unknown/foreign records;
current-master, already-migrated and future entries; target conflicts; repeat
migration/reloads; and failures before/after writes with recovery.

The full upgrade seeds 2 devices and 40 historical entities, migrates and loads
all seven platforms, then reloads twice: 2 devices / 40 entities remain, with no
duplicate records and with user customizations retained. A separate test proves
HA invokes migration before setup. Restart simulation serializes the real HA
registry storage schema, loads fresh registry instances and checks both completed
and partially migrated data before successful setup. It is not a real process
restart or a power-loss durability test.

Runtime diff before B01: all 16 original runtime files were byte-identical to
`2176600eece1c644f594a9608186bf395bb2488b`. After B01: 14 remain byte-identical;
`config_flow.py` only changes VERSION, `__init__.py` only adds the migration import
and hook, and `migration.py` is new. The integrity checker retains the original
SHA-256 fixture and verifies its baseline hashes, exact runtime inventory, the
VERSION-only change, the unchanged AST outside the hook/import, and all three
translation contracts. The new helper is covered by migration tests and review.

Run the foundation commands in `COMPATIBILITY_FOUNDATION.md`. Expected xfail delta:
29 -> 28, B01 now ordinary regression coverage, no unexpected XPASS. The separate
metadata workflow also runs on `fix/b01-registry-migration`. Its existing hassfest
manifest-order error and HACS license/issues/topics/issue_tracker failures remain
out of scope; report actual CI results for the pushed commit separately. HACS's
API target remains the default `master` branch and checks unchanged metadata,
not the new migration implementation. Hassfest checks the branch checkout.

Local verification on 2026-10-04: **207 cases, 179 passed, 28 strict xfailed,
0 failures/errors, 0 XPASS**. All 35 migration cases passed. Repository-wide
`ruff check`, the runtime/translation integrity check and `uv pip check` passed.
No controller networking was attempted; the isolation self-tests only exercise
and acknowledge their own deliberately blocked synthetic calls.

## Next scope

Recommend H01 only: close the owned Stove client when setup fails after creation.
Do not combine it with cancellation, config-flow cleanup or command behavior.
Neither B01 nor that proposed lifecycle test needs controller hardware.
