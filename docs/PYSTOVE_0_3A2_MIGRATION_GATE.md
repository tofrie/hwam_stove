# Isolated pystove artifact A/B gate

## Decision and scope

**READY — technically ready for a separately authorized hardware/packaging step.**
This does not authorize changing the dependency pin, installing the candidate in
production, releasing, merging, tagging or publishing. No hardware was accessed.

The integration base is `7d7a9cd58cb434479f8180e5605b22961dfff857` on gate branch
`compat/pystove-0.3a2-gate`. All 20 runtime files, including the manifest and
translations, remain byte-equal to that base. Manifest version is `1.0.0b2`,
ConfigEntry version is 2 and the production requirement remains `pystove==0.3a1`.
The aggregate before and after is
`b9a6556d39089803e4ad531599053a166330f5a3343ac229f2790d44c3ffe8ed`.
Its input is every sorted runtime path, NUL, file bytes, NUL, hashed with SHA-256.

## Artifacts and environments

| Property | A: official baseline | B: approved candidate |
| --- | --- | --- |
| Wheel | `pystove-0.3a1-py3-none-any.whl` | `pystove-0.3a2.dev0-py3-none-any.whl` |
| SHA-256 | `0a7432f79e7e428ba04f897cb6a23108c294c6bf52dc6fdeb58108eff3408581` | `934c34123084d555cbcd0185cf67598818eec3fcc6db55502c3f28309c20453f` |
| Source | Official PyPI distribution | `tofrie/pystove`, `22b75dd8a6fcd680d2ced8747671207e62be790f` |
| Python | 3.14.6 | 3.14.6 |
| Home Assistant | 2026.10.0b0 | 2026.10.0b0 |
| aiohttp | 3.14.3 | 3.14.3 |
| pytest | 9.0.3 | 9.0.3 |
| pytest-homeassistant-custom-component | 0.13.368 | 0.13.368 |
| defusedxml | Not installed or required | Not installed or required |

The obsolete `fcb7e8ac46b1cc5d73de7c5879e49073aa21769b` candidate is not used.
The candidate's wheel runtime files match the approved commit byte for byte.
Wheel metadata, source hashes, the imported module's actual path, its distribution
provider and version are verified. B is a wheel installation, not a Git/editable
installation. For a local wheel the verifier hashes the wheel itself; uv can omit
the optional archive hash in `direct_url.json` even after hash-checked installation.

Both clean environments contain 156 distributions. Their complete actual version
inventories are in [PYSTOVE_GATE_EVIDENCE.json](PYSTOVE_GATE_EVIDENCE.json).
Only pystove differs. Both `uv pip check` runs report no conflicts; there are no
duplicate distributions or competing pystove import providers. Both distributions
declare only `aiohttp`; no extra integration dependency pins were introduced.

The prior candidate results were verified against the saved matrix, logs and
artifacts, without repeating the library matrix: Python 3.11.17, 3.12.15,
3.13.16 and 3.14.6 each had 1055 passed / 6 xfailed; installed-wheel runs each had
959 passed / 6 xfailed. Ruff, wheel/sdist and isolated builds were green. The
approved candidate includes the Python 3.14 cancellation cleanup fix.

## Public contract

`tests/fixtures/pystove_public.json` was captured using the official artifact's
actual methods and parser with synthetic transport. Both installed artifacts
must match it. No controller traffic or captured personal data is used.

- Both import paths (`pystove.Stove`, `pystove.pystove`) and all 31 referenced
  symbols are checked against an AST inventory of the integration runtime.
- The nine method signatures are identical: create, destroy, get_data and the
  six command methods. Full create returns the same concrete Stove type and
  identity attributes; destroy returns None.
- A successful get_data returns the same ordered 25-key dictionary with exact
  values and types, including naive datetime, time and timedelta objects.
- All 6 phase, 11 operation-mode and 5 night-state strings match. Individual
  and combined maintenance/safety alarm masks produce the same ordered lists.
