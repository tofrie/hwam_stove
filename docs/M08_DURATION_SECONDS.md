# M08: signed total duration at the existing second precision

Base: `c2e7db4f41cf5039f36e393b8226027f672caaf1` (completed M03).
Manifest `1.0.0b2`, ConfigEntry `VERSION = 2`, published `pystove==0.3a1`.
Target: pinned HA `2026.10.0b0`, Python `3.14.6`.

## Meaning and existing contract

The released pystove parser constructs `DATA_TIME_TO_NEW_FIREWOOD` as a timedelta
from the controller's new-firewood hours and minutes. It also uses that interval
to derive its estimated refuelling datetime. The sensor represents the reported
duration, not a modulo-day clock time. Neither this field construction nor the HA
sensor contract establishes a nonnegative-only requirement. This change does not
invent a physical interpretation for negative values or claim their frequency on
real hardware; it faithfully keeps their sign at the existing second precision.

Previously, `state_func=lambda data, key: data[key].seconds` returned an int.
It omitted both the signed day component and normalized microseconds.
The [pinned HA SensorEntity source](https://github.com/home-assistant/core/blob/64ed916d9c22640b8d41b403fda0f7ed4d4c0bd5/homeassistant/components/sensor/__init__.py)
accepts finite int, float and Decimal numeric values, including negative duration
values. The [HA sensor contract](https://developers.home-assistant.io/docs/core/entity/sensor/)
supports native seconds with device class duration. HA's existing conversion to
the suggested hours can produce a floating-point displayed state; that is distinct
from this integration's native integer-second value.

## Type and precision decision

`timedelta.total_seconds()` was considered first. It returns float and includes
fractional seconds. HA accepts that type, but directly using it would change this
sensor's native type and subsecond precision. `int(total_seconds())` would preserve
an integer result but discard negative fractions toward zero, unlike omitting the
normalized microseconds component. For example, -1.5 seconds would become -1.

The chosen single-line correction is:

```python
state_func=lambda data, key: data[key].days * 86400 + data[key].seconds,
```

It adds the missing signed days using integer arithmetic while retaining the
existing omission of normalized microseconds. Integral durations are exact total
seconds. For fractional durations, this equals floor of total seconds: +1.999999
becomes 1; -0.000001 becomes -1; -1.5 becomes -2. Subseconds are deliberately not
added to the public sensor contract. This is not rounding to nearest or a new
truncation-toward-zero rule. There is no clamp to zero and no floating-point
conversion in the state function.

See Python's [timedelta normalization and total_seconds documentation](https://docs.python.org/3.14/library/datetime.html#timedelta-objects).

## Regression matrix

Values below are native integer seconds, before HA's existing hours conversion.

| Input timedelta | Expected |
| --- | ---: |
| 0 seconds | 0 |
| 1 second | 1 |
| 59 seconds | 59 |
| 1 minute | 60 |
| 23:59:59 | 86399 |
| 24 hours | 86400 |
| 26 hours | 93600 |
| 3 days, 4 hours, 5 minutes, 6 seconds | 273906 |
| -1 second | -1 |
| -2 hours | -7200 |
| -1 day | -86400 |
| -1 day + 2 hours | -79200 |
| +1 microsecond | 0 |
| 1 second + 999999 microseconds | 1 |
| 26 hours + 999999 microseconds | 93600 |
| -1 microsecond | -1 |
| -1 second - 500000 microseconds | -2 |
| -1 day + 2 hours + 1 microsecond | -79200 |

`tests/test_m08_duration.py` contains 20 regular tests: these 18 cases through a
registered HA sensor and real coordinator callbacks, the converted former
`test_M08_duration_includes_days`, and the unchanged normal fixture/metadata
contract. The normal 67-minute fixture remains int 4020. Matrix tests additionally
check actual HA state/unit conversion, source timedelta preservation, registry
identity, exact read count and absence of commands or extra refresh requests.

Before the correction, 12 cases failed on the documented day/sign defects and
8 passed. After the correction all 20 pass. Only the M08 strict-xfail is converted;
the remaining ten M02, one M04 and one M07 cases remain strict xfails.

## Scope and validation

Only the duration state_func line changes at runtime. Unique IDs, translation key,
device association, category, device class, native/suggested unit, display precision,
icon and enabled default remain unchanged. No state_class is added; O02 stays open.
All command, availability, coordinator, lifecycle, migration and time semantics
remain unchanged. No dependency update or real controller request occurs.

The integrity validator freezes the other 18 runtime files against the M03 base,
permits only that exact state_func replacement, and retains every preceding scope
check. Historical fixture hashes and entity descriptions remain unchanged.

Local verification, 2026-10-04: **453 cases: 441 passed, 12 strict xfailed,
0 XPASS**. This includes 20 M08, 29 M03, 47 H05, 83 H04, 48 H03, 7 H02,
10 H01B safety, 18 H01A, 35 B01, 83 existing entity-contract and 7 isolation/
environment cases. Ruff, integrity and whitespace checks pass; all 156 installed
packages are compatible. The other regression suites and fixtures are unchanged.

The Foundation workflow is unchanged. The existing pinned metadata workflow is
enabled for this branch. Local Docker is unavailable; official Hassfest/HACS
containers run in CI after push. The existing manifest-order and four HACS findings
are outside this scope. HACS repository API checks use the default branch.
