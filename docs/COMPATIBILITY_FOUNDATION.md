# hwam_stove Compatibility Foundation

This document records the original foundation phase. The separately approved
[B01 migration](B01_REGISTRY_MIGRATION.md) describes the subsequent version 1 -> 2
migration, its narrow runtime-integrity exception and the xfail delta 29 -> 28.
The baseline hashes and the remaining known-defect tests are retained.

The subsequent [H01A cleanup](H01_SETUP_CLIENT_CLEANUP.md) fixes only failures
after create and before forwarding. **H01A is fixed; H01B remains open** at the
forwarding safety boundary. Its first-refresh-only xfail becomes a regular
regression: its count was **27**, down from B01's 28. The subsequent H02 correction
preserves cancellation during create and converts only H02's xfail, bringing the
count to **26**. The subsequent [H03 temporary-client cleanup](H03_CONFIG_FLOW_CLEANUP.md)
converts only H03, bringing the current count to **25**. Other xfails are unchanged.
The tables and initial verification below record the historical foundation phase.

## Scope and provenance

This foundation tests the existing integration. It does not fix runtime defects,
change controller traffic, add features, or migrate registries.

| Component | Exact baseline |
| --- | --- |
| Upstream | https://github.com/mvn23/hwam_stove |
| Fork | https://github.com/tofrie/hwam_stove |
| Integration commit | `2176600eece1c644f594a9608186bf395bb2488b` |
| Manifest version | `1.0.0b2` |
| Runtime requirement | **`pystove==0.3a1`**, published distribution |
| Historical release | `1.0.0b2`, commit `5b7650a5435bffe94629cb523c36aff51d8159b6` |
| Audit HA target | `2026.10.0b0` |
| Audit HA source commit | `64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5` |

The 2026.10 final release was not available on the audit date, 2026-10-03.
Passing this foundation does not certify an untested final release or resolve the
known defects below. The master and historical release share a manifest version,
but have different registry identities.

All 16 existing files under `custom_components/hwam_stove/`, including the manifest
and translations, must remain byte-identical to the baseline in this phase.
`scripts/check_integrity.py` compares them both to recorded SHA-256 hashes and to
the actual Git baseline blobs. Runtime inventory changes also fail that check.

## Exact test environment

| Tool | Version |
| --- | --- |
| Python | `3.14.6` (HA requires at least `3.14.2`) |
| Home Assistant | Published `2026.10.0b0` |
| HA test framework | `pytest-homeassistant-custom-component==0.13.368` |
| pytest | `9.0.3` |
| pytest-asyncio | `1.4.0` |
| pytest-socket | `0.8.1` |
| pystove | `0.3a1` |
| Ruff | `0.9.2`, same version as the existing pre-commit configuration |
| Lock generator | uv `0.12.23`, universal Python-3.14.6 resolution |

`requirements-test.in` declares the direct pins. `requirements-test.txt` locks all
transitive versions and distribution hashes, including platform markers. Do not
install a local/Git pystove build into this environment. A test checks that pystove
is the published version and has no direct-URL installation metadata.

From this repository, with Python 3.14.6 installed:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-test.txt
.venv/bin/python -m pip check
.venv/bin/ruff check .
.venv/bin/python scripts/check_integrity.py
.venv/bin/python -m pytest -q --junitxml=foundation-results.xml
```

Package installation requires Internet access. The tests themselves prohibit
controller networking. Never run the real `Stove.create()` to prepare fixtures.
Lock regeneration is a reviewed dependency operation, not part of a test run:

```sh
uv pip compile requirements-test.in --python-version 3.14.6 --universal \
  --generate-hashes --output-file requirements-test.txt
