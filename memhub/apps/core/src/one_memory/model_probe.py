import asyncio
import json
import logging
from contextvars import ContextVar
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING, overload

import httpx
from pydantic import BaseModel, ConfigDict

from one_memory.http_client import create_async_client
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfile,
    ModelProfilePatch,
    ModelProfileStore,
    ProviderKind,
    attach_disable_thinking,
)
from one_memory.secrets import SecretStore

if TYPE_CHECKING:
    from one_memory.model_probe_operations import ModelProbeOperationReceipt, ModelProbeOperations

_operation: ContextVar[tuple["ModelProbeOperations", str] | None] = ContextVar(
    "model_probe_operation", default=None
)


class ProbeCode(StrEnum):
    OK = "ok"
    HY_DEFAULT_UNAVAILABLE = "hy_default_unavailable"
    MISSING_API_KEY = "missing_api_key"
    UNREACHABLE = "unreachable"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    MODEL_NOT_FOUND = "model_not_found"
    # Some compatible gateways expose generation but no model-list endpoint.
    # Listing failure does not prove generation is unavailable.
    MODEL_LIST_UNSUPPORTED = "model_list_unsupported"
    INVALID_RESPONSE = "invalid_response"
    STRUCTURED_OUTPUT_UNSUPPORTED = "structured_output_unsupported"
    EMBEDDING_MODEL_REQUIRED = "embedding_model_required"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"


class ProbeStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    ok: bool
    code: ProbeCode
    latency_ms: int
    embedding_dimensions: int | None = None


class ModelProbeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    profile_revision: int
    input_profile_revision: int | None = None
    stages: tuple[ProbeStage, ...]
    capabilities: ModelCapabilities


