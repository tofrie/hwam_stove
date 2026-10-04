"""Literal successful public method calls; all targets are simulated."""

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CommandCase:
    id: str
    platform: str
    key: str
    action: str
    arguments: tuple
    method: str
    expected_args: tuple = ()
    expected_kwargs: tuple = ()


LOCAL_TIME = datetime(2024, 3, 1, 12, 30, tzinfo=ZoneInfo("Europe/Berlin"))
SYNC_UTC_TIME = datetime(2026, 7, 1, 10, 20, 30, tzinfo=UTC)
SYNC_LOCAL_TIME = datetime(2026, 7, 1, 12, 20, 30, tzinfo=ZoneInfo("Europe/Berlin"))
CASES = [
    CommandCase("burn", "number", "burn_level", "async_set_native_value", (4.0,),
                "set_burn_level", (4,)),
    CommandCase("night_on", "switch", "night_lowering", "async_turn_on", (),
                "set_night_lowering", (True,)),
    CommandCase("night_off", "switch", "night_lowering", "async_turn_off", (),
                "set_night_lowering", (False,)),
    CommandCase("refill_on", "switch", "remote_refill_alarm", "async_turn_on", (),
                "set_remote_refill_alarm", (True,)),
    CommandCase("refill_off", "switch", "remote_refill_alarm", "async_turn_off", (),
                "set_remote_refill_alarm", (False,)),
    CommandCase("start", "button", "start", "async_press", (), "start"),
    CommandCase("sync", "button", "sync_clock", "async_press", (), "set_time",
                (SYNC_LOCAL_TIME,)),
    CommandCase("night_begin", "time", "night_begin_time", "async_set_value",
                (time(21),), "set_night_lowering_hours", (),
                (("start", time(21)), ("end", time(6, 30)))),
    CommandCase("night_end", "time", "night_end_time", "async_set_value", (time(7),),
                "set_night_lowering_hours", (),
                (("start", time(22, 15)), ("end", time(7)))),
    CommandCase("clock", "datetime", "date_time", "async_set_value", (LOCAL_TIME,),
                "set_time", (LOCAL_TIME,)),
]


async def invoke(case, entities):
    entity = entities[case.platform, case.key]
    await getattr(entity, case.action)(*case.arguments)