- All ten HA command variants retain their exact serialized requests and bool
  True/False results. Task cancellation propagates through every command and
  get_data. Each command makes one simulated request, without readback/retry.

No policy for HTTP status codes was introduced. A synthetic HTTP 500 with an
`{"response":"OK"}` body still follows the existing True path for both a POST
and a GET command, through the real library and unchanged HA confirmation code.
This is a characterization of unresolved H6, not evidence of firmware intent.

## Full HA tests and real boundary

| Suite | A | B | Difference |
| --- | --- | --- | --- |
| Existing suite | 523 passed, 11 xfailed | 523 passed, 11 xfailed | None |
| Complete extended suite | 614 passed, 11 xfailed | 614 passed, 11 xfailed | None |
| Added real-library boundary | 91 passed | 91 passed | Explicit legacy/candidate error expectations only |

All 11 strict xfails remain exactly the same: 10 M02 command-refresh cases and
one M04 YAML-import case. No XPASS, unexpected skip, warning summary,
ResourceWarning or captured loop exception occurred. Existing B01, H01A, H02,
H03, H04, H05, M03, M07 and M08 tests remain green. Only artifact-provenance
assertions in three existing test files were parameterized; functional assertions
and known-defect markers remain unchanged. Baseline is still the default.

The new boundary fixture restores actual Stove.create and delegates observed
destroy calls to the actual implementation. Library methods, HA coordinator and
command logic execute normally. Only the library's aiohttp transport is simulated,
without modifying HA's aiohttp module. Delegating confirmation observers assert
that candidate failure results are exactly False. Additional unregistered entity
instances use the existing state-write sinks; availability tests separately use
real registered HA states. Controlled coordinator-constructor failures test the
specific pre-forward ownership boundary.

| Boundary | Verified outcome |
| --- | --- |
| Valid status | Real parser output accepted by the real coordinator |
| Invalid `{}` status | Candidate None -> UpdateFailed -> unavailable; A retains its KeyError |
| Recovery | Valid data restores availability; identical subsequent data keeps it available |
| Malformed POST / non-object JSON | Candidate False -> H04 `command_not_confirmed` |
| POST body ClientPayloadError / ServerDisconnectedError / TimeoutError | Candidate False -> H04 for every POST-backed command variant |
| Error-path request budget | Exactly one command; no retry, refresh or inferred nonexecution |
| Command cancellation | Original task cancellation propagates for all ten variants |
| Read cancellation | Library propagates; HA marks failed update and propagates active task cancellation; later regular read recovers |
| Identify failure / cancellation | Candidate finishes its own cleanup before returning an error; HA never owns a returned client |
| Successful create | HA owns a live client until unload or pre-forward failure |
| H01A | Actual failed first read and injected pre-forward errors/cancellation close once and remove runtime data |
| Normal unload | Existing platform-first unload closes the client exactly once |
| H02 | Create cancellation remains CancelledError, not ConfigEntryNotReady |
| H03 | Successful temporary client closes once; cancellation during real destroy waits for cleanup and propagates |
| H05 | True preserves the confirmed pair; False/error/cancellation leave it uncertain and block another edit; a later regular equal-valued read resynchronizes |

The legacy 0.3a1 factory does not close its transport after failed/cancelled
identification. A explicitly asserts that existing defect before the test harness
closes the synthetic transport. B must close itself exactly once. This is not an
HA workaround and does not conceal a candidate leak.

Repeated cancellation during candidate initialization cleanup was exercised on
Python 3.14.6, including cleanup errors and cleanup cancellation. The primary
failure remains authoritative. The same tests cover H03's temporary-client
cleanup, with no `CancelledError exception in shielded future`, loop-handler
message or double close. ResourceWarnings are errors for full-suite runs; boundary
fixtures additionally record loop contexts and ResourceWarnings during cleanup/GC.
These are offline resource-ownership checks, not proof of real socket/firmware
behavior under every physical network failure.

**H01B remains open and unchanged.** The post-forward partial-platform lifecycle
is outside H01A and this gate. A/B compatibility neither fixes nor reclassifies it.

