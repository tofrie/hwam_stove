"""Artifact A/B: real pystove parsing/lifecycle through real HA boundaries."""

import asyncio
from copy import deepcopy
import gc
from importlib import import_module
import json
from pathlib import Path
from unittest.mock import patch
import warnings

from aiohttp import ClientPayloadError, ServerDisconnectedError
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from pystove import Stove, pystove

from .command_cases import CASES, invoke
from .helpers import DOMAIN, HOST, entity_id_for, status_data
from .library_transport import REAL_CREATE, Transport
from .pystove_contract import ENDPOINTS, cancel_at_body, snapshot, wait
from .test_h04_commands import assert_unconfirmed, prepare_direct_entity

pytestmark = pytest.mark.contract
POST_CASES = [case for case in CASES if ENDPOINTS[case.id][0] == "POST"]


def file_requests(session):
    return [call for call in session.calls
            if call[1] in ("/open_file", "/read_open_file", "/close_file")]


def expected_file_requests(candidate, confirmed=True):
    payload = {"data": '{"file_name":"info.xml","mode":1}'}
    calls = [("POST", "/open_file", payload)]
    if confirmed:
        calls.append(("POST", "/read_open_file", payload))
        if candidate:
            calls.append(("GET", "/close_file", {"allow_redirects": False}))
    return calls


@pytest.fixture
async def real_transport(monkeypatch, stove_factory):
    """Undo only the normal client stub; observe, never replace, real destroy."""
    transport = Transport()
    monkeypatch.setattr(Stove, "create", REAL_CREATE)
    monkeypatch.setattr(pystove, "aiohttp", transport.module())

    async def destroy(client):
        return await transport.observe_destroy(client)

    monkeypatch.setattr(Stove, "destroy", destroy)
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    contexts = []
    loop.set_exception_handler(lambda loop, context: contexts.append(context))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        try:
            yield transport
        finally:
            gc.collect()
            await asyncio.sleep(0)
            loop.set_exception_handler(previous)
        assert not contexts, contexts
        assert not caught, [(w.category.__name__, str(w.message)) for w in caught]
    assert all(s.closed and s.close_calls == 1 for s in transport.sessions)
    assert all(b.closed and b.close_calls == 1
               for s in transport.sessions for b in s.borrowers)
    assert all(r.exited for s in transport.sessions for r in s.responses)


@pytest.fixture
async def real_loaded(real_transport, loaded, installed_pystove):
    assert type(loaded.stove) is Stove
    (session,) = real_transport.sessions
    assert not session.closed and session.close_calls == 0
    assert loaded.data == status_data()
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    assert len(session.calls) == 5 + int(candidate)  # Candidate adds one file close.
    yield loaded


async def test_public_contract_against_official_artifact(
    real_transport, installed_pystove
):
    expected = json.loads(
        (Path(__file__).parent / "fixtures/pystove_public.json").read_text()
    )
    # Record real signature before the delegating destroy observer changes it.
    from .library_transport import REAL_DESTROY

    with patch.object(Stove, "destroy", REAL_DESTROY):
        actual = await snapshot(real_transport)
    if installed_pystove["version"] == "0.3a2.dev0":
        # The sole approved wire delta; retain the official golden file verbatim.
        expected["create_requests"].append(
            ["GET", "/close_file", {"allow_redirects": False}]
        )
    assert json.loads(json.dumps(actual)) == expected


@pytest.mark.parametrize("outcome", ["success", "rejected", "none", "lost", "cancel"])
async def test_info_file_open_confirmation_contract(
    outcome, real_transport, installed_pystove
):
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    session = real_transport.prepare()
    error = RuntimeError("lost open reply")
    response = session.queue(
        "POST", "/open_file",
        body={"success": '{"success":1}', "rejected": '{"success":0}'}.get(outcome),
        error=error if outcome == "lost" else None,
        hold=outcome == "cancel",
    )
    if outcome == "cancel":
        await cancel_at_body(Stove.create(HOST), response)
    elif outcome == "lost":
        with pytest.raises(RuntimeError) as caught:
            await Stove.create(HOST)
        assert caught.value is error
    else:
        client = await Stove.create(HOST)
        assert client.algo_version == (
            "Algorithm" if outcome == "success" else "Unknown"
        )
        assert session.close_calls == 0
        await client.destroy()
    if outcome in ("lost", "cancel"):
        assert not real_transport.destroyed
        assert session.close_calls == int(candidate)
        if not candidate:
            await session.close()  # Existing 0.3a1 unreturned-client leak.
    assert session.close_calls == 1
    assert file_requests(session) == expected_file_requests(
        candidate, confirmed=outcome == "success"
    )


