# A06 rc8 offline verification

Base rc7: b8b5c2490756fb55553e2185841167b4e37e65ab.
Validated in an isolated Python 3.14.6 environment with Home Assistant 2026.10.2,
pytest-homeassistant-custom-component 0.13.373, saynwerk-pystove 0.3.0rc3 and
Ruff 0.9.2. No production/hardware access. No multi-version matrix.

## Results

- Focused gate: **1005 passed, 0 xfailed, 0 XPASS**, 76.63 s.
- Includes 66 new A06 tests, 147 real-library-boundary tests and 35 B01 tests.
- Includes existing A02/A02.1/A04/A04.1/A04.2/A04.3/A05 suites, diagnostics,
  measurement statistics/Recorder, H01A/H01B/H02 lifecycle, M02 refresh,
  isolation and frozen-source integrity tests.
- Ruff: all checks passed. `git diff --check`: clean.
- ResourceWarnings treated as errors; real transport fixtures assert no loop
  errors and exactly-once session cleanup. Tests block real HTTP/DNS/IP sockets.
- Public PyPI rc3 owns `pystove` exclusively; original distribution absent.
  Installed package source hashes verified against the existing approved rc3
  contract fixture. Dependency version and protected 25-key mapping unchanged.
- No request/protocol/poll interval/retry changes. Local expiry and diagnostic
  callbacks issue zero requests. Invalid JSON exercised with actual rc3 parsing
  and simulated HTTP; failed status has exactly one GET, ordinary recovery one GET.
- 49 entity identities retained; no new entity/platform/translation or registry
  migration. Door, command platform and B01 migration source files unchanged.
- Frozen `_shadow_model.py` and `_analytics_model.py` byte-identical to rc7.
- rc7 real-file checkpoint migration tested with old file byte preservation,
  old model/benchmark evidence preservation, v4 reload, candidate invalidation
  after restart, corrupt checkpoint fail-closed behavior and existing storage
  failure/cancellation regressions. H01B remains OPEN.

## Reproduction

Use the repository's existing pytest isolation configuration and the above
versions. Run with ResourceWarnings elevated:

```sh
python -W error::ResourceWarning -m pytest -q \
  tests/test_a02* tests/test_a04* tests/test_a05_door.py tests/test_a06* \
  tests/test_o01_diagnostics.py tests/test_o02_statistics.py \
  tests/test_pystove_boundary.py tests/test_migration.py \
  tests/test_h01a_cleanup.py tests/test_h01b_safety_boundary.py \
  tests/test_h02_cancellation.py tests/test_lifecycle.py \
  tests/test_fixture_and_isolation.py tests/test_integrity.py \
  tests/test_m02_refresh.py
ruff check .
```

No independently validated physical-refill detector or production reliability
improvement is claimed. The new classifier remains observation-only POSSIBLE /
UNKNOWN. See A06_RESILIENCE.md for policy, historical separation and rollback.
