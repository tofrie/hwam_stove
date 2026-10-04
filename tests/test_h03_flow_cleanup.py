"""H03: temporary flow-client ownership, without controller networking."""

import asyncio
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
import pytest

from pystove import pystove

from .helpers import COMMANDS, DOMAIN, HOST, SimulatedStove

INPUT = {"name": "Test stove", "host": HOST}


@pytest.fixture(params=["user", "import"])
def step(request):
    return request.param


@pytest.fixture
def flow(hass, monkeypatch):
    from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

    flow = HWAMStoveConfigFlow()
    flow.hass = hass
    monkeypatch.setattr(flow, "_create_entry", Mock(wraps=flow._create_entry))
    return flow


async def invoke(flow, step):
    return await getattr(flow, f"async_step_{step}")(dict(INPUT))


def assert_no_extra_communication(stove):
    stove.get_data.assert_not_called()
    for command in COMMANDS:
        getattr(stove, command).assert_not_called()


@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_IMPORT])
async def test_success_closes_before_real_entry_creation(
    hass, stove, stove_factory, source
):
    from custom_components.hwam_stove.config_flow import HWAMStoveConfigFlow

    events = []
    create_entry = HWAMStoveConfigFlow._create_entry

    async def close():
        assert not hass.config_entries.async_entries(DOMAIN)
        events.append("closed")

    def entry_after_close(self, name, host):
        assert events == ["closed"]
        events.append("entry")
        return create_entry(self, name, host)

    stove.destroy.side_effect = close
    with (
        patch("custom_components.hwam_stove.async_setup_entry",
              AsyncMock(return_value=True)),
        patch.object(HWAMStoveConfigFlow, "_create_entry", entry_after_close),
    ):
        if source == SOURCE_USER:
            form = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": source}
            )
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], INPUT
            )
        else:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": source}, data=INPUT
            )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["title"] == INPUT["name"]
    assert result["data"] == INPUT
    assert result["result"].version == 2
    assert result["result"].source == source
    assert events == ["closed", "entry"]
    stove_factory.assert_awaited_once_with(HOST)
    stove.destroy.assert_awaited_once_with()
    assert_no_extra_communication(stove)


async def test_H03_flow_closes_after_validation_exception(flow, stove, step):
    """Converted original H03 strict-xfail; repeat for the shared import path."""
    del stove.name
    with pytest.raises(AttributeError):
        await invoke(flow, step)
    stove.destroy.assert_awaited_once_with()
    flow._create_entry.assert_not_called()
    assert_no_extra_communication(stove)


@pytest.mark.parametrize("field", ["name", "stove_ip"])
async def test_existing_unknown_identity_mapping(flow, stove, step, field):
    setattr(stove, field, pystove.UNKNOWN)
    result = await invoke(flow, step)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
    stove.destroy.assert_awaited_once_with()
    flow._create_entry.assert_not_called()
    assert_no_extra_communication(stove)


@pytest.mark.parametrize("primary", [RuntimeError("identity failed"),
                                     asyncio.CancelledError("validation cancelled", 7)])
@pytest.mark.parametrize("cleanup", [None, RuntimeError("close failed"),
                                     asyncio.CancelledError("close cancelled")])
async def test_primary_validation_error_or_cancellation_survives_cleanup(
    flow, stove, step, primary, cleanup, caplog
):
    stove.destroy.side_effect = cleanup
    with (
        patch.object(type(stove), "name", new_callable=PropertyMock,
                     create=True, side_effect=primary),
        pytest.raises(type(primary)) as caught,
    ):
        await invoke(flow, step)
    assert caught.value is primary
    assert caught.value.args == primary.args
    stove.destroy.assert_awaited_once_with()
    flow._create_entry.assert_not_called()
    assert_no_extra_communication(stove)
    if cleanup is not None:
        assert "Failed to close temporary config-flow Stove" in caplog.text


async def test_invalid_identity_and_close_failure_still_maps_primary_connection_error(
    flow, stove, step, caplog
):
    stove.name = pystove.UNKNOWN
    stove.destroy.side_effect = RuntimeError("secondary close failure")
    result = await invoke(flow, step)
    assert result["errors"] == {"base": "cannot_connect"}
    stove.destroy.assert_awaited_once_with()
    flow._create_entry.assert_not_called()
    assert "secondary close failure" in caplog.text


