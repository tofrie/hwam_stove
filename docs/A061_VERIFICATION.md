# A06.1 rc9 offline verification

Base: rc8 855cc958a5540eb9f8b4dff2b09b247d90de9156.
Isolated Home Assistant 2026.10.2 / Python 3.14.6, test framework 0.13.373,
Ruff 0.9.2. Installed saynwerk-pystove 0.3.0rc3 owns the pystove namespace
exclusively; original distribution absent. Approved installed source hashes
verified by the existing dependency gate.

Final focused gate: **1067 passed**, **0 xfailed / 0 XPASS**, 78.14 seconds.
Includes **62 A06.1**, **147 library-boundary**, **35 B01**, existing A02/A02.1,
A04/A04.1/A04.2/A04.3, A05/A06, O01/O02/Recorder, H01A/H01B/H02 lifecycle,
M02 readback, integrity and network isolation. ResourceWarnings are errors;
real-library fixtures assert no loop errors and exactly-once cleanup.

Ruff passed; diff whitespace checks passed. Tests block HTTP, DNS and IP sockets.
No production access, controller access, added request, retry, poll cadence change,
command or notification change. No multi-version/full release matrix.

Frozen formula/threshold file, factual A02 reducers/adapter, interventions,
coordinator, freshness, entity/sensor/command files, night-time handling and B01
migration were byte-compared to rc8. Only the shadow adapter, benchmark, new gap
policy, allowlisted diagnostics and manifest version change at runtime. 49 entity
identities and library protected 25-key contract remain unchanged. H01B remains OPEN.

Tests exercise the 11/15/30-second boundaries and epsilon neighbors, one/two/three
missed polls, outages, read-failure recovery, retained sparse 600-second history,
actual elapsed OLS, no synthesized rows, consumed opportunities, unknown edges,
conditional long-horizon endpoints, unknown horizon/outcome invalidation, bounded
uncertainty overflow, old-policy preservation, real atomic Store migration/reload,
corruption, duplicate callbacks and read-only diagnostics. Unhealthy evidence
cannot mask a later controller failure. Existing invalid-JSON/timeout/read-error
and display-recovery tests remain green.

Reproduce in the isolated environment:

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

The synthetic replay establishes policy behavior, not production forecast accuracy.
The number of historically recoverable rc8 censures is unknown. Conditional
cross-gap endpoint comparisons do not prove the next request, absence of an
intervening wood addition or a valid full-cycle accuracy result.
