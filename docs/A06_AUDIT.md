# A06 audit before implementation

Base b8b5c2490756fb55553e2185841167b4e37e65ab (private rc7).
No production/controller access. Household reports are USER-REPORTED, not new
controller measurements. No link between 5 GHz DFS and 2.4 GHz failures proven.

## Established from current installed code

- HA's DataUpdateCoordinator retains last successful data but makes coordinator
  entities unavailable on failed reads. Measurement values are not erased.
- rc3 get_data invalidates cached_status at read start, only publishes boolean
  door data after validated parsing. cached_diagnostics is last-success data;
  neither cache requires new requests. No library interface change is needed.
- Connector failures and invalid JSON can both collapse through library helpers
  to empty/invalid status and get_data None. The integration cannot reliably tell
  these apart. Timeout/disconnect/payload/cancellation exceptions can escape.
  Record only proven exception categories, never infer a network cause from None.
  Existing library malformed-JSON logging can include the raw body; no new body
  logging will be added. Changing that library logging is outside this phase.
- A02 gap() preserves session/counters/peaks/ledger, clears edge/time anchors and
  marks the session coverage_uncertain. It does not erase the heating session.
  A04.2 separately regains a fresh window in a known-start censored session.
- A04 pending forecasts are immutable. Door=true already closes a pending model
  prediction; non-request Glow also closes it. No prediction formula change is
  required for observed door intervention. Model-only input gaps remain separate.
- A04.3 native benchmarks do not currently censor on door intervention. Their
  completed-observation age rule is 11 s, stricter than A02's schedule/read-start
  rule. Read latency or one missed poll can censor a segment. This is conservative,
  not established packet-loss frequency or proof of a firmware/network defect.
- Dropping these gaps would risk missing short door/alarm transitions. Keep the
  current coverage/scoring rules. Separate resilient display from observations.
- Safety/door/refill and command entities must not reuse retained measurement
  availability. Equal accepted reads must still advance real observation time.

## Evidence boundary and safe implementation scope

Retain six noncritical measurement sensors (two temperatures, oxygen, three
valves) for at most 120 s since real last success, immediately marked stale on
failure. Successful-read freshness follows the existing 10/60 s cadence + 1 s
allowance. Other live states/commands remain strict. Local expiry callbacks only;
no polling/retries or transport changes. No persistence of display cache across
restart: old cache cannot bootstrap a live status.

Observation-only detector: record sampled door sequences, observed request
edges, and Glow→Burn without sampled door evidence. A complete
Glow→open→closed→Burn pattern remains POSSIBLE/INSUFFICIENT, not a counted or
probable wood addition. No validated temperature/oxygen/valve classifier or
threshold exists. Capture bounded endpoint context for future review; temperature
rise alone generates no wood-addition event. Probable/confirmed classification is
explicitly deferred. Household early-refill reports justify censoring possible
interventions conservatively, not asserting the action took place.

Introduce a separate benchmark intervention policy with preserved rc7 policy
history. Existing formula/thresholds and A02/A02.1 counters remain untouched.
A request is an observed alarm edge, never a complete physical-refill truth set.
No-alarm explanations remain possible causes; none establishes malfunction.
