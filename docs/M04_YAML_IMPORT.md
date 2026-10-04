# M04 — individual, idempotent legacy YAML imports

Base: `6992abeb0af5881accf72d298400331d2025c5f5`, including the private 1.0.0rc1
metadata and `saynwerk-pystove==0.3.0rc1`. Only YAML import and its repair text change.
No hardware/production access or deployment. H01B remains **OPEN/deferred**.

## Historical contract and boundary

Examined full local upstream history, including pre-UI root-level files:

- Before `470953b174c6b26dd21b1c995506e37cb00ee85e`, `async_setup` passed the YAML
  mapping key into `StoveDevice.create` as its name. Optional `name` was accepted
  by the schema but did not override that key. Sensor entity IDs incorporated the
  mapping key; sensor display names could use controller identification instead.
- `host` was passed directly to Stove.create. The Config Flow compares exact host
  strings. M04 retains this comparison, without trimming, case folding, DNS,
  address canonicalization or new persistent unique IDs. Different strings that
  address one physical controller remain the separate M05 identity boundary.
- Pre-UI `StoveDevice.init_stove/setup_monitored_vars` mapped selected known values
  into sensor/binary-sensor platforms. An absent/empty list added no such selected
  sensors; the fan platform was independent. Unsupported variables were logged.
- Upstream commit `470953b174c6b26dd21b1c995506e37cb00ee85e` explicitly discarded
  `monitored_variables` when introducing Config Entries and enabled the platform
  defaults. Import retained only name/host (and a historical legacy id then used
  by that version). Its batch code overwrote optional name with the YAML key.
  `d69efb48b0d1c251bfe163b97709370048068475` moved these files under
  `custom_components`; release 1.0.0b2 and the validated Saynwerk line retained
  the unused schema option and all-or-nothing entry guard.

**Boundary:** the old selective sensor/platform set has no equivalent in current
ConfigEntry data/options. M04 does not invent one, rewrite entity disabled states,
or claim that selection was migrated. `monitored_variables` remains accepted
(including scalar-to-list conversion and unknown strings) but unpersisted and
without entity filtering. The repair explicitly tells users to review entity
activation before removing YAML. Existing entity customizations remain untouched.
No persistent semantics beyond the established ConfigEntry import contract are
introduced. Existing names win; new entries use the YAML mapping key. For duplicate
exact hosts, the first YAML row supplies the name; aliases are not separate devices.

## Root cause and implementation

The old `if not async_entries(DOMAIN)` blocked every YAML device when any entry
existed. It also mutated the input YAML and unconditionally claimed completed
migration before asynchronous imports had succeeded.

`async_setup` now snapshots each YAML device and schedules one HA-owned batch,
returning promptly so ConfigEntry setup can pass HA's component-setup barrier.
An in-memory per-HA lock serializes overlapping YAML batches. It lives separately
from the ConfigEntry runtime dictionary, so normal unload cannot invalidate it.
No persistent import flags or completion markers are stored.

Each batch attempts each exact host at most once. Existing matching entries,
including disabled ones, are kept without a connection test, rename, replacement,
or registry update. Different hosts still import. Failures are logged independently
and do not suppress later hosts; duplicate YAML rows do not retry a failed host.
A later startup/setup may retry a still-missing host, but skips existing entries.
The import step checks matching entries again after its existing connection test,
covering an entry appearing while validation awaited I/O. User flow behavior and
broader concurrent manual-flow/alias handling remain M05 work.

The worker aborts only unfinished flows whose public init-data selector matches
its own data object by identity. This covers retained error forms and exceptions,
without cancelling unrelated user/import flows. Cancellation propagates after
existing H03 owned-client cleanup; the worker removes its own flow, releases the
lock and reports actual entry presence. No command, protocol, timeout or retry
policy changes. HA's existing entry setup/unload and H01B boundary are untouched.

## Truthful, stable repair

The existing issue ID `deprecated_import_from_configuration_yaml` is reused in
all three languages, with nonpersistent activation as before. Placeholders report
how many distinct exact hosts have entries, how many have none, and duplicate YAML
rows at the last YAML check. They do **not** say all devices migrated or loaded successfully. Existing
entries count as present, not newly imported. An entry whose runtime setup failed
still counts as an entry; the message explicitly distinguishes presence from setup
success. No unconditional instruction to remove all YAML remains.

Repeated setup updates the same issue instead of adding issues. HA's creation time
and dismissed version survive repeated setup and serialized-registry reload. With
no/empty YAML the obsolete issue is removed. This is startup import behavior, not
a new live YAML reload API or automatic retry service.

## Verification

The former final strict xfail is now a real regression in
`tests/test_m04_yaml_import.py`; its expectation checks the actual second entry,
not a mocked call alone. Added cases cover single/multiple/mixed YAML devices,
exact-host duplicates, unchanged names/schema/selection boundary, partial failures,
cancellation during create/owned close, concurrent batches, validation races,
component-startup barriers, failed runtime setup, repeated setup and a fresh HA
instance reconstructed from serialized ConfigEntries and repairs. Historical B01
fixtures with 38/40 existing entities preserve IDs/customizations and have no
replacement/duplicate registry records after adding another controller.

| Gate (Python 3.14.6, exclusive published Saynwerk dependency) | Result |
|---|---|
| HA 2026.9.4 | 859 passed, 0 xfailed, 0 XPASS |
| HA 2026.10.0b0 | 859 passed, 0 xfailed, 0 XPASS |

Full gates include Boundary 147, B01 35, H01A/H01B safety, H02/H03/H04/H05 and
M02/M03/M07/M08. Both block IP sockets and guard HTTP/DNS. No hardware is accessed.
The production-version copy changes only the established test/tool assertions to
HA 2026.9.4 / fixture package 0.13.367; no runtime substitution. Static integrity
checks constrain M04 to `async_setup`/its helpers, `async_step_import`, and the
existing repair descriptions, while preserving all prior scope checks.

Remaining non-xfail findings include H01B, M01, M05/M06/M09, L01, unresolved
L02 licensing/metadata, remaining L03 documentation and L04 alarm semantics;
O01–O03 remain optional follow-ups. Zero xfails is not a claim they are solved.
Next recommended phase: separately scope M05 host input and manual-flow duplicate
handling, preserving existing identities and avoiding assumptions about host aliases.

Final local verification: 2026-10-04. Ruff, both environment `pip check` runs and
static integrity pass. Diff whitespace check preserves the pre-existing German
translation CRLF line endings. Machine-readable evidence: `M04_GATE_EVIDENCE.json`.