class ModelProber:
    def __init__(
        self,
        *,
        store: ModelProfileStore,
        secrets: SecretStore,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.transport = transport

    @overload
    async def probe(self, profile_id: str) -> ModelProbeResult: ...

    @overload
    async def probe(
        self, profile_id: str, *, operation_id: str, expected_profile_revision: int
    ) -> "ModelProbeOperationReceipt": ...

    async def probe(
        self,
        profile_id: str,
        *,
        operation_id: str | None = None,
        expected_profile_revision: int | None = None,
    ) -> "ModelProbeResult | ModelProbeOperationReceipt":
        from one_memory.model_probe_operations import (
            ModelProbeOperationRequest,
            ModelProbeOperations,
            ProbeOperationConflict,
            ProbeOperationUnavailable,
        )

        if operation_id is None and expected_profile_revision is None:
            await asyncio.to_thread(ModelProbeOperations(self.store).assert_no_pending, profile_id)
            return await self._probe(profile_id)
        if operation_id is None or expected_profile_revision is None:
            raise ValueError("probe_operation_identity_required")
        request = ModelProbeOperationRequest(
            operation_id=operation_id, expected_profile_revision=expected_profile_revision
        )
        operations = ModelProbeOperations(self.store)
        existing = await asyncio.to_thread(operations.reserve, profile_id, request)
        if existing is not None:
            return existing
        token = _operation.set((operations, request.operation_id))
        try:
            await self._probe(profile_id)
        except ProbeOperationConflict:
            await asyncio.to_thread(
                operations.fail, request.operation_id, "model_probe_profile_changed"
            )
        except Exception:
            # Unknown dispatch/storage outcomes stay reserved, including across restart.
            raise ProbeOperationUnavailable("model_probe_operation_unavailable") from None
        finally:
            _operation.reset(token)
        return await asyncio.to_thread(operations.lookup, request.operation_id)

    async def _probe(self, profile_id: str) -> ModelProbeResult:
        profile = await asyncio.to_thread(self.store.require, profile_id, self.secrets)
        await self._check_operation()
        if profile.provider_kind is ProviderKind.HY_DEFAULT:
            stage = ProbeStage(
                stage="endpoint",
                ok=False,
                code=ProbeCode.HY_DEFAULT_UNAVAILABLE,
                latency_ms=0,
            )
            capabilities = ModelCapabilities()
            if _operation.get() is not None:
                return await asyncio.to_thread(self._finish, profile, (stage,), capabilities)
            await asyncio.to_thread(
                self.store.record_capabilities,
                profile_id,
                expected_revision=profile.revision,
                capabilities=capabilities,
                secrets=self.secrets,
            )
            return ModelProbeResult(
                profile_id=profile_id,
                profile_revision=profile.revision,
                input_profile_revision=profile.revision,
                stages=(stage,),
                capabilities=capabilities,
            )
        if profile.provider_kind is ProviderKind.ANTHROPIC:
            stage = ProbeStage(
                stage="provider",
                ok=False,
                code=ProbeCode.STRUCTURED_OUTPUT_UNSUPPORTED,
                latency_ms=0,
            )
            return await asyncio.to_thread(self._finish, profile, (stage,), ModelCapabilities())
        secret = await asyncio.to_thread(self._secret, profile_id)
        await self._check_operation()
        if profile.provider_kind is not ProviderKind.OLLAMA and secret is None:
            stage = ProbeStage(
                stage="authentication",
                ok=False,
                code=ProbeCode.MISSING_API_KEY,
                latency_ms=0,
            )
            if _operation.get() is not None:
                return await asyncio.to_thread(self._finish, profile, (stage,), ModelCapabilities())
            return ModelProbeResult(
                profile_id=profile_id,
                profile_revision=profile.revision,
                input_profile_revision=profile.revision,
                stages=(stage,),
                capabilities=ModelCapabilities(),
            )
        async with create_async_client(
            timeout=profile.timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            endpoint = await self._request_stage(
                client,
                "model",
                *_models_request(profile, secret),
                expected_model=profile.model,
                listing_optional=True,
            )
            if not endpoint.ok:
                return await asyncio.to_thread(
                    self._finish, profile, (endpoint,), ModelCapabilities()
                )
            # 网关没有列表接口时，这一步没有验证过任何东西，
            # 模型可用性只能由后面的生成调用来证明。
            structured = await self._request_stage(
                client,
                "structured_output",
                *_structured_request(profile, secret),
                require_structured_json=True,
            )
            schema_refused = False
            if structured.code is ProbeCode.STRUCTURED_OUTPUT_UNSUPPORTED:
                # The gateway will not take the parameter. That says nothing
                # about whether the model can generate: a compatible endpoint
                # may reject `response_format: json_schema` while returning
                # valid prompted JSON without that optional constraint.
                #
                # It matters because HY does the extracting and HY never sends
                # `response_format` — it asks for JSON in the prompt and parses
                # what comes back. Treating the parameter as the one test of a
                # model meant a whole profile was refused over a capability the
                # memory pipeline does not use, and on this machine that read
                # as model_available=False with extraction silently never
                # running.
                #
                # Asked in this order and not the other way round: the schema
                # is worth having where it works, so the constrained call goes
                # first and the plain one is only a fallback.
                schema_refused = True
                structured = await self._request_stage(
                    client,
                    "structured_output",
                    *_structured_request(profile, secret, force_plain=True),
                    require_structured_json=True,
                )
            # Embedding is probed even when structured output fails: recall and
            # the graph store both depend on it, and the graph needs the real
            # vector width before it can be created.
            embedding_provider = profile.embedding_provider_kind or profile.provider_kind
            if embedding_provider is ProviderKind.LOCAL_ONNX:
                await self._check_operation()
                # Built-in embedding does not leave the machine. Probing a
                # remote endpoint for it would be both wrong and a false
                # failure when the generation key is the only credential.
                from one_memory.embedding_assets import BUILTIN_EMBEDDING_DIMENSIONS
                from one_memory.local_embedding import LocalEmbedder

                try:
                    await asyncio.to_thread(LocalEmbedder().encode, ["probe"])
                except Exception:
                    # The code alone cannot distinguish "weights missing" from
                    # "onnxruntime not in the bundle", and the packaged build
                    # hit the second while reporting the same string as the
                    # first. Cost an hour to tell apart; log it once here.
                    logging.getLogger(__name__).warning(
                        "built-in embedding probe failed",
                        exc_info=True,
                    )
                    embedding = ProbeStage(
                        stage="embedding",
                        ok=False,
                        code=ProbeCode.EMBEDDING_UNAVAILABLE,
                        latency_ms=0,
                    )
                else:
                    embedding = ProbeStage(
                        stage="embedding",
                        ok=True,
                        code=ProbeCode.OK,
                        latency_ms=0,
                        embedding_dimensions=BUILTIN_EMBEDDING_DIMENSIONS,
                    )
            elif not profile.embedding_model:
                embedding = ProbeStage(
                    stage="embedding",
                    ok=False,
                    code=ProbeCode.EMBEDDING_MODEL_REQUIRED,
                    latency_ms=0,
                )
            else:
                embedding = await self._request_stage(
                    client,
                    "embedding",
                    *_embedding_request(profile, secret),
                    require_embedding=True,
                )
        capabilities = ModelCapabilities(
            endpoint_reachable=True,
            model_available=structured.ok,
            # Two different questions, and one answer used to serve both. The
            # generation call above proves the model works; whether a schema
            # can be enforced is a separate fact about the gateway, and after
            # a refusal it is False even though generation succeeded.
            structured_output=structured.ok and not schema_refused,
            embedding_available=embedding.ok,
        )
        return await asyncio.to_thread(
            self._finish, profile, (endpoint, structured, embedding), capabilities
        )

    def _secret(self, profile_id: str) -> str | None:
        secret_ref = self.store.secret_ref(profile_id)
        return self.secrets.resolve(secret_ref) if secret_ref else None

    async def _check_operation(self) -> None:
        operation = _operation.get()
        if operation is not None:
            await asyncio.to_thread(operation[0].check, operation[1])

    async def _request_stage(
        self,
        client: httpx.AsyncClient,
        stage: str,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object] | None,
        *,
        require_structured_json: bool = False,
        require_embedding: bool = False,
        expected_model: str | None = None,
        listing_optional: bool = False,
    ) -> ProbeStage:
        await self._check_operation()
        started = monotonic()
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            if _operation.get() is not None:
                from one_memory.model_probe_operations import ProbeOperationUnavailable

                raise ProbeOperationUnavailable("model_probe_operation_unavailable") from None
            return _stage(stage, False, ProbeCode.UNREACHABLE, started)
        # Some compatible endpoints intentionally omit model listing. A
        # missing list endpoint does not prove generation is unavailable.
        if listing_optional and response.status_code in {400, 404, 405, 501}:
            return _stage(stage, True, ProbeCode.MODEL_LIST_UNSUPPORTED, started)
        if response.status_code in {401, 403}:
            # Read a structured error body before classifying a 401. Some
            # compatible providers distinguish an unknown model in the body.
            if _body_error_code(response) is ProbeCode.MODEL_NOT_FOUND:
                return _stage(stage, False, ProbeCode.MODEL_NOT_FOUND, started)
            return _stage(stage, False, ProbeCode.UNAUTHORIZED, started)
        if response.status_code == 402:
            # 402 就是欠费，不看 body：网关常常只回这个状态码而不写字面量，
            # 落进下面的兜底会变成 INVALID_RESPONSE，读起来像「接口坏了」，
            # 指引人去改地址。地址错（404）与欠费（402）的修复动作完全不同。
            return _stage(stage, False, ProbeCode.INSUFFICIENT_BALANCE, started)
        if response.status_code == 404:
            return _stage(stage, False, ProbeCode.MODEL_NOT_FOUND, started)
        if response.status_code == 429:
            return _stage(stage, False, ProbeCode.RATE_LIMITED, started)
        if not response.is_success:
            # The body is read here too, not only on the 200 path below. A
            # gateway that refuses `response_format` says so in a 400 body, and
            # collapsing every 400 into INVALID_RESPONSE threw that sentence
            # away — which is why the refusal could not be told from a genuinely
            # broken endpoint and no fallback was possible.
            return _stage(
                stage,
                False,
                _body_error_code(response) or ProbeCode.INVALID_RESPONSE,
                started,
            )
        # Some compatible gateways return HTTP 200 with an error object. Read
        # the body before treating transport success as a usable model.
        body_error = _body_error_code(response)
        if body_error is not None:
            return _stage(stage, False, body_error, started)
        if expected_model is not None:
            try:
                if not _models_include(response.json(), expected_model, models_url=url):
                    return _stage(stage, False, ProbeCode.MODEL_NOT_FOUND, started)
            except ValueError:
                return _stage(stage, False, ProbeCode.INVALID_RESPONSE, started)
        if require_structured_json:
            try:
                body = response.json()
                content = _response_content(body)
                parsed = json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                return _stage(
                    stage,
                    False,
                    ProbeCode.STRUCTURED_OUTPUT_UNSUPPORTED,
                    started,
                )
            if not isinstance(parsed, dict) or parsed.get("memory_probe") is not True:
                return _stage(
                    stage,
                    False,
                    ProbeCode.STRUCTURED_OUTPUT_UNSUPPORTED,
                    started,
                )
        if require_embedding:
            try:
                body = response.json()
            except ValueError:
                return _stage(stage, False, ProbeCode.EMBEDDING_UNAVAILABLE, started)
            width = _embedding_width(body)
            if width is None:
                return _stage(stage, False, ProbeCode.EMBEDDING_UNAVAILABLE, started)
            return _stage(
                stage,
                True,
                ProbeCode.OK,
                started,
                embedding_dimensions=width,
            )
        return _stage(stage, True, ProbeCode.OK, started)

    def _finish(
        self,
        profile: ModelProfile,
        stages: tuple[ProbeStage, ...],
        capabilities: ModelCapabilities,
    ) -> ModelProbeResult:
        operation = _operation.get()
        if operation is not None:
            return operation[0].finish(operation[1], stages, capabilities)
        from one_memory.model_probe_operations import ModelProbeOperations

        ModelProbeOperations(self.store).assert_no_pending(profile.profile_id)
        # Measuring embedding width may create a new profile version of its own.
        input_revision = profile.revision
        profile = self._record_embedding_width(profile, stages)
        self.store.record_capabilities(
            profile.profile_id,
            expected_revision=profile.revision,
            capabilities=capabilities,
            secrets=self.secrets,
        )
        return ModelProbeResult(
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            input_profile_revision=input_revision,
            stages=stages,
            capabilities=capabilities,
        )

    def _record_embedding_width(
        self,
        profile: ModelProfile,
        stages: tuple[ProbeStage, ...],
    ) -> ModelProfile:
        """Store the measured vector width on the profile.

        The graph store fixes its embedding column width when it is first
        created, so guessing this leaves every relation write failing later.
        """
        width = next(
            (
                stage.embedding_dimensions
                for stage in stages
                if stage.stage == "embedding" and stage.embedding_dimensions
            ),
            None,
        )
        if width is None or width == profile.embedding_dimensions:
            return profile
        return self.store.update(
            profile.profile_id,
            ModelProfilePatch(
                expected_revision=profile.revision,
                embedding_dimensions=width,
            ),
            self.secrets,
        )


def _models_request(
    profile: ModelProfile,
    secret: str | None,
) -> tuple[str, str, dict[str, str], None]:
    base_url = (profile.base_url or "").rstrip("/")
    if profile.provider_kind is ProviderKind.OLLAMA:
        return "GET", f"{base_url}/api/tags", {}, None
    headers = _headers(profile.provider_kind, secret)
    suffix = "/v1/models" if profile.provider_kind is ProviderKind.ANTHROPIC else "/models"
    return "GET", f"{base_url}{suffix}", headers, None


def _structured_request(
    profile: ModelProfile,
    secret: str | None,
    *,
    force_plain: bool = False,
) -> tuple[str, str, dict[str, str], dict[str, object]]:
    """The generation probe. `force_plain` drops the schema constraint and
    asks the model for JSON in words instead, which is what HY does."""
    base_url = (profile.base_url or "").rstrip("/")
    schema = {
        "type": "object",
        "properties": {"memory_probe": {"type": "boolean"}},
        "required": ["memory_probe"],
        "additionalProperties": False,
    }
    if profile.provider_kind is ProviderKind.OLLAMA:
        return (
            "POST",
            f"{base_url}/api/chat",
            {},
            {
                "model": profile.model,
                "stream": False,
                # Without this a reasoning model spends the whole probe budget
                # on thinking tokens and gets recorded as unreachable.
                "think": False,
                "format": schema,
                "messages": [{"role": "user", "content": "Return memory_probe=true"}],
            },
        )
    if profile.provider_kind is ProviderKind.ANTHROPIC:
        return (
            "POST",
            f"{base_url}/v1/messages",
            _headers(profile.provider_kind, secret),
            {
                "model": profile.model,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "Return memory_probe=true as JSON"}],
            },
        )
    payload: dict[str, object] = {
        "model": profile.model,
        "max_tokens": 256,
        "messages": [
            {
                "role": "user",
                # Worded to still ask for JSON when the parameter is gone,
                # because that is the only thing left holding the shape.
                "content": 'Return memory_probe=true as JSON: {"memory_probe": true}',
            }
        ],
    }
    if not force_plain:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "memory_probe", "strict": True, "schema": schema},
        }
    if profile.disable_thinking:
        attach_disable_thinking(profile, payload)
    return (
        "POST",
        f"{base_url}/chat/completions",
        _headers(profile.provider_kind, secret),
        payload,
    )


