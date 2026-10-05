# M05 — address validation and duplicate protection

Base: `f72afd3819601e11fa9049139cd641799a05fb28`.
Branch: `fix/m05-host-validation-duplicates`.
Dependency remains `saynwerk-pystove==0.3.0rc1`; ConfigEntry version remains 2.
No hardware, production, protocol, timeout, discovery or reconfigure changes.

## Input contract

New user/import flows accept:

- Strict decimal dotted-quad IPv4 (four octets, 0–255, no leading zeroes).
- IPv6 literals, either bare or bracketed, without zone identifiers. Python's
  `IPv6Address.compressed` canonical representation is enclosed in brackets
  before being passed to pystove. IPv4-mapped IPv6 stays a separate IPv6 address;
  it is never equated with IPv4.
- ASCII DNS/local hostnames: labels of 1–63 letters/digits/hyphens, starting and
  ending with a letter/digit; at most 253 characters excluding an optional final
  root dot. Single-label names and ASCII punycode names are accepted. Unicode
  input is not converted to IDNA. Legacy numeric/hex/octal IPv4 shorthand is
  rejected rather than delegated to platform-specific resolver interpretation.

Only surrounding ASCII spaces are removed. ASCII hostname case is folded.
Tabs, newlines/control characters, Unicode, empty input, schemes, userinfo,
ports, paths, query strings, fragments, percent escapes and IPv6 zones are
rejected with a translated `host: invalid_host` form error, before client creation.
IPv6 `[...] :port`/`[...]:port` are rejected; a valid bare IPv6 literal is always
interpreted as an address, never as an address plus port.

A trailing DNS root dot is **retained**: absolute-name and resolver-search
semantics need not agree. `stove`, `stove.local`, `stove.local.`, an IP address,
and other aliases are not equated. No DNS requests or hardware identity lookup
are added. Existing entry data, titles, entry IDs, unique IDs and B01 registry
identifiers are never rewritten. Invalid historical values retain exact-string
comparison; existing configurations are not silently repaired or migrated.

## Evidence and flow ownership

The installed published pystove constructs its URLs by concatenating
`"http://" + self.stove_host + endpoint`; it does not bracket IPv6 itself.
The boundary tests execute its real create → get_data → destroy path with an
in-memory transport and inspect the resulting URLs with the installed yarl and
aiohttp request builder. Bracketed IPv6 is structurally supported through this
existing path. This does not establish that HWAM firmware listens on IPv6.
Ports are not a documented separate library input contract and are rejected.

HA's public `is_matching` / `async_has_matching_flow` mechanisms compare active,
flow-local reservations across both manual and import flows, including flows
whose first step is still running. Reservation and matching happen synchronously
before the first connection await. No persistent host-based `unique_id` is set.
See [HA's flow matching and unique-ID contract](https://developers.home-assistant.io/docs/core/integration/config_flow/).
The installed 2026.9.4 and 2026.10.0b0 implementations are exercised by the gates.

Reservations cover connection validation, existing H03 temporary-client cleanup,
and HA's CREATE_ENTRY-to-registration handoff (which can await). Failure, a
returned error form, or cancellation during validation releases the reservation
only after the existing cleanup returns. On success the owner task retains the
reservation through entry registration; a task completion callback releases it
even if registration itself fails/cancels. Completed tasks cannot reserve hosts.
HA removes completed/aborted flows from its index. The entry list is checked
both before and after connection validation. Existing hosts abort with
`already_configured`; active duplicate flows abort with `already_in_progress`.
There is no automatic retry. A user can retry a failed form or start a new flow.

These are connection-address checks, not physical-stove deduplication. Two
different aliases for one physical controller remain an operator responsibility.
Pre-existing duplicate entries are preserved, not automatically deleted/merged.

## M04 and scope boundaries

M04's per-device worker, batch lock, flow cleanup, name handling,
`monitored_variables` boundary and stable repair issue remain unchanged. Only
its comparison keys use the same host normalization as manual flows. Repair
counts now describe syntactically distinct hosts; failed/invalid/pending hosts
still count as missing. A pending user flow is neither stolen nor retried by
the YAML batch. A later setup rechecks actual entry presence.

Historical M04 evidence describes the exact-string contract at its own commit;
this M05 document supersedes only that comparison rule. No entity enablement,
registry identity, M02 command/readback logic, client lifecycle or dependency
changes are included. H01B remains **OPEN/deferred**. Zero xfails is not a claim
that the full audit is resolved. Next suggested independent scope: M06
reconfigure, with explicit entry/registry preservation requirements.

## Verification

`tests/test_m05_hosts.py` covers host grammar, invalid input without I/O, real
library URL construction, normalized historical entries, user/user,
user/import, import/user and import/import races, separate hosts, validation
failure/cancellation, cancellation-safe close, registration handoff failures,
retry without stale reservations, YAML idempotency/repairs and B01 identities.
The existing connection test and H03 cleanup implementation remain unchanged.
Static scope checks retain all previous audit guards and check the new scope.
Both full HA gates use Python 3.14.6, the published Saynwerk artifact exclusively,
blocked IP sockets, and HTTP/DNS guards. Exact results and runtime hashes are
recorded in `M05_GATE_EVIDENCE.json` after verification.
