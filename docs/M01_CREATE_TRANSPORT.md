# M01 — transport failures at client creation

Base: `69edff649cc0523bb363cb8f485114b58ebe37ab`.
Branch: `fix/m01-create-transport-errors`.
Dependency unchanged: published `saynwerk-pystove==0.3.0rc1`.

## Exact boundary

Only exceptions escaping the awaited `Stove.create(host)` call receive the new
mapping. Setup raises `ConfigEntryNotReady` with the original exception as its
cause. The shared user/import/reconfigure connection test raises `ConnectionError`
with the original cause, reaching the existing translated `cannot_connect` form.
HA retains its existing setup-retry scheduling; the integration adds no requests,
retries, deadlines or timeout values.

The allowlist, shared by setup and flow validation, is:

- `TimeoutError`, including aiohttp connection/socket/server timeout subclasses.
- `aiohttp.ClientConnectionError`: socket/connection failures, including
  `ClientConnectionResetError`, `ClientOSError`, `ClientConnectorError`,
  `ClientConnectorDNSError` and `ServerDisconnectedError`.
- `aiohttp.ClientPayloadError`: incomplete, malformed-framing or decompression
  failures during HTTP body transfer, distinct from invalid identification JSON.

The connection hierarchy also contains errors without established temporary
direct-HTTP semantics. `ClientSSLError` (including connector SSL/certificate
errors), `ClientProxyConnectionError` and `ServerFingerprintMismatch` are
explicitly excluded before applying that allowlist. No broad `ClientError`
handler is added. URL, redirect, generic HTTP response/content-type, structural
JSON/XML shape and programming errors remain outside this mapping.

This selection was checked against installed aiohttp **3.14.3**, especially
`client.py`, `client_reqrep.py`, `client_proto.py`, and `client_exceptions.py`,
and the exact published pystove source. Closed responses and socket failures can
raise the connection base class or its listed descendants; body reads can raise
payload errors. The library uses response text followed by JSON parsing, not
aiohttp's JSON content-type validator, and adds no `raise_for_status` policy.
Generic `ClientResponseError` can also describe HTTP parsing failures; it is not
assumed to mean a temporary connection failure.

References: [aiohttp exception hierarchy](https://docs.aiohttp.org/en/stable/client_reference.html#hierarchy-of-exceptions),
[HA setup failure handling](https://developers.home-assistant.io/docs/integration_setup_failures/).
Installed sources, rather than a newer online aiohttp version, define this gate.

## Ownership and actual library behavior

If create raises, no client was transferred to hwam_stove. The factory owns its
initialization cleanup; the integration does not invent a client to destroy.
After create returns, H03 temporary-client cleanup and H01A setup cleanup remain
unchanged. H01B remains **OPEN/deferred**.

An important distinction: pystove's `_get`/`_post` already consume
`ClientConnectorError` and return `None`. It often never reaches this new handler.
With all identification connections failing, create can return an UNKNOWN client;
flow validation then uses its existing `cannot_connect` path and destroys that
owned client once. Setup reaches the existing first-refresh/Coordinator failure
path and H01A destroys it once. Real-library tests verify this distinction.

Cancellation escaping create propagates unchanged. The existing library and H03
cleanup tasks still finish before returning/raising, including repeated caller
cancellation. An earlier primary read error remains primary if later cancellation
arrives during the library's bounded close cleanup; this existing dependency
contract is unchanged. Confirmed-open file close precedes session close, once
each, with no retry or speculative close after unconfirmed open.

M06 previously caught all `ClientError` and `TimeoutError` around the entire
validation helper. That catch is narrowed to the same existing `ConnectionError`
result used by user/import, so transport classification now occurs only at create.
Errors from post-create identity access or temporary-client cleanup are not newly
classified as connection failures. Existing H03 primary-error handling and legacy
UNKNOWN-identity `ConnectionError` behavior are retained. A failed validation or
cleanup cannot save the reconfigure host; the previous live runtime, registry and
entry stay intact. M05 reservations are released without affecting another flow.

## Verification and remaining boundary

Both complete gates: **1365 passed, 0 xfailed, 0 XPASS**, Python 3.14.6,
HA 2026.9.4 and 2026.10.0b0. The new 271-case suite covers mapped and excluded
exceptions across all four boundaries, real-library request counts, ownership,
repeated cancellation, concurrent reservations, retry and live reconfigure safety.
The unchanged boundary suite passes 147 tests and B01 passes 35; all existing
lifecycle, command, night-time, YAML, host and clock regressions pass.

IP sockets are blocked and HTTP/DNS guards detect attempted access. Observed
real-library lifecycle tests report zero loop errors and ResourceWarnings. Ruff,
both `pip check` runs and exact runtime integrity checks pass. Full counts and
source/JUnit hashes are recorded in `M01_GATE_EVIDENCE.json`.

The production-version gate uses only the established two test/tool version
substitutions (HA/pytest fixture versions); its integration and all M01 tests are
byte-identical to the secondary gate. Runtime changes are confined to
`__init__.py`, `config_flow.py` and the shared exception tuples in `const.py`.
Manifest/dependency, Coordinator/get_data, commands, host comparison, registry
identities and all cleanup implementations are unchanged.

The safe M01 transport-mapping scope is complete. Invalid response structures,
unclassified errors, post-create cleanup failures and a total validation timeout
budget are outside this change; they are not silently converted into connectivity
errors. A total budget still needs its own policy decision. No hardware or
production access occurred.

Next independent phase suggested: **M09 / pystove L2 — bounded, redacted transport
logging**, in the dependency repository with its own validation and migration gate.
