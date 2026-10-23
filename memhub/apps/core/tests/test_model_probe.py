import asyncio
import json
import time

import httpx
import pytest
from pydantic import SecretStr

from one_memory.model_probe import (
    PROBE_MAX_TOKENS,
    ModelProber,
    ProbeCode,
    _structured_request,
)
from one_memory.model_profiles import (
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore

API_KEY = "probe-secret-value"


def test_probe_still_measures_embedding_when_structured_output_fails(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = ModelProfileStore(database)
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="plain-model",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "plain-model"}]})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(200, json={"message": {"content": "not json"}})

    prober = ModelProber(
        store=store,
        secrets=secrets,
        transport=httpx.MockTransport(respond),
    )

    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.capabilities.structured_output is False
    assert result.capabilities.embedding_available is True
    assert store.require(profile.profile_id, secrets).embedding_dimensions == 768


def test_probe_records_the_real_embedding_width_on_the_profile(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = ModelProfileStore(database)
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="qwen3:8b",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )
    assert profile.embedding_dimensions is None

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text"}]},
            )
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps({"memory_probe": True})}},
        )

    prober = ModelProber(
        store=store,
        secrets=secrets,
        transport=httpx.MockTransport(respond),
    )

    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.capabilities.embedding_available is True
    embedding_stage = next(stage for stage in result.stages if stage.stage == "embedding")
    assert embedding_stage.embedding_dimensions == 768
    assert store.require(profile.profile_id, secrets).embedding_dimensions == 768


def test_openai_compatible_probe_checks_model_and_structured_output(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "memory-model"}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"memory_probe": true}'}}]},
        )

    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Provider",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="memory-model",
            embedding_model="embedding-model",
            api_key=SecretStr(API_KEY),
        ),
        secrets,
    )
    prober = ModelProber(
        store=store,
        secrets=secrets,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.capabilities.endpoint_reachable is True
    assert result.capabilities.model_available is True
    assert result.capabilities.structured_output is True
    assert result.capabilities.embedding_available is True
    assert [stage.code for stage in result.stages] == [
        ProbeCode.OK,
        ProbeCode.OK,
        ProbeCode.OK,
    ]
    assert API_KEY not in repr(result)
    assert requests[1].url.path == "/v1/chat/completions"


def test_probe_uses_local_embedding_endpoint_for_remote_generator(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "127.0.0.1":
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "memory-model"}]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"memory_probe": true}'}}]},
        )

    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Remote with local embedding",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="memory-model",
            embedding_model="nomic-embed-text",
            embedding_provider_kind=ProviderKind.OLLAMA,
            embedding_base_url="http://127.0.0.1:11434",
            api_key=SecretStr(API_KEY),
        ),
        secrets,
    )

    result = asyncio.run(
        ModelProber(
            store=store,
            secrets=secrets,
            transport=httpx.MockTransport(handler),
        ).probe(profile.profile_id)
    )

    assert result.capabilities.embedding_available is True
    assert requests[2].url == "http://127.0.0.1:11434/api/embed"


def test_local_embedding_probe_does_not_block_the_event_loop(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "memory-model"}]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"memory_probe": true}'}}]},
        )

    def slow_encode(_self, _texts):
        time.sleep(0.2)
        return [[0.1] * 384]

    monkeypatch.setattr("one_memory.local_embedding.LocalEmbedder.encode", slow_encode)
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Remote with built-in embedding",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="memory-model",
            embedding_model="bge-small-zh-v1.5",
            embedding_provider_kind=ProviderKind.LOCAL_ONNX,
            api_key=SecretStr(API_KEY),
        ),
        secrets,
    )

    async def scenario():
        ticks: list[float] = []
        running = True

        async def ticker() -> None:
            while running:
                ticks.append(asyncio.get_running_loop().time())
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            result = await ModelProber(
                store=store,
                secrets=secrets,
                transport=httpx.MockTransport(handler),
            ).probe(profile.profile_id)
        finally:
            running = False
            await ticker_task
        gaps = [right - left for left, right in zip(ticks, ticks[1:], strict=False)]
        assert result.capabilities.embedding_available is True
        assert len(ticks) >= 5
        assert max(gaps, default=0) < 0.1

    asyncio.run(scenario())


