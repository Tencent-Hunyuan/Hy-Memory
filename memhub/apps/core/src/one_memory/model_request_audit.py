"""Persistent, content-free accounting for precipitation model requests."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import httpx

from one_memory.database_access import main_database
from one_memory.memory_server.migrations import model_request_audit_ddl

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelRequestAuditContext:
    request_id: str


_audit_context: ContextVar[ModelRequestAuditContext | None] = ContextVar(
    "one_memory_model_request_audit_context",
    default=None,
)


@contextmanager
def audit_precipitation_request(request_id: str) -> Iterator[None]:
    """Enable physical-request accounting for one memory precipitation."""

    token = _audit_context.set(ModelRequestAuditContext(request_id=request_id))
    try:
        yield
    finally:
        _audit_context.reset(token)


def _current_prompt_stage() -> str:
    try:
        resolver = getattr(  # noqa: B009 - optional SDK version boundary
            import_module("hy_memory.utils.model_call_context"),
            "current_model_call_stage",
        )
    except (ImportError, AttributeError):
        return "other"
    return str(resolver())


class ModelRequestAuditStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        with main_database(database).write() as connection, connection:
            for statement in model_request_audit_ddl("sqlite").split(";"):
                if statement.strip():
                    connection.execute(statement)

    def start(
        self,
        *,
        request_id: str,
        profile_id: str,
        profile_revision: int,
        provider_kind: str,
        model: str,
        request_kind: str,
        prompt_stage: str,
    ) -> str:
        audit_id = str(uuid4())
        with main_database(self.database).write() as connection, connection:
            connection.execute(
                """
                INSERT INTO model_request_audit (
                    audit_id,request_id,profile_id,profile_revision,provider_kind,
                    model,request_kind,prompt_stage,started_at,outcome
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    audit_id,
                    request_id,
                    profile_id,
                    profile_revision,
                    provider_kind,
                    model,
                    request_kind,
                    prompt_stage,
                    datetime.now(UTC).isoformat(),
                    "in_flight",
                ),
            )
        return audit_id

    def finish(
        self,
        audit_id: str,
        *,
        outcome: str,
        http_status: int | None,
        error_type: str | None = None,
        provider_request_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cached_prompt_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> None:
        with main_database(self.database).write() as connection, connection:
            connection.execute(
                """
                UPDATE model_request_audit
                SET completed_at=?,outcome=?,http_status=?,error_type=?,
                    provider_request_id=?,prompt_tokens=?,completion_tokens=?,
                    total_tokens=?,cached_prompt_tokens=?,reasoning_tokens=?
                WHERE audit_id=?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    outcome,
                    http_status,
                    error_type,
                    provider_request_id,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cached_prompt_tokens,
                    reasoning_tokens,
                    audit_id,
                ),
            )

    def report(
        self,
        *,
        since: str | None = None,
        request_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, object]:
        clauses: list[str] = []
        params: list[object] = []
        if since is not None:
            clauses.append("started_at>=?")
            params.append(since)
        if request_id is not None:
            clauses.append("request_id=?")
            params.append(request_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with main_database(self.database).read() as connection:
            summary = connection.execute(
                f"""
                SELECT COUNT(*) AS request_count,
                       SUM(CASE WHEN outcome='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN outcome='failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN outcome='in_flight' THEN 1 ELSE 0 END) AS in_flight,
                       SUM(prompt_tokens) AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens,
                       SUM(total_tokens) AS total_tokens,
                       SUM(cached_prompt_tokens) AS cached_prompt_tokens,
                       SUM(reasoning_tokens) AS reasoning_tokens,
                       AVG(prompt_tokens) AS avg_prompt_tokens,
                       AVG(completion_tokens) AS avg_completion_tokens
                FROM model_request_audit {where}
                """,
                tuple(params),
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT audit_id,request_id,profile_id,profile_revision,provider_kind,
                       model,request_kind,prompt_stage,started_at,completed_at,outcome,http_status,
                       error_type,provider_request_id,prompt_tokens,completion_tokens,
                       total_tokens,cached_prompt_tokens,reasoning_tokens
                FROM model_request_audit {where}
                ORDER BY started_at DESC,audit_id DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            minute_rows = connection.execute(
                f"""
                SELECT substr(started_at,1,16) || ':00Z' AS minute,
                       prompt_stage,
                       COUNT(*) AS request_count,
                       SUM(prompt_tokens) AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens,
                       SUM(total_tokens) AS total_tokens,
                       AVG(prompt_tokens) AS avg_prompt_tokens,
                       AVG(completion_tokens) AS avg_completion_tokens
                FROM model_request_audit {where}
                GROUP BY substr(started_at,1,16),prompt_stage
                ORDER BY minute,prompt_stage
                """,
                tuple(params),
            ).fetchall()
        keys = (
            "audit_id",
            "request_id",
            "profile_id",
            "profile_revision",
            "provider_kind",
            "model",
            "request_kind",
            "prompt_stage",
            "started_at",
            "completed_at",
            "outcome",
            "http_status",
            "error_type",
            "provider_request_id",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_prompt_tokens",
            "reasoning_tokens",
        )
        summary_keys = (
            "request_count",
            "succeeded",
            "failed",
            "in_flight",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_prompt_tokens",
            "reasoning_tokens",
            "avg_prompt_tokens",
            "avg_completion_tokens",
        )
        return {
            "summary": {
                key: (
                    round(float(value), 2)
                    if key.startswith("avg_") and value is not None
                    else int(value)
                    if value is not None
                    else 0
                )
                for key, value in zip(summary_keys, summary, strict=True)
            },
            "requests": [dict(zip(keys, tuple(row), strict=True)) for row in rows],
            "by_minute": [
                {
                    "minute": str(row[0]),
                    "prompt_stage": str(row[1]),
                    "request_count": int(row[2]),
                    "prompt_tokens": int(row[3] or 0),
                    "completion_tokens": int(row[4] or 0),
                    "total_tokens": int(row[5] or 0),
                    "avg_prompt_tokens": round(float(row[6] or 0), 2),
                    "avg_completion_tokens": round(float(row[7] or 0), 2),
                }
                for row in minute_rows
            ],
        }


def _non_negative_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class _ResponseUsage(TypedDict):
    provider_request_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_prompt_tokens: int | None
    reasoning_tokens: int | None


def _empty_usage() -> _ResponseUsage:
    return {
        "provider_request_id": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cached_prompt_tokens": None,
        "reasoning_tokens": None,
    }


def _response_usage(content: bytes) -> _ResponseUsage:
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _empty_usage()
    if not isinstance(body, dict):
        return _empty_usage()
    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    cached = (
        prompt_details.get("cached_tokens")
        if isinstance(prompt_details, dict)
        else usage.get("prompt_cache_hit_tokens")
    )
    reasoning = (
        completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    )
    return {
        "provider_request_id": body.get("id") if isinstance(body.get("id"), str) else None,
        "prompt_tokens": _non_negative_int(usage.get("prompt_tokens")),
        "completion_tokens": _non_negative_int(usage.get("completion_tokens")),
        "total_tokens": _non_negative_int(usage.get("total_tokens")),
        "cached_prompt_tokens": _non_negative_int(cached),
        "reasoning_tokens": _non_negative_int(reasoning),
    }


def _request_kind(request: httpx.Request) -> str:
    path = request.url.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return "chat_completion"
    if path.endswith("/embeddings"):
        return "embedding"
    return "model_http"


def _request_model(content: bytes, fallback: str) -> str:
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return fallback
    model = body.get("model") if isinstance(body, dict) else None
    return model if isinstance(model, str) and model else fallback


class AuditedModelTransport(httpx.AsyncBaseTransport):
    """Record one row for each physical precipitation HTTP attempt."""

    def __init__(
        self,
        store: ModelRequestAuditStore,
        *,
        profile_id: str,
        profile_revision: int,
        provider_kind: str,
        model: str,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = store
        self.profile_id = profile_id
        self.profile_revision = profile_revision
        self.provider_kind = provider_kind
        self.model = model
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        context = _audit_context.get()
        if context is None:
            return await self.inner.handle_async_request(request)
        content = await request.aread()
        audit_id = await asyncio.to_thread(
            self.store.start,
            request_id=context.request_id,
            profile_id=self.profile_id,
            profile_revision=self.profile_revision,
            provider_kind=self.provider_kind,
            model=_request_model(content, self.model),
            request_kind=_request_kind(request),
            prompt_stage=_current_prompt_stage(),
        )
        try:
            response = await self.inner.handle_async_request(request)
        except BaseException as error:
            await asyncio.to_thread(
                self.store.finish,
                audit_id,
                outcome="failed",
                http_status=None,
                error_type=type(error).__name__,
            )
            raise
        content = await response.aread()
        usage = _response_usage(content)
        try:
            await asyncio.to_thread(
                self.store.finish,
                audit_id,
                outcome="succeeded" if response.is_success else "failed",
                http_status=response.status_code,
                error_type=None if response.is_success else f"http_{response.status_code}",
                **usage,
            )
        except Exception:
            # The durable in_flight row is more truthful than hiding the model
            # response after it has already incurred cost.
            logger.exception("model_request_audit_finish_failed audit_id=%s", audit_id)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