```

Use the documented uv version for regeneration. Review every dependency change.

## A. Compatibility contract

- Domain `hwam_stove`, name `HWAM Smart Stove`, config flow enabled,
  `local_polling`, unchanged manifest version and pystove pin.
- Successful setup obtains one Stove, completes an initial status refresh, and
  forwards all seven platforms. Normal unload unloads platforms, closes the Stove,
  and removes its runtime data. HA may retain unavailable restored placeholders.
- The UI accepts name and host, tests identification, closes the temporary Stove,
  and creates an entry containing name/host. An identical already-configured host
  produces `already_configured` without another connection attempt.
- Polling is 10 seconds for active phases and 60 seconds for Standby. Unchanged
  status does not notify listeners with `always_update=False`. `None` is rejected
  with `UpdateFailed`. HA coordinator availability recovers after a valid read.
- Seven platforms contain exactly **40 descriptions**: 14 sensor, 18 binary_sensor,
  2 button, 2 switch, 2 time, 1 datetime, 1 number. Message ID and DateTime start
  disabled; all other descriptions start enabled.
- `tests/fixtures/entities.json` records the descriptions, units, defaults,
  categories, enums, device associations and alarm mappings. It was captured once
  from the pinned baseline and reviewed against the audit; tests never regenerate
  it. Existing metadata is frozen for this phase, not declared ideal forever.
- Current device identifiers are `(hwam_stove, <entry_id>-stove)` and
  `(hwam_stove, <entry_id>-remote)`. Entity unique IDs are `<entry_id>-<key>`;
  uniqueness is scoped by HA platform/domain. Reload preserves registry identities.
- The historical release instead uses stored `entry.data['id']` (a slugified name)
  as the prefix. The historical fixture seeds 40 real HA entity-registry records
  and two device records. It does not implement migration or execute old runtime.
- Successful commands call the same existing public pystove methods and arguments,
  exactly once. Night/remote switches send explicit booleans. Successful number
  and switch calls currently update local state optimistically; this does not
  replace the required future readback in M02.
- Sensor values preserve the existing processed pystove contract, including
  integer-scaled temperatures/oxygen, naive stove date/time, time objects,
  timedelta duration, and alarm text lists. The literal synthetic status fixture
  is checked against the **installed 0.3a1 parser**, with raw I/O mocked.
- All 18 binary descriptions are exercised with empty alarm lists, each known
  alarm (including shared valve texts), and an unknown text in a valid list.
- DE/EN/NL have matching structural keys, all 40 entity names, all enum options,
  config-flow keys and the existing YAML issue key.
- Legacy YAML accepts named devices, host, optional name and monitored_variables.
  Successful import with no existing entries uses SOURCE_IMPORT and the YAML key
  as entry name; monitored_variables is accepted by the schema but not persisted.
  The latter limitations and explicit-name overwrite belong to M04, not a desired
  future feature contract. No YAML handling is removed in this phase.

Historical source references:

- https://github.com/mvn23/hwam_stove/blob/5b7650a5435bffe94629cb523c36aff51d8159b6/custom_components/hwam_stove/entity.py
- https://github.com/mvn23/hwam_stove/blob/5b7650a5435bffe94629cb523c36aff51d8159b6/custom_components/hwam_stove/coordinator.py
- https://github.com/mvn23/hwam_stove/compare/1.0.0b2...2176600eece1c644f594a9608186bf395bb2488b

## B. Known defects: desired future behavior, not compatibility promises

Audit IDs here refer to the **hwam_stove audit**, not the separate pystove audit.
All remaining expected failures are `xfail(strict=True, raises=MissingAuditBehavior)`.
Only a specifically labelled final expectation raises that exception. Setup,
fixture, unexpected-exception and unrelated assertion failures stay real failures.
An unexpected pass fails the suite and requires review of the finding/test.

Historical foundation xfails (before B01 and H01A):

| ID | Cases | Desired behavior asserted |
| --- | ---: | --- |
| B01 | 1 | Preserve all original device/entity registry identities on release upgrade |
| H01 | 1 | Close an owned Stove after failed initial refresh |
| H02 | 1 | Preserve real task cancellation during setup |
| H03 | 1 | Close a temporary flow client if validation fails after create |
| H04 | 10 | Report unconfirmed command success as a HA error, without retry |
| H05 | 2 | Preserve both night-time edits, in either sequential order |
| M02 | 10 | Read status after a confirmed command |
| M04 | 1 | Import a distinct YAML device even when another entry exists |
| M07 | 1 | Convert aware UTC clock input to the existing HA-local-time convention |
| M08 | 1 | Include complete days in duration values |
| **Total** | **29** | **Strict expected failures, zero XPASS permitted** |

H01/H03 tests observe cleanup counts before explicit test-harness cleanup. H03
injects an identity-validation failure after factory return; it does not claim
that ordinary 0.3a1 identity attributes normally raise. H05 explicitly arranges
an acknowledged edit becoming visible on the next simulated status read; this is
a test scenario, not a measured firmware timing guarantee.

`False` means **no reliable success confirmation**. A command may already have
changed the controller. Do not add automatic retries or claim non-execution.

The audit's other findings remain tracked but are not all reproduced by an xfail:

| ID | Deferred finding |
| --- | --- |
| H06 | Missing foundation: addressed by this test/CI work, without claiming full coverage |
| M01 | Setup/flow error mapping and timeout budget |
| M03 | Button availability does not follow coordinator availability |
| M05 | Stronger host input validation and duplicate-flow handling |
| M06 | Reconfigure host while preserving the entry |
| M09 | Dependency raw-response logging; separate pystove L2 scope |
| L01 | runtime_data and stricter runtime typing |
| L02 | Manifest/ownership/license/release metadata |
| L03 | User documentation, translation improvements and copy/paste text |
| L04 | Door-alarm device class and precise overheat naming |
| O01 | Redacted cached diagnostics |
| O02 | Appropriate long-term statistics |
| O03 | Discovery only after protocol/identity evidence |

H01B's remaining lifecycle variants, M04's remaining YAML limitations and the clock
sync button's timezone behavior still need targeted tests in their own scopes.
This foundation does not claim complete coverage of every audit subcase.

## Network isolation and simulated contract

`SimulatedStove` exposes create (via an autouse AsyncMock factory), destroy,
get_data, set_burn_level, set_night_lowering, set_night_lowering_hours,
set_remote_refill_alarm, set_time and start. Its methods enforce the installed
0.3a1 bound signatures using autospec. Reads return independent status copies.
Tests arrange success, False, readback and exceptions explicitly.

Three independent barriers are active:

1. Autouse replacement of `Stove.create()` shared by all integration imports.
2. HTTP request and DNS guards fail before transport and also fail teardown if
   an unexpected attempt was caught by production code.
3. pytest-socket blocks IPv4/IPv6 sockets; only Unix sockets needed by asyncio
   are allowed. Guard self-tests acknowledge only their own expected sentinel.

Fixtures use `.invalid` hostnames and synthetic identities. No real controller
address, credentials, response capture or controller command is used. The parser
contract test creates an uninitialized Stove with a mocked get_raw_data; it does
not create a session. The test harness points HA's custom-component search path
at this checkout, without patching integration runtime logic.

## CI and metadata gates

`foundation.yml` uses Ubuntu 24.04, Python 3.14.6, hashed requirements, dependency
consistency, repository-wide read-only Ruff, static integrity/translation checks,
and the full offline pytest suite. Checkout/setup-python actions are SHA-pinned.

`metadata-validation.yml` runs official Hassfest/HACS containers separately, pinned
by immutable image digest. It does not post comments, ignore validators, or use
`continue-on-error`. It runs on pushes to this foundation branch and supports
manual dispatch once available to GitHub's workflow dispatcher.

**STOP reason for making metadata checks required:** baseline L02 has an absent
issue_tracker, empty codeowners, and no license file/recognized license metadata.
Other validator findings, including translation schema issues, must be recorded
from actual runs rather than silently waived. Fixing these existing runtime or
publication metadata is not authorized in this phase. Do not configure these
checks as required until a separate metadata scope has been approved. The actual
red check results remain visible; a green foundation is not a release approval.

Pinned validator images:

- `ghcr.io/home-assistant/hassfest@sha256:39031fe75baf5566814a01f3c414764c029c54a0e1d742965d5b94a0d6120875`
- `ghcr.io/hacs/action@sha256:dc92fdad2f6ffbe74bffb7269d781ea8e064f52d9bb486cdf3925d74e7ab6ebf`

### Observed GitHub results, 2026-10-03

Foundation commit `378dd63ba98aa847794d6835ef6ae844c00597c7` passed the Linux
foundation workflow, including dependency installation/consistency, Ruff,
integrity checks and pytest:
https://github.com/tofrie/hwam_stove/actions/runs/37156308564

The separate validator run completed with visible failures:
https://github.com/tofrie/hwam_stove/actions/runs/37156308491

- Hassfest: existing manifest keys are not ordered as domain, name, then
  alphabetical. This is its reported error; do not change the manifest here.
- HACS: **4 of 9 checks failed**: no license, issues disabled, no valid topics,
  and missing `issue_tracker` in the manifest. Brands passed using the central
  fallback. Issues/topics are configuration of the newly created fork, distinct
  from the unchanged upstream runtime metadata. None was altered to pass checks.
- The HACS action reports its API target as `tofrie/hwam_stove@master`. That branch
  contains the same baseline runtime/manifest/HACS metadata as this foundation;
  it is not a validation of new test files. Hassfest checks the checked-out branch.

The required-gate STOP reason therefore includes the manifest ordering and these
four HACS findings. Address them only in a separately approved metadata scope.

## Explicit exclusions and next phase

Initial local verification on 2026-10-03 (macOS, Python 3.14.6): **173 cases,
144 passed, 29 strict xfailed, 0 failures/errors, 0 XPASS**. Repository-wide Ruff
passes. All 16 runtime files match the baseline bytes and hashes; all 40 entity
descriptions and DE/EN/NL translation contracts pass. The installed dependency
set passes `uv pip check`. Linux and metadata results are recorded independently
by the fork's GitHub Actions runs for the pushed commit.

No files in the runtime subtree changed. No new HTTP status policy, retries,
controller endpoint, live-data use, self-test, registry migration, runtime_data
refactor, YAML removal or dependency switch is included. The six xfails in the
separate pystove project were not copied or modified.

The safe next recommendation is **B01 registry migration only**, protected by the
historical and current fixtures. It requires no controller hardware and should
not be combined with other findings. It is not implemented by this foundation.