@pytest.mark.parametrize("body", [None, "<broken>", "<Info/>"])
async def test_info_file_read_and_xml_outcomes(real_transport, installed_pystove, body):
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    session = real_transport.prepare()
    session.queue("POST", "/read_open_file", body=body)
    if body is None:
        with pytest.raises(TypeError):
            await Stove.create(HOST)
        assert session.close_calls == int(candidate)
        if not candidate:
            await session.close()
        assert not real_transport.destroyed
    else:
        client = await Stove.create(HOST)
        assert client.algo_version == client.series == "Unknown"
        await client.destroy()
    assert file_requests(session) == expected_file_requests(candidate)
    assert session.close_calls == 1


@pytest.mark.parametrize("caller", ["setup", "flow"])
@pytest.mark.parametrize("primary_kind", ["read_error", "read_cancel", "close_cancel"])
@pytest.mark.parametrize("close_outcome", ["success", "exception", "timeout"])
async def test_info_file_cleanup_at_ha_ownership_boundary(
    caller, primary_kind, close_outcome, real_transport, installed_pystove,
    hass, request, caplog
):
    """No returned client: library cleanup precedes H01A/H02/H03 ownership."""
    from custom_components.hwam_stove import async_setup_entry
    from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

    candidate = installed_pystove["version"] == "0.3a2.dev0"
    session = real_transport.prepare()
    primary = RuntimeError("original info.xml read error")
    primary.__cause__ = OSError("original transport cause")
    read = session.queue(
        "POST", "/read_open_file", body=session.defaults["POST", "/read_open_file"],
        error=primary if primary_kind == "read_error" else None,
        hold=primary_kind != "close_cancel" or not candidate,
    )
    close_error = {
        "success": None,
        "exception": RuntimeError("secondary file close error"),
        "timeout": TimeoutError("secondary file close timeout"),
    }[close_outcome]
    close = session.queue("GET", "/close_file", hold=True, error=close_error)
    if caller == "setup":
        entry = request.getfixturevalue("entry")
        operation = async_setup_entry(hass, entry)
    else:
        flow = HWAMStoveConfigFlow()
        flow.hass = hass
        operation = flow.async_step_user({CONF_HOST: HOST, CONF_NAME: "Synthetic"})
    task = asyncio.create_task(operation)
    try:
        if primary_kind == "close_cancel" and candidate:
            await wait(close.entered)
            task.cancel("original caller cancellation")
        else:
            await wait(read.entered)
            if primary_kind == "read_error":
                read.release.set()
            else:
                # 0.3a1 has no close phase: characterize its cancellation at read.
                task.cancel("original caller cancellation")
        if candidate:
            await wait(close.entered)
            assert not session.close_started.is_set()
            assert not real_transport.destroyed
            for _ in range(3):
                task.cancel("later caller cancellation")
                await asyncio.sleep(0)
                assert not task.done() and not session.closed
                assert not close.exited
            close.release.set()
        with pytest.raises(RuntimeError if primary_kind == "read_error"
                           else asyncio.CancelledError) as caught:
            await task
        if primary_kind == "read_error":
            assert caught.value is primary
            assert isinstance(caught.value.__cause__, OSError)
        else:
            assert caught.value.args == ("original caller cancellation",)
        assert not real_transport.destroyed
        if caller == "setup":
            assert not hass.data[DOMAIN]["stoves"]
        if candidate:
            assert close.exited and close.reader.done()
            if close_error is not None:
                assert any(r.exc_info and r.exc_info[1] is close_error
                           and "controller close unconfirmed" in r.message
                           for r in caplog.records)
        else:
            assert session.close_calls == 0
            await session.close()  # Assert, then clean, the baseline factory leak.
        assert file_requests(session) == expected_file_requests(candidate)
        assert session.close_calls == 1
        assert all(r.reader.done() for r in session.responses if r.reader)
    finally:
        read.release.set()
        close.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_status_recovery_and_equal_data(
    real_loaded, real_transport, installed_pystove, hass
):
    coordinator = real_loaded
    (session,) = real_transport.sessions
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    session.queue("GET", "/get_stove_data", body="{}")
    if candidate:
        assert await coordinator.stove.get_data() is None
    else:
        with pytest.raises(KeyError):
            await coordinator.stove.get_data()
    target = entity_id_for(hass, "number", "burn_level")
    previous = deepcopy(coordinator.data)
    session.queue("GET", "/get_stove_data", body="{}")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert isinstance(
        coordinator.last_exception, UpdateFailed if candidate else KeyError
    )
    assert hass.states.get(target).state == "unavailable"
    await coordinator.async_refresh()
    assert coordinator.last_update_success and coordinator.data == previous
    assert hass.states.get(target).state != "unavailable"
    await coordinator.async_refresh()
    assert coordinator.last_update_success and coordinator.data == previous
    assert hass.states.get(target).state != "unavailable"
    assert len(session.calls) == 9 + int(candidate)


