# A05 physical stove door — private 1.0.0rc3

Integration base: 7bad773f16bb8488e717be46b0c9e2e50af3100e (A02.1).
Dependency: unpublished saynwerk-pystove==0.3.0rc3; import stays pystove.

Adds exactly one binary sensor: door_open, DOOR, DE Ofentür / EN Stove door /
NL Kacheldeur, enabled by default, associated with the existing stove device.
Identity is the existing ConfigEntry-ID + door_open scheme. No registry migration.
The 47 previous entity identities remain, for 48 total. The existing door-open-too-
long sensor remains a PROBLEM alarm with its exact identity and alarm mapping.

Only existing coordinator reads feed the sensor. The coordinator immediately copies
cached_status after its serialized get_data call, requires the matching next
read generation, and accepts only valid=True plus an actual boolean. A retained,
absent, malformed or mismatched snapshot yields unavailable. A failed/cancelled
coordinator update clears the door; cancellation explicitly notifies because HA
can propagate cancellation before its ordinary completion callback. Door-only
changes notify listeners even when the protected 25-key data compares equal;
M02 reconciliation is coalesced into the same callback. Changed data/recovery use
HA's normal notification. No new polling, timers, requests or commands are added.

HA keeps its last accepted observation while the normal read is in flight, just
as for other polled data; failure/cancellation invalidates it. There is no invented
age timeout. This sensor is the last observed physical state, not continuous door
monitoring: short transitions can be missed at the unchanged active/Standby cadence.
A new/reloaded client starts without door state and must obtain valid status.

A02/A02.1, refill-request accounting, H05 and command behavior are untouched. Door
opening is not proof of wood addition. H01B remains OPEN. O01 diagnostics' existing
allowlist and cached_diagnostics behavior remain unchanged.

Verification targets Python 3.14.6 / HA 2026.9.4, with network blocked: focused door
and generation tests, all existing compatibility tests, B01, library boundary,
A02.1, identity upgrade from 47 to 48, reload, failure/cancellation and equal-data
notifications. RC2 public-boundary tests remain available with --pystove-scenario rc2;
without cached_status the new entity remains unavailable. No multi-version matrix
or real hardware test is claimed. Release evidence is recorded separately.

The private ZIP contains only custom_components/hwam_stove. It is not self-contained:
rc3 is not yet on PyPI, so the verified wheel must first be installed into the HA
Core Python environment (not a terminal add-on). A later HA Core container
recreation can discard such a private install. Do not rely on automatic PyPI
resolution until rc3 is published separately. See the delivered private installation
instructions; no production operations are performed by this phase.

Public integration redistribution remains blocked pending upstream licensing
clarification. No license is invented; this deliverable is for the authorized
private test only.

## Offline verification (2026-10-12)

- Python 3.14.6 / HA 2026.9.4: 1,684 passed, no xfail/XPASS;
  includes 147 real-library-boundary, 35 B01 and 17 door-specific tests.
- Same integration with published rc2: 265 boundary/registry/entity tests pass;
  the new door remains unavailable without the new library cache.
- Clean rc2/rc3 HA environments differ only in saynwerk-pystove version.
- Library Python 3.14.6: 1,235 passed, the existing six strict xfails;
  installed wheel: 1,076 passed, the same six xfails; no XPASS.
- Ruff, runtime/source restoration checks, offline package validation,
  reproducible isolated wheel/sdist build and strict Twine check pass.
- Test network isolation remains enforced; no hardware or production access.

Library source: 9aebfd71a5d58cb00c4b29baf3b308310d9947d3.
Wheel SHA256: 03dc8a11e0db7a8c2e3acff4a03ae6c0a41030dd8247c690755387a6eaadc85a.
Sdist SHA256: a49548c8c7f11c27cc30679740eda0d4dba06928f474675c570ab9043d420869.
CI builds that exact source and rejects artifacts with different hashes. The rc3
publishing workflow is not prepared; no PyPI upload, tag or release was performed.
