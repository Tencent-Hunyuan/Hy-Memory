"""Offline contract and failure tests, not a recall quality evaluation."""

import contextvars
import json
import threading
import urllib.error

import pytest
from hermes_hy_memory import HyMemoryProvider, register
from hermes_hy_memory.provider import ProviderError, _NoRedirect


@pytest.fixture
def provider(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("HY_MEMORY_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HY_MEMORY_USER_ID", "owner-A")
    monkeypatch.setenv("HY_MEMORY_AGENT_ID", "agent-A")
    p = HyMemoryProvider()
    p.initialize("session-A", hermes_home="/profile/A", platform="cli")
    yield p
    p.shutdown()


class Response:
    status = 200

    def __init__(self, data):
        self.data = data
        self.fp = __import__("types").SimpleNamespace(
            raw=__import__("types").SimpleNamespace(_sock=self)
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        assert timeout > 0

    def read1(self, size):
        assert size <= 1048577
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk


def transport(monkeypatch, result=None, error=None):
    calls = []

    class Opener:
        def open(self, req, timeout):
            calls.append((req, timeout))
            if error:
                raise error
            return Response(
                result if isinstance(result, bytes) else json.dumps(result).encode()
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    return calls


def call(p, name="search", **args):
    return json.loads(p.handle_tool_call("hy_memory_" + name, args))


def test_native_contract(provider):
    from agent.memory_provider import MemoryProvider

    assert isinstance(provider, MemoryProvider)
    registered = []

    class Context:
        def register_memory_provider(self, provider):
            assert isinstance(provider, MemoryProvider)
            registered.append(provider)

    register(Context())
    assert len(registered) == 1
    assert isinstance(registered[0], HyMemoryProvider)
    assert registered[0].name == "hy_memory"
    assert {s["name"] for s in provider.get_tool_schemas()} == {
        "hy_memory_search",
        "hy_memory_add",
    }
    assert provider.hermes_home == "/profile/A"
    assert provider.system_prompt_block() == provider.system_prompt_block()
    assert provider.is_available()


def test_scope_nested_and_sanitization(provider, monkeypatch):
    fixture = {
        "request_id": "r",
        "memories": {
            "normal": [{"content": "fact", "metadata": {"secret": "hidden"}}],
            "schema": [{"content": "pattern"}],
            "proactive": [{"content": "reminder"}],
        },
    }
    calls = transport(monkeypatch, fixture)
    assert call(provider, query="remember")["memories"] == [
        {"content": "fact"},
        {"content": "pattern"},
        {"content": "reminder"},
    ]
    payload = json.loads(calls[0][0].data)
    assert payload == {
        "query": "remember",
        "user_ids": ["owner-A"],
        "agent_ids": ["agent-A"],
        "limit": 5,
    }
    assert call(provider, "add", text="save") == {
        "success": False,
        "error": "backend_failure",
    }
    transport(monkeypatch, {"success": True})
    assert call(provider, "add", text="save")["success"]


@pytest.mark.parametrize(
    "args",
    [
        dict(query="x", user_id="other"),
        dict(query="x", agent_id="other"),
        dict(query="x", limit=True),
        dict(query="x", limit=float("nan")),
        dict(query=1),
        dict(query=""),
        dict(query="x", limit=0),
        dict(query="x", limit=6),
    ],
)
def test_validation_no_transport(provider, monkeypatch, args):
    calls = transport(monkeypatch, {})
    assert not call(provider, **args)["success"]
    assert not calls


@pytest.mark.parametrize(
    "result",
    [
        b"bad json",
        [],
        {},
        {"error": "SECRET"},
        {"success": False, "message": "SECRET"},
        {"memories": None},
        {"memories": {"normal": "bad"}},
        {"memories": [None]},
        {"memories": [{"content": 1}]},
    ],
)
def test_bad_response_failure(provider, monkeypatch, result):
    transport(monkeypatch, result)
    output = call(provider, query="x")
    assert not output["success"]
    assert "SECRET" not in str(output)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("SECRET"),
        urllib.error.URLError("SECRET"),
        urllib.error.HTTPError("https://secret", 500, "SECRET", {}, None),
    ],
)
def test_http_failure(provider, monkeypatch, error):
    transport(monkeypatch, error=error)
    output = call(provider, query="x")
    assert not output["success"] and "SECRET" not in str(output)


def test_empty_and_budgets(provider, monkeypatch):
    transport(monkeypatch, {"memories": []})
    assert call(provider, query="x") == {"success": True, "memories": []}
    transport(monkeypatch, {"memories": [{"content": "a" * 9000}] * 20})
    assert len(call(provider, query="x")["memories"][0]["content"]) == provider.chars
    transport(monkeypatch, b"x" * (provider.body_bytes + 1))
    assert call(provider, query="x")["error"] == "response_too_large"


@pytest.mark.parametrize("context", ["cron", "subagent", "flush"])
def test_nonprimary(provider, monkeypatch, context):
    calls = transport(monkeypatch, {"success": True})
    provider._primary = False
    provider.auto_capture = True
    assert not call(provider, "add", text="save")["success"]
    provider.sync_turn("user", "assistant")
    provider._queue.join()
    assert not calls
    provider._primary = True
    assert not json.loads(
        provider.handle_tool_call(
            "hy_memory_add", {"text": "save"}, agent_context=context
        )
    )["success"]


def test_context_session_and_cache(provider, monkeypatch):
    scope = contextvars.ContextVar("profile", default="default")
    observed = []

    def request(operation, payload):
        observed.append((scope.get(), operation, payload))
        return {"success": True, "memories": [{"content": "reference"}]}

    monkeypatch.setattr(provider, "_request", request)
    provider.auto_capture = provider.auto_recall = True
    token = scope.set("profile-A")
    provider.sync_turn("user", "assistant")
    provider.queue_prefetch("query")
    scope.reset(token)
    provider._queue.join()
    assert all(x[0] == "profile-A" for x in observed)
    assert observed[0][2]["session_id"] == "session-A"
    assert "reference" in provider.prefetch("query")
    assert provider.recall_status().count == 1
    assert provider.prefetch("different") == ""
    assert provider.recall_status() is None
    provider.on_session_switch("session-B")
    provider._queue.join()
    assert provider.prefetch("query", session_id="session-A") == ""
    assert provider._cache is None


def test_queue_bounded_and_shutdown(provider):
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        release.wait(2)

    provider._enqueue(blocked)
    assert entered.wait(1)
    for _ in range(30):
        provider._enqueue(lambda: None)
    assert provider._queue.qsize() == 8
    assert provider.last_error == "queue_full"
    release.set()
    provider.shutdown()
    assert not provider._worker.is_alive()
    assert not call(provider, "add", text="x")["success"]


def test_defaults_and_missing_owner(monkeypatch):
    monkeypatch.delenv("HY_MEMORY_USER_ID", raising=False)
    p = HyMemoryProvider()
    assert not p.is_available()
    assert p.unavailable_reason()


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "http://u:SECRET@localhost",
        "http://localhost/?key=SECRET",
        "ftp://localhost",
        "https://host:bad",
    ],
)
def test_unsafe_url(provider, monkeypatch, url):
    monkeypatch.setenv("HY_MEMORY_URL", url)
    p = HyMemoryProvider()
    assert not p.is_available()
    assert "SECRET" not in p.unavailable_reason()
    assert p.identity_signature()["hy_memory_url"] is None


