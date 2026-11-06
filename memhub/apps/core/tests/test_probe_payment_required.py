import asyncio

import httpx
from pydantic import SecretStr

from one_memory.model_probe import ModelProber, ProbeCode
from one_memory.model_profiles import ModelProfileInput, ModelProfileStore, ProviderKind
from one_memory.secrets import InMemorySecretStore

API_KEY = "probe-secret-value"


def _probe(handler, tmp_path):
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Provider",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="memory-model",
            api_key=SecretStr(API_KEY),
        ),
        secrets,
    )
    return asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler)).probe(
            profile.profile_id
        )
    )


def _generation_only(handler):
    """Answer the model list, then let the caller answer the generation call."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "memory-model"}]})
        return handler(request)

    return respond


def test_http_402_without_a_useful_body_is_still_understood_as_unpaid(tmp_path) -> None:
    """A gateway that returns 402 and says nothing useful still means unpaid.

    The body classifier only recognised a balance error when the body spelled
    one out. A bare 402 fell through to INVALID_RESPONSE, which reads as a
    broken endpoint and sends someone to change the address instead of to pay.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": ""}})

    result = _probe(_generation_only(handler), tmp_path)

    assert result.stages[1].code is ProbeCode.INSUFFICIENT_BALANCE


def test_a_wrong_address_is_still_a_missing_model_not_an_unpaid_one(tmp_path) -> None:
    """404 stays its own thing: the fix is the address, and it is free."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "no such route"}})

    result = _probe(_generation_only(handler), tmp_path)

    assert result.stages[1].code is ProbeCode.MODEL_NOT_FOUND


def test_http_403_quota_error_body_is_not_swallowed_as_unauthorized(
    tmp_path,
) -> None:
    """A 403 whose body names a quota problem must not read as a bad key.

    Gateways commonly answer 403 for an exhausted free tier. Classifying every
    403 as UNAUTHORIZED sends the user to re-check a key that is perfectly
    valid, while the actual fix is to wait for the quota or top up. The body
    classifier already decides quota vs. rate limit; the 403 path must use it
    instead of returning UNAUTHORIZED unconditionally.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Free quota exhausted",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            },
        )

    result = _probe(_generation_only(handler), tmp_path)

    assert result.stages[1].code is ProbeCode.RATE_LIMITED


def test_http_403_rate_limit_body_stays_a_rate_limit(tmp_path) -> None:
    """A 403 whose body is about rate limiting is a wait, not a new key."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": "rate limit exceeded, too many requests"}},
        )

    result = _probe(_generation_only(handler), tmp_path)

    assert result.stages[1].code is ProbeCode.RATE_LIMITED


def test_http_403_without_a_recognisable_body_stays_unauthorized(tmp_path) -> None:
    """A bare 403 keeps meaning "the key is not allowed" (regression guard)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    result = _probe(_generation_only(handler), tmp_path)

    assert result.stages[1].code is ProbeCode.UNAUTHORIZED


def test_http_401_unknown_model_is_still_a_missing_model(tmp_path) -> None:
    """The model-not-found body path already worked for 401; keep it working."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "model not found: memory-model"}},
        )

    result = _probe(_generation_only(handler), tmp_path)

    assert result.stages[1].code is ProbeCode.MODEL_NOT_FOUND
