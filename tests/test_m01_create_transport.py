"""M01: only proved create transport failures; real HA flows, no networking."""

import asyncio
from copy import deepcopy
import errno
import json
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
import pytest
from yarl import URL

from custom_components import hwam_stove
from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

from .helpers import COMMANDS, DOMAIN, HOST, SimulatedStove
from .test_m04_yaml_import import add_entry, entries
from .test_m05_hosts import finish, start, wait
from .test_m06_reconfigure import identities, reconfigure, yaml_config
from .test_pystove_boundary import real_transport

__all__ = ["real_transport", "yaml_config"]
pytestmark = pytest.mark.contract

TRANSPORT = [
    "timeout", "connection", "reset", "os", "connector", "dns", "disconnected",
    "server_timeout", "connect_timeout", "socket_timeout", "payload",
]
EXCLUDED = [
    "runtime", "key", "type", "attribute", "json", "cancel", "client",
    "invalid_url", "response", "redirect", "content_type", "tls", "certificate",
    "fingerprint", "proxy",
]
CALLERS = ["setup", "user", "import", "reconfigure"]


def failure(kind):
    key = SimpleNamespace(host=HOST, port=80, ssl=False, is_ssl=False)
    request = SimpleNamespace(real_url=URL(f"http://{HOST}/synthetic"))
    os_error = OSError(errno.ECONNREFUSED, "synthetic refusal")
    return {
        "timeout": lambda: TimeoutError("synthetic timeout"),
        "connection": lambda: aiohttp.ClientConnectionError("closed"),
        "reset": lambda: aiohttp.ClientConnectionResetError("reset"),
        "os": lambda: aiohttp.ClientOSError(errno.ECONNRESET, "reset"),
        "connector": lambda: aiohttp.ClientConnectorError(key, os_error),
        "dns": lambda: aiohttp.ClientConnectorDNSError(key, os_error),
        "disconnected": lambda: aiohttp.ServerDisconnectedError("disconnected"),
        "server_timeout": lambda: aiohttp.ServerTimeoutError("server timeout"),
        "connect_timeout": lambda: aiohttp.ConnectionTimeoutError("connect timeout"),
        "socket_timeout": lambda: aiohttp.SocketTimeoutError("socket timeout"),
        "payload": lambda: aiohttp.ClientPayloadError("incomplete body"),
        "runtime": lambda: RuntimeError("unexpected programming error"),
        "key": lambda: KeyError("success"),
        "type": lambda: TypeError("invalid XML body type"),
        "attribute": lambda: AttributeError("not a mapping"),
        "json": lambda: json.JSONDecodeError("invalid JSON", "synthetic", 0),
        "cancel": lambda: asyncio.CancelledError("original cancellation", 17),
        "client": lambda: aiohttp.ClientError("unclassified"),
        "invalid_url": lambda: aiohttp.InvalidURL("synthetic-invalid"),
        "response": lambda: aiohttp.ClientResponseError(request, (), status=500),
        "redirect": lambda: aiohttp.TooManyRedirects(request, ()),
        "content_type": lambda: aiohttp.ContentTypeError(request, ()),
        "tls": lambda: aiohttp.ClientConnectorSSLError(key, ssl.SSLError("TLS")),
        "certificate": lambda: aiohttp.ClientConnectorCertificateError(
            key, ssl.CertificateError("certificate")
        ),
        "fingerprint": lambda: aiohttp.ServerFingerprintMismatch(b"a", b"b", HOST, 443),
        "proxy": lambda: aiohttp.ClientProxyConnectionError(key, os_error),
    }[kind]()


@pytest.fixture(params=CALLERS)
def boundary(request, hass, monkeypatch, yaml_config):
    """Keep a real manager/entry; suppress only reload after a successful retry."""
    caller = request.param
    entry = add_entry(hass, "old.invalid" if caller == "reconfigure" else HOST) \
        if caller in {"setup", "reconfigure"} else None
    before = deepcopy(entry.as_dict()) if entry else None
    reload = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload)

    async def invoke():
        if caller == "setup":
            return await hwam_stove.async_setup_entry(hass, entry)
        if caller == "reconfigure":
            return await reconfigure(hass, entry, HOST)
        return await start(hass, caller)

    return SimpleNamespace(caller=caller, entry=entry, before=before,
                           reload=reload, invoke=invoke)


