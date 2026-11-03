"""Real HA auth middleware and file exports, without HTTP sockets or a controller."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import stat
import threading
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.http import request_handler_factory
import pytest

from custom_components.hwam_stove import _observation_export as ex
from custom_components.hwam_stove._observation_data import LIMIT


async def export(hass, context, data=None):
    return await hass.services.async_call(
        "hwam_stove",
        "export_observations",
        data or {},
        blocking=True,
        return_response=True,
        context=context,
    )


def folder(hass):
    return Path(hass.config.path(".storage", ex.DIRECTORY))


async def download(hass, path, headers=None):
    """Exercise real signature verification AND requires_auth, with no network."""
    view = ex.ObservationDownload(hass, asyncio.Lock())
    request = make_mocked_request(
        "GET",
        path,
        headers=headers or {},
        app=hass.http.app,
        match_info={"filename": path.split("?")[0].rsplit("/", 1)[-1]},
    )
    middleware = next(
        m for m in hass.http.app.middlewares if m.__name__ == "auth_middleware"
    )
    handler = request_handler_factory(hass, view, view.get)
    return await middleware(request, handler)


async def test_20000_rows_complete_private_file_and_small_reply(
    hass,
    export_context,
    loaded,
    stove,
    stove_factory,
    monkeypatch,
):
    recorder = loaded.observations
    prototype = deepcopy(recorder.rows[-1])
    recorder.rows.clear()
    for i in range(LIMIT):
        row = prototype.copy()
        row[0] = i + 1
        recorder.rows.append(row)
    expected = recorder.export()
    model = deepcopy(loaded.shadow.model.dump())
    analytics = deepcopy(loaded.analytics.state)
    calls = (stove.get_data.await_count, stove_factory.await_count)
    loop_thread = threading.get_ident()
    original = ex.write_export

    def checked(*args):
        assert threading.get_ident() != loop_thread
        return original(*args)

    monkeypatch.setattr(ex, "write_export", checked)
    reply = await export(hass, export_context)
    assert set(reply) == {"filename", "records", "bytes", "download_path"}
    assert reply["records"] == 20000 and len(json.dumps(reply)) < 1500
    assert ex.NAME.fullmatch(reply["filename"])
    response = await download(hass, reply["download_path"])
    assert response.status == 200
    assert json.loads(response.body) == expected
    assert len(response.body) == reply["bytes"]
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.content_type == "application/json"
    assert stat.S_IMODE(folder(hass).stat().st_mode) == 0o700
    assert stat.S_IMODE((folder(hass) / reply["filename"]).stat().st_mode) == 0o600
    assert not Path(hass.config.path("www")).exists()
    assert recorder.export() == expected
    assert loaded.shadow.model.dump() == model
    assert loaded.analytics.state == analytics
    assert (stove.get_data.await_count, stove_factory.await_count) == calls
    assert recorder.healthy


async def test_auth_required_signed_link_expiry_and_path_binding(
    hass,
    export_context,
    loaded,
    hass_access_token,
    freezer,
):
    result = await export(hass, export_context)
    path = ex.PREFIX + result["filename"]
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, path)
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, result["download_path"] + "tampered")
    other = result["download_path"].replace(result["filename"], "different.json")
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, other)
    assert (await download(hass, result["download_path"])).status == 200
    freezer.tick(timedelta(seconds=301))
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, result["download_path"])
    assert (
        await download(hass, path, {"Authorization": f"Bearer {hass_access_token}"})
    ).status == 200


async def test_revocation_and_restart_invalidate_link(hass, export_context, loaded):
    result = await export(hass, export_context)
    user = await hass.auth.async_get_user(export_context.user_id)
    token = next(iter(user.refresh_tokens.values()))
    hass.auth.async_remove_refresh_token(token)
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, result["download_path"])
    # HA's signing secret is per process; a restart never restores it.
    hass.data.pop("http.auth.sign_secret")
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, result["download_path"])


async def test_non_admin_and_no_user_cannot_export_or_download(
    hass,
    export_context,
    loaded,
    hass_read_only_user,
    hass_read_only_access_token,
):
    for ctx in [
        Context(),
        Context(user_id=hass_read_only_user.id),
        Context(user_id="missing"),
    ]:
        with pytest.raises(HomeAssistantError, match="administrator"):
            await export(hass, ctx)
    assert not folder(hass).exists()
    result = await export(hass, export_context)
    with pytest.raises(web.HTTPForbidden):
        await download(
            hass,
            ex.PREFIX + result["filename"],
            {"Authorization": f"Bearer {hass_read_only_access_token}"},
        )
    token = next(iter(hass_read_only_user.refresh_tokens.values()))
    path = async_sign_path(
        hass,
        ex.PREFIX + result["filename"],
        timedelta(minutes=5),
        refresh_token_id=token.id,
    )
    with pytest.raises(web.HTTPForbidden):
        await download(hass, path)


async def test_retention_and_concurrent_exports(hass, export_context, loaded):
    results = await asyncio.gather(*(export(hass, export_context) for _ in range(7)))
    assert len({r["filename"] for r in results}) == 7
    assert len(list(folder(hass).iterdir())) == ex.MAX_FILES
    with pytest.raises(web.HTTPNotFound):
        await download(hass, results[0]["download_path"])
    filename = folder(hass) / results[-1]["filename"]
    await hass.async_add_executor_job(os.utime, filename, (0, 0))
    with pytest.raises(web.HTTPNotFound):
        await download(hass, results[-1]["download_path"])
    assert not filename.exists()


@pytest.mark.parametrize("failure", ["write", "sync", "serialize", "size", "unhealthy"])
async def test_failure_does_not_touch_collection(
    hass, export_context, loaded, monkeypatch, failure
):
    recording = loaded.observations
    before = deepcopy(recording._payload())
    previous = await export(hass, export_context)
    disk = (folder(hass) / previous["filename"]).read_bytes()
    if failure == "write":
        monkeypatch.setattr(
            ex, "write_export", lambda *a: (_ for _ in ()).throw(PermissionError())
        )
    elif failure == "sync":
        monkeypatch.setattr(ex.os, "fsync", lambda *a: (_ for _ in ()).throw(OSError()))
    elif failure == "serialize":
        monkeypatch.setattr(
            recording, "export", lambda: {"rows": [], "bad": float("nan")}
        )
    elif failure == "size":
        monkeypatch.setattr(ex, "MAX_BYTES", 10)
    else:
        monkeypatch.setattr(
            recording, "export", lambda: (_ for _ in ()).throw(ValueError())
        )
    with pytest.raises(HomeAssistantError, match="export failed"):
        await export(hass, export_context)
    assert recording._payload() == before and recording.healthy
    assert (folder(hass) / previous["filename"]).read_bytes() == disk
    assert not list(folder(hass).glob("*.tmp"))


@pytest.mark.parametrize(
    "unsafe",
    ["directory_symlink", "directory_mode", "file_symlink", "file_mode", "hardlink"],
)
async def test_unsafe_paths_fail_closed(hass, export_context, loaded, tmp_path, unsafe):
    if unsafe.startswith("directory"):
        if unsafe == "directory_symlink":
            folder(hass).symlink_to(tmp_path, target_is_directory=True)
        else:
            folder(hass).mkdir(mode=0o755)
        with pytest.raises(HomeAssistantError, match="export failed"):
            await export(hass, export_context)
    else:
        result = await export(hass, export_context)
        file = folder(hass) / result["filename"]
        if unsafe == "file_symlink":
            file.unlink()
            file.symlink_to(tmp_path / "secret")
        elif unsafe == "file_mode":
            file.chmod(0o644)
        else:
            os.link(file, tmp_path / "another_link")
        with pytest.raises(web.HTTPNotFound):
            await download(hass, result["download_path"])
    assert loaded.observations.healthy


@pytest.mark.parametrize(
    "name", ["../manifest.json", "x.json", "%2e%2e", "hwam_observations_bad.json"]
)
async def test_no_traversal(hass, export_context, loaded, name):
    with pytest.raises(FileNotFoundError):
        await hass.async_add_executor_job(
            ex.read_export, hass.config.path(".storage"), name
        )


async def test_cancelled_export_settles_worker_and_lock(
    hass, export_context, loaded, monkeypatch
):
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ex.write_export
    calls = []

    def blocked(*args):
        calls.append(1)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=10)
        return original(*args)

    monkeypatch.setattr(ex, "write_export", blocked)
    task = asyncio.create_task(export(hass, export_context))
    await entered.wait()
    task.cancel()
    task.cancel()
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(list(folder(hass).glob("*.json"))) == 1
    assert loaded.observations.healthy and calls == [1]
    await export(hass, export_context)
    assert len(list(folder(hass).glob("*.json"))) == 2


async def test_view_registered_once_on_export_not_polls(hass, export_context, loaded):
    with patch.object(
        hass.http, "register_view", wraps=hass.http.register_view
    ) as register:
        await loaded.async_refresh()
        register.assert_not_called()
        await export(hass, export_context)
        await export(hass, export_context)
        assert register.call_count == 1


async def test_restart_secret_loss_alone_invalidates_link(hass, export_context, loaded):
    result = await export(hass, export_context)
    hass.data.pop("http.auth.sign_secret")
    with pytest.raises(web.HTTPUnauthorized):
        await download(hass, result["download_path"])
    new = await export(hass, export_context)
    assert (await download(hass, new["download_path"])).status == 200


async def test_orphan_temporary_cleanup_preserves_unrelated_files(
    hass,
    export_context,
    loaded,
):
    first = await export(hass, export_context)
    temporary = folder(hass) / ("." + first["filename"] + ".tmp")
    temporary.write_bytes(b"incomplete")
    temporary.chmod(0o600)
    unrelated = folder(hass) / "unrelated.txt"
    unrelated.write_text("leave untouched")
    await export(hass, export_context)
    assert not temporary.exists()
    assert unrelated.read_text() == "leave untouched"


async def test_reload_uses_new_runtime_same_route(hass, export_context, loaded):
    first = await export(hass, export_context)
    entry_id = loaded.config_entry.entry_id
    with patch.object(
        hass.http, "register_view", wraps=hass.http.register_view
    ) as register:
        assert await hass.config_entries.async_reload(entry_id)
        await hass.async_block_till_done()
        assert hass.data["hwam_stove"]["stoves"][entry_id] is not loaded
        current = await export(hass, export_context)
        register.assert_not_called()
    assert current["filename"] != first["filename"]
    assert (await download(hass, current["download_path"])).status == 200
    assert (await download(hass, first["download_path"])).status == 200
