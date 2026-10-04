# H04: report unconfirmed commands as action errors

Base: `6d708b69eabe450e78a58550e0685655ef42eec6` (completed H03).
Manifest `1.0.0b2`, ConfigEntry `VERSION = 2`, published `pystove==0.3a1`.
Target: HA `2026.10.0b0`, Python `3.14.6`, using the existing locked environment.

## Error contract and uncertainty

Previously, all ten action variants returned normally when pystove returned
`False`. Number/switch withheld their optimistic updates; button/time/datetime
ignored the result entirely. HA therefore received no action error even though
success was unconfirmed.

The internal `_commands.require_command_confirmation()` now raises a translated
`HomeAssistantError` only for the explicit boolean `False`, after the existing
single command await. There is no new interpretation of other return types.
The exception uses domain `hwam_stove`, key `command_not_confirmed`, and no
placeholders. It is an action error, not a `ServiceValidationError`: this does
not establish that user input was invalid.

The exception strings live under the root `exceptions` object in the existing
custom-integration translation files, with the same key and structure in all
three languages:

| Language | Message |
| --- | --- |
| en | The stove did not confirm the command. The command may already have been carried out. |
| de | Der Ofen hat den Befehl nicht bestätigt. Der Befehl kann bereits ausgeführt worden sein. |
| nl | De kachel heeft de opdracht niet bevestigd. De opdracht kan al zijn uitgevoerd. |

This reports missing confirmation, not known non-execution or rollback. Nothing
resends, undoes, reads back, or infers controller state from the result. Actual
execution remains unknown. In particular, this is not a reason to automatically
retry a command.

HA's official [action exception rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-exceptions/)
applies to entity platform actions as well as custom actions. Its
[translated exception guidance](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/exception-translations/)
and [exception translation structure](https://developers.home-assistant.io/docs/internationalization/core/#exceptions)
support this contract. Custom integrations use their
[language JSON files](https://developers.home-assistant.io/docs/internationalization/custom_integration/).

## Preserved behavior

| Variant | Existing pystove call, unchanged |
| --- | --- |
| Burn level | `set_burn_level(int(value))` |
| Night lowering on / off | `set_night_lowering(True / False)` |
| Remote refill alarm on / off | `set_remote_refill_alarm(True / False)` |
| Start | `start()` |
| Synchronize clock | `set_time()` |
| Night begin | `set_night_lowering_hours(start=value, end=cached_end)` |
| Night end | `set_night_lowering_hours(start=cached_start, end=value)` |
| Datetime | `set_time(value)` |

At `True`, number and switch keep their optimistic state feedback; the other
platforms keep their existing behavior. No coordinator refresh is added, at
either `True` or `False`. M02 stays open. Night-time counterpart calculation
(H05), datetime/timezone arguments (M07), and button availability (M03) are
unchanged.

At `False`, the integration writes/schedules no state, changes no local controller
value, and raises the action error. At an existing exception, the await exits
before the helper: the original exception, including `CancelledError`, propagates
with its identity and arguments intact.

The HA rule also recommends translating known library communication errors into
`HomeAssistantError`. That is a separate exception-mapping task, not part of the
authorized False-result correction. This change deliberately adds no catch-all
handler, aiohttp/timeout mapping, HTTP-status policy, retry policy, or timeout.

### HA button timestamp boundary

HA's final `ButtonEntity._async_press_action()` writes its last-press timestamp
**before** calling the integration's `async_press()`. Thus a real button service
call records the attempt even if H04 then raises an unconfirmed-command error.
This framework timestamp is not a controller-state value or a success
confirmation. The integration does not override this final method or roll back
the timestamp. Direct-method tests prove zero integration state writes; real
service tests explicitly prove the timestamp behavior alongside the raised error.

This was checked against the installed pinned HA code, verified byte-for-byte
against official source commit `64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5`:

- [Button action wrapper](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/components/button/__init__.py)
- [Entity service dispatch](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/service.py)
- [Exception API](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/exceptions.py)
- [Translation loading and formatting](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/helpers/translation.py)

HA's English exception formatter strips the final period; tests separately check
the unchanged literal language strings and HA's formatted exception text.

## Regression coverage and scope

`tests/test_h04_commands.py` covers all ten variants with direct True/False calls,
unchanged cancellation, unexpected errors, connection errors and timeouts, plus
real HA entity-service True/False calls. Each command executes exactly once with
the original arguments; all other command mocks must remain unused. Refresh
request spies, status-read counts and coordinator-data snapshots rule out
readback or inferred controller-state changes. Switch tests exercise both
directions from the opposite state. The normally disabled datetime entity is
enabled only in the test registry; its production default remains unchanged.

All three languages are loaded through HA's actual translation loader; exact
wording, placeholder parity, exception domain/key and unsupported claims of
non-execution/rollback are checked. No controller is used: factory simulation,
HTTP/DNS guards and blocked IP sockets remain active for the complete suite.

The ten H04 strict xfails become regular regressions. **25 -> 15 strict xfails**,
with zero XPASS allowed. H05 (2), M02 (10), M04 (1), M07 (1) and M08 (1) remain.
H01B remains open and its existing safety tests continue to protect that boundary.
B01, H01A, H01B, H02 and H03 regression files are unchanged.

Runtime edits are only five command platforms, three translation files and one
small internal helper. Integrity checks freeze the other nine runtime files to
the H03 base. Removing only the authorized helper imports, confirmation checks
and result assignments yields byte-identical original platform code. Existing
translation values and the 40 entity descriptions are retained. Earlier scope
guards and historical hashes are preserved. The German file retains its existing
CRLF line endings; diff whitespace checks recognize CR at end of line.

Foundation CI is unchanged. The existing metadata workflow additionally runs on
the H04 branch. Official pinned Hassfest/HACS jobs remain separate and visible:
the existing manifest-order error and four HACS findings are not fixed. Local
Docker is unavailable; those validators run after push. HACS's repository API
checks target the default branch; Hassfest checks this branch's actual files.

## Local verification, 2026-10-04

Full suite: **360 cases, 345 passed, 15 strict xfailed, 0 XPASS**, including 83
H04 tests and all 35 B01, 18 H01A, 10 H01B, 7 H02, 48 H03 and 7 isolation/environment
cases. The remaining known-defect test bodies are AST-identical to the H03 base;
the previous lifecycle, command and fixture files are byte-identical. The xfail
report differs solely by removal of the ten H04 cases. Repository-wide Ruff,
integrity checks, dependency consistency (156 packages) and whitespace checks
pass. No real controller access or command was performed.