def test_probe_reports_missing_model_without_sending_generation(tmp_path) -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"models": [{"name": "other"}]})

    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Ollama",
            provider_kind=ProviderKind.OLLAMA,
            model="memory-model",
            embedding_model="embedding-model",
        ),
        secrets,
    )

    result = asyncio.run(
        ModelProber(
            store=store,
            secrets=secrets,
            transport=httpx.MockTransport(handler),
        ).probe(profile.profile_id)
    )

    assert result.stages[0].code is ProbeCode.MODEL_NOT_FOUND
    assert request_count == 1


def test_remote_provider_without_key_fails_before_network(tmp_path) -> None:
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Anthropic",
            provider_kind=ProviderKind.ANTHROPIC,
            model="claude",
        ),
        secrets,
    )

    result = asyncio.run(ModelProber(store=store, secrets=secrets).probe(profile.profile_id))

    assert result.stages[0].code is ProbeCode.STRUCTURED_OUTPUT_UNSUPPORTED
    assert result.capabilities.endpoint_reachable is False


def test_probe_does_not_follow_redirects(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://attacker.example.com/steal"},
        )

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

    result = asyncio.run(
        ModelProber(
            store=store,
            secrets=secrets,
            transport=httpx.MockTransport(handler),
        ).probe(profile.profile_id)
    )

    assert result.stages[0].code is ProbeCode.INVALID_RESPONSE


def _probe_with(handler, tmp_path, model: str = "memory-model"):
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Provider",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model=model,
            api_key=SecretStr(API_KEY),
        ),
        secrets,
    )
    return asyncio.run(
        ModelProber(
            store=store,
            secrets=secrets,
            transport=httpx.MockTransport(handler),
        ).probe(profile.profile_id)
    )


def test_http_200_with_auth_error_body_is_unauthorized(tmp_path) -> None:
    """A compatible gateway may put an auth failure in an HTTP 200 body."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "requestId": "probe",
                "error": {
                    "message": "invalid token: appId not found",
                    "type": "requestAuthError",
                    "code": "401",
                    "ret_code": 401,
                },
            },
        )

    result = _probe_with(handler, tmp_path)
    assert result.stages[0].code is ProbeCode.UNAUTHORIZED
    assert result.capabilities.model_available is False


def test_http_200_with_permission_error_body_is_unauthorized(tmp_path) -> None:
    """Localized permission errors remain authorization failures."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "memory-model"}]})
        return httpx.Response(
            200,
            json={
                "error": {
                    "message": "permission denied for this project",
                    "type": "requestAuthError",
                    "code": "permission_denied",
                    "ret_code": -2000,
                }
            },
        )

    result = _probe_with(handler, tmp_path)
    assert result.stages[0].code is ProbeCode.OK
    assert result.stages[1].code is ProbeCode.UNAUTHORIZED
    assert result.capabilities.structured_output is False


def test_http_200_with_quota_error_body_is_rate_limited(tmp_path) -> None:
    """额度耗尽必须与鉴权失败区分：两者修复动作完全不同。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"code": "insufficient_quota", "message": "credit_balance_exhausted"}},
        )

    assert _probe_with(handler, tmp_path).stages[0].code is ProbeCode.RATE_LIMITED


def test_listed_model_with_http_402_generation_is_not_available(tmp_path) -> None:
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": profile.model}]})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(
            402,
            json={"error": {"code": "payment_required", "message": "Insufficient Balance"}},
        )

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.stages[1].code is ProbeCode.INSUFFICIENT_BALANCE
    assert result.capabilities.model_available is False
    assert result.capabilities.structured_output is False


def test_healthy_body_without_error_field_still_passes(tmp_path) -> None:
    """回归保护：正常响应不得被新增的 body 检查拦下。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "memory-model"}], "error": None})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"memory_probe": true}'}}]},
        )

    result = _probe_with(handler, tmp_path)
    assert result.stages[0].code is ProbeCode.OK
    assert result.stages[1].code is ProbeCode.OK


