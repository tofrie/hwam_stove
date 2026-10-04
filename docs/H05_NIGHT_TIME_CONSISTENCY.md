# H05: consistent paired night-time commands

Base: `a6182dd5ab6269ab807288cf05eccf017880e9b2` (completed H04).
Manifest `1.0.0b2`, ConfigEntry `VERSION = 2`, published `pystove==0.3a1`.
Target: HA `2026.10.0b0`, Python `3.14.6`, existing locked test environment.

## Cause and approved boundary

The begin/end Time entities share one StoveCoordinator, but previously combined
each new value with the other time from its unchanged cache. Two edits before
polling could therefore overwrite the first edit. HA's current Time service
path does not serialize these two asynchronous entities by default.

The initial analysis stopped at the coordinator safety gate. Public coordinator
listeners miss successful equal-data reads with `always_update=False`. Also, a
read started before a command may complete after it with an older snapshot;
neither a new data object nor a later completion timestamp proves resynchronization.

The separately approved coordinator addition marks only the start and successful
completion of reads already taking place. It creates no request, changes no
polling interval, does not mutate `Coordinator.data`, leaves `always_update=False`,
and does not trigger listeners. No private HA task structure is used.

## State and ordering proof

Each coordinator owns one `NightTimeCommands` instance, shared by both Time
entities and by direct entity calls. It stores only a last usable read pair, an
optional locally confirmed pair, uncertainty, a command generation and an active
command flag, plus its client reference and an `asyncio.Lock`. There is no global
registry, shared lock across stoves, or second copy of the full status.

The lock covers selecting a safe pair, building the requested pair, the single
pystove call, and recording its outcome. Immediately before that call, without
an intervening suspension point, generation increases, the command becomes active,
the previous local confirmation is cleared, and uncertainty becomes true.

| Outcome | Night-time state | Action result |
| --- | --- | --- |
| `True` | Requested pair becomes locally confirmed; uncertainty clears | Existing success behavior |
| `False` | Uncertain; neither old nor new pair is assumed | Existing H04 `command_not_confirmed` |
| Exception after call begins | Uncertain | Original exception propagates |
| Cancellation after call begins | Uncertain | Original `CancelledError` propagates |
| Cancellation before call, including lock wait | State and generation untouched | Cancellation propagates; no command |
| Later action while uncertain | Remains uncertain | H05 error before sending anything |

`finally` clears the active flag; `async with` releases the lock. There is no
broad exception conversion. Time's existing H04 confirmation helper remains
unchanged and handles the returned False after the paired operation exits.

Read-start metadata is `None` if a command is active, otherwise the current
integer generation. A successful read is usable only when all of these hold:

1. Its token is not `None`: it did not begin during a command.
2. Its token equals the current generation: no command began since the read began.
3. No command is active at read completion.
4. Both processed night-time values are `datetime.time` values.

Acceptance replaces the last-read pair, clears any local confirmed pair and clears
uncertainty, even when the read equals `Coordinator.data` and no listener fires.
A rejected overlapping read still follows normal HA status processing; it cannot
overwrite H05's command basis. The helper never alters HA's data or UI state.

In `_async_update_data`, the token is captured before the existing `get_data()`;
completion is reported after the existing validation/device-metadata processing,
immediately before returning the unchanged data. None, parsing/transport errors,
or cancellation cannot reach that successful completion notification. An unusable
night-time pair cannot clear uncertainty and does not introduce an exception for
other entity types.

