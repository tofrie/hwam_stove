# L04 — precise safety alarm presentation

Base: `7e2ae84ffba85f452b8ce51e29173d97c7b9d820`.
Branch: `fix/l04-alarm-semantics`.
Dependency unchanged: `saynwerk-pystove==0.3.0rc1`.

## Alarm contract

| Existing translation key | Published safety bit/text | Change |
| --- | --- | --- |
| `safety_alarms_door_open_too_long` | bit 11 / `Door Open Too Long` | `DOOR` becomes `PROBLEM`; inactive/active refer to this alarm only |
| `safety_alarms_stove_overheat` | bit 10 / `Chimney Overheat` | Precise chimney name; existing `HEAT` class remains |

The [HA binary sensor contract](https://developers.home-assistant.io/docs/core/entity/binary-sensor/)
defines DOOR as physical open/closed and PROBLEM as problem/no problem. An
inactive door-too-long alarm establishes only absence of that alarm, not a
closed door. The existing cached alarm membership check and published parser
remain unchanged. No new measurement or interpretation of firmware is added.

| Language | Door alarm name | Alarm state labels | Chimney alarm name |
| --- | --- | --- | --- |
| DE | Tür zu lange offen | Inaktiv / Aktiv | Schornsteinüberhitzung |
| EN | Door open too long | Inactive / Active | Chimney overheating |
| NL | Deur te lang open | Inactief / Actief | Oververhitting van de schoorsteen |

Only these two translation blocks change. Door alarm state strings explicitly
describe activation. Chimney heat state labels keep HA's existing hot/normal
semantics. User-assigned entity names continue to take precedence.

## Registry, history and automations

Both HA 2026.9.4 and 2026.10.0b0 keep the existing entities when the same entry is
reloaded: unique IDs, registry IDs, entity IDs, device associations, translation
keys, diagnostic category, enabled defaults and user customizations are retained.
No new entity, registry migration, replacement or history rewrite occurs.
On a fresh installation, HA may derive a different initial entity_id from the
more precise default name; already registered entity_ids are unchanged.

The raw states remain `on` / `off` (and HA's normal `unavailable`). Existing
entity_id state automations continue to fire. New device automation choices for
the door alarm are `problem` / `no_problem`, with corresponding conditions,
instead of `opened` / `not_opened`. HA's public device trigger implementation in
both versions still accepts already-saved old door trigger types and maps them
to the same raw on/off states; those saved triggers remain alarm transitions,
not proof of physical door movement. The integration does not rewrite them.
Templates explicitly comparing the old `device_class` see the intended change.

The current presentation changes to a problem alarm and the refined names.
Recorder rows, including historical device-class/name attributes, remain intact.
Frontend formatting may use current metadata; no frontend rendering or guarantee
of unchanged historical display labels is claimed. The real Recorder tests
verify the stored states, timestamps and attributes, without direct DB changes.

## Offline verification

`test_l04_alarm_semantics.py` exercises the actual installed parser with every
individual safety bit, no alarms, and both relevant alarms together; actual HA
entities retain the same on/off mapping. It also verifies translations loaded by
HA, unchanged-data handling, unavailable/recovery, public device automation
choices, and saved state/device automation actions before and after reload.

Upgrade tests start with pre-L04 class/name metadata and preserve both devices
and all 40 registry entities, including custom IDs, names, areas, icons, hidden
and disabled state. A real in-memory SQLite Recorder test confirms unchanged old
history and continued recording under the same entity_ids after reload.

The runtime integrity guard allows exactly one description class change and the
two translation blocks in DE/EN/NL. The other 18 runtime files are byte-identical,
including O01, O02, manifest, dependency, migration and commands. Full gate results
and hashes are recorded in `L04_GATE_EVIDENCE.json`; existing Boundary, B01 and all
regressions remain included. HTTP/DNS guards and blocked IP sockets remain active.
H01B remains **OPEN/deferred**. No production, hardware or controller access.

Next feature candidate: an optional refill-notification blueprint using the
existing refill-alarm entity, without new protocol assumptions or controller
requests. No such feature is included here.