def _compatible_profile(store: ModelProfileStore, secrets: InMemorySecretStore):
    """A remote compatible generator with a separate local embedder."""
    return store.create(
        ModelProfileInput(
            name="Compatible provider",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="memory-model",
            api_key=SecretStr(API_KEY),
            embedding_provider_kind=ProviderKind.OLLAMA,
            embedding_base_url="http://127.0.0.1:11434",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )


def test_probe_survives_a_gateway_without_a_model_listing_endpoint(tmp_path) -> None:
    """A missing /models endpoint does not disprove successful generation."""
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                400,
                json={"error": {"message": "请求中未解析到模型信息", "code": "2001"}},
            )
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"memory_probe": True})}}]},
        )

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.capabilities.endpoint_reachable is True
    assert result.capabilities.structured_output is True
    assert result.capabilities.embedding_available is True
    # 列不出模型时，模型可用性由生成调用背书，不能直接判死
    assert result.capabilities.model_available is True


def test_a_gateway_without_model_listing_still_fails_when_generation_fails(tmp_path) -> None:
    """正向对照：列不出模型 + 生成也失败时，绝不能报成可用。

    没有这条，上一条测试可以被「无条件把 model_available 设成 True」骗过。
    """
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(400, json={"error": {"message": "请求中未解析到模型信息"}})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.capabilities.structured_output is False
    assert result.capabilities.model_available is False


def test_probe_reads_the_body_before_calling_a_401_an_auth_failure(tmp_path) -> None:
    """A structured 401 body may identify an unknown model."""
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "model(memory-model) does not exist",
                    "type": "UserParamError",
                }
            },
        )

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    codes = {stage.code for stage in result.stages}
    assert ProbeCode.MODEL_NOT_FOUND in codes
    assert ProbeCode.UNAUTHORIZED not in codes


def test_a_real_401_is_still_reported_as_an_auth_failure(tmp_path) -> None:
    """正向对照：真的鉴权失败仍须报 UNAUTHORIZED。

    没有这条，上一条可以被「把所有 401 都改判成 MODEL_NOT_FOUND」骗过。
    """
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        return httpx.Response(401, json={"error": {"message": "invalid token", "code": "401"}})

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    assert ProbeCode.UNAUTHORIZED in {stage.code for stage in result.stages}


def test_a_gateway_that_refuses_response_format_is_not_a_dead_model(tmp_path) -> None:
    """A provider may reject response_format but return prompted JSON."""
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)
    attempts: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(400, json={"error": {"message": "请求中未解析到模型信息"}})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        asked_for_schema = b"response_format" in request.content
        attempts.append(asked_for_schema)
        if asked_for_schema:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "This response_format type is unavailable now",
                        "type": "UpstreamParamError",
                        "code": "3002",
                    }
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"memory_probe": True})}}]},
        )

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    # 先按能约束 schema 的方式问，被拒了才退一步 —— 顺序反了就永远不会去要 schema。
    assert attempts == [True, False]
    # 能生成，所以卡可用；约束不了 schema，所以这项如实报 False。
    assert result.capabilities.model_available is True
    assert result.capabilities.structured_output is False
    assert result.capabilities.embedding_available is True


def test_a_gateway_that_refuses_everything_is_still_a_dead_model(tmp_path) -> None:
    """正向对照：退一步之后还是不出 JSON，就得照旧判死。

    没有这条，上一条可以被「凡是 response_format 被拒就无条件放行」骗过。
    """
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(400, json={"error": {"message": "请求中未解析到模型信息"}})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        if b"response_format" in request.content:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "This response_format type is unavailable now",
                        "code": "3002",
                    }
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "还是不是 JSON"}}]})

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    assert result.capabilities.model_available is False
    assert result.capabilities.structured_output is False


