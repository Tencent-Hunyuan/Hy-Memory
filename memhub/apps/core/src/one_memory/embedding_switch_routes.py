"""HTTP surface of the embedding switch: spaces, plans, jobs, cleanup.

The contract the App and the CLI build on is
`memhub/docs/contracts/embedding-switch-v1.md`. Errors keep Core's shape,
`{"detail": "<code>"}`; a refusal that computed a plan also carries it as
`"plan"`, so a client can show the confirmation without asking again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from fastapi.params import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from one_memory.embedding_backend import ReembedUnavailableError
from one_memory.embedding_spaces import EmbeddingSpaceError
from one_memory.embedding_switch import (
    EmbeddingSwitchError,
    EmbeddingSwitchPlan,
    EmbeddingSwitchService,
)

#: HTTP status per refusal code; anything not listed is a 409.
_STATUS = {
    "model_profile_not_found": 404,
    "embedding_switch_not_found": 404,
    "embedding_space_not_found": 404,
    "sdk_reembed_unavailable": 503,
    "secure_storage_unavailable": 503,
    "insufficient_disk_space": 507,
}


class EmbeddingSwitchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1)


class EmbeddingSwitchStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    plan_token: str = Field(min_length=1, max_length=128)
    requested_by: str = Field(default="app", pattern=r"^(app|cli)$")


def refusal(code: str, *, plan: EmbeddingSwitchPlan | None = None) -> JSONResponse:
    content: dict[str, Any] = {"detail": code}
    if plan is not None:
        content["plan"] = plan.model_dump(mode="json")
    return JSONResponse(status_code=_STATUS.get(code, 409), content=content)


def _service(request: Request) -> EmbeddingSwitchService:
    return cast(EmbeddingSwitchService, request.app.state.embedding_switch)


def embedding_switch_router(dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(dependencies=dependencies)

    @router.get("/api/v1/embedding-spaces")
    async def list_embedding_spaces(request: Request) -> dict[str, object]:
        """The registry and what the vector store holds, space by space."""
        service = _service(request)
        spaces = await asyncio.to_thread(service.spaces.list_spaces)
        live = next((space for space in spaces if space.state == "live"), None)
        scanned: list[dict[str, object]] | None = None
        scan_error = service.backend.unavailable_reason()
        if scan_error is None:
            try:
                found = await service.backend.scan()
            except Exception as error:  # noqa: BLE001 - the registry still answers
                scan_error = getattr(error, "code", None) or "embedding_space_scan_failed"
            else:
                known = {(space.collection_base, space.name_dims or 0): space for space in spaces}
                scanned = [
                    {
                        "collection_base": item.collection_base,
                        "name_dims": item.name_dims,
                        "width": item.width,
                        "vectors": item.vectors,
                        "counts": dict(item.counts),
                        "space_id": known[item.key].space_id if item.key in known else None,
                        "state": known[item.key].state if item.key in known else "unregistered",
                    }
                    for item in found
                ]
        return {
            "schema_version": 1,
            "live_space_id": live.space_id if live is not None else None,
            "spaces": [space.model_dump(mode="json") for space in spaces],
            "scanned": scanned,
            "scan_error": scan_error,
        }

    @router.delete("/api/v1/embedding-spaces/{space_id}", response_model=None)
    async def delete_embedding_space(space_id: str, request: Request) -> Any:
        """Clean up a retired space now instead of after its 14 days."""
        try:
            space = await _service(request).drop_space(space_id)
        except (EmbeddingSwitchError, EmbeddingSpaceError, ReembedUnavailableError) as error:
            return refusal(error.code)
        except RuntimeError as error:
            if "held open" in str(error):
                return refusal("embedding_space_in_use")
            raise
        return {"schema_version": 1, "space": space.model_dump(mode="json")}

    @router.post("/api/v1/embedding-switches/plan", response_model=None)
    async def plan_embedding_switch(value: EmbeddingSwitchPlanRequest, request: Request) -> Any:
        try:
            plan = await _service(request).plan(
                value.profile_id, expected_revision=value.expected_revision
            )
        except EmbeddingSwitchError as error:
            return refusal(error.code, plan=error.plan)
        return plan.model_dump(mode="json")

    @router.post("/api/v1/embedding-switches", status_code=202, response_model=None)
    async def start_embedding_switch(value: EmbeddingSwitchStartRequest, request: Request) -> Any:
        # One lifecycle lock orders mode/evolve/backfill and embedding
        # maintenance. Without it each endpoint could pass its own preflight
        # and then begin concurrently.
        async with request.app.state.memory_mode_lock:
            evolve = request.app.state.evolve_service
            backfill = request.app.state.memory_graph_backfill
            if evolve.running() or backfill.running:
                return refusal("memory_lifecycle_busy")
            try:
                job = await _service(request).start_switch(
                    value.profile_id,
                    expected_revision=value.expected_revision,
                    plan_token=value.plan_token,
                    requested_by=value.requested_by,
                )
            except EmbeddingSwitchError as error:
                return refusal(error.code, plan=error.plan)
        return job.model_dump(mode="json")

    @router.get("/api/v1/embedding-switches/current")
    async def current_embedding_switch(request: Request, response: Response) -> dict[str, object]:
        """Polled by the loading screen, the banner and `memhub models switch status`."""
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(_service(request).current)

    @router.get("/api/v1/embedding-switches/{job_id}", response_model=None)
    async def get_embedding_switch(job_id: str, request: Request) -> Any:
        job = await asyncio.to_thread(_service(request).get_job, job_id)
        if job is None:
            return refusal("embedding_switch_not_found")
        return job.model_dump(mode="json")

    @router.post("/api/v1/embedding-switches/{job_id}/cancel", response_model=None)
    async def cancel_embedding_switch(job_id: str, request: Request) -> Any:
        try:
            job = await asyncio.to_thread(_service(request).cancel, job_id)
        except EmbeddingSwitchError as error:
            return refusal(error.code)
        return job.model_dump(mode="json")

    return router
