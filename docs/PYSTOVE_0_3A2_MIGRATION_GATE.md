# Isolated pystove artifact A/B gate

## Decision and scope

**READY — offline compatibility gate passed. Stage A success-path hardware
validation PASSED, as reported by the operator.** This records the approved
single/repeated library cycles and isolated HA setup/reload; it does not claim
outage/recovery or command validation. The agent did not access hardware.
Production remains on `pystove==0.3a1`. No release, publication, tag, merge or
production dependency change is authorized by this evidence update.

This rerun starts from gate commit `d4fa621758d8b3d110e73517ba6a3395c83cfee8`.
The unchanged runtime base is `7d7a9cd58cb434479f8180e5605b22961dfff857` on branch
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
| SHA-256 | `0a7432f79e7e428ba04f897cb6a23108c294c6bf52dc6fdeb58108eff3408581` | `7c274a0ea1dc6ff9a2d0c79489d013842829a2da898fe144cad9a816e071bb02` |
| Source | Official PyPI distribution | `tofrie/pystove`, `eec0d60a0120140171a7ef2a5b6c6005da04c51b` |
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

The exact candidate results were verified against the saved matrix, logs and
artifacts, without repeating the library matrix: Python 3.11.17, 3.12.15,
3.13.16 and 3.14.6 each had 1110 passed / 6 xfailed; installed-wheel runs each had
1004 passed / 6 xfailed. Ruff, wheel/sdist and isolated builds were green. The
approved candidate includes the Python 3.14 cancellation fix and bounded info.xml
close cleanup. Its known-failure module was rerun: 18 passed / 6 xfailed / 0 XPASS.
The wheel from the verified build is reused; no mutable ref or replacement build
is substituted. The isolated A/B environments are reused and checked against the
hashed locks; only B's candidate artifact was reinstalled, without dependency
changes.

## Public contract

`tests/fixtures/pystove_public.json` was captured using the official artifact's
actual methods and parser with synthetic transport. Both installed artifacts
must match it, allowing exactly one additional candidate identification request:
`GET /close_file` without body/query and with redirects disabled. The official
golden fixture remains byte-for-byte unchanged. No controller traffic or captured
personal data is used.

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
| Original tests within the full run | 523 passed, 11 xfailed | 523 passed, 11 xfailed | None |
| Complete extended suite | 640 passed, 11 xfailed | 640 passed, 11 xfailed | None |
| Real-library boundary | 117 passed | 117 passed | Explicit legacy/candidate cleanup and error expectations |

All 11 strict xfails remain exactly the same: 10 M02 command-refresh cases and
one M04 YAML-import case. No XPASS, unexpected skip, warning summary,
ResourceWarning or captured loop exception occurred. Existing B01, H01A, H02,
H03, H04, H05, M03, M07 and M08 tests remain green. The original foundation
assertions and known-defect markers remain unchanged. Boundary request counts
and identification expectations now include the approved candidate close GET.
Baseline is still the default.

The rerun adds 26 info.xml cases per artifact: confirmed/rejected/missing/lost or
cancelled open response; read None and invalid XML; and read failure/cancellation
or cancellation during close through both HA setup and config flow. The candidate
must finish its one close attempt before session cleanup, including repeated
caller cancellation and secondary close exceptions/timeouts. Original errors and
cancellation remain primary. Unconfirmed open never triggers speculative close.
Baseline controls explicitly retain 0.3a1's missing-close/factory-leak behavior.
Since A has no close stage, its close-cancellation controls cancel during read.
Secondary close timeouts are simulated at the transport boundary; the actual
five-second deadline was already tested in the exact candidate's library suite.

The transport now models a separate close-only wrapper borrowing the original
connector/headers/cookies. Disposing it cannot close the owned client session.
Its retry flag must be disabled, its only permitted request is the close GET, and
all wrappers must be released before the owning session is closed. Existing
H01A/H02/H03 and H04/H05 assertions remain in the complete A/B run.

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

## Stage A hardware evidence — operator-reported PASS

Recorded on 2026-10-04 from the operator's reports in this conversation; the
execution date was not separately supplied. These are user-attested results,
not hardware runs executed or independently observed by the agent. No raw HA
report was attached to the final attestation. The machine-readable record is
`stage_a_hardware` in [PYSTOVE_GATE_EVIDENCE.json](PYSTOVE_GATE_EVIDENCE.json).
No automatic test is presented as proof that physical hardware ran.

