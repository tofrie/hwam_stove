"""M05 host grammar and real HA flow races; no controller/DNS/socket access."""

import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientRequest
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from yarl import URL

from custom_components.hwam_stove._host import host_key, normalize_host
from pystove import Stove, pystove

from .helpers import COMMANDS, DOMAIN, HOST, SimulatedStove
from .library_transport import BorrowedSession
from .registry_helpers import seed_historical
from .test_m04_yaml_import import add_entry, counts, entries, imports, run
from .test_pystove_boundary import real_transport

pytestmark = pytest.mark.contract
# Reuse fixtures explicitly; Ruff must not mistake them for unused imports.
__all__ = ["imports", "real_transport"]

VALID = [
    ("192.0.2.1", "192.0.2.1"),
    (" 192.0.2.1 ", "192.0.2.1"),
    ("0.0.0.0", "0.0.0.0"),
    ("255.255.255.255", "255.255.255.255"),
    (" STOVE.Local ", "stove.local"),
    ("STOVE", "stove"),
    ("localhost", "localhost"),
    ("3rd-stove.home.arpa", "3rd-stove.home.arpa"),
    ("stove.local.", "stove.local."),
    ("xn--bcher-kva.local", "xn--bcher-kva.local"),
    ("a" * 63 + ".local", "a" * 63 + ".local"),
    (".".join(["a" * 63] * 3 + ["b" * 61]) + ".",
     ".".join(["a" * 63] * 3 + ["b" * 61]) + "."),
    ("2001:0DB8:0:0:0:0:0:1", "[2001:db8::1]"),
    ("[2001:DB8::1]", "[2001:db8::1]"),
    (" ::1 ", "[::1]"),
    ("::ffff:192.0.2.1", "[::ffff:192.0.2.1]"),
]
INVALID = [
    "", " ", "\t", "stove\n", "\tstove", "st ove", "stove\r\n",
    "http://stove", "https://stove", "//stove", "stove/", "stove/path",
    "stove?query", "stove#fragment", "user@stove", "user:pass@stove",
    "stove:80", "192.0.2.1:80", "[2001:db8::1]:80", "[2001:db8::1]:",
    "stove\\path", "stove%2flocal", "stove%00", "stove_local", "*.local",
    ".", ".stove", "stove..local", "stove.local..", "-stove", "stove-",
    "stove.-local", "a" * 64, ".".join(["a" * 63] * 4),
    "bücher.local", "Kamin.local", "stove\u00a0", "stove。local",
    "127.1", "127.0.1", "2130706433", "0177.0.0.1", "127.01.2.3",
    "0x7f000001", "0x7f.0.0.1", "256.1.2.3", "1.2.3.4.5", "1.2.3.4.",
    "[192.0.2.1]", "[::1", "::1]", "[::1]/", "2001:db8::zz", ":::",
    "fe80::1%en0", "[fe80::1%25en0]",
    None, 123,
    *[f"stove{chr(i)}local" for i in [*range(32), 127]],
]


@pytest.mark.parametrize(("value", "expected"), VALID)
def test_normalization(value, expected):
    assert normalize_host(value) == expected
    assert normalize_host(expected) == expected
    assert host_key(value) == host_key(expected)


@pytest.mark.parametrize("value", INVALID)
async def test_invalid_host_stays_in_form_without_client(hass, stove_factory, value):
    with pytest.raises(ValueError):
        normalize_host(value)
    result = await start(hass, SOURCE_USER, value)
    assert result["type"] == "form"
    assert result["errors"] == {"host": "invalid_host"}
    assert not entries(hass)
    stove_factory.assert_not_called()


@pytest.mark.parametrize(("first", "second"), [
    ("stove", "stove.local"), ("stove.local", "192.0.2.1"),
    ("stove.local", "stove.local."), ("192.0.2.1", "::ffff:192.0.2.1"),
    ("stove.local", "other.local"),
])
def test_no_identity_or_dns_equivalence(first, second):
    assert host_key(first) != host_key(second)


async def start(hass, source, host=HOST, name="Test stove"):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source}, data={"host": host, "name": name}
    )


async def wait(event):
    async with asyncio.timeout(5):
        await event.wait()


