"""`embedding_unavailable`: a search that could not embed its query says so.

With a remote embedding model, every search embeds the query through that
service. When the service is down, the SDK swallows the failure and answers
with no hits, which reads as "you have no such memory". There is no lexical
fallback (owner decision): the search fails with the explicit code
`embedding_unavailable`, the API answers 503 with that code, and the App
shows a toast.

The check is only made when a search came back empty or raised, so a working
service costs nothing on searches that find something. It sends one tiny
embedding request to the same endpoint, model and width the read client
uses; the answer is remembered briefly so a burst of searches sends one.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from one_memory.http_client import create_client
from one_memory.hy_sdk_types import HySdkError
from one_memory.model_profiles import ModelProfileSnapshot, ProviderKind

EMBEDDING_UNAVAILABLE = "embedding_unavailable"
_OK_TTL_S = 30.0
_FAILED_TTL_S = 10.0
_TIMEOUT_S = 8.0


def _no_transport() -> httpx.BaseTransport | None:
    return None


#: Tests replace this to answer the check without a network.
transport_factory: Callable[[], httpx.BaseTransport | None] = _no_transport


class EmbeddingUnavailableError(HySdkError):
    """The live space's embedding service did not answer; search cannot run."""

    def __init__(self) -> None:
        super().__init__(EMBEDDING_UNAVAILABLE)


class EmbeddingHealth:
    """Whether a remote embedding answers, remembered for a few seconds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._known: dict[tuple[str, str, int | None], tuple[bool, float]] = {}

    def available(
        self,
        profile: ModelProfileSnapshot,
        *,
        endpoint: str | None,
        name_dims: int | None,
        api_key: str | None,
    ) -> bool:
        provider = profile.embedding_provider_kind or profile.provider_kind
        if provider is ProviderKind.LOCAL_ONNX or endpoint is None:
            return True
        model = profile.embedding_model or ""
        key = (endpoint, model, name_dims)
        now = time.monotonic()
        with self._lock:
            known = self._known.get(key)
            if known is not None and known[1] > now:
                return known[0]
        ok = _answers(
            endpoint,
            model=model,
            name_dims=name_dims,
            api_key="ollama" if provider is ProviderKind.OLLAMA else api_key,
        )
        with self._lock:
            self._known[key] = (ok, now + (_OK_TTL_S if ok else _FAILED_TTL_S))
        return ok

    def forget(self) -> None:
        with self._lock:
            self._known.clear()


def _answers(endpoint: str, *, model: str, name_dims: int | None, api_key: str | None) -> bool:
    body: dict[str, Any] = {"model": model, "input": ["ping"]}
    if name_dims:
        body["dimensions"] = name_dims
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    transport = transport_factory()
    try:
        with create_client(
            timeout=_TIMEOUT_S,
            **({"transport": transport} if transport is not None else {}),
        ) as client:
            response = client.post(f"{endpoint.rstrip('/')}/embeddings", json=body, headers=headers)
        if response.status_code != 200:
            return False
        data = response.json().get("data")
        return bool(isinstance(data, list) and data and data[0].get("embedding"))
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def has_hits(result: object) -> bool:
    """Whether an SDK search result holds any hit, whatever its scene's shape."""
    if not isinstance(result, Mapping):
        return bool(result)
    for key in ("memories", "facts", "experiences"):
        value = result.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, Mapping) and any(
            isinstance(item, list) and item for item in value.values()
        ):
            return True
    return False


class EmbeddingGuardedClient:
    """An SDK client whose `search` reports a dead embedding instead of nothing.

    Everything else is the client's own attribute, unchanged.
    """

    def __init__(self, client: Any, available: Callable[[], bool]) -> None:
        self._client = client
        self._available = available

    def search(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self._client.search(*args, **kwargs)
        except Exception:
            if not self._available():
                raise EmbeddingUnavailableError() from None
            raise
        if not has_hits(result) and not self._available():
            raise EmbeddingUnavailableError()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