def assert_unchanged(boundary, hass):
    boundary.reload.assert_not_called()
    if boundary.entry:
        assert boundary.entry.as_dict() == boundary.before
        assert entries(hass) == [boundary.entry]
    else:
        assert not entries(hass)


def no_extra_requests(stove):
    stove.get_data.assert_not_called()
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()


async def retry_flow(boundary, hass, stove_factory, failed=None):
    """Public retry proves a still-running caller left no host reservation."""
    if boundary.caller == "setup":
        return
    client = SimulatedStove()
    stove_factory.side_effect = None
    stove_factory.return_value = client
    with patch.object(hwam_stove, "async_setup_entry", AsyncMock(return_value=True)):
        if failed:
            data = {"host": HOST}
            if boundary.caller != "reconfigure":
                data["name"] = "Retry"
            result = await hass.config_entries.flow.async_configure(
                failed["flow_id"], data
            )
        else:
            result = await boundary.invoke()
        await hass.async_block_till_done()
    if boundary.caller == "reconfigure":
        assert result["reason"] == "reconfigure_successful"
    else:
        assert result["type"] == "create_entry"
    client.destroy.assert_awaited_once_with()
    no_extra_requests(client)


@pytest.mark.parametrize("kind", TRANSPORT)
async def test_create_mapping_preserves_ownership_and_retry(
    boundary, hass, stove, stove_factory, kind
):
    error = failure(kind)
    stove_factory.side_effect = error
    result = None
    if boundary.caller == "setup":
        with pytest.raises(ConfigEntryNotReady) as caught:
            await boundary.invoke()
        assert caught.value.__cause__ is error
    else:
        result = await boundary.invoke()
        assert result["errors"] == {"base": "cannot_connect"}
    assert_unchanged(boundary, hass)
    stove_factory.assert_awaited_once_with(HOST)
    stove.destroy.assert_not_called()  # Factory never transferred client ownership.
    no_extra_requests(stove)
    await retry_flow(boundary, hass, stove_factory, result)


@pytest.mark.parametrize("kind", EXCLUDED)
async def test_nontransport_error_and_cancellation_propagate_unchanged(
    boundary, hass, stove, stove_factory, kind
):
    error = failure(kind)
    stove_factory.side_effect = error
    with pytest.raises(type(error)) as caught:
        await boundary.invoke()
    assert caught.value is error
    assert_unchanged(boundary, hass)
    stove_factory.assert_awaited_once_with(HOST)
    stove.destroy.assert_not_called()
    no_extra_requests(stove)
    await retry_flow(boundary, hass, stove_factory)


@pytest.mark.parametrize("kind", TRANSPORT)
async def test_ha_setup_enters_retry_state(hass, entry, stove, stove_factory, kind):
    stove_factory.side_effect = failure(kind)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    stove_factory.assert_awaited_once_with(HOST)
    stove.destroy.assert_not_called()
    assert not hass.data[DOMAIN]["stoves"]
    # Explicitly cancel HA's existing retry schedule; no integration retry added.
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("kind", TRANSPORT)
async def test_shared_validation_retains_transport_cause(stove_factory, kind):
    error = failure(kind)
    stove_factory.side_effect = error
    with pytest.raises(ConnectionError) as caught:
        await HWAMStoveConfigFlow()._async_test_connection(HOST)
    assert type(caught.value) is ConnectionError
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("boundary", CALLERS[1:], indirect=True)
@pytest.mark.parametrize(
    "kind", ["timeout", "disconnected", "payload", "runtime", "cancel"]
)
@pytest.mark.parametrize("point", ["identity", "close"])
async def test_mapping_does_not_extend_past_create(
    boundary, hass, stove, stove_factory, kind, point
):
    error = failure(kind)
    if point == "identity":
        from unittest.mock import PropertyMock

        with patch.object(type(stove), "name", PropertyMock(side_effect=error),
                          create=True), pytest.raises(type(error)) as caught:
            await boundary.invoke()
    else:
        stove.destroy.side_effect = error
        with pytest.raises(type(error)) as caught:
            await boundary.invoke()
    assert caught.value is error
    assert_unchanged(boundary, hass)
    stove_factory.assert_awaited_once_with(HOST)
    stove.destroy.assert_awaited_once_with()
    no_extra_requests(stove)
    await retry_flow(boundary, hass, stove_factory)


