# M03: coordinator availability for both buttons

Base: `cd78643e6962c1af58037198448f82f0c7822cd4` (completed H05).
Manifest `1.0.0b2`, ConfigEntry `VERSION = 2`, published `pystove==0.3a1`.
Target: pinned HA `2026.10.0b0`, Python `3.14.6`.

## Cause and implementation

The Start and Synchronize clock buttons inherited only `HWAMStoveBaseEntity`.
They therefore stayed available when the shared coordinator's status read failed.
Other HWAM entities already use `HWAMStoveCoordinatorEntity`.

Only `button.py` changes at runtime: import and inherit the existing coordinator
entity class, and pass the coordinator rather than its Stove and ConfigEntry.
The common class still sets the exact same IDs, device association and description.
Button descriptions and the complete `async_press()` body remain byte-identical.

HA's `CoordinatorEntity.available` supplies `last_update_success`.
`BaseCoordinatorEntity.async_added_to_hass()` registers one listener and its
removal callback. The existing HWAM base writes initial state; no second listener,
custom availability property, manual update notification or polling is added.
Removal/unload invokes HA's existing listener removal mechanism.

Unavailable means the most recent coordinator status read failed. It does not
prove that the controller could not receive a command. The buttons intentionally
share the other entities' availability contract. Standby with a successful status
read remains available; this change adds no start eligibility rule.

## Verified HA behavior

Source verification uses installed HA code matching
`64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5`:

- [Coordinator and entity listener lifecycle](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/update_coordinator.py):
  with `always_update=False`, a change in `last_update_success` still notifies
  listeners even when the recovered payload equals the previous successful data.
  Tests exercise the actual error/recovery path without synthetic notifications.
- [Entity service resolution](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/service.py):
  unavailable targets are filtered before button execution. The tested call does
  not invoke pystove; it does not promise a new action exception for that target.
- [Button action](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/components/button/__init__.py):
  HA records the press timestamp before calling `async_press()`, including an
  unconfirmed attempt. Filtering an unavailable target does not create a press
  timestamp. Recovery preserves its previous timestamp, if any.

There is no availability check inside `async_press()`. Direct calls retain the
existing command semantics; normal HA service calls supply the standard filtering.
Availability is an observation, not an atomic controller-command interlock.

## Offline regression coverage

`tests/test_m03_button_availability.py` adds 29 regular cases:

- Successful initial setup in Burn and Standby, and a failed read before entities
  are added: correct initial availability and one listener per button.
- None, TimeoutError and ClientConnectionError followed by recovery, each with
  either changed or identical data: correct actual HA states and notifications.
- Actual scheduled coordinator reads at the unchanged 10/60-second intervals:
  exactly one read per timer firing and no availability-triggered request.
- Both real HA button services while unavailable, with and without an earlier
  press: no command, retry, refresh or timestamp mutation.
- Both available services returning True/False: exact existing zero-argument
  calls, H04 translated error on False, original HA press timestamp semantics.
- Original command exceptions and real task cancellation: propagation, no retry,
  no readback, no availability change caused by the command itself.
- Entity removal and two consecutive reloads: one listener per live button, no
  removed-entity callback, no old coordinator listeners, exact-once client close,
  unchanged registries and one initial status read per new client.

Before the runtime change, the initial-unavailable test reproduced
`available == True` despite coordinator failure; the listener test observed zero
button listeners. One early reload test used a deprecated HA test-side registry
access, which was corrected to supported device iteration. Runtime scope did not
expand.

## Boundaries and verification

M02 remains open: no `async_request_refresh()`, no command-triggered `get_data()`.
All ten M02 xfails, M04, M07 and M08 remain unchanged. The sync callback still calls
`set_time()` without an argument; no timezone change. All H04/H05 behavior, read
ordering, lifecycle code, migration, translations and dependency remain unchanged.
H01B's documented safety boundary is not changed or claimed fixed.

The integrity check now freezes all 18 other runtime files against the H05 base,
permits only the three exact button edits, and retains every earlier scope check.
The original foundation hashes and entity fixtures are unchanged.

Local verification, 2026-10-04: **434 cases: 421 passed, 13 strict xfailed,
0 XPASS**. This includes 29 M03 tests, 35 B01, 18 H01A, 10 H01B safety,
7 H02, 48 H03, 83 H04, 47 H05 and 7 isolation/environment cases.
Ruff, runtime integrity and whitespace checks pass. All previous behavioral
tests, including the known-defect file and all H05 read-race tests, are unchanged.

Network isolation continues to replace Stove.create and block HTTP, DNS and IP
sockets. No real controller was accessed. The Foundation workflow is unchanged;
the existing pinned metadata workflow is enabled for this branch. Local Docker
is unavailable, so official Hassfest/HACS containers run in CI after push.
Their existing findings remain out of scope: manifest sorting, plus HACS license,
issues, topics and issue_tracker. HACS repository API checks use the default branch.
