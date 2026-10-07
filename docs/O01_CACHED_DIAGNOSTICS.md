# O01 — cached config-entry diagnostics

Base: `2a93f53b8e60d80fa422d64e6b526936686897c9`.
Branch: `feature/o01-cached-diagnostics`.
Dependency: unchanged, published `saynwerk-pystove==0.3.0rc1`.

## Data contract

The standard `async_get_config_entry_diagnostics` platform produces schema 1.
It reads memory synchronously inside the async callback: no awaits, tasks,
client methods, refresh, registry enumeration, files or network. HA discovers
the platform automatically; it is not added to the entity-platform setup list.

| Section | Explicitly selected fields |
| --- | --- |
| `integration` | Loaded integration and pystove versions, entry version/minor version and lifecycle state, runtime/cache presence, coordinator `last_update_success`, current effective update interval in seconds |
| `stove` | Cached model/series, firmware/remote versions, phase, operation mode, numeric status algorithm, identification algorithm version, updating flag |
| `status` | Room/stove temperature, oxygen, three valve positions, burn level, seconds since remote message, night-lowering state, refill alarm, known maintenance/safety alarm names |

Values retain the existing public status units. Flags are booleans, the explicit
polling interval becomes seconds, and supported enum scalars become their value.
No generic recursive serializer or arbitrary object/string conversion is used.

Known categorical values and alarm names are allowlisted from the pinned library.
Model text is restricted to a four-digit series, optionally prefixed with HWAM;
only the digits are returned. Version strings have a bounded numeric version
format. Identification algorithm text permits only the known `HW.NNNN.NNNNNN`
and `DS.DSBN[N].NNNNNN` forms. Unsupported spellings are deliberately omitted
as null rather than exporting arbitrary identification text. This filters the
diagnostic output only; it does not change parsing, validation or device data.

Missing runtime, cache, optional attributes and unrecognized field values produce
null. An empty valid alarm list means no reported alarm; unknown alarm content
produces null, not an empty list. After an update failure, any last successful
cache is retained alongside `last_update_success: false`; diagnostics does not
claim that it is fresh. Recovery is reflected only after the existing polling
path succeeds. No refresh is triggered to fill missing fields.

## Privacy and HA framework boundary

The integration's `data` contains no host/IP, mDNS, custom stove name, SSID, MAC,
entry/device/entity IDs, unique IDs, config data/options, credentials, raw bodies,
files, exception text or unknown protocol fields. It also excludes controller
clock/date, night schedules, refill timestamps/countdowns and message counters.
The entry ID is used internally only to select its existing runtime.

HA 2026.9.4 and 2026.10.0b0 add their own standard download envelope around `data`:
system information, integration metadata, setup durations and existing integration
Repair issues. HA also includes the entry ID in the download **filename**.
An integration diagnostics callback cannot change that envelope or filename.
The unchanged HA framework is explicitly approved: its filename and Repair/
framework data are outside the hwam_stove privacy allowlist. The integration's
`data` remains strictly free of excluded identifiers and sensitive data.
No HA-framework workaround or modification is implemented.

## Verification

The O01 tests cover normal, offline, recovered, missing and partial caches,
missing optional identification, enums, copied alarm lists, malformed/private
values and ordinary/HA JSON encoding. The real HA admin download handler and
platform discovery are exercised in memory without starting a listener or
sending an HTTP request.

HTTP/DNS/socket and all Stove-method guards, plus explicit coordinator refresh
guards, fail even on swallowed attempts. Tests also use the published library
with the existing in-memory transport: repeated downloads create no sessions,
requests, cleanup operations or tasks. Network isolation remains enabled for
both full gates. Lifecycle cleanup and Python 3.14 warning checks remain active.

Exact gate counts and source hashes are recorded in `O01_GATE_EVIDENCE.json`.
The integrity gate proves all 21 pre-existing runtime files, including the
manifest, are byte-identical to the base. Only `diagnostics.py` is added to runtime.
Polling, commands, H05 state, entities, registry identities and dependency are
unchanged. H01B remains **OPEN/deferred**. No hardware or production access.

Next independent feature: evaluate and add appropriate measurement state classes
for existing temperature/oxygen sensors (O02 long-term statistics), with offline
HA metadata/statistics tests and no new protocol fields or controller requests.
