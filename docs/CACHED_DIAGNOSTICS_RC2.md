# O01 optional cached diagnostics and the unpublished rc2 gate

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