Exact candidate: `eec0d60a0120140171a7ef2a5b6c6005da04c51b`, version
`0.3a2.dev0`; wheel SHA-256
`7c274a0ea1dc6ff9a2d0c79489d013842829a2da898fe144cad9a816e071bb02`.
HA gate under test: `52bca6e562a3274355fcd2c5b6d2153f67fb3748`.
Reported controller firmware: **3.34.0**, remote: **1.2.0**.

| Hardware exercise | Operator-reported result |
| --- | --- |
| First library cycle | PASS; create, open/read/close, get_data, destroy/session cleanup; Standby; 1444 ms; no exception |
| Three sequential library cycles | 3/3 PASS; every cycle completed identification, status and cleanup; no exceptions/cleanup failures |
| Isolated HA setup and first refresh | PASS; 40 entities |
| HA reload including unload | PASS; second refresh successful; 40 entities; registry stable |
| Final HA unload/session cleanup | PASS; 2 create attempts, 2 destroys; both identification sequences completed open/read/close |

The prepared diagnostics verify the exact wheel and installed source bytes before
I/O. They permit each identification/status request only once, disable redirects
and transport retries in the diagnostic process, and require exclusive file access.
The HA exercise uses the real in-process HA 2026.10.0b0 config-entry/platform
lifecycle with temporary integration files and ephemeral test registries; command
methods/services are blocked. All 40 entities are enabled only in that registry.
Automatic dependency installation and periodic polling are suppressed, so this
is not a test of normal production package resolution or continuous polling.
These guards do not change the candidate's ordinary runtime transport policies.

Expected identification requests remain GET `/esp/get_identification`, GET
`/esp/get_current_accesspoint`, POST `/open_file`, POST `/read_open_file`, and
GET `/close_file`. Both POST payloads are exactly
`{"file_name":"info.xml","mode":1}`. File requests are ordered open -> read ->
close; the two identification GETs may run alongside the file task. Each cycle
then uses the normal GET `/get_stove_data`; destroy closes the local session.
Open/close affect temporary controller file context. No commands, file writes,
file deletes or concurrent file operations were part of the approved probes.

A received close response is not proof of undocumented firmware context-release
semantics. A lost open response can remain ambiguous; no speculative close is
permitted. Real loss-of-reachability/recovery, failure/cancellation behavior on
hardware, concurrent file access, other firmware, and command-status semantics
remain untested. Existing offline failure tests retain their separate evidence.
The optional test-host-only outage/recovery subtest is **DEFERRED**, not passed;
it needs a separate safe plan/approval or explicit acceptance of deferral before
production rollout. The recorded Stage-A success scope is complete.

No IP, SSID, MAC, controller name, raw XML/body or unfiltered exception is stored.
Production runtime, manifest and dependency pin are unchanged. Existing test
cases, public-contract fixtures and all xfail markers are unchanged by this
hardware-evidence update.

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

The next action is the packaging/ownership decision below. Further hardware
execution, particularly Stage B, still requires separate authorization.

## Release/packaging decision — analysis only

