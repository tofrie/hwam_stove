# H01A cleanup and H01B forwarding safety boundary

Base: `f98d5b46530550c76c0390f9bef15280d5bdd8f3` (B01).
Manifest `1.0.0b2`, ConfigEntry version `2`, dependency `pystove==0.3a1`.
Lifecycle evidence targets the installed, pinned **HA 2026.10.0b0**, source
`64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5`; it does not certify a later HA release.

## Ownership and implementation

| Window | Ownership and error behavior |
| --- | --- |
| Before successful `Stove.create()` | No returned client owned by the integration. Existing creation error mapping is unchanged. |
| After create, before forwarding | H01A: exclusively owned client. Construction, storage and first-refresh errors trigger cleanup. |
| From the call to `async_forward_entry_setups()` | H01B: client may be shared with platform work. The H01A exception handler no longer applies. |
| Successful setup | Client remains live. The unchanged normal unload removes platforms, calls destroy once, then removes runtime data. |

Only `async_setup_entry()` in `__init__.py` changes. Its narrow exception handler
stops a fully constructed coordinator using public `async_shutdown()`, removes
only this attempt's coordinator from runtime storage (preserving other entries
and unrelated domain data), and awaits `destroy()` exactly once. It then
re-raises the original exception. There is no platform unload before forwarding
because no platform has started, and no cleanup handler enclosing forwarding.

The awaited first refresh has already returned/raised before cleanup starts.
Cancellation during that refresh unwinds the read before client closure. A
partially constructed coordinator has not begun refresh or acquired platform
listeners; HA processes any shutdown callback registered by its base constructor
on setup failure. Persistent device-registry identities are retained, not treated
as active resources or removed by H01A. HA may also invoke the fully constructed
coordinator's idempotent shutdown after H01A has already stopped it.

Cleanup errors are logged separately and cannot replace the primary setup error;
failure in coordinator shutdown or runtime removal does not skip the client-close
attempt. If `destroy()` itself fails or is interrupted, successful resource closure
cannot be promised. There is no retry. A broken runtime mapping that refuses
removal can likewise retain data; its failure is logged. This does not introduce
general setup error suppression or a new logging policy.

H02 remains open: cancellation during `Stove.create()` is still converted to
`ConfigEntryNotReady`. Cancellation after successful create, before forwarding,
now cleans up and propagates `CancelledError` unchanged. H03 and config flow are
unchanged. No coordinator, platform, migration, manifest or dependency changes.

## H01B remains open

`async_unload_platforms() == True` is not a general proof that every previously
started resource consumer has finished. In the pinned HA implementation:

- Platform setup bodies can run under `asyncio.shield`; cancellation or the HA
  platform timeout can finish the outer wait while an injected suspended body
  remains alive. Platform unload/reset removes current entities but does not join
  that shielded setup body; it can subsequently add entities to a removed platform.
- Forwarding uses gather. An escaped error can leave another forwarding branch
  running. Ordinary errors inside a platform are instead handled by HA and need
  not fail the parent integration setup.
- Entry unload callbacks can run before other entry-owned work has completed.
  Registering `destroy()` as a generic unload callback is not a safe join barrier.

Therefore there is no automatic destroy on a forwarding error/cancellation,
recursive entry unload, private task tracking or new TaskGroup. A synthetic
forwarding failure can leave an open client/runtime reference; that is the
explicit unresolved H01B boundary, not a completed H01 fix.

The unchanged HWAM platform bodies currently have no suspending await. Diagnostic
observations show all seven bodies and eight entity-add batches complete eagerly
on the tested ordinary path. Suspended-body and timeout tests **inject** delays;
they demonstrate HA lifecycle limits, not an observed HWAM hardware hang or proof
that H01B occurs during ordinary controller operation. Translation cancellation
delays an existing HA await without changing a HWAM setup body.

Primary source:
[HA config-entry setup, forwarding and unload](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/config_entries.py),
[entity-platform setup/shield/reset](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/entity_platform.py),
[entity-component platform unload](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/entity_component.py),
[coordinator shutdown](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/update_coordinator.py).

## Regression coverage and audit status

- **H01A: fixed. H01B: open / Forwarding Safety Boundary.**
- The former single H01 strict-xfail only failed the first refresh. Its expectation
  moves to ordinary `test_H01_failed_first_refresh_closes_client` in
  `tests/test_h01a_cleanup.py`, now also proving no forwarding, entities or active
  simulated client, exact-once destroy, error identity and cleanup order.
- Additional tests cover immediate/partial constructor failure, storage failure
  before/after assignment, missing storage, real cancellation, cleanup failures,
  repeated failures, unrelated runtime data, success, B01 upgrade and normal unload.
- The ten pre-existing offline diagnostics are retained in
  `tests/test_h01b_safety_boundary.py`, explicitly labeled as H01B safety tests.
  Assertions now additionally forbid integration-driven destroy after forwarding
  errors/cancellation. Test-only close callbacks are deliberately unsafe examples;
  harness cleanup joins injected work before disposing of simulated clients.
- **28 -> 27 strict xfails**: only the first-refresh H01 expectation is converted.
  H02/H03 and all other known-defect tests are unchanged. Zero XPASS is allowed.
  H01B's ordinary safety assertions protect the authorized no-close boundary;
  they do not count as a resolved audit finding or a new auto-close xfail.
- All 35 B01 migration tests remain applicable. Integrity validation preserves the
  original foundation hashes and additionally freezes all runtime files against
  B01 except the single setup function. Normal unload and migration remain checked.
- Full tests are offline under the existing HTTP/DNS/socket guards. No controller
  hardware, protocol change, command retry or separate pystove-fork change occurs.

The next recommended scope is H02's cancellation mapping during create, separately
authorized and tested; it is not implemented here. H01B needs its own design and
evidence before any broader cleanup is attempted.

## Local verification, 2026-10-04

Full suite: **234 cases, 207 passed, 27 strict xfailed, 0 XPASS**, including all
35 B01 tests, 18 cleanup/ownership tests and all 10 retained lifecycle diagnostics.
Ruff, runtime-integrity validation, dependency consistency (156 packages), and
`git diff --check` pass. The xfail list differs only by the converted H01 case.

The existing Foundation workflow validates the pushed commit independently.
Pinned Hassfest/HACS jobs also run on this branch; their known baseline findings
remain visible rather than being suppressed. Local Docker is unavailable, so
the actual metadata-validator results must be read from those CI runs. HACS uses
the repository's default branch as its API target; unchanged metadata alone does
not make that a validation of this branch's new Python code.
