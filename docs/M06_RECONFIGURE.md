# M06 — change an existing entry's host

Base: `1c255c25cd0aeac0ac2d479a61673df1ca48b440`.
Branch: `fix/m06-host-reconfigure`.
Dependency unchanged: `saynwerk-pystove==0.3.0rc1`.
Manifest version remains `1.0.0rc1`; ConfigEntry version remains 2.

## Flow and commit boundary

HA's existing entry menu exposes Reconfigure. The form contains only `host`,
prefilled from the selected entry. It uses the unchanged M05 parser, normalizer
and comparison keys. A normalized match to the existing host is a no-op: no
stored spelling change, validation client, reload or registry mutation.

For a changed address, after duplicate and YAML checks, the shared connection
test calls `Stove.create(host)`, checks the same `name`/`stove_ip != UNKNOWN`
conditions, and closes the temporary client with the unchanged H03 cleanup.
There are no commands or additional `get_data()` calls in validation.
The original user/import connection-test statements are extracted unchanged;
their prior exception mapping is retained. The new reconfigure step maps
ConnectionError, TimeoutError and aiohttp ClientError to translated
`cannot_connect`. Unexpected exceptions still propagate; CancelledError is
never converted. A failed close prevents update. Primary validation errors
remain primary when close also fails, as in H03.

After validation/cleanup and a final YAML check, synchronous checks reject an
occupied target, a changed/removed entry, a removed flow, or an unsafe setup
state. With no intervening await, the flow calls HA's public
`async_update_reload_and_abort(entry, data_updates={"host": host},
reload_even_if_entry_is_unchanged=False, reason="reconfigure_successful")`.
Only the host changes, plus HA's normal modified timestamp. Data/options/title,
source, entry ID/unique ID, major/minor version and preferences are preserved.

HA schedules reload of that same entry. Normal integration unload removes
platform consumers before destroying the old client; setup then creates a
runtime client using the saved new host and performs its existing first refresh.
B01 device/entity identifiers derive from entry ID and remain unchanged. Tests
preserve all registry IDs, entity_ids/unique_ids, associations, names, areas,
icons, custom sensor options and disabled/hidden states through real HA reload.
The historical first-ever setup fixture allows only HA's normal addition of
`sensor.suggested_display_precision`; loaded-entry tests require full equality.

The user chooses a new connection address for the selected entry. No comparison
claims that different IPs/names identify the same physical stove; there is no
new permanent identity, merge or registry migration. A wrong but reachable
unconfigured controller cannot be distinguished by the existing validation
contract. Existing M05 alias limitations remain.

## Public HA APIs and reload failure

The installed 2026.9.4 and 2026.10.0b0 sources were compared for
`_get_reconfigure_entry`, `async_update_reload_and_abort`, `async_schedule_reload`,
`async_reload` and `async_has_matching_flow`. The first helper is the documented
ConfigFlow API despite its leading underscore. There are no private HA lifecycle
calls in production code. See [HA reconfigure flow documentation](https://developers.home-assistant.io/docs/core/integration/config_flow/#reconfigure).

Both versions synchronously save data and schedule an independently owned reload
task. The 2026.10 helper additionally selects core-domain translations when no
reason is supplied. An explicit, translated reason is supplied in both versions:
it reports **host saved / reload requested**, not that reload has succeeded.

Cancellation before the commit point leaves the entry and old runtime untouched.
Repeated cancellation during temporary-client destruction is drained by H03;
close completes once before cancellation propagates. If `create()` has not yet
returned a client, pystove owns its cleanup, including confirmed-open file close.

After commit, reload is not an atomic transaction. Failed unload leaves the new
host saved and HA in `FAILED_UNLOAD`; the old client is not prematurely closed.
A failed new setup leaves the new host saved and HA in `SETUP_ERROR` or
`SETUP_RETRY`, as appropriate. A failed first refresh still uses H01A cleanup.
No automatic data/registry rollback, destructive recovery, command retries or
new retry policy is introduced. HA's existing setup retry policy is unchanged.
The user must inspect HA's entry state/logs and resolve the reported failure.

Host changes are rejected during setup/unload or with unsupported/unmigrated
entry versions. Failed setups with a remaining runtime client are also rejected:
M06 does not attempt to recover H01B. An offline old address can be changed after
normal B01 migration when the failed connection left no runtime consumer.
H01B remains **OPEN/deferred**; its original safety tests are retained.

## Duplicate races and YAML

User, import and reconfigure steps share M05's transient host reservations and
public HA flow matching. Concurrent changes to the same entry also match even
when they target different hosts. Existing-entry comparison excludes only the
entry being reconfigured. The target and original stored host are checked again
immediately before commit. No persistent host unique_id/reservation is stored.
Reservations are released on no-op, error, success and cancellation. A cancelled
or removed UI flow cannot commit later merely because validation finished.

**YAML prerequisite:** an old YAML host must be removed and HA restarted before
changing that entry's host. Otherwise M04 would correctly see an unconfigured
old address on the next startup and attempt another import. Silently associating
the old YAML address/name with the newly addressed entry would violate M05 and
the requirement to update only `host`.

The chosen safe boundary therefore refuses that change. `async_setup` records
only an in-memory set of normalized YAML hosts loaded in this HA process, outside
the entry runtime dictionary. It is not cleared by unload or a later setup call,
so already scheduled batches cannot be forgotten. Reconfigure also reads current
YAML using HA's public `async_integration_yaml_config` before and after connection
validation (including HA's normal configuration/package processing). Unknown
startup state or unreadable/invalid YAML fails closed with a translated error.
An unchanged-host no-op does not require those reads or a restart.

The import worker, name/monitored_variables contract, repair messaging and M05
comparison rules are unchanged. Distinct remaining YAML controllers continue
to work. A fresh HA-instance test restores the reconfigured imported entry and
remaining YAML without creating entries or losing the saved new host. No YAML
file is edited automatically, and no persistent alias/history/tombstone is
introduced. Reintroducing a removed old address later as a new YAML/manual
configuration is a separate user action; physical alias deduplication remains
impossible without a stronger identity contract.

## Scope and verification

Runtime changes: `config_flow.py`, six startup-snapshot lines in `__init__.py`,
and new English/German/Dutch flow translations. The M05 `_host.py`, all platform,
coordinator, B01 migration and command code, setup/unload functions, manifest,
dependencies and protocol remain unchanged. No OptionsFlow or discovery.

`tests/test_m06_reconfigure.py` exercises public flows and real HA registry/reload
operations, concurrent user/import/reconfigure attempts, late conflicts, failed
validation/retry, repeated cancellation, reload failures and the YAML boundary.
Real-library tests use the exact installed published artifact with a strict
per-session in-memory HTTP transport; they verify open/read/close, no command
requests, no validation status expansion, ownership and zero loop errors or
ResourceWarnings. Existing boundary tests still use the original default host.

Both full gates run on Python 3.14.6 with IP sockets blocked and HTTP/DNS guards.
The production-version worktree changes only test-tool version expectations from
2026.10.0b0/fixture 0.13.368 to 2026.9.4/fixture 0.13.367. Exact counts, artifact
ownership and source hashes are recorded in `M06_GATE_EVIDENCE.json`.

Next suggested independent finding: **M01 — general setup-error mapping**, while
keeping H01B ownership and cancellation semantics separate. No hardware or
production access, release, tag or deployment is part of M06.
