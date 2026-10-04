# H03: temporary Config Flow client cleanup

Base: `8550621c1801dc7cbf2e933834397b72ef198baf` (completed H02).
Manifest `1.0.0b2`, ConfigEntry `VERSION = 2`, published `pystove==0.3a1`.
Target: HA `2026.10.0b0`, Python `3.14.6`, using the existing locked environment.

## Ownership and unchanged connection contract

The user/init and import steps share one connection test. Until `Stove.create(host)`
returns successfully, the flow has no client and performs no destroy, even on
factory error/cancellation. Factory-internal resources remain pystove's concern.
After return, the flow owns the temporary client until its single awaited destroy
operation ends. No client is transferred to the created ConfigEntry.

The original short-circuit identification check is unchanged:
`stove.name != pystove.UNKNOWN and stove.stove_ip != pystove.UNKNOWN`.
No `get_data()`, command, discovery, additional identification request, retry or
timeout policy is added. Name/host, title/data, duplicate-host checks and import
formatting remain unchanged. Duplicate hosts return `already_configured` before
create; an unknown identity still produces the existing `cannot_connect` form.

Validation success closes before entry creation. Validation failure or cancellation
also closes exactly once, then re-raises the original cause. Built-in
`ConnectionError` still follows the existing outer `cannot_connect` mapping.
No new aiohttp/timeout mapping, M01 or YAML/M04 change is included.

## Cleanup failure semantics

These rules were defined before implementation:

| Case | Result |
| --- | --- |
| A: validation succeeds, destroy fails | No successful Entry creation. Destroy's error propagates; a `ConnectionError` uses the already-existing `cannot_connect` mapping. No new catch-all form/error policy. |
| B: validation fails, destroy also fails | Original validation exception remains authoritative, including its existing mapping. The secondary cleanup failure is logged. |
| C: validation/caller is cancelled, destroy also fails | Original `CancelledError` remains authoritative; cleanup failure is logged. No `cannot_connect` or `ConfigEntryNotReady` conversion of cancellation. |

This follows the HA pattern of awaited temporary-client cleanup before returning
success, and retains this integration's existing exception mapping. The pinned
[Tonewinner flow](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/components/tonewinner/config_flow.py)
uses a cleanup `finally` around probing and applies its existing connection-error
handler outside the probe. The pinned
[HA flow manager](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/data_entry_flow.py)
does not turn arbitrary step exceptions into successful entries. H03 additionally
protects the primary error against a secondary cleanup failure.

## Cancellation while closing

The identity check is synchronous. The real suspension point after create is the
awaited close. `_async_destroy_stove()` holds one close task, protects that task
with Python's [shield](https://docs.python.org/3.14/library/asyncio-task.html#shielding-from-cancellation),
and waits until it finishes before propagating caller cancellation. Repeated
cancellation continues waiting on that same operation, without restarting destroy.
The first caller cancellation and its args are retained. No task is detached when
the flow returns, and no cancellation state is cleared with `uncancel()`.

The close coroutine returns its exception to its awaiting owner for handling.
This also prevents Python 3.14.6's cancelled-shield exception callback from
reporting an already-handled cleanup error as an unhandled background exception.
No global exception handler or logging policy is modified.

The guarantee is one completed close attempt before leaving the test; if destroy
itself fails, actual resource release cannot be asserted. An indefinitely hanging
destroy also delays flow completion/cancellation. No timeout/retry policy is added
in this phase. These are temporary Config Flow resources only: no setup/platform
ownership or general H01B safety guarantee follows from this implementation.

## Regression coverage and scope

The original H03 strict-xfail becomes a normal validation-error regression for
both user and import flows. Other cases cover real HA entry creation after close,
both unknown identity fields, factory errors/cancellation without ownership,
duplicate hosts, primary exceptions/cancellation with secondary close failures,
failure after otherwise successful validation, repeated attempts and repeated
real task cancellation during a delayed close (including a subsequent close error).
No additional controller communication is permitted by the simulated boundary.

**26 -> 25 strict xfails**, solely by converting H03; zero XPASS is allowed.
B01, H01A, H01B and H02 tests remain unchanged. H01B remains open. Runtime changes
are restricted to `config_flow.py`: its connection test, private close helper,
and required asyncio/logging imports. Integrity checks freeze all other Runtime
files to the H02 base and preserve flow version, duplicate detection, forms,
entry creation and import formatting. Manifest/dependency/translations are unchanged.

## Local verification, 2026-10-04

Full suite: **287 cases, 262 passed, 25 strict xfailed, 0 XPASS**, including 48 H03
and 4 unchanged Config Flow tests, all 35 B01, 18 H01A, 10 H01B, 7 H02 and 7
environment/isolation cases. Other known-defect function bodies and prior lifecycle
test files are unchanged. Ruff, integrity checks, dependency consistency
(156 packages) and diff checks pass. The two HA source files cited above were
also verified byte-for-byte against the pinned official source commit.

Foundation and the existing pinned Hassfest/HACS jobs run on this branch after
push. Local Docker is unavailable. Known metadata findings remain visible rather
than being suppressed; HACS uses the repository default branch as its API target.