def test_only_a_schema_refusal_gets_a_second_try(tmp_path) -> None:
    """失败原因不是「网关不收这个参数」时，不许重试。

    第一版这条测试是用 404 写的，而变异测试证明它什么都没守：404 退一步还是
    404，把条件放宽成「任何失败都重试」照样全绿。真正的风险是重试会把间歇性
    故障洗白 —— 这里第一发 500、第二发 200，无条件重试就会把一台正在抽风的
    网关报成可用，而探针存在的意义正是如实报告。
    """
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = _compatible_profile(store, secrets)
    generation_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal generation_calls
        if request.url.path.endswith("/models"):
            return httpx.Response(400, json={"error": {"message": "请求中未解析到模型信息"}})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 768]})
        generation_calls += 1
        if generation_calls == 1:
            return httpx.Response(500, text="Bad Gateway")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"memory_probe": True})}}]},
        )

    prober = ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler))
    result = asyncio.run(prober.probe(profile.profile_id))

    assert generation_calls == 1
    assert result.capabilities.model_available is False


@pytest.mark.parametrize("force_plain", [False, True])
@pytest.mark.parametrize("disable_thinking", [False, True])
@pytest.mark.parametrize(
    "base_url", ["https://models.example.com/v1", "https://api.deepseek.com/v1"]
)
def test_bounded_openai_probe_payload_preserves_profile_and_recipient(
    tmp_path, base_url, disable_thinking, force_plain
):
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Bounded synthetic probe",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url=base_url,
            model="unchanged-model",
            api_key=SecretStr(API_KEY),
            disable_thinking=disable_thinking,
        ),
        secrets,
    )
    before = profile.model_dump()

    method, url, headers, body = _structured_request(profile, API_KEY, force_plain=force_plain)

    expected = {
        "model": "unchanged-model",
        "max_tokens": PROBE_MAX_TOKENS,
        "messages": [
            {"role": "user", "content": 'Return memory_probe=true as JSON: {"memory_probe": true}'}
        ],
    }
    if not force_plain:
        expected["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "memory_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"memory_probe": {"type": "boolean"}},
                    "required": ["memory_probe"],
                    "additionalProperties": False,
                },
            },
        }
    # A generation preference must not shape the probe: with it on, a model
    # that refuses to stop thinking failed a probe it would otherwise pass.
    assert "reasoning_effort" not in expected
    assert "thinking" not in expected
    assert (method, url, headers) == (
        "POST",
        f"{base_url}/chat/completions",
        {"Authorization": f"Bearer {API_KEY}"},
    )
    assert body == expected
    assert profile.model_dump() == before
    assert store.require(profile.profile_id, secrets).model_dump() == before


@pytest.mark.parametrize("disable_thinking", [False, True])
@pytest.mark.parametrize("schema_refused", [False, True])
def test_bounded_probe_transport_keeps_existing_fallback_and_request_count(
    tmp_path, disable_thinking, schema_refused
):
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Bounded wire fixture",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="unchanged-model",
            api_key=SecretStr(API_KEY),
            disable_thinking=disable_thinking,
            embedding_provider_kind=ProviderKind.OLLAMA,
            embedding_base_url="http://127.0.0.1:11434",
            embedding_model="unchanged-embedding",
            embedding_dimensions=2,
        ),
        secrets,
    )
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "unchanged-model"}]})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        if schema_refused and "response_format" in json.loads(request.content):
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"memory_probe": true}'}}]}
        )

    result = asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler)).probe(
            profile.profile_id
        )
    )
    generation = requests[1:-1]
    assert len(requests) == (4 if schema_refused else 3)
    assert len(generation) == (2 if schema_refused else 1)
    assert ["response_format" in json.loads(request.content) for request in generation] == (
        [True, False] if schema_refused else [True]
    )
    for request in generation:
        body = json.loads(request.content)
        assert request.url == "https://models.example.com/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert body["model"] == "unchanged-model"
        assert body["max_tokens"] == PROBE_MAX_TOKENS
        assert "reasoning_effort" not in body
        assert "thinking" not in body
    assert requests[-1].url == "http://127.0.0.1:11434/api/embed"
    assert "Authorization" not in requests[-1].headers
    assert json.loads(requests[-1].content) == {
        "model": "unchanged-embedding",
        "input": "memory probe",
    }
    saved = store.require(profile.profile_id, secrets)
    assert saved.revision == profile.revision
    assert saved.disable_thinking is disable_thinking
    assert not saved.is_active
    assert result.capabilities.structured_output is (not schema_refused)
    assert result.capabilities.model_available