There is no H05 lock around polling. HA already serializes its ordinary scheduled
and explicit refresh paths with its own coordinator refresh lock; first refresh
precedes platform setup. This was checked in the pinned
[HA coordinator source](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/update_coordinator.py).
The service parallelism and equal-data listener behavior were also checked against
the [official fetching documentation](https://developers.home-assistant.io/docs/integration_fetching_data/)
and the pinned entity/platform sources. No `PARALLEL_UPDATES` setting is changed.

## Command and error behavior

From a read pair of `20:00–06:00`:

- Begin `21:00`, then end `07:00`, both True: send `21:00–06:00`, then `21:00–07:00`.
- End `07:00`, then begin `21:00`, both True: send `20:00–07:00`, then `21:00–07:00`.

False still means missing reliable confirmation, not proven non-execution. The
first False uses H04's unchanged translated error. A subsequent coupled edit is
blocked using domain `hwam_stove`, key `night_times_not_synchronized`, no placeholders:

| Language | Message |
| --- | --- |
| en | The current night times must be read from the stove again before they can be changed. |
| de | Die aktuellen Nachtzeiten müssen erneut vom Ofen gelesen werden, bevor sie geändert werden können. |
| nl | De huidige nachttijden moeten opnieuw van de kachel worden uitgelezen voordat ze kunnen worden gewijzigd. |

Neither error claims which pair is currently on the stove. No retry, rollback,
status assumption or automatic refresh follows an error. A suitable existing read
restores usability. If ordinary polling is disabled or fails, the coupled edits
remain blocked until such a read succeeds. The polling intervals remain 10 seconds
in active phases and 60 seconds in Standby; no new timeout is introduced.

## Deterministic regression coverage

The two former H05 xfails are ordinary sequential regressions through both direct
methods and real HA services. Event barriers hold the first simulated command,
observe an actual lock waiter, and release/cancel requests in a controlled order.
No timing sleeps or production test hooks are needed. True, False, unexpected
exceptions and task cancellation are covered in both begin/end orders. A direct
first action and a service second action prove the same lock is used. Separate
tests cancel before task execution and during lock waiting, including an already
confirmed pair with no later command outcome masking accidental invalidation.

| Read case | Required result tested |
| --- | --- |
| R1 | Successful read after confirmed command replaces the local pair |
| R2 | Read started before False and completed after it cannot clear uncertainty |
| R3 | Read started after False can resynchronize without an overlapping command |
| R4 | While that read is pending, a blocked action sends nothing and does not invalidate its generation |
| R5 | A read crossed by a True command cannot overwrite the confirmed pair |
| R6 | A different freshly read controller pair becomes the next command basis |
| R7 | Equal data resynchronizes both uncertain and confirmed states without listener notification |
| R8 | None or UpdateFailed does not resynchronize |
| R9 | Timeout or connection error does not resynchronize |
| R10 | Actual read-task cancellation does not resynchronize |

Additional cases cover reads starting during a command and ending before/after
it, and a read between commands N and N+1 becoming obsolete. The four external
safety-gate analysis cases are represented by corrected service-serialization and
read-boundary regressions. A second real ConfigEntry works while the first command
is paused; its confirmations and reads do not change the first entry's uncertainty.

Tests assert exact paired arguments and counts, prohibit all other commands, spy
on refresh requests, and count reads before/after every relevant scenario. Reads
explicitly invoked by a test represent ordinary coordinator reads; the command
path invokes none. H04's existing no-readback/state-write/exception tests are
unchanged. M02's ten strict xfails still fail as expected, including both Time cases.

## Local verification, 2026-10-04

**405 cases: 392 passed, 13 strict xfailed, 0 XPASS.** This includes 47 H05 tests,
all 83 H04 tests, 35 B01, 18 H01A, 10 H01B safety, 7 H02, 48 H03 and 7 network/
environment tests. H01B remains open. Only H05's two xfails are converted; M02,
M04, M07 and M08 are unchanged. No real hardware access occurred.

Ruff, translation consistency, dependency consistency (156 packages) and runtime
integrity pass. Runtime changes are restricted to `time.py`, `coordinator.py`,
`_night_times.py`, and the three language files. The other 13 existing runtime
files are byte-identical to the H04 base, including all other command platforms,
the H04 helper, lifecycle/flow/migration files and manifest. Scope checks retain
the earlier hashes and verify the exact passive coordinator additions, only two
changed Time setter callbacks, and unchanged existing translations. German CRLF
line endings are retained; whitespace checks account for CR at end of line.

Foundation CI is unchanged. The existing pinned metadata workflow is enabled for
this branch. Local Docker is unavailable, so Hassfest/HACS run after push. The
existing manifest-order and four HACS findings remain visible and are not fixed.
Hassfest checks this branch; HACS repository API checks target the default branch.

This coordinates commands issued by this integration, not external writers or
controller-side transactions. True remains pystove's confirmation, not a new
protocol guarantee. M02, timezone semantics, dependency, HTTP policy, timeout and
retry policies remain outside this change.