@pytest.mark.parametrize("kind", TRANSPORT + ["cancel", "runtime"])
async def test_failed_reconfigure_keeps_live_runtime(
    loaded, hass, entry, stove, stove_factory, yaml_config, kind
):
    before = deepcopy(identities(hass, entry))
    data = deepcopy(entry.as_dict())
    error = failure(kind)
    stove_factory.reset_mock()
    stove_factory.side_effect = error
    if kind in {"cancel", "runtime"}:
        with pytest.raises(type(error)) as caught:
            await reconfigure(hass, entry)
        assert caught.value is error
    else:
        result = await reconfigure(hass, entry)
        assert result["errors"] == {"base": "cannot_connect"}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.as_dict() == data
    assert identities(hass, entry) == before
    assert hass.data[DOMAIN]["stoves"][entry.entry_id] is loaded
    stove.destroy.assert_not_called()
    await loaded.async_refresh()
    assert loaded.last_update_success  # The original running client still works.


@pytest.mark.parametrize("kind", ["disconnected", "payload"])
async def test_failed_flow_does_not_release_other_host(
    hass, stove_factory, kind, yaml_config
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(host):
        if host == "other.invalid":
            entered.set()
            await release.wait()
            return SimulatedStove()
        raise failure(kind)

    stove_factory.side_effect = create
    with patch.object(hwam_stove, "async_setup_entry", AsyncMock(return_value=True)):
        task = asyncio.create_task(start(hass, "user", "other.invalid"))
        try:
            await wait(entered)
            assert (await start(hass, "import"))["errors"] == {"base": "cannot_connect"}
            assert (await start(hass, "import", "OTHER.INVALID"))["reason"] \
                == "already_in_progress"
            release.set()
            assert (await task)["type"] == "create_entry"
        finally:
            await finish(task, release)


@pytest.mark.parametrize(
    "kind", ["timeout", "connection", "reset", "os", "disconnected", "payload"]
)
@pytest.mark.parametrize("endpoint", ["identity", "open", "read"])
async def test_real_create_transport_and_file_cleanup(
    boundary, hass, real_transport, kind, endpoint
):
    session = real_transport.prepare()
    path = {"identity": ("GET", "/esp/get_identification"),
            "open": ("POST", "/open_file"), "read": ("POST", "/read_open_file")}
    session.queue(*path[endpoint], error=failure(kind))
    if boundary.caller == "setup":
        with pytest.raises(ConfigEntryNotReady):
            await boundary.invoke()
    else:
        assert (await boundary.invoke())["errors"] == {"base": "cannot_connect"}
    assert_unchanged(boundary, hass)
    assert not real_transport.destroyed  # Library closes its unreturned client.
    assert session.closed and session.close_calls == 1
    calls = [call[:2] for call in session.calls]
    assert len(calls) == len(set(calls))  # No integration retries/additional reads.
    assert ("GET", "/get_stove_data") not in calls
    assert calls.count(("GET", "/close_file")) == int(endpoint != "open")
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            code = getattr(task.get_coro(), "cr_code", None)
            assert code is None or "/pystove/" not in code.co_filename


async def test_real_connector_error_is_consumed_before_factory_returns(
    boundary, hass, real_transport
):
    """Do not falsely claim a normally swallowed connector error reaches M01."""
    session = real_transport.prepare()
    for method, path in [("GET", "/esp/get_identification"),
                         ("GET", "/esp/get_current_accesspoint"),
                         ("POST", "/open_file"), ("GET", "/get_stove_data")]:
        session.queue(method, path, error=failure("connector"))
    if boundary.caller == "setup":
        # Direct setup requires HA's setup state for first refresh.
        assert not await hass.config_entries.async_setup(boundary.entry.entry_id)
        assert boundary.entry.state is ConfigEntryState.SETUP_RETRY
        assert await hass.config_entries.async_unload(boundary.entry.entry_id)
    else:
        assert (await boundary.invoke())["errors"] == {"base": "cannot_connect"}
        assert_unchanged(boundary, hass)
    # Successful create transferred ownership; integration closes exactly once.
    assert len(real_transport.destroyed) == 1
    assert session.closed and session.close_calls == 1
    calls = [call[:2] for call in session.calls]
    assert len(calls) == 3 + int(boundary.caller == "setup")
    assert ("POST", "/read_open_file") not in calls
    assert ("GET", "/close_file") not in calls


@pytest.mark.parametrize("outcome", ["transport", "cancel"])
async def test_real_create_cleanup_finishes_before_mapping_or_cancellation(
    boundary, hass, real_transport, outcome
):
    session = real_transport.prepare()
    primary = aiohttp.ClientPayloadError("primary read failure")
    read = session.queue("POST", "/read_open_file", hold=True,
                         error=primary if outcome == "transport" else None)
    close = session.queue("GET", "/close_file", hold=True)
    task = asyncio.create_task(boundary.invoke())
    try:
        await wait(read.entered)
        if outcome == "transport":
            read.release.set()
        else:
            task.cancel("original cancellation")
        await wait(close.entered)
        for _ in range(3):
            task.cancel("later cancellation during library cleanup")
            await asyncio.sleep(0)
            assert not task.done() and not session.closed
            assert not real_transport.destroyed
        close.release.set()
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ("original cancellation",)
        elif boundary.caller == "setup":
            with pytest.raises(ConfigEntryNotReady) as caught:
                await task
            assert caught.value.__cause__ is primary
        else:
            assert (await task)["errors"] == {"base": "cannot_connect"}
        assert_unchanged(boundary, hass)
        assert session.closed and session.close_calls == 1
        assert close.exited and close.reader.done()
        assert not real_transport.destroyed
        calls = [call[:2] for call in session.calls]
        assert calls.count(("POST", "/open_file")) == 1
        assert calls.count(("POST", "/read_open_file")) == 1
        assert calls.count(("GET", "/close_file")) == 1
        if boundary.caller != "setup":
            # The cancelled/failed attempt's reservation is gone before a new flow.
            result = await boundary.invoke()
            if boundary.caller == "reconfigure":
                assert result["reason"] == "reconfigure_successful"
            else:
                assert result["type"] == "create_entry"
                # Normally setup owns a new runtime; unload it explicitly below.
                await hass.async_block_till_done()
                assert await hass.config_entries.async_unload(result["result"].entry_id)
    finally:
        read.release.set()
        await finish(task, close.release)


@pytest.mark.parametrize("endpoint,body,error_type", [
    (("POST", "/open_file"), "invalid", json.JSONDecodeError),
    (("POST", "/open_file"), "{}", KeyError),
    (("POST", "/read_open_file"), None, TypeError),
    (("GET", "/esp/get_current_accesspoint"), "[]", AttributeError),
])
async def test_real_identification_structure_errors_are_not_transport(
    boundary, hass, real_transport, endpoint, body, error_type
):
    session = real_transport.prepare()
    session.queue(*endpoint, body=body)
    with pytest.raises(error_type):
        await boundary.invoke()
    assert_unchanged(boundary, hass)
    assert session.closed and session.close_calls == 1
    assert not real_transport.destroyed
