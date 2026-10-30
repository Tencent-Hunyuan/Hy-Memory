"""The memory routes report an ambiguous id as 409 and pass kind/variant on.

An id can exist in more than one namespace (chat, experience, fact). The
client fails closed with AmbiguousMemoryIdError; over HTTP that was a 500.
"""

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from hy_memory import server
from hy_memory.retrieval import AmbiguousMemoryIdError


class _Client:
    def __init__(self):
        self.calls = []

    def get(self, memory_id, **kwargs):
        self.calls.append(("get", memory_id, kwargs))
        if not kwargs:
            raise AmbiguousMemoryIdError(memory_id, ("chat", "working/experience"))
        return {"memory_id": memory_id}

    def update(self, memory_id, content, **kwargs):
        self.calls.append(("update", memory_id, kwargs))
        return {"success": True, "memory_id": memory_id}

    def delete(self, memory_id, **kwargs):
        self.calls.append(("delete", memory_id, kwargs))
        if kwargs.get("kind") == "bogus":
            raise ValueError("kind must be one of: chat, experience, fact")
        return {"success": True, "deleted_count": 1}


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


def _call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_an_ambiguous_id_is_409_not_500(running):
    _, base = running

    status, body = _call("GET", f"{base}/api/v1/memories/m1")

    assert status == 409
    assert body["error"] == "ambiguous_memory_id"


def test_kind_and_variant_disambiguate_get_update_and_delete(running):
    client, base = running

    assert _call("GET", f"{base}/api/v1/memories/m1?kind=experience&variant=coding")[0] == 200
    assert _call(
        "PUT", f"{base}/api/v1/memories/m1",
        {"content": "new", "kind": "experience", "variant": "working", "task": "T"},
    )[0] == 200
    assert _call("DELETE", f"{base}/api/v1/memories/m1?kind=fact&variant=coding")[0] == 200

    assert client.calls == [
        ("get", "m1", {"kind": "experience", "variant": "coding"}),
        ("update", "m1", {"kind": "experience", "variant": "working", "task": "T"}),
        ("delete", "m1", {"kind": "fact", "variant": "coding"}),
    ]


def test_a_bad_namespace_is_400(running):
    _, base = running

    status, _ = _call("DELETE", f"{base}/api/v1/memories/m1?kind=bogus")

    assert status == 400


def test_chat_calls_without_a_namespace_are_unchanged(running):
    client, base = running

    _call("PUT", f"{base}/api/v1/memories/m1", {"content": "new"})

    assert client.calls == [("update", "m1", {})]