Recommend **0.3.0rc1** for the first release candidate of the 0.3 series
(`0.3rc1` is equivalent). `0.3a2` would still be an alpha; `0.3a2rc1` is not a
valid combined prerelease suffix. No version has been changed. A version change
produces a new artifact/hash: do not relabel the existing wheel or transfer its
hash/evidence without verifying the new package. See the
[PyPA version scheme](https://packaging.python.org/en/latest/specifications/version-specifiers/#pre-releases).

The public [pystove project](https://pypi.org/project/pystove/) lists `mvn23` as
maintainer. No PyPI role or authorized Trusted Publisher for this fork is evidenced
in this session; GitHub ownership does not grant PyPI upload rights. Publication
under `pystove` must be treated as unavailable until an existing project owner
grants the required role/publisher configuration. The user's private PyPI account
permissions were not inspected; this is not proof that no private grant exists.
No login, credential inspection, upload probe or contact with the maintainer was
performed. [PyPI roles](https://pypi.org/help/#project-roles) distinguish project
Owners/Maintainers from unrelated repository permissions.

The smallest path, if permission is granted, is an owner-approved release under
the existing distribution/import name. Without it, recommend an explicitly named
fork on PyPI, for example **pystove-tofrie**, then an exact HA requirement such as
`pystove-tofrie==0.3.0rc1` for the approved RC rollout. This name is a proposal,
not a reserved or verified available project. For general production deployment,
prefer the subsequently verified stable version over an unreviewed RC.

A distribution-only rename can retain `import pystove`, but that is not a clean
automatic upgrade in HA's shared environment: old `pystove` may remain installed,
and the two distributions would own the same import files. `pip check` alone
does not prove import ownership. It would require a proven single-provider
migration/removal plan and confirmation no other integration needs upstream.
For a maintained fork, prefer its own import namespace (e.g. `pystove_tofrie`),
plus narrowly scoped mechanical HA imports and renewed API/packaging checks in
a later approved phase. No protocol or entity behavior change is needed for that
packaging route. [PyPA distinguishes distribution and import names](https://packaging.python.org/en/latest/discussions/distribution-package-vs-import-package/).
Do not ship two packages that silently compete for `pystove` files. Neither a
permanent Git dependency, vendoring, nor a production skip-pip override is the
recommended solution. HA supports exact normal package requirements in its
[manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/#requirements).

### Six xfails: scoped RC release disposition

All are in candidate `tests/test_known_failures.py`; no markers are changed.

| Exact case | Disposition for this compatibility RC |
| --- | --- |
| `test_command_rejects_http_error_status[post-command]` (H6) | DEFERRED: HTTP 500 + OK body still returns True, as in baseline. Stage A cannot resolve command semantics. |
| `test_command_rejects_http_error_status[get-command]` (H6) | DEFERRED: same existing defect for GET commands. No new command guarantee or status policy. |
| `test_get_live_data_rejects_missing_body` (M1) | DEFERRED: unused by hwam_stove; missing body reaches bytearray. |
| `test_get_live_data_length_guard_matches_legacy_index_ranges` (M1) | DEFERRED: unused API; 960-byte synthetic input conflicts with 120-byte guard. |
| `test_get_live_data_rejects_undersized_accepted_body` (M1) | DEFERRED: accepted 120-byte input can exceed index bounds. |
| `test_self_test_empty_replies_obey_retry_budget` (M2) | DEFERRED: unused Self-Test API can poll empty replies without its intended budget. |

**None of these six blocks the narrowly scoped, documented compatibility RC.**
This is a release recommendation, not resolution of the defects or certification
of every library API. H6 still affects HA command confirmation; transparent known
limitations and acceptance of unchanged behavior are required. Do not advertise
HTTP-status correctness, functioning live-history or bounded Self-Test polling.
Any changed outcome, unexpected XPASS, unbounded new retry, artifact mismatch,
resource leak or import-provider collision blocks the proposed release path.
The six xfails are not a complete inventory of all audit findings. In particular,
ordinary transport timeout/retry and file-concurrency policies remain unchanged;
the diagnostic's stricter transport guards are not production fixes.

### Remaining steps before a production dependency change

1. Confirm the publishing right/name and import-namespace strategy; obtain explicit
   approval for the packaging/release scope. No upload can infer its own authority.
2. In a separate packaging change, set the RC version and accurate fork/project
   metadata, retain upstream attribution and GPL license/source files, declare the
   supported Python floor consistent with 3.11-3.14 testing, and write scoped release
   notes including all six known failures. Resolve provider collisions if renaming.
3. Build wheel and sdist in isolation, verify metadata/contents/source correspondence,
   run Twine checks, the Python 3.11/3.12/3.13/3.14.6 matrix and installed-wheel tests,
   and record the new immutable artifact hashes (6 strict xfails, 0 XPASS).
4. Rerun the existing A/B public-contract and HA gate on that exact release artifact,
   adjusting only provenance and any explicitly approved mechanical import mapping.
   Exercise clean installation and upgrade from 0.3a1 with actual dependency
   resolution; verify provider ownership, 40 entities, stable registries and rollback.
   The previous HA probe deliberately skipped normal dependency installation.
5. Carry forward hardware evidence only with a documented behavior-preserving diff.
   After packaging/import changes, run a separately authorized isolated setup/reload
   smoke check of the release artifact. Resolve the deferred host-only outage/recovery
   check through testing or explicit risk acceptance; no automatic command test.
6. Obtain publication approval, publish the tested artifacts through the authorized
   PyPI project/publisher, then download and verify hashes/installation. Publication,
   tags and releases are future actions, not performed by this evidence commit.
7. Only after explicit migration approval, change the production requirement in a
   separate hwam_stove change (and approved imports if namespaced), run Foundation,
   metadata/HACS and upgrade/rollback checks, and stage the deployment. Prefer a
   validated stable package for general production, or explicitly approve an RC pin.
   M02/M04 and other independent findings remain outside that change.