@pytest.mark.parametrize("case", POST_CASES, ids=lambda c: c.id)
@pytest.mark.parametrize(
    "failure", ["malformed", "nonobject", "payload", "disconnect", "timeout"]
)
async def test_post_failures_are_unconfirmed_without_retry(
    case, failure, real_loaded, entities, real_transport, installed_pystove
):
    (session,) = real_transport.sessions
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    errors = {
        "payload": ClientPayloadError,
        "disconnect": ServerDisconnectedError,
        "timeout": TimeoutError,
    }
    kwargs = {"body": "{" if failure == "malformed" else "[]"}
    if failure in errors:
        kwargs = {"error": errors[failure]("synthetic body failure")}
    response = session.queue(*ENDPOINTS[case.id], **kwargs)
    prepare_direct_entity(case, entities)
    before = len(session.calls)
    data = deepcopy(real_loaded.data)
    expected = (
        HomeAssistantError
        if candidate
        else errors.get(
            failure, json.JSONDecodeError if failure == "malformed" else AttributeError
        )
    )
    platform = import_module(f"custom_components.hwam_stove.{case.platform}")
    with (
        patch.object(real_loaded, "async_request_refresh") as refresh,
        patch.object(
            platform, "require_command_confirmation",
            wraps=platform.require_command_confirmation,
        ) as confirmation,
        pytest.raises(expected) as raised,
    ):
        await invoke(case, entities)
    if candidate:
        assert_unconfirmed(raised.value)
        assert confirmation.call_count == 1
        assert confirmation.call_args.args[0] is False
    elif "error" in kwargs:
        assert raised.value is kwargs["error"]
    if not candidate:
        confirmation.assert_not_called()
    refresh.assert_not_called()
    assert len(session.calls) == before + 1 and response.exited
    assert real_loaded.data == data


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("confirmed", [True, False])
async def test_real_commands_true_false(
    case, confirmed, real_loaded, entities, real_transport
):
    (session,) = real_transport.sessions
    prepare_direct_entity(case, entities)
    session.queue(
        *ENDPOINTS[case.id],
        body=('{"response":"OK"}' if confirmed else '{"response":"ERROR"}'),
    )
    before = len(session.calls)
    if confirmed:
        await invoke(case, entities)
    else:
        with pytest.raises(HomeAssistantError) as raised:
            await invoke(case, entities)
        assert_unconfirmed(raised.value)
    assert len(session.calls) == before + 1


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_real_command_cancellation(case, real_loaded, entities, real_transport):
    (session,) = real_transport.sessions
    prepare_direct_entity(case, entities)
    response = session.queue(*ENDPOINTS[case.id], hold=True)
    before = len(session.calls)
    await cancel_at_body(invoke(case, entities), response)
    assert len(session.calls) == before + 1


async def test_real_get_data_cancellation(real_loaded, real_transport):
    (session,) = real_transport.sessions
    before = len(session.calls)
    response = session.queue("GET", "/get_stove_data", hold=True)
    await cancel_at_body(real_loaded.stove.get_data(), response)
    response = session.queue("GET", "/get_stove_data", hold=True)
    await cancel_at_body(real_loaded.async_refresh(), response)
    assert not real_loaded.last_update_success
    assert isinstance(real_loaded.last_exception, asyncio.CancelledError)
    await real_loaded.async_refresh()
    assert real_loaded.last_update_success
    assert len(session.calls) == before + 3