@pytest.mark.parametrize("error", [ConnectionError("create connection error"),
    TimeoutError("create timeout"), aiohttp.ClientConnectionError("aiohttp failure"),
    asyncio.CancelledError("create cancelled")])
async def test_factory_failure_does_not_close_unowned_client(
    flow, stove, stove_factory, step, error
):
    stove_factory.side_effect = error
    if isinstance(error, ConnectionError):
        result = await invoke(flow, step)
        assert result["errors"] == {"base": "cannot_connect"}
    else:
        with pytest.raises(type(error)) as caught:
            await invoke(flow, step)
        assert caught.value is error
    stove.destroy.assert_not_called()
    flow._create_entry.assert_not_called()
    assert_no_extra_communication(stove)


async def test_task_cancellation_inside_create_has_no_owned_client(
    flow, stove, stove_factory, step
):
    entered = asyncio.Event()

    async def create(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    stove_factory.side_effect = create
    task = asyncio.create_task(invoke(flow, step))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        task.cancel("cancel inside create")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("cancel inside create",)
        stove.destroy.assert_not_called()
        flow._create_entry.assert_not_called()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("error", [
    RuntimeError("close failed"), ConnectionError("close connection error"),
    asyncio.CancelledError("close cancelled")
])
async def test_close_failure_after_success_prevents_real_entry(
    hass, stove, step, error
):
    stove.destroy.side_effect = error
    if isinstance(error, ConnectionError):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": step}, data=INPUT
        )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}
    else:
        with pytest.raises(type(error)) as caught:
            await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": step}, data=INPUT
            )
        assert caught.value.args == error.args
    stove.destroy.assert_awaited_once_with()
    assert not hass.config_entries.async_entries(DOMAIN)
    assert_no_extra_communication(stove)


@pytest.mark.parametrize("cleanup_error", [None, RuntimeError("close failed later"),
                                           asyncio.CancelledError("close cancelled")])
async def test_real_cancellation_waits_for_single_close_and_preserves_first_message(
    flow, stove, step, cleanup_error, caplog
):
    """Only cleanup suspends after create: cancel twice while that close is pending."""
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    close_task = None

    async def close():
        nonlocal close_task
        close_task = asyncio.current_task()
        entered.set()
        await release.wait()
        finished.set()
        if cleanup_error is not None:
            raise cleanup_error

    stove.destroy.side_effect = close
    task = asyncio.create_task(invoke(flow, step))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        task.cancel("first cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        assert not close_task.done()
        task.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not task.done()
        assert not close_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("first cancellation",)
        assert finished.is_set()
        assert close_task.done()
        stove.destroy.assert_awaited_once_with()
        flow._create_entry.assert_not_called()
        assert_no_extra_communication(stove)
        if cleanup_error is not None:
            assert "Failed to close temporary Stove during cancellation" in caplog.text
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if close_task is not None:
            await asyncio.gather(close_task, return_exceptions=True)


async def test_duplicate_host_never_acquires_client(
    flow, entry, stove, stove_factory, step
):
    result = await invoke(flow, step)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "already_configured"}
    stove_factory.assert_not_called()
    stove.destroy.assert_not_called()
    flow._create_entry.assert_not_called()


async def test_repeated_flow_attempts_close_each_temporary_client(
    flow, stove_factory, step
):
    clients = [SimulatedStove() for _ in range(3)]
    clients[0].name = clients[1].name = pystove.UNKNOWN
    stove_factory.side_effect = clients
    for index, client in enumerate(clients):
        result = await invoke(flow, step)
        assert result["type"] == ("form" if index < 2 else "create_entry")
        client.destroy.assert_awaited_once_with()
        assert_no_extra_communication(client)
    assert stove_factory.await_count == 3
    for client in clients:
        client.destroy.assert_awaited_once_with()
    flow._create_entry.assert_called_once_with(INPUT["name"], HOST)
