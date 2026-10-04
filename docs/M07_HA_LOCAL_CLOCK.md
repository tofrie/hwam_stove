# M07: write the stove clock in Home Assistant's configured timezone

Base: `7ab07db3da3c2cf7e9783a034adeb2deb0eb7f1e` (completed M08).
Manifest `1.0.0b2`, ConfigEntry `VERSION = 2`, published `pystove==0.3a1`.
Target: pinned HA `2026.10.0b0`, Python `3.14.6`.

## Existing read contract and root cause

`HwamStoveTime._handle_coordinator_update()` combines the controller's naive date
and time with `get_default_time_zone()`. The read path treats the stove clock as
HA-local wall time. This method is byte-for-byte unchanged.

Previously, the datetime setter passed any aware input straight to pystove.
Published pystove 0.3a1 serializes the input's visible fields without converting
its timezone. A UTC input therefore wrote UTC clock fields even when HA used
Europe/Berlin. The Sync button called `set_time()` without an argument, selecting
pystove's `datetime.now()` fallback and the operating system's timezone.

## Shared time contract

Both callbacks now call `_clock.stove_local_time()`. The datetime setter supplies
its input; Sync obtains an explicit current instant from HA's `utcnow()` utility.
The helper applies `as_utc()` then `as_local()` and returns an aware datetime in
HA's currently configured timezone. No timezone is cached. HA's
[configured-timezone setter](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/core_config.py)
updates the default zone used by these utilities when `hass.config.time_zone`
changes. Tests change HA's zone between actions, including Kathmandu, Los Angeles
and UTC, to verify that the value is not hardcoded to Berlin.

For aware inputs the absolute instant is preserved, including non-UTC offsets
and zoneinfo inputs. Replacing the input's tzinfo is not used. The UTC round trip
is deliberate: HA's `as_local()` alone returns an input already carrying the
HA timezone unchanged, including an imaginary local representation in a DST gap.
Converting via UTC produces the valid local representation of that same instant.

