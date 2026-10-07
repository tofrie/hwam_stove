# O01 optional cached diagnostics and rc2 gates

## Final published-artifact gate

The published PyPI `saynwerk-pystove==0.3.0rc2` now passes the final gate on
integration commit `9f9a397599da0279eef2b46f68c08e97af650704`.
`PUBLISHED_RC2_GATE_EVIDENCE.json` records the new verification; the original
built-artifact A/B evidence below remains historical and unchanged.

Two fresh Python 3.14.6 environments installed the wheel directly from public
PyPI with caching disabled and its approved SHA256 enforced:
`1aa40ab8a5a53e31f999b0dd98c2742a37890269196b3fcb38faef81a9c09028`.
An independent PyPI wheel download, both pip installation reports, installed
source hashes and comparison with source commit
`0ddf5fa413969ff9037b54575a1be46bb91ece04` all match. Original `pystove` is absent;
only `saynwerk-pystove` owns the import. No editable, Git or local-wheel install
was used for either final environment.

HA 2026.9.4 and 2026.10.0b0 each pass **1506 tests**, zero failures, skips,
xfails or XPASS: boundary 147, B01 35, O01 56 (including 10 optional-cache
checks), O02 56, L04 29 and all earlier regressions. Ruff, integrity, pip check
and network-isolation checks pass. ResourceWarnings are errors; real-library
lifecycle checks report no loop errors, ResourceWarnings or double cleanup.

The test-only artifact verifier now permits rc2 index installs without
`direct_url.json`, retaining local-artifact verification for the earlier A/B
gate. Exact version, exclusive namespace ownership, installed file inventory
and every source hash remain required. The final gate separately checks the
downloaded wheel and pip reports. Negative checks reject wrong source/version,
ambiguous ownership and arbitrary direct URLs. Existing Foundation CI remains
unchanged and continues to reproduce the frozen approved source artifact.

All 22 integration runtime files, the manifest pin, registry identities and
completed ancestry remain unchanged. The existing golden boundary contract
retains the ordered 25-key `get_data()` result. Against published rc1, all
existing library methods except `get_data()` are AST-identical; its sole change
is the synchronous cache update before the unchanged return. Request/protocol
delta is zero. O01 reads the two optional values only from `cached_diagnostics`;
diagnostics never refresh or request missing data. No production/hardware access.

Verdict: **READY for a private HA update**. H01B remains OPEN. This gate does not
deploy the integration, publish hwam_stove, or establish new hardware semantics.

## Original pre-publication preparation

Base: `64710b2a90cefeb099926c649bdad6720d8b281d`, including M01, M02, M04,
M05, M06, O01, O02 and L04. The manifest prepares `saynwerk-pystove==0.3.0rc2`;
this branch must not be deployed before separate publication approval.

Only O01 diagnostics and the dependency pin change at runtime. Diagnostics read
`Stove.cached_diagnostics` synchronously, accepting only Wi-Fi version and remote
refill beep count. Existing version filtering and an explicit non-negative integer
check protect the allowlist. Missing/invalid values are represented as null,
without trying another status field or requesting an update. No entities change.
Door and service_date remain excluded, as do all existing identifiers/secrets.

The library keeps the ordered 25-key status result unchanged and replaces its
separate copied cache after a successful status parse. Optional invalid/missing
values clear, while failed/cancelled reads retain the last successful snapshot.
O01 reports the existing coordinator last_update_success alongside cached values;
retained values are never claimed current. Cache-only changes do not cause extra
entity callbacks under always_update=False. There are zero additional requests,
no command, protocol, polling, identity or lifecycle changes. H01B remains OPEN.

## Exact artifact A/B evidence

`CACHED_DIAGNOSTICS_RC2_EVIDENCE.json` records the artifact hashes, source commit,
ancestry, runtime hashes and all test groups. A uses the published rc1; B uses the
SHA-verified built rc2 wheel in fresh Python 3.14.6 environments. Original pystove
is absent, and only saynwerk-pystove owns the import. The installed distribution
inventories differ only in rc1 -> rc2. The original official 0.3a1 public-contract
snapshot is unchanged and remains checked through actual parsing/HTTP doubles.

Both HA 2026.9.4/framework 0.13.367 and HA 2026.10.0b0/framework 0.13.368 pass
1506 tests on A and B, including 147 boundary and 35 B01 tests. No xfail, XPASS,
skip, loop error or ResourceWarning. HTTP/DNS and IP sockets remain blocked.
The environment assertion now accepts exactly these two verified HA/framework
pairs instead of requiring a temporary production-version test checkout.

Reproduce B with a clean environment, the pinned test dependencies and the exact
rc2 wheel, then run `python -m pytest -q -W error::ResourceWarning`.
Use `--pystove-scenario=published` explicitly for A; rc2 is now the default gate.
The artifact verifier checks version, sole namespace ownership, file URL, wheel
SHA and every installed source hash. Full packaging checks live in pystove.

## CI while rc2 is unpublished

Foundation CI checks out the exact candidate commit recorded in the artifact
fixture, builds using the existing pinned tools and SOURCE_DATE_EPOCH, and checks
both wheel and normalized sdist hashes before installing. No moving Git dependency
is added to the integration: its requirement stays a versioned PyPI distribution.
The test lock uses the verified wheel file only inside the isolated gate. This
workflow has contents:read only and contains no upload/publishing job.

`prepare_rc2_gate.py` is also executed locally against the committed source;
its artifacts must be byte-identical to the four Python matrix builds. Publication,
production installation, hardware tests and changing H01B are outside this phase.
