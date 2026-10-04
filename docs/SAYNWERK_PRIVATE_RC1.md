# Saynwerk HWAM 1.0.0rc1 — local preparation only

Base: `801d9bc9ed137872b07e222c178734281da2e2b5`.
Branch: `release/saynwerk-hwam-stove-1.0.0rc1`.
Domain/imports unchanged; dependency remains `saynwerk-pystove==0.3.0rc1`.
H01B is **OPEN**; M02/M04 remain deferred. No hardware or production access.

## Distribution boundary

Private local testing is permitted by the user's scope and is not blocked by the
missing license in this preparation. No license is invented or added. Public
redistribution/publication remains blocked until licensing of the inherited
`mvn23/hwam_stove` code is clarified.

HACS supports public GitHub repositories only. `tofrie/hwam_stove` is currently
public. Publishing a GitHub prerelease there is public distribution even if only
one person intends to install it. A draft release is not downloadable through HACS.
Consequently there is no supported private HACS-release route here. This branch
is prepared locally; it has not been pushed, tagged or released. A private test
can use the previously prepared local-install procedure independently of HACS.

## Metadata changes and verification

- Manifest version `1.0.0rc1`, Hassfest ordering, documentation and issue tracker
  directed to the fork. Integration display name, domain, codeowners and dependency
  are otherwise unchanged.
- HACS display name distinguishes Saynwerk; minimum HA is the tested `2026.9.4`.
  Hide the default-branch download to favor an explicitly selected future release.
- The integrity guard permits only these exact metadata changes and freezes all
  executable sources/translations to the validated base.
- Metadata CI is enabled for the preparation branch. Removing the explicit HACS
  repository override lets push events validate the pushed ref rather than stale
  `master`. No checks are ignored and no workflow publishes anything.
- HA 2026.9.4 / Python 3.14.6: **640 passed, 11 strict xfailed, 0 XPASS**.
  Includes **117 real-library-boundary** and **35 B01** tests. The existing two
  environment-version expectation substitutions are applied only to the isolated
  production-version gate copy, as in the accepted previous gate.
- Ruff and integrity checks: PASS. Installed Saynwerk distribution and exact import
  source ownership are verified by the existing published-artifact contract.
- Official Hassfest from HA 2026.9.4, run locally: **0 invalid integrations**.
  No Docker/GitHub CI run is claimed; extra validator tools are isolated from the
  gate environment. Full integration validation ran, without skipping plugins.
- Official HACS 2.0.5 validators with local files and captured public metadata:
  manifest, hacs.json, README, description, archived and brands checks PASS.
  **Issues disabled / no topics** remain repository-setting failures. The separately
  executed current official license validator fails on missing license as expected.
  This is a local validator run, not a successful remote HACS Action.

Repository settings are prepared, not changed. When separately appropriate:

```sh
gh repo edit tofrie/hwam_stove --enable-issues
gh repo edit tofrie/hwam_stove --add-topic hwam --add-topic smartcontrol --add-topic saynwerk
```

Generic topics alone can be filtered by HACS; retain these substantive topics.
No validator exemption should conceal the licensing/publication boundary.

## Exact future tag/release plan — blocked, not executed

1. Clarify public-distribution licensing and obtain separate publication approval.
2. Enable the prepared GitHub settings; verify HACS and Hassfest against the final
   release commit, not stale `master`. Recheck the final commit's metadata-only
   runtime delta against the base and its exclusive Saynwerk dependency.
3. Tag **`1.0.0rc1`** at that exact approved metadata commit, not at the old base.
   Use GitHub release title **`Saynwerk HWAM Smart Stove 1.0.0rc1`**, marked
   **prerelease**. Standard repository layout is sufficient; no ZIP release asset
   or packaging change is needed. Tag alone does not provide a HACS release.
4. Ensure new-repository discovery also sees valid fork metadata on its default
   branch; a first prerelease is hidden until HACS prereleases are enabled. Do not
   let HACS select the old default branch or an inherited tag accidentally.
5. No merge, tag, draft release, release or upload occurs as part of this preparation.

## Future HACS switch while keeping the existing HA entry

These steps are conditional on a legitimately available release, separate production
authorization, the existing backup/VM 100 snapshot and a captured baseline of
**one config entry, two HWAM devices and 38 original registry entities**.

1. Pause HWAM commands/automations and HACS updates. Temporarily disable the existing
   **HWAM config entry**, keeping its ID. Do not delete it or its devices/entities.
2. In **HACS**, uninstall the downloaded `mvn23/hwam_stove` repository, then remove
   its custom-repository registration if present. This removes integration files
   and HACS's own repository/update device; it does not delete the HWAM config entry.
   Do this BEFORE installing the fork: uninstalling the old repository afterward
   would remove the same `custom_components/hwam_stove` directory used by the fork.
3. Add `https://github.com/tofrie/hwam_stove` as custom repository, category
   **Integration**. Enable **Show beta versions**, choose **1.0.0rc1** explicitly,
   and download. Do not use Add Integration or the README new-entry button.
4. Keep the HWAM entry disabled while completing the separate controlled Core-Python
   dependency migration. HACS does not uninstall the original Python distribution.
   Remove original `pystove` before installing `saynwerk-pystove==0.3.0rc1` from
   public PyPI. Verify exact approved wheel/source, one import owner and no stale
   imported original client before re-enabling. HACS-only clicking cannot guarantee
   this namespace transition. Use the established maintenance/restart procedure.
5. Restart only after the target files and dependency are verified; re-enable the
   SAME HWAM entry. Assert B01 migration preserves the two device IDs and all 38
   original entity registry IDs, entity IDs, associations and customizations.
   Only expected B01 unique-id/device-identifier changes are allowed. Document any
   legitimate additions separately; never require exactly 40 total entities.
6. Validate first refresh, identification open/read/close, one controlled unload/
   reload, second refresh and no new integration/resource errors. Keep updates
   manual during validation. On ambiguity/failure, stop and use the prepared rollback;
   never retry partial setup on the assumption that H01B has been solved.

Sources:
- https://www.hacs.xyz/docs/faq/private_repositories/
- https://www.hacs.xyz/docs/publish/start/
- https://github.com/hacs/integration/blob/2.0.5/custom_components/hacs/repositories/base.py
- https://github.com/hacs/integration/blob/2.0.5/custom_components/hacs/websocket/repository.py
