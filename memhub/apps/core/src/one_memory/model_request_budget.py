"""Optional local physical-request limits, shared across leases and restarts."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from one_memory.database_access import main_database
from one_memory.memory_server.migrations import model_request_budget_ddl


class ModelRequestBudgetError(RuntimeError):
    pass


class ModelRequestBudget:
    def __init__(
        self, database: Path, *, request_limit: int, body_byte_limit: int, output_token_limit: int
    ) -> None:
        if min(request_limit, body_byte_limit, output_token_limit) < 1:
            raise ValueError("model_request_budget_invalid")
        self.database = database
        self.request_limit = request_limit
        self.body_byte_limit = body_byte_limit
        self.output_token_limit = output_token_limit
        with main_database(database).write() as db, db:
            db.execute(
                # Schema owned by `memory_server.migrations` (migration 18).
                model_request_budget_ddl("sqlite")
            )

    def reserve(self, content: bytes) -> None:
        try:
            body = json.loads(content)
            # Compatible endpoints may select either field when both are present.
            limits = [body[key] for key in ("max_tokens", "max_completion_tokens") if key in body]
            if (
                len(content) > self.body_byte_limit
                or not limits
                or any(
                    type(value) is not int or not 0 < value <= self.output_token_limit
                    for value in limits
                )
            ):
                raise ValueError
            ceiling = max(limits)
        except (ValueError, TypeError, AttributeError):
            raise ModelRequestBudgetError("model_request_budget_payload_exceeded") from None
        with main_database(self.database).write() as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM model_request_budget_events").fetchone()[0]
            if count >= self.request_limit:
                raise ModelRequestBudgetError("model_request_budget_exhausted")
            db.execute(
                "INSERT INTO model_request_budget_events(body_bytes,output_tokens,reserved_at) "
                "VALUES (?,?,?)",
                (len(content), ceiling, datetime.now(UTC).isoformat()),
            )


class BudgetedModelTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, budget: ModelRequestBudget, *, inner: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.budget = budget
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        content = await request.aread()
        self.budget.reserve(content)
        # Failure still consumes the reservation; no request body or key is saved.
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()
