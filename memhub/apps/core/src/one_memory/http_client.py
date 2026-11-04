"""Construct httpx clients that never send loopback traffic through env proxies."""

from __future__ import annotations

from typing import Any

import httpx

# Prefix mounts beat trust_env proxy selection for matching hosts.
# Keep remote hosts on the default transport so user-configured HTTPS services
# still use HTTP_PROXY / HTTPS_PROXY when present.
LOOPBACK_DIRECT_MOUNTS: dict[str, str] = {
    "http://127.0.0.1": "direct",
    "https://127.0.0.1": "direct",
    "http://localhost": "direct",
    "https://localhost": "direct",
    "http://[::1]": "direct",
    "https://[::1]": "direct",
}


def _loopback_mounts(
    *, async_client: bool
) -> dict[str, httpx.AsyncBaseTransport | httpx.BaseTransport]:
    if not LOOPBACK_DIRECT_MOUNTS:
        return {}
    transport: httpx.AsyncBaseTransport | httpx.BaseTransport = (
        httpx.AsyncHTTPTransport() if async_client else httpx.HTTPTransport()
    )
    return {prefix: transport for prefix in LOOPBACK_DIRECT_MOUNTS}


def create_client(**kwargs: Any) -> httpx.Client:
    """Sync httpx client: loopback is direct; everything else still trusts env proxies."""
    return _assemble(httpx.Client, async_client=False, **kwargs)


def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Async httpx client: loopback is direct; everything else still trusts env proxies."""
    return _assemble(httpx.AsyncClient, async_client=True, **kwargs)


def _assemble[ClientT: (httpx.Client, httpx.AsyncClient)](
    factory: type[ClientT],
    *,
    async_client: bool,
    **kwargs: Any,
) -> ClientT:
    mounts = dict(kwargs.pop("mounts", None) or {})
    # Tests inject MockTransport as `transport=`. Do not override those hosts
    # with a real socket transport or the mocks would silently miss.
    if kwargs.get("transport") is None:
        for prefix, transport in _loopback_mounts(async_client=async_client).items():
            mounts.setdefault(prefix, transport)
    if mounts:
        kwargs["mounts"] = mounts
    return factory(**kwargs)


class ClientAsyncTransport(httpx.AsyncBaseTransport):
    """Let an observing transport preserve normal environment-proxy and loopback routing.

    Passing a custom transport to httpx disables its automatic proxy mounts.
    Forwarding through an ordinary client keeps the answerer's existing network
    policy, while the outer transport can observe usage without buffering SSE.
    """

    def __init__(self) -> None:
        self.client = create_async_client(follow_redirects=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.client.send(request, stream=True)

    async def aclose(self) -> None:
        await self.client.aclose()