def _headers(provider: ProviderKind, secret: str | None) -> dict[str, str]:
    if secret is None:
        return {}
    if provider is ProviderKind.ANTHROPIC:
        return {
            "x-api-key": secret,
            "anthropic-version": "2023-06-01",
        }
    return {"Authorization": f"Bearer {secret}"}


def _embedding_request(
    profile: ModelProfile,
    secret: str | None,
) -> tuple[str, str, dict[str, str], dict[str, object]]:
    provider = profile.embedding_provider_kind or profile.provider_kind
    base_url = (profile.embedding_base_url or profile.base_url or "").rstrip("/")
    if provider is ProviderKind.OLLAMA:
        return (
            "POST",
            f"{base_url}/api/embed",
            {},
            {"model": profile.embedding_model, "input": "memory probe"},
        )
    return (
        "POST",
        f"{base_url}/embeddings",
        _headers(provider, secret),
        {"model": profile.embedding_model, "input": "memory probe"},
    )


def _body_error_code(response: httpx.Response) -> ProbeCode | None:
    """在 HTTP 2xx 响应体里识别网关级错误，返回归类后的 ProbeCode。

    没有 error 字段就返回 None（正常响应）。

    分类顺序按"修复动作不同"排列，从最具体到最兜底：额度/限流要等或加配额、
    鉴权要重新申请或绑定业务空间、模型不存在要换模型 ID。顺序颠倒会把
    "额度耗尽"误报成"鉴权失败"，把排查带向错误方向。
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if error in (None, "", {}, []):
        return None

    if isinstance(error, str):
        code_text = error
    elif isinstance(error, dict):
        code_text = " ".join(
            str(error.get(key, "")) for key in ("code", "ret_code", "type", "message")
        )
    else:
        return None
    probe = code_text.lower()

    # 1. 余额不足需要充值或换 key，不能误导成稍后再试的限流。
    if any(
        token in probe
        for token in (
            "insufficient balance",
            "insufficient_balance",
            "balance exhausted",
            "余额不足",
            "欠费",
        )
    ):
        return ProbeCode.INSUFFICIENT_BALANCE
    # 2. 额度与限流
    if any(
        token in probe
        for token in ("429", "rate_limit", "rate limit", "too many", "quota", "credit", "balance")
    ):
        return ProbeCode.RATE_LIMITED
    # 3. Authentication and authorization.
    if any(
        token in probe
        for token in (
            "401",
            "403",
            "auth",
            "invalid token",
            "permission",
            "forbidden",
        )
    ):
        return ProbeCode.UNAUTHORIZED
    # 4. Model not found. Keep the generic localized marker because compatible
    # providers may return localized error messages.
    if any(
        token in probe
        for token in (
            "404",
            "model not found",
            "does not exist",
            "unknown model",
            "no such model",
            "不存在",
        )
    ):
        return ProbeCode.MODEL_NOT_FOUND
    # Checked after the model-name cases on purpose. A gateway that refuses
    # the parameter and a model name that does not exist both arrive as a 400
    # here, and reading them in the other order would let a typo in the model
    # name be retried as a plain call and reported as a working profile.
    if any(
        marker in probe
        for marker in (
            "response_format",
            "response format",
        )
    ):
        return ProbeCode.STRUCTURED_OUTPUT_UNSUPPORTED
    return ProbeCode.INVALID_RESPONSE


def _embedding_width(body: object) -> int | None:
    """Return the real vector width, which the graph store needs up front."""
    if not isinstance(body, dict):
        return None
    embeddings = body.get("embeddings")
    if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
        return len(embeddings[0]) or None
    data = body.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    embedding = data[0].get("embedding")
    if not isinstance(embedding, list) or not embedding:
        return None
    return len(embedding)


def _response_content(body: object) -> object:
    if not isinstance(body, dict):
        return body
    message = body.get("message")
    if isinstance(message, dict):
        return message.get("content")
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice_message = choices[0].get("message")
        if isinstance(choice_message, dict):
            return choice_message.get("content")
    content = body.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        return content[0].get("text")
    return body


def _models_include(body: object, expected: str, *, models_url: str = "") -> bool:
    if not isinstance(body, dict):
        return False
    values = body.get("models") if isinstance(body.get("models"), list) else body.get("data")
    if not isinstance(values, list):
        return False
    endpoint = httpx.URL(models_url)
    # Google's documented OpenAI endpoint may list resource names while chat
    # accepts bare model IDs. Keep this alias out of other providers' namespaces
    # and leave the configured model unchanged for the actual generation probe.
    google_alias = (
        endpoint.scheme == "https"
        and endpoint.host == "generativelanguage.googleapis.com"
        and endpoint.port in {None, 443}
        and endpoint.path == "/v1beta/openai/models"
    )
    for value in values:
        if not isinstance(value, dict):
            continue
        model_id = value.get("id") or value.get("name") or value.get("model")
        if model_id == expected or (
            isinstance(model_id, str) and model_id.removesuffix(":latest") == expected
        ):
            return True
        if (
            google_alias
            and isinstance(model_id, str)
            and model_id.removeprefix("models/") == expected.removeprefix("models/")
        ):
            return True
    return False


def _stage(
    name: str,
    ok: bool,
    code: ProbeCode,
    started: float,
    *,
    embedding_dimensions: int | None = None,
) -> ProbeStage:
    return ProbeStage(
        stage=name,
        ok=ok,
        code=code,
        latency_ms=max(0, round((monotonic() - started) * 1000)),
        embedding_dimensions=embedding_dimensions,
    )