async def finish(task, release):
    """Always drain test-owned tasks, even if an assertion fails."""
    release.set()
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("sources", [
    (SOURCE_USER, SOURCE_USER), (SOURCE_IMPORT, SOURCE_USER),
    (SOURCE_USER, SOURCE_IMPORT), (SOURCE_IMPORT, SOURCE_IMPORT),
])
@pytest.mark.parametrize(("first", "second", "canonical"), [
    (HOST, HOST, HOST), (" STOVE.invalid ", HOST, HOST),
    ("2001:0DB8:0:0:0:0:0:1", "[2001:db8::1]", "[2001:db8::1]"),
])
async def test_same_host_concurrent_flows_have_one_owner(
    imports, hass, stove_factory, sources, first, second, canonical
):
    entered, release = asyncio.Event(), asyncio.Event()
    client = SimulatedStove()

    async def create(host):
        assert host == canonical
        entered.set()
        await release.wait()
        return client

    stove_factory.side_effect = create
    task = asyncio.create_task(start(hass, sources[0], first))
    try:
        await wait(entered)
        duplicate = await start(hass, sources[1], second)
        assert duplicate["type"] == "abort"
        assert duplicate["reason"] == "already_in_progress"
        assert not entries(hass)
        assert stove_factory.await_count == 1
        release.set()
        result = await task
        assert result["type"] == "create_entry"
        assert result["result"].unique_id is None
        assert result["data"] == {"host": canonical, "name": "Test stove"}
        assert len(entries(hass)) == 1
        client.destroy.assert_awaited_once_with()
        for command in COMMANDS:
            getattr(client, command).assert_not_called()
        duplicate = await start(hass, sources[1], second)
        assert duplicate["reason"] == "already_configured"
        assert stove_factory.await_count == 1
    finally:
        await finish(task, release)


async def test_distinct_hosts_validate_concurrently(imports, hass, stove_factory):
    release = asyncio.Event()
    entered = {host: asyncio.Event() for host in [HOST, "other.invalid"]}
    clients = {host: SimulatedStove() for host in entered}

    async def create(host):
        entered[host].set()
        await release.wait()
        return clients[host]

    stove_factory.side_effect = create
    tasks = [asyncio.create_task(start(hass, SOURCE_USER, host)) for host in entered]
    try:
        for event in entered.values():
            await wait(event)
        release.set()
        results = await asyncio.gather(*tasks)
        assert all(r["type"] == "create_entry" for r in results)
        assert {e.data["host"] for e in entries(hass)} == set(entered)
        assert stove_factory.await_count == 2
        for client in clients.values():
            client.destroy.assert_awaited_once_with()
    finally:
        for task in tasks:
            await finish(task, release)


@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_IMPORT])
@pytest.mark.parametrize(("stored", "entered"), [
    (" STOVE.invalid ", HOST), (HOST, "STOVE.INVALID"),
    ("[2001:0DB8:0:0:0:0:0:1]", "2001:db8::1"),
])
async def test_existing_entry_is_preserved(
    imports, hass, stove_factory, source, stored, entered
):
    entry = add_entry(hass, stored, "Preserved name")
    before = entry.as_dict()
    result = await start(hass, source, entered, "New name")
    assert result["reason"] == "already_configured"
    assert entries(hass) == [entry]
    assert entry.as_dict() == before
    stove_factory.assert_not_called()


@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_IMPORT])
@pytest.mark.parametrize("failure", ["identity", "connection", "exception", "cancel"])
async def test_failed_validation_releases_host_for_other_flow_and_retry(
    imports, hass, stove_factory, source, failure
):
    entered, release = asyncio.Event(), asyncio.Event()
    client = SimulatedStove()
    error = RuntimeError("synthetic failure")

    async def create(host):
        entered.set()
        await release.wait()
        if failure == "connection":
            raise ConnectionError
        if failure == "exception":
            raise error
        client.name = pystove.UNKNOWN
        return client

    stove_factory.side_effect = create
    task = asyncio.create_task(start(hass, source))
    try:
        await wait(entered)
        other = await start(hass, SOURCE_USER, "STOVE.INVALID")
        assert other["reason"] == "already_in_progress"
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if failure == "exception":
                with pytest.raises(RuntimeError) as caught:
                    await task
                assert caught.value is error
            else:
                failed = await task
                assert failed["errors"] == {"base": "cannot_connect"}
        assert not entries(hass)
        assert client.destroy.await_count == int(failure == "identity")
        stove_factory.side_effect = lambda host: SimulatedStove()
        retry = await start(hass, SOURCE_USER)
        assert retry["type"] == "create_entry"
        assert len(entries(hass)) == 1
        assert stove_factory.await_count == 2
    finally:
        await finish(task, release)


async def test_failed_form_can_retry_and_invalid_form_does_not_reserve(
    imports, hass, stove_factory
):
    invalid = await start(hass, SOURCE_USER, "http://stove.invalid")
    stove_factory.assert_not_called()
    stove_factory.side_effect = ConnectionError
    failed = await hass.config_entries.flow.async_configure(
        invalid["flow_id"], {"host": HOST, "name": "Retry"}
    )
    assert failed["errors"] == {"base": "cannot_connect"}
    stove_factory.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        failed["flow_id"], {"host": HOST, "name": "Retry"}
    )
    assert result["type"] == "create_entry"
    assert stove_factory.await_count == 2


