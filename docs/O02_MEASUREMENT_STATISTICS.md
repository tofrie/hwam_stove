# O02 — measurement statistics for existing sensors

Base: `7b5187b617bd2672b27fa9660cf941a547d66bef`.
Branch: `feature/o02-measurement-statistics`.
Dependency unchanged: `saynwerk-pystove==0.3.0rc1`.

## Complete sensor audit

There are 14 sensor entities: ten numeric values, three enums and one timestamp.
The separate numeric burn-level entity is a Number control, not a Sensor.

| Existing sensor key | Existing unit / device class | Decision and reason |
| --- | --- | --- |
| `room_temperature` | °C / temperature | MEASUREMENT: current room temperature |
| `stove_temperature` | °C / temperature | MEASUREMENT: current stove temperature |
| `oxygen_level` | % / none | MEASUREMENT: current reported oxygen level |
| `valve1_position` | % / none | MEASUREMENT: current reported valve position |
| `valve2_position` | % / none | MEASUREMENT: current reported valve position |
| `valve3_position` | % / none | MEASUREMENT: current reported valve position |
| `algorithm` | none / none | Unchanged: algorithm identifier, not a measured quantity |
| `message_id` | none / none | Unchanged: protocol sequence identifier, not a measured quantity or accumulated consumption |
| `time_since_remote_msg` | s, displayed h / duration | Unchanged: age since a communication event; no established measured-duration statistics contract |
| `time_to_new_fire_wood` | s, displayed h / duration | Unchanged: predicted countdown to refilling, not a present measurement |
| `new_fire_wood_estimate` | none / timestamp | Unchanged: predicted point in time |
| `night_lowering` | none / enum | Unchanged: operating state |
| `operation_mode` | none / enum | Unchanged: operating state |
| `phase` | none / enum | Unchanged: operating state |

Burn level remains an unchanged Number control. Binary sensors, clock/time
entities and buttons are also unchanged; numeric encodings do not make them
measurement sensors. No sensor is a TOTAL or TOTAL_INCREASING candidate here.

The six selected values are current controller-reported quantities already used
by this integration. A valve-position statistic does not assert independently
measured mechanical feedback or introduce a new interpretation of its accuracy.
No new endpoints or unused status fields are introduced.

Both tested HA versions technically permit MEASUREMENT on the duration device
class. That permissive compatibility table is not proof that a countdown or
freshness age meets the semantic contract; these two sensors remain unchanged.

## HA and Recorder contract

[HA's sensor contract](https://developers.home-assistant.io/docs/core/entity/sensor/#long-term-statistics)
requires a current measurement for MEASUREMENT. HA computes min/max and a
time-weighted arithmetic mean. Temperature with °C and an unclassified numeric
percentage sensor are compatible combinations. No device class is invented for
oxygen or valve position. Category DIAGNOSTIC does not itself preclude statistics.

The installed HA 2026.9.4 and 2026.10.0b0 sources were compared, particularly
`sensor/const.py`, `sensor/recorder.py` and `recorder/statistics.py`. Their relevant
measurement selection/aggregation contract is the same. The newer sensor
Recorder implementation allows converters to return None for certain other unit
classes; that does not change these unchanged °C/% paths. Native units remain
unchanged and existing user temperature-unit options continue to apply normally.

Adding state_class does not replace an entity. Existing IDs, associations,
customizations and raw Recorder history remain. Statistics use the existing
entity_id as statistic_id. Recorder's normal retention and include/exclude policy
remain authoritative; disabled or excluded sensors are not force-enabled/recorded.

The integration does no backfill. HA skips statistics periods it has already
compiled; adding a state_class alone does not regenerate the entire past history.
Future compilation begins using the eligible sensor. An open interval (or a
globally missing interval processed by HA's normal catch-up) can include retained
earlier samples because the compiler selects current eligible entities and queries
their history. There is no promised sharp per-sample cutover at the upgrade instant.

Unavailable readings are handled by HA's existing statistics implementation, not
converted to zeros by hwam_stove. HA uses available finite samples and its own
time-weighting rules; this change does not add gap interpolation or a new policy.

## Scope and verification

Runtime diff: one `SensorStateClass` import plus six MEASUREMENT description
attributes in `sensor.py`. All other description fields and sensor code remain
byte-identical. All 21 other runtime files, including O01 diagnostics, manifest,
polling, commands and identity code, remain byte-identical to the base.

The pinned parser still uses `int(raw / 100)` for temperatures and oxygen, and
passes valve values through. Tests exercise this actual installed parser, then
the real HA coordinator/entities, preserving integer truncation and native types.
Synthetic zero, ordinary, negative, 0/100 percentage endpoints and fractional
pass-through examples are compatibility cases, not asserted firmware limits.

Tests cover all 14 descriptions, unchanged repeated reads with
`always_update=False`, unavailable/recovery, existing °F user options, custom
entity_ids, names, areas, hidden and disabled state. No identity migration occurs.

A real in-memory SQLite Recorder test starts with the pre-O02 descriptions,
records history, reloads the same ConfigEntry with current descriptions and
checks preserved identity/history. It compiles real five-minute and hourly
statistics, verifies six statistic IDs, arithmetic means and no sums, and runs
HA's statistics validation with no issues. This uses only the HA test clock and
Recorder test helpers; no direct DB edits, imported statistics or Recorder changes.

Both full gates, subgroup counts and source/JUnit hashes are recorded in
`O02_GATE_EVIDENCE.json`. Existing regressions, O01, boundary and B01 remain in
the full gates with network isolation enabled. H01B remains **OPEN/deferred**.
No hardware or production access occurred.

Next feature phase: L04 alarm semantics, specifically representing the existing
"Door Open Too Long" safety alarm as a problem rather than a live door contact.
That is an independent, offline-testable change and is not implemented here.
