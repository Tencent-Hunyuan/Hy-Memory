"""A failed request must not tear down another request's pooled connection."""

import asyncio
import json

import pytest

from hy_memory.agent.llm_provider import LLMProvider, LLMRetryError
from hy_memory.config import MemoryConfig


@pytest.mark.asyncio
async def test_failed_request_does_not_close_concurrent_success(monkeypatch):
    # The service is entirely local. Do not send loopback traffic through a
    # proxy inherited from the test runner.
    for name in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    both_started = asyncio.Event()
    count = 0

    async def serve(reader, writer):
        nonlocal count
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            request = json.loads(await reader.readexactly(length))
            prompt = request["messages"][-1]["content"]
            count += 1
            if count == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 2)
            if prompt == "bad":
                writer.transport.abort()
                return
            await asyncio.sleep(0.2)
            body = json.dumps(
                {
                    "id": "test-id",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "OK"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.base_url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1"
    config.llm.model = "test"
    config.llm.max_retries = 1
    provider = LLMProvider(config)
    try:
        bad, good = await asyncio.wait_for(
            asyncio.gather(
                provider.complete("bad"),
                provider.complete("good"),
                return_exceptions=True,
            ),
            timeout=4,
        )
        assert isinstance(bad, LLMRetryError)
        assert good.content == "OK"
        assert not provider._retired_clients.get(asyncio.get_running_loop())
    finally:
        await provider.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_late_failure_does_not_evict_newer_client():
    import httpx

    started = {name: asyncio.Event() for name in ("old-a", "old-b", "new-a", "new-b")}
    release = {name: asyncio.Event() for name in started}
    transports = []

    class ControlledTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.closed = False
            transports.append(self)

        async def handle_async_request(self, request):
            name = json.loads(request.content)["messages"][-1]["content"]
            started[name].set()
            await release[name].wait()
            if name.startswith("old"):
                raise httpx.ConnectError(
                    "synthetic connection failure", request=request
                )
            if self.closed:
                raise httpx.ReadError(
                    "client closed while request active", request=request
                )
            return httpx.Response(
                200,
                json={
                    "id": "id",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "OK"},
                        }
                    ],
                },
                request=request,
            )

        async def aclose(self):
            self.closed = True

    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.model = "test"
    config.llm.max_retries = 1
    config.http_transport_factory = ControlledTransport
    provider = LLMProvider(config)
    pending = []
    try:

        def call(name):
            task = asyncio.create_task(provider.complete(name))
            pending.append(task)
            return task

        old_a, old_b = call("old-a"), call("old-b")
        await asyncio.wait_for(
            asyncio.gather(started["old-a"].wait(), started["old-b"].wait()), 2
        )
        assert len(transports) == 1
        release["old-a"].set()
        with pytest.raises(LLMRetryError):
            await old_a
        assert not transports[0].closed  # old-b still owns it
        new_a = call("new-a")
        await asyncio.wait_for(started["new-a"].wait(), 2)
        assert len(transports) == 2
        release["old-b"].set()
        with pytest.raises(LLMRetryError):
            await old_b
        assert transports[0].closed
        new_b = call("new-b")
        await asyncio.wait_for(started["new-b"].wait(), 2)
        assert len(transports) == 2  # late old failure did not evict the new generation
        release["new-a"].set()
        release["new-b"].set()
        assert [x.content for x in await asyncio.gather(new_a, new_b)] == ["OK", "OK"]
    finally:
        for event in release.values():
            event.set()
        await asyncio.gather(*pending, return_exceptions=True)
        await provider.close()
    assert all(transport.closed for transport in transports)


@pytest.mark.asyncio
async def test_cancelled_last_user_releases_retired_client():
    import httpx

    both = asyncio.Event()
    started = 0
    transports = []

    class BlockingTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.closed = False
            transports.append(self)

        async def handle_async_request(self, request):
            nonlocal started
            name = json.loads(request.content)["messages"][-1]["content"]
            started += 1
            if started == 2:
                both.set()
            await both.wait()
            if name == "bad":
                raise httpx.ConnectError("connection reset", request=request)
            await asyncio.Event().wait()
            raise AssertionError("cancelled request should not return")

        async def aclose(self):
            self.closed = True

    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.model = "test"
    config.llm.max_retries = 1
    config.http_transport_factory = BlockingTransport
    provider = LLMProvider(config)
    bad = asyncio.create_task(provider.complete("bad"))
    blocked = asyncio.create_task(provider.complete("blocked"))
    try:
        await asyncio.wait_for(both.wait(), 2)
        with pytest.raises(LLMRetryError):
            await bad
        assert not transports[0].closed
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        assert transports[0].closed
        assert not provider._retired_clients.get(asyncio.get_running_loop())
    finally:
        blocked.cancel()
        await asyncio.gather(bad, blocked, return_exceptions=True)
        await provider.close()


@pytest.mark.asyncio
async def test_cancel_during_retired_close_is_still_tracked(monkeypatch):
    import openai

    closing = asyncio.Event()
    allow_close = asyncio.Event()
    instances = []

    class FakeClient:
        def __init__(self, **_kwargs):
            from types import SimpleNamespace

            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            instances.append(self)

        async def create(self, **_kwargs):
            raise RuntimeError("synthetic failure")

        async def close(self):
            self.calls += 1
            closing.set()
            await allow_close.wait()

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.max_retries = 1
    provider = LLMProvider(config)
    call = asyncio.create_task(provider.complete("fail"))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert instances[0] in provider._retired_clients[asyncio.get_running_loop()]
        allow_close.set()
        await provider.close()
        assert instances[0].calls == 2
    finally:
        allow_close.set()
        await asyncio.gather(call, return_exceptions=True)
        await provider.close()


