# A06.1 audit before runtime changes

Base rc8 855cc958a5540eb9f8b4dff2b09b247d90de9156. Offline source review only.
No production Store or detailed diagnostic export was supplied locally; the
request's aggregate production counters are USER-REPORTED.

- A04 adapter: request-start lateness > prior completion + cadence + 1 second,
  OR completed observation age > cadence + 1. Active cadence 10 s, Standby 60 s.
- Any failed regular read calls async_gap and then the outer coordinator invokes
  boundary again. Both call model.gap, clearing the 600 s history and pending
  scoring anchor. They can increment boundary generations twice; intervention
  gap rows coalesce without a previous sample and benchmark only closes an
  active cycle. Counters from different components are not outage counts.
- A04.3/A06 active benchmark independently applies >11 s completed age. Its
  gap closes the entire current comparison. A06 interventions independently use
  >11 / >61 s. Reload/restart/command boundaries are explicit and separate.
- Diagnostics reads do not mutate evidence. Abstention counters count reason
  transitions, not packets or outages. Restart/reload may add lifecycle censures.
- Native benchmark forecasts remain Glow-only, matching the existing timestamp
  entity contract. A household report of a 1–3 hour countdown does not establish
  the phase or historical availability at each benchmark horizon. Missing long
  comparisons cannot be attributed exclusively to short gaps.
- The model uses actual timestamp-based OLS and actual elapsed window duration,
  not sample indices or uniform-sampling assumptions. Sparse numerical inputs
  are mathematically possible without changing crossing(), TARGET or WINDOW.
  This does not prove hidden phase/door/alarm transitions did not happen.
- A02 factual counters must retain their existing strict gap contract.
- Historical benchmark closed rows omit sample buffers; at most 64 closed rows
  survive, with cumulative aggregates for evicted rows. rc8 does not retain a
  complete failed-read/recovery interval ledger. Neither aggregate totals nor
  those rows establish how many of the 206 censures or long horizons are salvageable.
  No retrospective recovery count or reclassification will be claimed.

Prospective policy design: normal numerical jitter <=15 s, short >15..30 s,
longer hard; active edge coverage remains strict at 11 s and every failed read.
15 s allows half one existing active interval of scheduling variation; 30 s
allows at most two missing 10 s samples. These are bounded analytical choices,
not firmware timing guarantees, and were not fitted to outcomes. Standby keeps
its 61 s allowance but no active numerical-history relaxation across phase changes.

Preserve real numerical rows only across bounded same-session Burn observations;
never restore an old pending forecast's scoreability across edge uncertainty.
Do not reissue a consumed opportunity merely because a short gap occurred.
Keep historical horizons for conditional comparison with a later directly
observed request edge. After edge uncertainty they cannot establish that it was
the *next* request or absence of physical intervention: mark whole-cycle eligibility
false and exclude those conditional scores from quality/ranking conclusions.
Queries falling inside unknown intervals and unknown outcome edges remain invalid.
Restart/commands/interventions retain hard boundaries. New policy and old rc7/rc8
archives must be separately attributed; never rescore old records.
