"""Strict in-memory HTTP boundary for the actually installed pystove methods."""

import asyncio
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp

from pystove import Stove

from .helpers import HOST

REAL_CREATE = Stove.__dict__["create"]
REAL_DESTROY = Stove.destroy
RAW = json.loads((Path(__file__).parent / "fixtures/raw_status.json").read_text())


@dataclass
class Response:
    body: str = '{"response":"OK"}'
    error: BaseException | None = None
    status: int = 200
    hold: bool = False
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    exited: bool = False
    reader: asyncio.Task | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def text(self):
        self.reader = asyncio.current_task()
        self.entered.set()
        if self.hold:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.body


class Session:
    def __init__(self):
        self.calls = []
        self.queues = defaultdict(deque)
        self.responses = []
        self.raw = deepcopy(RAW)
        self.closed = False
        self.close_calls = 0
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.close_error = None
        self.defaults = {
            ("GET", "/esp/get_identification"): json.dumps(
                {
                    "name": "Simulated stove",
                    "ip": HOST,
                    "mdns": HOST,
                }
            ),
            ("GET", "/esp/get_current_accesspoint"): '{"ssid":"synthetic"}',
            ("POST", "/open_file"): '{"success":1}',
            ("POST", "/read_open_file"): (
                "<Info><Name>Algorithm</Name>"
                "<StoveType>Synthetic series</StoveType></Info>"
            ),
        }

    def queue(self, method, path, **kwargs):
        response = Response(**kwargs)
        self.queues[method, path].append(response)
        return response

    def request(self, method, url, kwargs):
        assert not self.closed
        parsed = urlsplit(url)
        assert parsed.scheme == "http" and parsed.netloc == HOST
        key = method, parsed.path
        self.calls.append((method, parsed.path, kwargs))
        if self.queues[key]:
            response = self.queues[key].popleft()
        elif key == ("GET", "/get_stove_data"):
            response = Response(body=json.dumps(self.raw))
        else:
            assert key in self.defaults, f"Unqueued command: {key}"
            response = Response(body=self.defaults[key])
        self.responses.append(response)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, kwargs)

    async def close(self):
        self.close_calls += 1
        self.close_started.set()
        await self.close_release.wait()
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class Transport:
    def __init__(self):
        self.sessions = []
        self.pending = deque()
        self.headers = []
        self.destroyed = []

    def prepare(self):
        session = Session()
        self.pending.append(session)
        return session

    def factory(self, **kwargs):
        assert kwargs == {"headers": {"Accept": "application/json"}}
        session = self.pending.popleft() if self.pending else Session()
        self.sessions.append(session)
        self.headers.append(kwargs)
        return session

    def module(self):
        # Replace pystove's transport dependency only; leave HA's aiohttp intact.
        return SimpleNamespace(**{**vars(aiohttp), "ClientSession": self.factory})

    async def observe_destroy(self, client):
        # Observation delegates to the real implementation, with no stubbed result.
        self.destroyed.append(client)
        return await REAL_DESTROY(client)
