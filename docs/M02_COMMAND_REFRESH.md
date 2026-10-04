# M02 — coordinated readback after confirmed commands

Base: private RC `61820eab74a8a97140f1abe462c8d35593ea9495`, containing validated
compatibility commit `801d9bc9ed137872b07e222c178734281da2e2b5`. Version remains
1.0.0rc1; dependency remains `saynwerk-pystove==0.3.0rc1`. No deployment, hardware
access, library changes, release or tag. H01B remains **OPEN/deferred**; M04 remains
a strict xfail. Earlier gate/RC documents describe their historical snapshots.

## Command and read outcomes

All ten variants (burn level, night lowering on/off, remote refill on/off, start,
sync clock, night begin/end, datetime) request the existing DataUpdateCoordinator
only after the command returns **exactly `True`**. One Stove command invocation
per allowed action; no application command retries, additional setters or delays.
Existing library HTTP policy/retry behavior is not changed by this integration fix.

`False` retains H04's translated `command_not_confirmed`: execution is uncertain,
not proven absent. Command exceptions and cancellation propagate unchanged, without
requesting readback. A confirmed command followed by `None`, transport/read error
or unexpected refresh exception remains confirmed. Coordinator read failure and
availability are separate. Unexpected escaping refresh exceptions are logged in
the readback path, not translated into command failure. Caller cancellation during
an awaited read propagates as cancellation of the action; it does not undo the
confirmed command and does not retry it. Deferred reads have no awaiting action.

Number/Switch optimism is retained only after exact `True`. A successful read
reconciles registered entities to the observed status, even if that observation
is still the previous value. A pending reconciliation flag enables one listener
notification for otherwise equality-suppressed data; `always_update=False` stays
in force. Failed reads leave reconciliation pending until a successful read.
Buttons retain HA press-attempt timestamps and M03 availability. M07 clock
conversion is unchanged. A successful `/start` does not require a changed phase.

## Actual HA scheduling and request budget

Inspected the installed official sources for both HA 2026.9.4 and 2026.10.0b0:
`homeassistant/helpers/debounce.py` is byte-identical (SHA256
`81093ac20caf91d4ea6b4c21d2e01b46f46c08f8c833447c512ea68437e3b3ac`).
Coordinator `async_request_refresh`, `async_refresh`, `_async_refresh` and
`_async_refresh_finished` are AST-identical. The finish hook is expressly intended
for subclasses. The wrapper calls HA's private `_async_refresh` under the existing
Debouncer lock; calling public `async_refresh` there would acquire it twice.
Future HA upgrades must rerun these contracts.

HA's existing defaults are immediate execution and a **10-second debounce
cooldown**, not an invented firmware wait. An idle successful action adds one
status read. Further confirmations during cooldown/in-flight read can return
before another read occurs; HA coalesces them into one trailing request. A normal
poll/explicit regular refresh can satisfy/cancel a pending request. If the timer
fires while a read is still running, HA can absorb the request into that read
("any call is good"). Thus no promise of one new/completed/fresh GET per action,
and no fixed upper bound on controller freshness. No polling until a desired
value. All reads still use the original coordinator `_async_update_data` path.
Unload cancels the pending debounce via HA's existing lifecycle.

## H05 boundary

Requested/debounced reads are conservatively observations only. Tag the actual
execution (including deferred execution), not merely the request call. They update
coordinator data/UI, but **do not** replace H05's confirmed local night-time pair
or clear uncertainty. This rule also applies to other callers of the same requested
refresh path; a request is not a freshness certificate. There is no new claim that
firmware has applied a setter before the next response.

Regular polls/explicit `async_refresh` retain the unchanged H05 read-generation
contract: a successful valid pair can resynchronize only when no night-time
command overlaps the read. A pre-command read finishing after a command remains
rejected by the generation check. The original shared night-time lock and helper
are byte-identical. UI observations and the paired-write safety cache are distinct.
A regular read is accepted under the pre-existing H05 contract, not a newly proven
firmware ordering guarantee. Continuous requested refreshes may postpone the
regular poll; no special recovery poll is introduced.

## Offline verification

Full suite on Python 3.14.6 with exclusive published Saynwerk import ownership:

| Gate | Result |
|---|---|
| HA 2026.9.4 | 824 passed, 1 strict xfailed, 0 XPASS |
| HA 2026.10.0b0 | 824 passed, 1 strict xfailed, 0 XPASS |
| Focused M02 | 154 passed |
| Real-library boundary | 147 cases included in each full gate |
| B01 migration | 35 cases included in each full gate |

Ten M02 strict xfails are now regression cases. Exactly one M04 strict xfail
remains, with zero XPASS. H01A/H01B characterization/H02/H03/H04/H05/M03/M07/M08
remain covered. Focused old H04/M03/M07 tests isolate the requested read to retain
their command/error assertions; H05's focused ordering fixture similarly isolates
readback. Actual M02 reads, deferred requests, stale responses, equal data, lock
serialization and recovery are exercised together in `test_m02_refresh.py`.
Thirty added real-library cases cover all commands followed by invalid, exceptional
or cancelled status reads, with exact call budgets and owned-session cleanup.

Both full gates block IP sockets and guard HTTP/DNS. M02 and real-library tests
capture loop errors and ResourceWarnings; session close ownership remains checked.
Production-version reproduction uses the same source with only the existing
test/tool HA-version assertions substituted (2026.9.4 / fixture package 0.13.367).
The default gate retains 2026.10.0b0 / 0.13.368. No runtime substitution.

Runtime diff is confined to coordinator.py and the five command platforms:
number.py, switch.py, button.py, time.py, datetime.py. Integrity checks constrain
those changes and retain historical B01/H01/H04/H05/M03/M07/M08 scope checks.
Manifest, dependency, identity/migration/setup/unload, translations and `_night_times`
are unchanged. Hardware is not needed for this scheduling/ownership contract;
actual firmware propagation latency remains unmeasured and is not assumed.

Final local verification: 2026-10-04. Ruff, `pip check` in both environments,
static integrity and `git diff --check` pass. Loop errors: 0; ResourceWarnings: 0.
Machine-readable results and runtime hashes: `M02_GATE_EVIDENCE.json`.
