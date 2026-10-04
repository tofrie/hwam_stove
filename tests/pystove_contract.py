"""Successful public contract, obtained through the installed HTTP parser."""

import ast
import asyncio
from datetime import datetime, time, timedelta
import inspect
from pathlib import Path

from pystove import Stove, pystove

from .command_cases import CASES
from .helpers import COMMANDS, HOST, status_data

ENDPOINTS = {
    "burn": ("POST", "/set_burn_level"),
    "night_on": ("GET", "/set_night_lowering_on"),
    "night_off": ("GET", "/set_night_lowering_off"),
    "refill_on": ("POST", "/set_remote_refill_alarm"),
    "refill_off": ("POST", "/set_remote_refill_alarm"),
    "start": ("GET", "/start"),
    "sync": ("POST", "/set_time"),
    "night_begin": ("POST", "/set_night_time"),
    "night_end": ("POST", "/set_night_time"),
    "clock": ("POST", "/set_time"),
}


def typed(value):
    """Keep dict/list order and distinguish bool, int and temporal types."""
    if isinstance(value, dict):
        value = [[k, typed(v)] for k, v in value.items()]
        return ["dict", value]
    if isinstance(value, list):
        return ["list", [typed(v) for v in value]]
    if isinstance(value, (datetime, time)):
        return [type(value).__name__, value.isoformat(), str(value.tzinfo)]
    if isinstance(value, timedelta):
        return ["timedelta", value.days, value.seconds, value.microseconds]
    return [type(value).__name__, value]


async def wait(event):
    async with asyncio.timeout(5):
        await event.wait()


async def cancel_at_body(coroutine, response):
    task = asyncio.create_task(coroutine)
    try:
        await wait(response.entered)
        task.cancel("gate cancellation")
        try:
            await task
        except asyncio.CancelledError as error:
            assert error.args == ("gate cancellation",)
        else:
            raise AssertionError("Cancellation swallowed")
        assert response.exited
    finally:
        response.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def imported_symbols():
    """Inventory the actual runtime, so a new import cannot evade the gate."""
    runtime = Path(__file__).resolve().parents[1] / "custom_components/hwam_stove"
    names = set()
    imports = set()
    for path in runtime.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "pystove":
                imports.update(alias.name for alias in node.names)
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "pystove"
            ):
                names.add(node.attr)
    assert imports == {"Stove", "pystove"}
    assert Stove is pystove.Stove
    return sorted(names)


async def snapshot(transport):
    names = imported_symbols()
    result = {
        "import_paths": ["pystove.Stove", "pystove.pystove"],
        "symbols": names,
        "constants": {n: typed(getattr(pystove, n)) for n in names if n != "Stove"},
        "signatures": {
            n: str(inspect.signature(getattr(Stove, n)))
            for n in ("create", "destroy", "get_data", *COMMANDS)
        },
    }
    client = await Stove.create(HOST)
    session = transport.sessions[-1]
    try:
        result["identity"] = {
            n: getattr(client, n)
            for n in (
                "name",
                "stove_host",
                "stove_ip",
                "stove_mdns",
                "stove_ssid",
                "algo_version",
                "series",
            )
        }
        result["create_requests"] = session.calls.copy()
        result["headers"] = transport.headers
        data = await client.get_data()
        assert data == status_data() and len(data) == 25
        assert list(data) == list(status_data())
        result["status"] = typed(data)
        states = {}
        for field, table in (
            ("phase", pystove.PHASE),
            ("operation_mode", pystove.OPERATION_MODES),
            ("night_lowering", pystove.NIGHT_LOWERING_STATES),
        ):
            original = session.raw[field]
            values = []
            for index in range(len(table)):
                session.raw[field] = index
                values.append((await client.get_data())[field])
            session.raw[field] = original
            states[field] = values
        result["states"] = states
        alarms = {}
        for field, table in (
            ("maintenance_alarms", pystove.MAINTENANCE_ALARMS),
            ("safety_alarms", pystove.SAFETY_ALARMS),
        ):
            values = []
            for mask in [
                0,
                *(1 << i for i in range(len(table))),
                (1 << len(table)) - 1,
            ]:
                session.raw[field] = mask
                values.append((await client.get_data())[field])
            session.raw[field] = 0
            alarms[field] = values
        result["alarms"] = alarms
        commands = {}
        for case in CASES:
            method = getattr(client, case.method)
            records = []
            for confirmed in (True, False):
                before = len(session.calls)
                session.queue(
                    *ENDPOINTS[case.id],
                    body=('{"response":"OK"}' if confirmed else '{"response":"ERROR"}'),
                )
                outcome = await method(
                    *case.expected_args, **dict(case.expected_kwargs)
                )
                assert outcome is confirmed
                assert len(session.calls) == before + 1
                records.append(
                    {"outcome": typed(outcome), "request": session.calls[-1]}
                )
            response = session.queue(*ENDPOINTS[case.id], hold=True)
            before = len(session.calls)
            await cancel_at_body(
                method(*case.expected_args, **dict(case.expected_kwargs)), response
            )
            assert len(session.calls) == before + 1
            commands[case.id] = records
        result["commands"] = commands
        response = session.queue("GET", "/get_stove_data", hold=True)
        await cancel_at_body(client.get_data(), response)
    finally:
        result["destroy_result"] = typed(await client.destroy())
    assert session.closed and session.close_calls == 1
    assert all(response.exited for response in session.responses)
    return result