@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_does_not_release_another_hosts_reservation(
    imports, hass, stove_factory, cancel
):
    hosts = [HOST, "other.invalid"]
    entered = {host: asyncio.Event() for host in hosts}
    released = {host: asyncio.Event() for host in hosts}
    client = SimulatedStove()

    async def create(host):
        entered[host].set()
        await released[host].wait()
        if host == HOST:
            raise ConnectionError
        return client

    stove_factory.side_effect = create
    tasks = [asyncio.create_task(start(hass, SOURCE_USER, host)) for host in hosts]
    try:
        for event in entered.values():
            await wait(event)
        if cancel:
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
        else:
            released[HOST].set()
            assert (await tasks[0])["errors"] == {"base": "cannot_connect"}
        result = await start(hass, SOURCE_IMPORT, "OTHER.INVALID")
        assert result["reason"] == "already_in_progress"
        assert stove_factory.await_count == 2
        released["other.invalid"].set()
        assert (await tasks[1])["type"] == "create_entry"
        client.destroy.assert_awaited_once_with()
    finally:
        for host, task in zip(hosts, tasks, strict=True):
            await finish(task, released[host])


@pytest.mark.parametrize("outcome", ["success", "exception", "cancel"])
async def test_reservation_covers_entry_registration_handoff(
    imports, hass, stove_factory, outcome
):
    """HA's finisher may await after CREATE_ENTRY but before entry insertion."""
    entered, release = asyncio.Event(), asyncio.Event()
    manager = hass.config_entries.flow
    original = manager.async_finish_flow
    error = RuntimeError("synthetic registration failure")
    clients = []

    def create(host):
        clients.append(SimulatedStove())
        return clients[-1]

    stove_factory.side_effect = create

    async def delayed(flow, result):
        if result["type"] == "create_entry":
            entered.set()
            await release.wait()
            if outcome == "exception":
                raise error
        return await original(flow, result)

    with patch.object(manager, "async_finish_flow", delayed):
        task = asyncio.create_task(start(hass, SOURCE_USER))
        try:
            await wait(entered)
            assert not entries(hass)
            other = await start(hass, SOURCE_IMPORT, "STOVE.INVALID")
            assert other["reason"] == "already_in_progress"
            assert stove_factory.await_count == 1
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                if outcome == "exception":
                    with pytest.raises(RuntimeError) as caught:
                        await task
                    assert caught.value is error
                else:
                    assert (await task)["type"] == "create_entry"
        finally:
            await finish(task, release)
    retry = await start(hass, SOURCE_USER)
    assert retry["type"] == ("abort" if outcome == "success" else "create_entry")
    assert len(entries(hass)) == 1
    for client in clients:
        client.destroy.assert_awaited_once_with()


@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_IMPORT])
async def test_entry_appearing_during_validation_is_not_replaced(
    imports, hass, stove_factory, source
):
    created = []

    async def create(host):
        created.append(add_entry(hass, " STOVE.INVALID ", "Existing"))
        return SimulatedStove()

    stove_factory.side_effect = create
    result = await start(hass, source)
    assert result["reason"] == "already_configured"
    assert entries(hass) == created
    assert created[0].data["host"] == " STOVE.INVALID "


async def test_cancelled_close_keeps_reservation_until_client_is_closed(
    imports, hass, stove, stove_factory
):
    entered, release = asyncio.Event(), asyncio.Event()
    closed = asyncio.Event()

    async def close():
        entered.set()
        await release.wait()
        closed.set()

    stove.destroy.side_effect = close
    task = asyncio.create_task(start(hass, SOURCE_USER))
    try:
        await wait(entered)
        task.cancel("first")
        await asyncio.sleep(0)
        task.cancel("second")
        await asyncio.sleep(0)
        assert not task.done() and not closed.is_set()
        duplicate = await start(hass, SOURCE_IMPORT)
        assert duplicate["reason"] == "already_in_progress"
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("first",)
        assert closed.is_set()
        stove.destroy.assert_awaited_once_with()
        stove_factory.side_effect = lambda host: SimulatedStove()
        assert (await start(hass, SOURCE_USER))["type"] == "create_entry"
        assert stove_factory.await_count == 2
    finally:
        await finish(task, release)


