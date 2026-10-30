import asyncio

from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.auth import local_api_guard
from one_memory.config import Settings


async def protected_request(*, token: str | None, origin: str | None) -> int:
    settings = Settings(api_token=SecretStr("test-token"))
    app = create_app(settings)

    @app.post("/protected", dependencies=[Depends(local_api_guard(settings))])
    def protected() -> dict[str, bool]:
        return {"ok": True}

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/protected", headers=headers)
    return response.status_code


def test_local_api_requires_bearer_token() -> None:
    assert asyncio.run(protected_request(token=None, origin=None)) == 401
    assert asyncio.run(protected_request(token="wrong", origin=None)) == 401
    assert asyncio.run(protected_request(token="test-token", origin=None)) == 200


def test_browser_origin_is_checked_in_addition_to_token() -> None:
    assert (
        asyncio.run(
            protected_request(
                token="test-token",
                origin="https://malicious.example",
            )
        )
        == 403
    )
    assert (
        asyncio.run(
            protected_request(
                token="test-token",
                origin="one-memory://app",
            )
        )
        == 200
    )
