# Published Saynwerk dependency migration

Base: `27dfc8796df2d5aaa5aef9422d2665f4f2e65cb3`.
Branch: `compat/saynwerk-pystove-0.3.0rc1`.

The sole integration runtime change is the manifest requirement:
`pystove==0.3a1` → `saynwerk-pystove==0.3.0rc1`. The Python import namespace
remains `pystove`. Integration version, ConfigEntry version, implementation,
translations, API calls, and M02/M04 behavior are unchanged.

## Artifact and environment

The dependency was installed directly from the public PyPI index into a new
private Python 3.14.6 environment, without cache or a local-wheel/direct URL
requirement. The hash-locked supporting HA test dependencies were installed
from the existing local cache first, without either stove distribution.
All their pins and hashes remain unchanged.

- Wheel: `6017d0b14dfc904cbda82f899b99d2f45c9bf705361355a368a9526a6aad3044`
- sdist: `fcaec778ba9c0beadcf9dfea69d92bde178b583c9b6423bd05b914838f3764cc`
- Installed version: `0.3.0rc1`; sole namespace owner: `saynwerk-pystove`.
- Original `pystove` distribution absent; imported module paths, package files,
  metadata and source hashes match the published wheel.
- HA `2026.10.0b0`, aiohttp `3.14.3`, pytest `9.0.3`; `pip check` passes.

The test lock now selects the published package. The default test scenario is
`published`; earlier local-artifact scenarios retain their original evidence.
The integrity guard accepts exactly this manifest replacement relative to the
gate base, with all other runtime files byte-identical. Historical audit scope
checks remain enforced after normalizing that single approved replacement.

## Offline verification

- Complete suite: **640 passed, 11 strict xfailed, 0 XPASS**.
- Real-library boundary: **117 passed**.
- Known defects: exactly 10 M02 command-refresh cases and 1 M04 YAML case.
- H01A/H02/H03/H04/H05/M03/M07/M08 regression modules all pass.
- Real-library fixtures enforce zero loop errors, zero ResourceWarnings,
  exactly-once session/borrower cleanup, and open/read/close ordering, including
  failure and repeated cancellation paths.
- HTTP/DNS guards and pytest-socket forbid controller/network access during
  tests. Actual library methods use the existing in-memory transport.
- Ruff and runtime integrity checks pass. No hardware access occurred.

Reproduce in a clean Python 3.14.6 environment:

```sh
python -m pip install --index-url https://pypi.org/simple --require-hashes -r requirements-test.txt
python -m pip check
python -m scripts.pystove_gate environment published
python scripts/check_integrity.py
python -m pytest -q -W error::ResourceWarning
python -m pytest -q -W error::ResourceWarning tests/test_pystove_boundary.py
ruff check .
```

Foundation CI uses the same lock and default published scenario. The separate
pinned Hassfest/HACS workflow is run on the dedicated branch. Existing expected
findings remain out of scope: manifest key ordering; missing license, disabled
issues, missing repository topics, and missing `issue_tracker`. The existing
HACS action queries the repository default branch (`master`); Hassfest validates
the checked-out migration branch. Fresh workflow results accompany delivery.

Detailed local results are in `PYSTOVE_GATE_EVIDENCE.json` under
`published_dependency_migration`; earlier A/B and hardware reports are historical.

Next action is a separately authorized controlled HA deployment/upgrade, ensuring
that the old and new distributions do not coexist in the target environment.
This clean-install gate does not claim an in-place uninstall/upgrade was tested.
No deployment, merge, tag, release, publication, or hardware test is included.