async def test_yaml_normalized_duplicates_invalid_and_distinct_hosts(
    imports, hass, stove_factory
):
    existing = add_entry(hass, " STOVE.INVALID ")
    devices = {
        "old": {"host": HOST}, "same": {"host": "STOVE.invalid"},
        "new": {"host": " SECOND.invalid "}, "duplicate": {"host": "second.invalid"},
        "absolute": {"host": "second.invalid."},
        "bad": {"host": "http://stove.invalid"},
    }
    await run(hass, devices)
    assert existing in entries(hass)
    assert len(entries(hass)) == 3
    assert {e.data["host"] for e in entries(hass)} == {
        " STOVE.INVALID ", "second.invalid", "second.invalid."
    }
    assert counts(hass) == dict(configured=3, total=4, missing=1, duplicates=2)
    assert stove_factory.await_count == 2
    before = [entry.as_dict() for entry in entries(hass)]
    await run(hass, devices)
    assert [entry.as_dict() for entry in entries(hass)] == before
    assert stove_factory.await_count == 2


async def test_yaml_batch_does_not_steal_pending_user_host(
    imports, hass, stove_factory
):
    entered, release = asyncio.Event(), asyncio.Event()
    client = SimulatedStove()

    async def create(host):
        entered.set()
        await release.wait()
        return client

    stove_factory.side_effect = create
    task = asyncio.create_task(start(hass, SOURCE_USER))
    try:
        await wait(entered)
        # Await the batch directly: global block_till_done would wait for the
        # deliberately held user flow as well. This is the real M04 worker.
        from custom_components.hwam_stove import _async_import_yaml

        await _async_import_yaml(hass, {"yaml": {"host": "STOVE.INVALID"}})
        assert counts(hass) == dict(configured=0, total=1, missing=1, duplicates=0)
        assert stove_factory.await_count == 1
        assert not task.done()
        release.set()
        result = await task
        assert result["type"] == "create_entry"
        assert result["result"].source == SOURCE_USER
        await run(hass, {"yaml": {"host": "STOVE.INVALID"}})
        assert counts(hass)["missing"] == 0
        assert len(entries(hass)) == 1
        assert stove_factory.await_count == 1
        client.destroy.assert_awaited_once_with()
    finally:
        await finish(task, release)


async def test_b01_historical_identities_untouched_by_duplicate_guard(
    imports, hass, entry, stove_factory
):
    seed_historical(hass, entry)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    before_devices = deepcopy(
        dr.async_entries_for_config_entry(devices, entry.entry_id)
    )
    before_entities = deepcopy(
        er.async_entries_for_config_entry(entities, entry.entry_id)
    )
    before_entry = entry.as_dict()
    result = await start(hass, SOURCE_USER, " STOVE.INVALID ")
    assert result["reason"] == "already_configured"
    await run(hass, {"old": {"host": " STOVE.INVALID "}})
    assert entry.as_dict() == before_entry
    assert dr.async_entries_for_config_entry(devices, entry.entry_id) == before_devices
    assert (
        er.async_entries_for_config_entry(entities, entry.entry_id) == before_entities
    )
    stove_factory.assert_not_called()


@pytest.mark.parametrize(("value", "expected"), VALID)
async def test_existing_library_url_builder_accepts_normalized_hosts(
    real_transport, monkeypatch, value, expected
):
    """Actual published create/read/close URLs + aiohttp encoding; no sockets."""
    session = real_transport.prepare()
    request = session.request
    urls = []

    def observe(method, url, kwargs):
        parsed = URL(url)
        wire = ClientRequest(method, parsed)
        assert wire.connection_key.host == expected.strip("[]")
        assert parsed.scheme == "http" and parsed.port == 80
        assert parsed.user is None and parsed.password is None
        assert not parsed.query and not parsed.fragment
        urls.append((method, parsed.path))
        return request(method, f"http://{HOST}{parsed.path}", kwargs)

    def borrowed_get(borrower, url, **kwargs):
        assert borrower._retry_connection is False
        assert url == f"http://{expected}/close_file"
        assert kwargs == {"allow_redirects": False}
        return borrower.parent.get(url, **kwargs)

    monkeypatch.setattr(session, "request", observe)
    monkeypatch.setattr(BorrowedSession, "get", borrowed_get)
    client = await Stove.create(normalize_host(value))
    try:
        assert client.stove_host == expected
        assert await client.get_data()
    finally:
        await client.destroy()
    assert sorted(urls) == sorted([
        ("GET", "/esp/get_identification"), ("GET", "/esp/get_current_accesspoint"),
        ("POST", "/open_file"), ("POST", "/read_open_file"),
        ("GET", "/close_file"), ("GET", "/get_stove_data"),
    ])


def test_error_and_abort_translations():
    import json

    for language in ("en", "de", "nl"):
        path = Path(__file__).parents[1] / (
            f"custom_components/hwam_stove/translations/{language}.json"
        )
        config = json.loads(path.read_text())["config"]
        assert config["error"]["invalid_host"]
        assert {"already_configured", "already_in_progress"} <= set(config["abort"])
        assert all(config["abort"].values())
