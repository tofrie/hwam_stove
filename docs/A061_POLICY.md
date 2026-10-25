# A06.1 — rc9 prospective sparse forecast observations

Base rc8: 855cc958a5540eb9f8b4dff2b09b247d90de9156.
Library remains saynwerk-pystove 0.3.0rc3. 49 entities; H01B OPEN.

## Audit and historical recoverability

See A061_AUDIT.md. A failed 10-second read was a hard A04/benchmark boundary.
Active completed intervals >11 seconds, late scheduling, restart, commands,
invalid inputs and missing door/alarm coverage could also censor observations.
A02 already distinguishes factual coverage from retained display values.
Diagnostics do not create events; counters count different things. The inner
failed-read handler and outer coordinator may report one failure twice, but
closed benchmark cycles require an active cycle and intervention rows require
an observation anchor. Treating all diagnostic gap counts as Wi-Fi outages is
unsupported.

The production figures supplied by the user are aggregate reports, not a
replayable event stream. No corresponding production Store is available locally.
rc8 closed comparisons omit their sample arrays; bounded ledgers evict older
records. We cannot recover the duration distribution or compute how many of the
206 communication/missing-observation censures, or 30–120-minute forecasts, would
survive this policy. Previously censored records are NOT rescored. The existing
A03 capture is not a substitute for the later rc8 production session.

## Two independent coverage questions

Numerical policy: `a061-sparse-observations-v1`.

| Accepted-observation spacing, active | Numerical class | Edge coverage |
| --- | --- | --- |
| <=11 s, no failed read | NORMAL_JITTER | Existing sampled contract |
| >11..15 s | NORMAL_JITTER | EDGE_SENSITIVE_GAP |
| >15..30 s | SHORT_GAP | EDGE_SENSITIVE_GAP |
| >30 s | HARD_GAP | Unknown |
| Any failed read, even recovery <=11 s | Classified at recovery | Unknown |
| Restart/reload/command/storage error/clock discontinuity | HARD_GAP | Unknown |

15 s allows half the existing 10-second cadence of scheduler variation. 30 s
bounds recovery to at most two missing 10-second samples; three missing polls
(40 s) are hard. These are explicit analytical policy choices, not measured
firmware guarantees or thresholds fitted to outcomes. Standby keeps its existing
61-second allowance; that is not an active Burn prediction window. Missing
required inputs remain a hard boundary. Classification uses UTC and monotonic
intervals and rejects clock disagreement. No polling or timeout setting changes.

## Numerical model

The frozen OLS uses actual timestamps and elapsed durations, not sample indices.
Only real observations survive a bounded same-session Burn-to-Burn interval;
there is no interpolation, forward fill, synthetic temperature plateau or
changed temperature/phase threshold. The prediction model remains
`a04-phase-matched-r1-v1`; `_shadow_model.py` is byte-identical to rc8.

A failed read immediately removes outcome edge anchors and invalidates an issued
prediction for causal scoring. It leaves numerical history temporarily retained
until recovery establishes the interval bound. Display availability still follows
rc8. If recovery is too late or inputs/session/phase no longer support the window,
the window is reset. The consumed-opportunity flag is retained across short gaps:
a gap itself cannot cause a duplicate prediction. Without a consumed opportunity,
sparse real history can satisfy the original 600-second numerical requirement.

A02/A02.1 and intervention observations retain their strict edge accounting.
There is no cross-gap false→true request count, inferred door timestamp,
Glow→Burn intervention, session transition or physical wood-addition claim.
A recovery snapshot with door open establishes only a left-censored possible
intervention. A new directly observed false→true edge later can be observed in
the normal way; an alarm already true on recovery is not replayed as an edge.

## Per-horizon benchmark

Policy: `a061-observed-horizon-endpoints-v1`, benchmark schema 3.

The existing native forecast remains Glow-only; no new meaning is assigned to
countdown fields in other phases. Historical forecast rows can survive short
uncertainty elsewhere in the buffer.
A horizon query uses the last actual observation at or before that timestamp,
still at most 11 seconds old. Queries inside a missing interval are invalid;
there is no forecast interpolation. Stability-window metrics remain invalid if
any part of that window is unknown.

A directly observed later false→true outcome can support a **conditional endpoint
comparison** with a prior observed native forecast. A gap means the request might
not have been the next request and an intervention might have been missed. Such
cycles have `qualified=false`, are excluded from eligible metrics/rankings, and
are explicitly labelled `conditional_observed_endpoint_only` in diagnostics.
No claim of useful next-request accuracy is derived from them. Frozen-model
comparisons still require its own valid scored issuance; an invalidated issued
Saynwerk forecast cannot become valid just because the benchmark kept its row.

An uncertain outcome edge closes the active comparison with
`outcome_edge_unknown`, without an actual timestamp or error score. Known possible
interventions still censor the whole affected comparison. Restart, long gaps and
commands remain hard boundaries. At most 128 uncertainty intervals are kept per
active cycle; exceeding capacity censors it rather than forgetting uncertainty.
Existing sample/ledger bounds remain in place.

`comparisons_preserved_after_short_gap` counts valid source/horizon (or final)
endpoint comparisons whose supporting observation preceded a retained uncertainty
interval. It is a prospective retained-evidence counter, not a count of rescued
historical rc8 records or proven next requests. Unknown outcome edges have their
own counter. Gap-class counters are fixed-key, persistent and capped at 65535;
longest short gap and cause categories are exposed without raw timelines.

## Persistence and policy attribution

Outer schema 5 uses `hwam_stove.shadow_v5.<entry_id>`. When v5 is absent, load v4;
retain the rc8 benchmark in `rc8_archive` and the existing rc7 archive separately.
Completed archive records/scores remain unchanged. A pending old comparison is
closed only as a restart boundary, never scored. Original v4/v3 files are not
modified. New prospective benchmark counters start separately with the new policy.
The original A04 engine ledger and accumulated evaluation evidence remain intact;
the formula version and its existing eligibility rules are unchanged. New numerical
acceptance policy is separately visible in `forecast_gap_policy`; no old model row
is relabelled as rescued/valid. Reported all-scored model aggregates remain historical
aggregates, not independent validation of the new gap policy.

Use existing atomic cancellation-safe HA Store writes and readback verification.
Do not restore numerical/edge anchors after restart. Corruption fails closed,
without silently replacing stored evidence. Analytics storage problems never
introduce commands or change stove control.

## Installation and rollback

Before manual installation, back up the current integration and HA data, including
analytics/shadow Stores. Prevent HACS from overwriting this private line. Verify
ZIP SHA256, replace the whole `custom_components/hwam_stove` directory, and restart
HA manually. Preserve the ConfigEntry and all 49 identities; no library install or
controller command is necessary. Check version rc9, fresh ordinary status,
analytics health, schema 5 and separately attributed rc7/rc8/current benchmarks.

To roll back, restore the whole rc8 integration directory and restart. rc8 reads
its untouched v4 checkpoint and cannot consume progress accumulated in v5. Keep
v5 evidence for investigation. Never copy/modify `.storage` while HA is running;
a full data rollback requires stopped Core and the matching pre-upgrade backup,
and loses subsequent observations. Do not delete evidence just to clear an error.

## Limits

No threshold proves absence of short door/alarm transitions. Eligible comparisons
may remain scarce; retaining conditional evidence must not manufacture confidence.
No production outage attribution, firmware fault, accuracy improvement, physical
refill detection, notification or control change is claimed. Further independent
normal-use evidence is needed to assess the policy. No new capture or hardware
access was performed or required for these deterministic offline checks.