@pytest.mark.asyncio
async def test_explicit_provider_close_also_closes_inflight_retired_client():
    import httpx

    started = 0
    both = asyncio.Event()
    release = asyncio.Event()
    transports = []

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.closed = False
            transports.append(self)

        async def handle_async_request(self, request):
            nonlocal started
            prompt = json.loads(request.content)["messages"][-1]["content"]
            started += 1
            if started == 2:
                both.set()
            await both.wait()
            if prompt == "bad":
                raise httpx.ConnectError("reset", request=request)
            await release.wait()
            if self.closed:
                raise httpx.ReadError("closed", request=request)
            raise AssertionError("explicit close should close transport")

        async def aclose(self):
            self.closed = True

    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.max_retries = 1
    config.http_transport_factory = Transport
    provider = LLMProvider(config)
    bad = asyncio.create_task(provider.complete("bad"))
    active = asyncio.create_task(provider.complete("active"))
    try:
        await asyncio.wait_for(both.wait(), 2)
        with pytest.raises(LLMRetryError):
            await bad
        assert not transports[0].closed
        await provider.close()
        assert transports[0].closed
        release.set()
        with pytest.raises(LLMRetryError):
            await active
    finally:
        release.set()
        await asyncio.gather(bad, active, return_exceptions=True)
        await provider.close()


def test_concurrent_loops_do_not_share_retirement_state(monkeypatch):
    import concurrent.futures
    import threading
    from types import SimpleNamespace

    import openai

    barrier = threading.Barrier(2, timeout=3)
    release = threading.Event()
    clients = []

    class FakeClient:
        def __init__(self, **_kwargs):
            self.closed = False
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            clients.append(self)

        async def create(self, **kwargs):
            name = kwargs["messages"][-1]["content"]
            await asyncio.to_thread(barrier.wait)
            if name == "bad":
                raise RuntimeError("synthetic failure")
            assert await asyncio.to_thread(release.wait, 2)
            assert not self.closed
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="OK", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
                model="test",
                id="id",
            )

        async def close(self):
            self.closed = True

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.max_retries = 1
    provider = LLMProvider(config)

    def run(name):
        async def call():
            try:
                return await provider.complete(name)
            finally:
                if name == "bad":
                    release.set()

        return asyncio.run(call())

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bad = pool.submit(run, "bad")
        good = pool.submit(run, "good")
        with pytest.raises(LLMRetryError):
            bad.result(timeout=5)
        assert good.result(timeout=5).content == "OK"
    assert len(clients) == 2
    assert sum(client.closed for client in clients) == 1
    # Cleanup cross-loop real transports is not exercised by these fake clients.
    asyncio.run(provider.close())


@pytest.mark.asyncio
async def test_retired_close_error_keeps_client_available_to_explicit_cleanup(
    monkeypatch,
):
    from types import SimpleNamespace

    import openai

    clients = []

    class FakeClient:
        def __init__(self, **_kwargs):
            self.close_calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            clients.append(self)

        async def create(self, **_kwargs):
            raise RuntimeError("request failed")

        async def close(self):
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("close failed")

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.max_retries = 1
    provider = LLMProvider(config)
    with pytest.raises(LLMRetryError):
        await provider.complete("fail")
    assert clients[0] in provider._retired_clients[asyncio.get_running_loop()]
    await provider.close()
    assert clients[0].close_calls == 2
    # A fake close can be retried. Underlying HTTP transports may not support
    # retry after marking themselves closed; no such guarantee is implied.


@pytest.mark.asyncio
async def test_second_cancel_during_cleanup_keeps_retired_client_tracked(monkeypatch):
    from types import SimpleNamespace

    import openai

    both = asyncio.Event()
    closing = asyncio.Event()
    allow_close = asyncio.Event()
    started = 0
    clients = []

    class FakeClient:
        def __init__(self, **_kwargs):
            self.close_calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            clients.append(self)

        async def create(self, **kwargs):
            nonlocal started
            name = kwargs["messages"][-1]["content"]
            started += 1
            if started == 2:
                both.set()
            await both.wait()
            if name == "bad":
                raise RuntimeError("failed")
            await asyncio.Event().wait()

        async def close(self):
            self.close_calls += 1
            closing.set()
            await allow_close.wait()

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    config = MemoryConfig()
    config.llm.api_key = "test"
    config.llm.max_retries = 1
    provider = LLMProvider(config)
    bad = asyncio.create_task(provider.complete("bad"))
    active = asyncio.create_task(provider.complete("active"))
    try:
        await asyncio.wait_for(both.wait(), 2)
        with pytest.raises(LLMRetryError):
            await bad
        active.cancel()
        await asyncio.wait_for(closing.wait(), 2)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert clients[0] in provider._retired_clients[asyncio.get_running_loop()]
        allow_close.set()
        await provider.close()
        assert clients[0].close_calls == 2
    finally:
        allow_close.set()
        active.cancel()
        await asyncio.gather(bad, active, return_exceptions=True)
        await provider.close()