The [HA datetime entity contract](https://developers.home-assistant.io/docs/core/entity/datetime/)
provides aware values. The actual pinned
[datetime service wrapper](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/components/datetime/services.py)
attaches HA's timezone to naive service input. Direct Python calls with naive
input receive the same interpretation through HA's `as_utc()` utility. They are
not treated as UTC or process-local time. For naive ambiguous/nonexistent inputs,
HA/zoneinfo supplies the offset from its normal fold convention (default fold=0).
The helper then normalizes that inferred instant. It cannot determine a user's
intention for an intrinsically ambiguous naive clock time and introduces no new
DST selection or rejection policy.

## Berlin time and DST matrix

All rows are tested through the datetime entity and real HA services. The same
aware instants also exercise Sync with a deterministic HA clock.

| Input | HA-local output |
| --- | --- |
| 2026-07-01 10:00 UTC | 12:00 +02:00 |
| 2026-01-01 10:00 UTC | 11:00 +01:00 |
| 2026-07-01 08:00 America/New_York | 14:00 +02:00 |
| 2026-07-01 10:00 +05:30 | 06:30 +02:00 |
| 2026-07-01 08:00 -04:00 | 14:00 +02:00 |
| 2026-03-29 00:59:59 UTC | 01:59:59 +01:00 |
| 2026-03-29 01:00:00 UTC | 03:00:00 +02:00 |
| 2026-10-25 00:59:59 UTC | 02:59:59 +02:00, fold=0 |
| 2026-10-25 01:00:00 UTC | 02:00:00 +01:00, fold=1 |
| 2026-10-25 Berlin 02:30, fold=0 | 02:30 +02:00, first occurrence |
| 2026-10-25 Berlin 02:30, fold=1 | 02:30 +01:00, second occurrence |
| 2026-03-29 Berlin 02:30, fold=0 | 03:30 +02:00, same underlying instant |
| 2026-03-29 Berlin 02:30, fold=1 | 01:30 +01:00, same underlying instant |
| 2026-12-31 23:59:58 UTC | 2027-01-01 00:59:58 +01:00 |

Naive ordinary summer/winter inputs retain their HA-local wall time. Default-fold
naive 2026-10-25 02:30 selects the first occurrence; default-fold naive
2026-03-29 02:30 normalizes to 03:30. Direct and real service tests agree.
The DST conversion uses HA utilities and
[Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html), not custom rules.

## Process timezone and wire proof

Tests freeze only the integration's binding of HA `utcnow()` with pytest's
monkeypatch fixture, restoring it after each test. HA's timers, global datetime
class and parent process TZ remain untouched. Two disposable subprocesses use
real process TZ values UTC and America/Los_Angeles while HA uses Europe/Berlin.
Their system clocks show 10:20:30 and 03:20:30 respectively for the frozen instant;
both Sync callbacks pass 12:20:30 +02:00. Child DNS, socket connect and aiohttp
requests are blocked; all output is synthetic. This works independently of the
CI host's timezone.

Twelve tests run the actual installed pystove `set_time()` AND `_post()` through
both integration paths against a fake HTTP session. They verify exact compact JSON
payloads and context-manager exit, with no network. Expected payload for
2026-07-01 10:20:30.123456 UTC in Berlin:

```json
{"year":2026,"month":6,"day":1,"hours":12,"minutes":20,"seconds":30}
```

All six fields use HA-local values. The month remains zero-based; microseconds
are still omitted by pystove. January, year rollover, both autumn folds and the
spring gap are covered. The library's OS-clock fallback raises in these tests if
accidentally used. No pystove file or protocol field changes.

The wire protocol here carries neither timezone offset nor fold. Thus two aware
instants in the repeated autumn hour necessarily produce identical local clock
fields. The integration preserves the instant until serialization; this existing
wire limitation prevents transmitting the distinction. No firmware DST behavior,
hardware latency, HTTP-status policy or command acknowledgement is inferred.

## Scope, compatibility and validation

Runtime changes are exactly the two callback arguments, their helper imports and
the 14-line new `_clock.py`. The other 17 existing runtime files remain byte-equal
to the M08 base. The integrity validator enforces those exact changes and retains
all historical scope checks. Entity metadata, registry IDs, native/read values,
button availability and press-attempt timestamps are unchanged.

Each admitted action performs one `set_time(explicit_ha_local_datetime)` call.
There is no retry, rollback, refresh, extra `get_data()` or optimistic datetime
state. H04 keeps False as `command_not_confirmed` without claiming nonexecution.
Exceptions and cancellation retain their identity; real task cancellation is
covered for both paths. Unavailable Sync is filtered by HA before even reading
the clock. H05 night-time behavior is untouched. Existing generic/H04/M03 tests
only adopt the intentional explicit Sync argument and deterministic clock; their
other assertions remain intact.

Before the runtime correction, all five selected defect tests failed (former
M07 xfail, summer, winter, both imaginary aware inputs). The new M07 suite has
82 passing cases. Only `test_M07_clock_uses_ha_local_time` leaves the strict-xfail
list. The remaining ten M02 and one M04 cases remain strict xfails.

Official pinned Hassfest/HACS containers run after push in the existing metadata
workflow, now enabled for this branch. Local Docker is unavailable. The unchanged
manifest-order issue and four HACS metadata findings remain outside M07; HACS
repository API validation uses the default branch. Foundation CI remains separate.
No hardware access is performed or needed for this integration-level contract.

Local verification, 2026-10-04: **534 cases: 523 passed, 11 strict xfailed,
0 XPASS**. Included: 82 M07, 20 M08, 29 M03, 47 H05, 83 H04, 48 H03,
7 H02, 18 H01A, 10 H01B safety-boundary, 35 B01 migration, 83 existing
entity-contract and 7 environment/network-isolation cases. Ruff, integrity and
whitespace checks pass; all 156 installed packages are compatible.