@pytest.mark.parametrize("force_plain", [False, True])
@pytest.mark.parametrize("provider", [ProviderKind.OLLAMA, ProviderKind.ANTHROPIC])
def test_other_provider_probe_payloads_are_unchanged(tmp_path, provider, force_plain):
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Unchanged provider",
            provider_kind=provider,
            model="unchanged-model",
            api_key=SecretStr(API_KEY),
            disable_thinking=True,
        ),
        secrets,
    )
    method, url, headers, body = _structured_request(profile, API_KEY, force_plain=force_plain)
    assert method == "POST"
    if provider is ProviderKind.OLLAMA:
        assert url == "http://127.0.0.1:11434/api/chat"
        assert headers == {}
        assert body == {
            "model": "unchanged-model",
            "stream": False,
            "think": False,
            "format": {
                "type": "object",
                "properties": {"memory_probe": {"type": "boolean"}},
                "required": ["memory_probe"],
                "additionalProperties": False,
            },
            "messages": [{"role": "user", "content": "Return memory_probe=true"}],
        }
    else:
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers == {"x-api-key": API_KEY, "anthropic-version": "2023-06-01"}
        assert body == {
            "model": "unchanged-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "Return memory_probe=true as JSON"}],
        }


def test_probe_reports_a_truncated_reply_as_a_budget_fact(tmp_path) -> None:
    """A thinking model that ran out of budget is not a model that cannot answer.

    Regression: the probe asked for 256 tokens, a reasoning model spent 251 of
    them thinking, and the empty body was reported as
    `structured_output_unsupported`. That code also fired the schema fallback,
    which re-asked with the same starved budget, so a working model was recorded
    as unavailable and the dialog blamed the model name.
    """
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    secrets = InMemorySecretStore()
    profile = store.create(
        ModelProfileInput(
            name="Thinking model",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.com/v1",
            model="glm-5.3-flash",
            api_key=SecretStr(API_KEY),
            embedding_provider_kind=ProviderKind.OLLAMA,
            embedding_base_url="http://127.0.0.1:11434",
            embedding_model="unchanged-embedding",
            embedding_dimensions=2,
        ),
        secrets,
    )
    generation: list[dict] = []

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "glm-5.3-flash"}]})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        generation.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": ""}}],
                "usage": {
                    "completion_tokens": PROBE_MAX_TOKENS,
                    "completion_tokens_details": {"reasoning_tokens": PROBE_MAX_TOKENS - 5},
                },
            },
        )

    result = asyncio.run(
        ModelProber(store=store, secrets=secrets, transport=httpx.MockTransport(handler)).probe(
            profile.profile_id
        )
    )

    structured = next(stage for stage in result.stages if stage.stage == "structured_output")
    assert structured.code is ProbeCode.STRUCTURED_OUTPUT_TRUNCATED
    # The schema parameter was never the problem, so it is neither dropped nor
    # re-asked: exactly one generation request, with the schema still on.
    assert len(generation) == 1
    assert "response_format" in generation[0]
    assert generation[0]["max_tokens"] == PROBE_MAX_TOKENS