## Six retained candidate cases

| Case | Classification | Reason |
| --- | --- | --- |
| H6 POST HTTP status | DEFERRED/NON-BLOCKING | Existing 0.3a1 policy is unchanged; the actual HA boundary introduces no new interpretation or retry. Command-status semantics still require separate hardware evidence. |
| H6 GET HTTP status | DEFERRED/NON-BLOCKING | Same preservation for state-changing GET commands; no raise_for_status workaround. A successful read-only status response cannot settle command semantics. |
| M1 missing live body | DEFERRED/NON-BLOCKING | HA never calls get_live_data; its scheduled reads use get_data. |
| M1 live length/index mismatch | DEFERRED/NON-BLOCKING | Same unused API; synthetic buffer expectations do not establish real framing. |
| M1 undersized live body | DEFERRED/NON-BLOCKING | Same unused API; no new caller is introduced. |
| M2 self-test retry budget | DEFERRED/NON-BLOCKING | HA never starts or polls Self-Test. |

Runtime inspection found no get_live_data/self_test invocation, HTTP-status
workaround, raise_for_status or command-retry loop. The `self_test` sensor option
and translations only label a reported operation mode; they do not execute a
Self-Test. All command paths were also checked dynamically for one request.
These classifications apply to this compatibility migration, not to resolution
of the underlying defects. None is a new integration migration blocker.

## Reproduction (offline tests; no hardware)

Use Python 3.14.6 and uv 0.12.20. Obtain the two wheels above in an artifact
directory, and a separate checkout of the exact approved candidate commit.
Do not point the commands at a stove. From this integration checkout, assign
`GATE_DIR` to an absolute directory outside this repository and `PYSTOVE_SOURCE`
to that separate source checkout. Keep the candidate wheel available because
the provenance check rehashes it. The existing hashed lock remains unchanged.

```sh
python3.14 -m scripts.pystove_gate artifacts \
  --baseline "$GATE_DIR/artifacts/pystove-0.3a1-py3-none-any.whl" \
  --candidate "$GATE_DIR/artifacts/pystove-0.3a2.dev0-py3-none-any.whl" \
  --source "$PYSTOVE_SOURCE"
python3.14 -m scripts.pystove_gate candidate-lock \
  --wheel "$GATE_DIR/artifacts/pystove-0.3a2.dev0-py3-none-any.whl" \
  --output "$GATE_DIR/requirements-candidate.txt"
uv venv --python python3.14 "$GATE_DIR/env-a"
uv venv --python python3.14 "$GATE_DIR/env-b"
uv pip sync --require-hashes --python "$GATE_DIR/env-a/bin/python" requirements-test.txt
uv pip sync --require-hashes --python "$GATE_DIR/env-b/bin/python" "$GATE_DIR/requirements-candidate.txt"
uv pip check --python "$GATE_DIR/env-a/bin/python"
uv pip check --python "$GATE_DIR/env-b/bin/python"
"$GATE_DIR/env-a/bin/python" -m scripts.pystove_gate environment baseline > "$GATE_DIR/environment-a.json"
"$GATE_DIR/env-b/bin/python" -m scripts.pystove_gate environment candidate > "$GATE_DIR/environment-b.json"
"$GATE_DIR/env-a/bin/python" -m scripts.pystove_gate compare "$GATE_DIR/environment-a.json" "$GATE_DIR/environment-b.json"
"$GATE_DIR/env-a/bin/python" -m pytest -q -W error::ResourceWarning --pystove-scenario=baseline --junitxml="$GATE_DIR/full-a.xml"
"$GATE_DIR/env-b/bin/python" -m pytest -q -W error::ResourceWarning --pystove-scenario=candidate --junitxml="$GATE_DIR/full-b.xml"
"$GATE_DIR/env-a/bin/ruff" check .
"$GATE_DIR/env-a/bin/python" scripts/check_integrity.py
"$GATE_DIR/env-a/bin/python" -m scripts.pystove_gate runtime
```