@pytest.mark.parametrize("failure", ["exception", "cancel", "child_cancel"])
async def test_create_failure_before_ha_ownership(
    failure, real_transport, installed_pystove, hass, entry
):
    from custom_components.hwam_stove import async_setup_entry

    session = real_transport.prepare()
    error = RuntimeError("synthetic identify failure")
    response = session.queue(
        "GET",
        "/esp/get_identification",
        **(
            {"hold": True}
            if failure == "cancel"
            else {"error": asyncio.CancelledError("child cancellation")}
            if failure == "child_cancel"
            else {"error": error}
        ),
    )
    if failure == "cancel":
        await cancel_at_body(async_setup_entry(hass, entry), response)
    else:
        with pytest.raises(
            RuntimeError if failure == "exception" else asyncio.CancelledError
        ) as raised:
            await async_setup_entry(hass, entry)
        if failure == "exception":
            assert raised.value is error
    assert not real_transport.destroyed  # No successfully returned client.
    assert not hass.data[DOMAIN]["stoves"]
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    assert session.close_calls == int(candidate)
    assert session.closed is candidate
    if not candidate:
        # Document the legacy factory leak, then clean the test-owned transport.
        # This is not an integration workaround or a suppressed candidate leak.
        await session.close()


@pytest.mark.parametrize("failure", ["exception", "cancel"])
async def test_h01a_owned_real_client(real_transport, hass, entry, failure):
    from custom_components.hwam_stove import async_setup_entry

    error = (
        RuntimeError("pre-forward construction failure")
        if failure == "exception"
        else asyncio.CancelledError("pre-forward cancellation")
    )
    with (
        patch("custom_components.hwam_stove.StoveCoordinator", side_effect=error),
        pytest.raises(type(error)) as raised,
    ):
        await async_setup_entry(hass, entry)
    assert raised.value is error
    (session,) = real_transport.sessions
    assert session.closed and session.close_calls == 1
    assert len(real_transport.destroyed) == 1
    assert DOMAIN not in hass.data