def test_no_redirect():
    with pytest.raises(ProviderError, match="redirect_refused"):
        _NoRedirect().redirect_request(None, None, 302, "", {}, "https://other")


def test_stale_inflight_recall_and_write_session_snapshot(provider, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def request(operation, payload):
        calls.append((operation, payload))
        if operation == "search":
            entered.set()
            assert release.wait(2)
        return {"success": True, "memories": [{"content": "old-session"}]}

    monkeypatch.setattr(provider, "_request", request)
    provider.auto_recall = provider.auto_capture = True
    provider.queue_prefetch("old query")
    assert entered.wait(1)
    provider.sync_turn("old user", "old assistant")
    provider.on_session_switch("session-B")
    release.set()
    provider._queue.join()
    assert provider._cache is None
    assert calls[1][1]["session_id"] == "session-A"
    assert provider.prefetch("old query", session_id="session-A") == ""


def test_background_failure_defaults_and_other_profile(provider, monkeypatch):
    assert not provider.auto_recall and not provider.auto_capture
    calls = transport(monkeypatch, error=TimeoutError("SECRET"))
    provider.sync_turn("user", "assistant")
    provider.queue_prefetch("query")
    assert provider.prefetch("query") == ""
    assert not calls
    provider.auto_recall = True
    provider.queue_prefetch("query")
    provider._queue.join()
    assert provider.last_error == "transport_or_response_failure"
    monkeypatch.setenv("HY_MEMORY_USER_ID", "owner-B")
    monkeypatch.setenv("HY_MEMORY_AGENT_ID", "agent-B")
    other = HyMemoryProvider()
    other.initialize("session-B", hermes_home="/profile/B")
    try:
        calls = transport(monkeypatch, {"memories": []})
        assert call(other, query="q")["success"]
        payload = json.loads(calls[0][0].data)
        assert payload["user_ids"] == ["owner-B"]
        assert payload["agent_ids"] == ["agent-B"]
        assert provider.user_id == "owner-A"
        assert other.identity_signature() != provider.identity_signature()
    finally:
        other.shutdown()


@pytest.mark.parametrize(
    "key,value",
    [
        ("TIMEOUT", "nan"),
        ("TIMEOUT", "inf"),
        ("LIMIT", "1.5"),
        ("MAX_CHARS", "-1"),
        ("AUTO_RECALL", "yes"),
        ("AUTO_CAPTURE", "1"),
    ],
)
def test_finite_configuration(provider, monkeypatch, key, value):
    monkeypatch.setenv("HY_MEMORY_" + key, value)
    assert not HyMemoryProvider().is_available()


def test_add_payload_and_failure_no_retry(provider, monkeypatch):
    calls = transport(monkeypatch, {"success": True, "memory_id": "private-id"})
    assert call(provider, "add", text="remember") == {"success": True}
    assert json.loads(calls[0][0].data) == {
        "text": "remember",
        "user_id": "owner-A",
        "agent_id": "agent-A",
        "session_id": "session-A",
    }
    calls = transport(monkeypatch, error=TimeoutError("SECRET"))
    assert not call(provider, "add", text="remember")["success"]
    assert len(calls) == 1


def test_availability_static_prompt_and_initialization_without_network(
    provider, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("network on configuration or static prompt")

    monkeypatch.setattr("urllib.request.build_opener", forbidden)
    fresh = HyMemoryProvider()
    assert fresh.is_available()
    assert fresh.system_prompt_block() == provider.system_prompt_block()
    fresh.initialize(
        "fresh", agent_context="cron", hermes_home="/profile/cron", platform="cron"
    )
    try:
        assert not call(fresh, "add", text="save")["success"]
    finally:
        fresh.shutdown()


def test_http_status_and_partial_body(provider, monkeypatch):
    import http.client

    transport(monkeypatch, error=http.client.IncompleteRead(b"SECRET"))
    assert not call(provider, query="x")["success"]
    calls = transport(monkeypatch, {"success": True})
    monkeypatch.setattr(Response, "status", 500)
    assert call(provider, "add", text="x")["error"] == "http_failure"
    assert len(calls) == 1


def test_bearer_header_and_manifest(provider, monkeypatch):
    from pathlib import Path

    provider.token = "fixture-key"
    calls = transport(monkeypatch, {"memories": []})
    assert call(provider, query="q")["success"]
    assert calls[0][0].get_header("Authorization") == "Bearer fixture-key"
    manifest = (Path(__file__).parents[1] / "hy_memory/plugin.yaml").read_text()
    assert "type: memory" in manifest and "name: hy_memory" in manifest


@pytest.mark.parametrize("memory_id", [
    "8c135ec6-41f8-4e53-8588-f01ebbcf3194",
    "8C135EC6-41F8-4E53-8588-F01EBBCF3194",
    "SECRET-token", "8c135ec641f84e538588f01ebbcf3194", None,
])
def test_add_receipt_is_only_canonical_uuid(provider, monkeypatch, memory_id):
    transport(monkeypatch, {"success": True, "memory_id": memory_id})
    result = call(provider, "add", text="remember")
    assert result["success"]
    if isinstance(memory_id, str) and len(memory_id) == 36:
        assert result["memory_id"] == memory_id.lower()
    else:
        assert "memory_id" not in result


def test_official_module_is_the_provider_base():
    import sys
    from agent.memory_provider import MemoryProvider

    assert HyMemoryProvider.__bases__ == (MemoryProvider,)
    assert "hermes_cli.memory.provider" not in sys.modules
    assert not getattr(HyMemoryProvider, "__abstractmethods__", set())


@pytest.mark.parametrize("chunked", [False, True])
def test_network_drip_body_has_total_budget(provider, chunked):
    import socket
    import time

    try:
        listener = socket.socket()
    except PermissionError:
        pytest.skip("sandbox forbids sockets; run this network regression at parent")
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    stop = threading.Event()

    def serve():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(1)
                conn.recv(65536)
                framing = (b"Transfer-Encoding: chunked" if chunked
                           else b"Content-Length: 1000")
                conn.sendall(b"HTTP/1.1 200 OK\r\n" + framing + b"\r\n\r\n")
                while not stop.wait(0.035):
                    conn.sendall(b"1\r\nx\r\n" if chunked else b"x")
        except OSError:
            pass

    worker = threading.Thread(target=serve)
    worker.start()
    provider.url = "http://127.0.0.1:" + str(listener.getsockname()[1])
    provider.timeout = 0.2
    started = time.monotonic()
    try:
        result = call(provider, query="drip")
        elapsed = time.monotonic() - started
        assert not result["success"]
        assert elapsed < 0.65
    finally:
        stop.set()
        listener.close()
        worker.join(2)
    assert not worker.is_alive()


@pytest.mark.parametrize("wire", [
    b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 1000,
    b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"x" * 1000,
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;" + b"x" * 1000,
], ids=["headers", "body", "chunk-extension"])
def test_real_http_parser_deadline_including_framing(wire):
    import http.client
    import io
    import time
    from hermes_hy_memory.provider import _DeadlineSocket

    class Drip(io.RawIOBase):
        def readinto(self, buffer):
            time.sleep(0.002)
            buffer[:1] = b"x"
            return 1

    class Sock:
        def makefile(self, mode, buffering):
            class Wire(Drip):
                offset = 0

                def readinto(self, buffer):
                    if self.offset < len(wire):
                        # Deliver framing quickly until the deliberately slow region.
                        slow_at = wire.index(b"x")
                        if self.offset >= slow_at:
                            time.sleep(0.005)
                        buffer[:1] = wire[self.offset:self.offset + 1]
                        self.offset += 1
                        return 1
                    return 0
            return Wire()

        def settimeout(self, remaining):
            assert 0 < remaining <= 0.08

    started = time.monotonic()
    response = http.client.HTTPResponse(_DeadlineSocket(Sock(), started + 0.08))
    try:
        with pytest.raises(ProviderError, match="request_deadline_exceeded"):
            response.begin()
            while response.read1(8192):
                pass
        assert time.monotonic() - started < 0.4
    finally:
        response.close()


@pytest.mark.parametrize("operation,body", [
    ("search", b'{"memories":[]}'),
    ("add", b'{"success":true}'),
])
@pytest.mark.parametrize("framing", [
    "short", "full", "unknown", "negative", "malformed", "chunked", "chunked-short",
])
def test_real_http_response_body_framing(provider, monkeypatch, operation, body, framing):
    import http.client
    import io

    headers = b""
    payload = body
    if framing in {"short", "full", "negative", "malformed"}:
        length = {"short": "100", "full": str(len(body)),
                  "negative": "-1", "malformed": "SECRET"}[framing]
        headers = b"Content-Length: " + length.encode() + b"\r\n"
    elif framing.startswith("chunked"):
        # Chunked framing takes precedence over Content-Length in HTTPResponse.
        headers = b"Transfer-Encoding: chunked\r\nContent-Length: 100\r\n"
        payload = f"{len(body):x}\r\n".encode() + body + b"\r\n"
        if framing == "chunked":
            payload += b"0\r\n\r\n"

    class Sock:
        def makefile(self, mode):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + payload)

    response = http.client.HTTPResponse(Sock())
    response.begin()

    class Opener:
        def open(self, request, timeout):
            return response

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    args = {"query": "x"} if operation == "search" else {"text": "x"}
    result = call(provider, operation, **args)
    if framing in {"short", "negative", "malformed", "chunked-short"}:
        assert result == {"success": False, "error": "transport_or_response_failure"}
    else:
        assert result["success"] is True
