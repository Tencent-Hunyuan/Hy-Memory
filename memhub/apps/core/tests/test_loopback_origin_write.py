import asyncio

from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.auth import local_api_guard
from one_memory.config import Settings


async def write_from(origin: str | None) -> int:
    """Status of a write that carries a valid token and the given page origin."""
    settings = Settings(api_token=SecretStr("test-token"))
    app = create_app(settings)

    @app.post("/protected", dependencies=[Depends(local_api_guard(settings))])
    def protected() -> dict[str, bool]:
        return {"ok": True}

    headers = {"Authorization": "Bearer test-token"}
    if origin is not None:
        headers["Origin"] = origin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/protected", headers=headers)
    return response.status_code


def _statuses(origins: list[str]) -> list[int]:
    return [asyncio.run(write_from(origin)) for origin in origins]


def test_the_web_app_can_write_from_the_loopback_it_is_served_on() -> None:
    # The page is served from the Core's own port in a normal install, so the
    # two names a browser actually shows for it have to be accepted.
    assert _statuses(
        [
            "http://127.0.0.1:8765",
            "http://localhost:8765",
        ]
    ) == [200, 200]


def test_dev_server_and_desktop_shell_still_pass() -> None:
    assert _statuses(
        [
            "http://127.0.0.1:5173",
            "one-memory://app",
        ]
    ) == [200, 200]


def test_a_page_from_anywhere_else_is_still_refused() -> None:
    # LAN and public origins, and a loopback name reached over a bare IP that
    # is not loopback, must stay out: opening loopback does not mean opening
    # the network.
    assert _statuses(
        [
            "https://malicious.example",
            "http://192.168.1.5:8765",
            "http://10.0.0.7:8765",
        ]
    ) == [403, 403, 403]


def test_dns_names_that_only_start_with_127_are_refused() -> None:
    # A prefix check treated any "127.*" hostname as loopback. These are
    # ordinary DNS names an attacker can point at 127.0.0.1 (DNS rebinding).
    assert _statuses(
        [
            "http://127.0.0.1.attacker.example:8765",
            "https://127.attacker.example",
            "http://127.0.0.1.nip.io:8765",
        ]
    ) == [403, 403, 403]


def test_ipv6_loopback_is_still_accepted() -> None:
    assert _statuses(["http://[::1]:8765"]) == [200]
