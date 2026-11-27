"""POST /api/v1/add must not drop a non-ISO ``memory_at``.

The SDK serialises ``memory_at`` as epoch seconds (``hy_memory.client
._epoch_seconds``) and reads it back in every representation through
``MemoryNode.from_dict`` (datetime / ISO text / epoch number).  The HTTP
handlers only pushed the body value through ``datetime.fromisoformat`` and
swallowed the failure, so a client replaying the SDK's own payload lost the
timestamp without any signal and the memory was stamped with "now".
"""

import json
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from hy_memory import server


class _Client:
    def __init__(self):
        self.calls = []

    def add(self, data, **kwargs):
        self.calls.append(("add", data, kwargs))
        return {"success": True}

    def add_extracted(self, contents, **kwargs):
        self.calls.append(("add_extracted", contents, kwargs))
        return {"success": True}


@pytest.fixture
def running(monkeypatch):
    client = _Client()
    monkeypatch.setattr(server, "_get_client", lambda: client)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.MemoryHTTPHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield client, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(url, body):
    request = Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_add_accepts_an_epoch_memory_at(running):
    client, base = running

    status, _ = _call(f"{base}/api/v1/add", {"data": "hello", "memory_at": 1700000000})

    assert status == 200
    assert client.calls == [("add", "hello", {"memory_at": datetime.fromtimestamp(1700000000)})]


def test_add_accepts_a_utc_iso_memory_at(running):
    client, base = running

    status, _ = _call(
        f"{base}/api/v1/add", {"data": "hello", "memory_at": "2026-11-27T02:00:00Z"}
    )

    assert status == 200
    assert len(client.calls) == 1
    memory_at = client.calls[0][2]["memory_at"]
    assert memory_at.timestamp() == datetime.fromisoformat("2026-11-27T02:00:00+00:00").timestamp()


def test_add_keeps_a_naive_iso_memory_at(running):
    client, base = running

    status, _ = _call(
        f"{base}/api/v1/add", {"data": "hello", "memory_at": "2026-11-27T10:00:00"}
    )

    assert status == 200
    assert client.calls == [
        ("add", "hello", {"memory_at": datetime(2026, 11, 27, 10, 0, 0)})
    ]


def test_add_extracted_accepts_an_epoch_memory_at(running):
    client, base = running

    status, _ = _call(
        f"{base}/api/v1/add_extracted", {"contents": ["a fact"], "memory_at": 1700000000}
    )

    assert status == 200
    assert client.calls == [
        ("add_extracted", ["a fact"], {"memory_at": datetime.fromtimestamp(1700000000)})
    ]


def test_an_unreadable_memory_at_is_ignored(running):
    client, base = running

    status, _ = _call(f"{base}/api/v1/add", {"data": "hello", "memory_at": "not-a-time"})

    assert status == 200
    assert client.calls == [("add", "hello", {})]