async def test_setup_unload_ownership(real_transport, hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (session,) = real_transport.sessions
    coordinator = hass.data[DOMAIN]["stoves"][entry.entry_id]
    assert type(coordinator.stove) is Stove
    assert not session.closed and not real_transport.destroyed
    assert await coordinator.stove.get_data() == status_data()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert real_transport.destroyed == [coordinator.stove]
    assert session.closed and session.close_calls == 1
    assert DOMAIN not in hass.data


async def test_h03_temporary_real_client(real_transport, hass):
    from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

    flow = HWAMStoveConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user({CONF_HOST: HOST, CONF_NAME: "Synthetic"})
    assert result["type"] == "create_entry"
    (session,) = real_transport.sessions
    assert session.closed and session.close_calls == 1
    assert len(real_transport.destroyed) == 1


@pytest.mark.parametrize("cause", ["error", "cancel"])
@pytest.mark.parametrize("cleanup", ["success", "error", "cancel"])
async def test_py314_create_cleanup_with_repeated_cancellation(
    cause, cleanup, real_transport, installed_pystove, hass, entry
):
    from custom_components.hwam_stove import async_setup_entry

    session = real_transport.prepare()
    response = session.queue("GET", "/esp/get_identification", hold=True)
    primary = RuntimeError("primary identify error")
    if cause == "error":
        response.error = primary
    candidate = installed_pystove["version"] == "0.3a2.dev0"
    if candidate:
        session.close_release.clear()
        if cleanup != "success":
            error_type = RuntimeError if cleanup == "error" else asyncio.CancelledError
            session.close_error = error_type("secondary close error")
    task = asyncio.create_task(async_setup_entry(hass, entry))
    try:
        await wait(response.entered)
        if cause == "cancel":
            task.cancel("primary cancellation")
        else:
            response.release.set()
        if candidate:
            await wait(session.close_started)
            for _ in range(2):
                task.cancel("cancellation during owned cleanup")
                await asyncio.sleep(0)
                assert not task.done()
            session.close_release.set()
        with pytest.raises(
            RuntimeError if cause == "error" else asyncio.CancelledError
        ) as raised:
            await task
        if cause == "error":
            assert raised.value is primary
        else:
            assert raised.value.args == ("primary cancellation",)
        assert not real_transport.destroyed
        assert session.close_calls == int(candidate)
        if not candidate:
            await session.close()  # Explicit baseline factory-leak characterization.
    finally:
        session.close_release.set()
        response.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cleanup", ["success", "error", "cancel"])
async def test_py314_h03_cancel_while_real_destroy_finishes(
    cleanup, real_transport, hass
):
    from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

    session = real_transport.prepare()
    session.close_release.clear()
    if cleanup != "success":
        error_type = RuntimeError if cleanup == "error" else asyncio.CancelledError
        session.close_error = error_type("synthetic close failure")
    flow = HWAMStoveConfigFlow()
    flow.hass = hass
    task = asyncio.create_task(
        flow.async_step_user({CONF_HOST: HOST, CONF_NAME: "Synthetic"})
    )
    try:
        await wait(session.close_started)
        for _ in range(2):
            task.cancel("config-flow caller cancellation")
            await asyncio.sleep(0)
            assert not task.done()
        session.close_release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("config-flow caller cancellation",)
        assert len(real_transport.destroyed) == 1
        assert session.closed and session.close_calls == 1
    finally:
        session.close_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_h01a_actual_invalid_first_read(real_transport, hass, entry):
    session = real_transport.prepare()
    session.queue("GET", "/get_stove_data", body="{}")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert session.closed and session.close_calls == 1
    assert len(real_transport.destroyed) == 1
    assert DOMAIN not in hass.data


@pytest.mark.parametrize("outcome", ["true", "false", "exception", "cancel"])
async def test_h05_real_pair_and_passive_resync(
    outcome, real_loaded, entities, real_transport
):
    begin = next(c for c in CASES if c.id == "night_begin")
    end = next(c for c in CASES if c.id == "night_end")
    (session,) = real_transport.sessions
    kwargs = {
        "body": '{"response":"OK"}' if outcome == "true" else '{"response":"ERROR"}'
    }
    if outcome == "exception":
        kwargs = {"error": RuntimeError("synthetic command failure")}
    elif outcome == "cancel":
        kwargs = {"hold": True}
    response = session.queue("POST", "/set_night_time", **kwargs)
    before = len(session.calls)
    if outcome == "cancel":
        await cancel_at_body(invoke(begin, entities), response)
    elif outcome == "true":
        await invoke(begin, entities)
    else:
        with pytest.raises(HomeAssistantError if outcome == "false" else RuntimeError):
            await invoke(begin, entities)
    if outcome == "true":
        session.queue("POST", "/set_night_time")
        await invoke(end, entities)
        assert json.loads(session.calls[-1][2]["data"]) == {
            "begin_hour": 21,
            "begin_minute": 0,
            "end_hour": 7,
            "end_minute": 0,
        }
    else:
        with pytest.raises(HomeAssistantError) as raised:
            await invoke(end, entities)
        assert raised.value.translation_key == "night_times_not_synchronized"
    assert len(session.calls) == before + (2 if outcome == "true" else 1)
    previous = deepcopy(real_loaded.data)
    await real_loaded.async_refresh()  # The only read: a later regular poll.
    assert real_loaded.data == previous and real_loaded.last_update_success
    session.queue("POST", "/set_night_time")
    await invoke(end, entities)
    assert json.loads(session.calls[-1][2]["data"]) == {
        "begin_hour": 22,
        "begin_minute": 15,
        "end_hour": 7,
        "end_minute": 0,
    }
    assert len(session.calls) == before + (4 if outcome == "true" else 3)


@pytest.mark.parametrize("case_id", ["burn", "start"])
async def test_open_h6_has_no_ha_status_workaround(
    case_id, real_loaded, entities, real_transport
):
    case = next(c for c in CASES if c.id == case_id)
    (session,) = real_transport.sessions
    session.queue(*ENDPOINTS[case_id], status=500)
    before = len(session.calls)
    await invoke(case, entities)
    assert len(session.calls) == before + 1