Environment construction needs the package index unless the complete wheel cache
is available (`--offline` was used for the local A/B installations). Test execution
itself is offline: pytest-socket blocks IP sockets and fixture guards reject DNS
and real HTTP. The transport permits only the synthetic `stove.invalid` target.
It has no automatic response for an unqueued command. Simulated `/start` cases
exercise existing tests only and never authorize a hardware request.

The existing Foundation workflow runs the complete baseline suite by default.
The candidate A/B gate is reproduced using the exact wheel and commands above;
it does not silently substitute a mutable Git ref or rebuild an unverified wheel.
No CI workflow or metadata-validation policy was changed.

## Hardware validation plan — NOT EXECUTED, separate approval required

### Stage A: identification, status and lifecycle

Use an isolated, explicitly approved local test environment with this exact wheel
and unchanged integration source. Do not alter the production manifest or allow
HA dependency management to silently replace the wheel with 0.3a1. Verify the
installed artifact in that environment first; agree the temporary test setup
separately before running it. No live execution tool is introduced by this gate.

1. Record the observed starting phase, firmware and remote version at test time.
   The previous measurement was firmware 3.34.0 / remote 1.2.0 / phase 5 Standby;
   it is historical evidence, not an assumption about the current stove state.
   Pause other controller file readers for the identification window. Retain
   normal physical safety supervision and controller protections.
2. Run full Stove.create with identification enabled. Expected paths are
   GET `/esp/get_identification`, GET `/esp/get_current_accesspoint`, POST
   `/open_file` and POST `/read_open_file`. Both POSTs carry
   `{"file_name":"info.xml","mode":1}`. These are intended file reads, but
   **opening a file changes the controller's open-file context**. This is not a
   purely GET-only or globally state-neutral probe. No file writes/deletes or
   unsolicited extra close/reset command may be added. Approve this distinction
   explicitly before the test, and avoid concurrent file operations.
3. Read get_data once, then destroy in finally; verify cleanup. Repeat one full
   create/read/destroy cycle. No parallel identification sessions or retries.
4. In the isolated HA test instance, verify Config Entry setup, populated states,
   unload/reload and cleanup. Each successful HA setup owns one client; normal
   unload must release it after platform unload. A config flow can create an
   additional temporary client, so count it separately if used.
5. Simulate loss of reachability on the **test host only**, if possible without
   disturbing stove operation or other safety systems. Observe unavailable and
   recovery after restoring that path and allowing normal polls. Do not power
   cycle the stove, send a controller command or test H01B partial-platform
   failure. If this network setup is unavailable, defer this subtest.
6. Record per request: method, endpoint category, HTTP status, normalized content
   type, byte length, structure/known field types and elapsed time. Record phase,
   firmware/remote versions, client create/destroy counts, HA availability and
   any ResourceWarning/loop error. Redact host/IP, SSID, name and mDNS; do not
   persist raw bodies or unfiltered exceptions containing controller data.

Stop on unexpected endpoints, required authentication, uncertain file mode,
unexplained phase changes, repeated requests, cleanup failures or artifact drift.
Manually inspect the observation before continuing. Stage A cannot prove that
command responses use HTTP status codes conventionally or that `response: OK`
is intentionally independent of status. Do not infer such a policy from 200-only
reads. Full create/file-context behavior itself needs this first hardware check.

### Stage B: commands — no authorization implied

Review Stage A before deciding whether any command test is necessary. The offline
gate provides no technical necessity to issue a mutating command immediately.
If command HTTP semantics must later be resolved, require a separate plan and
approval specifying the exact command, observed pre-state, reason it is safe in
that phase, expected effect, recovery method and metadata to retain. Do not use
`/start` for testing. Do not use set_burn_level without separate approval, and do
not assume it is safe or informative in Standby. A successful same-value command
also does not prove the semantics of error statuses or physical nonexecution.
No speculative substitute command is selected here.

The next action is solely review and separate authorization of Stage A.
