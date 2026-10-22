import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.params import Depends

from one_memory.external_memory_access import external_access, search_external_matter_contexts
from one_memory.matters.discovery import MatterDiscovery
from one_memory.matters.models import (
    DiscoveryInput,
    DiscoveryStatus,
    Matter,
    MatterContext,
    MatterNote,
    MatterPatch,
    MatterQuery,
    SampleRecord,
)
from one_memory.matters.store import MatterConflict, MatterStore


def matter_router(dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/matters", tags=["matters"], dependencies=dependencies)

    @router.get("", response_model=list[Matter])
    async def list_matters(request: Request) -> list[Matter]:
        return await _call_async(_store(request).list)

    @router.get("/runs", response_model=list[DiscoveryStatus])
    def list_runs(request: Request) -> list[DiscoveryStatus]:
        return _legacy_store(request).runs()

    @router.post("/discover", response_model=DiscoveryStatus, status_code=202)
    async def discover(options: DiscoveryInput, request: Request) -> DiscoveryStatus:
        try:
            return _discovery(request).start(options)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/context/search", response_model=list[MatterContext])
    async def search_contexts(
        query: MatterQuery, request: Request, response: Response
    ) -> list[MatterContext]:
        started = time.monotonic()
        access = external_access(request.headers)
        store = _store(request)
        if getattr(store, "authority", "legacy") == "sdk":
            found = await _call_async(store.search_contexts, query.query)
            if access is not None:
                access.require("om_matters", frozenset({"all-apps"}))
                await asyncio.to_thread(
                    access.readable_source_ids,
                    request.app.state.evidence_store.database_path,
                )
                permitted = []
                for context in found:
                    sources = await _call_async(store.source_ids, context.matter_id)
                    current_allowed = await asyncio.to_thread(
                        access.readable_source_ids,
                        request.app.state.evidence_store.database_path,
                    )
                    if sources <= current_allowed:
                        permitted.append(context)
                found = permitted
        else:
            found = (
                search_external_matter_contexts(
                    access,
                    query.query,
                    matters=store,
                    governance=request.app.state.memory_governance_store,
                )
                if access is not None
                else store.search_contexts(query.query)
            )
        if query.query != "__om_probe__":
            from one_memory.call_event_recorder import matter_call_event, try_record

            try_record(
                request,
                lambda store: store.record(
                    matter_call_event(tuple(found), access, (time.monotonic() - started) * 1000)
                ),
                response,
            )
        return found

    @router.get("/{matter_id}", response_model=Matter)
    async def get_matter(matter_id: str, request: Request) -> Matter:
        return await _call_async(_store(request).get, matter_id)

    @router.patch("/{matter_id}", response_model=Matter)
    async def patch_matter(matter_id: str, patch: MatterPatch, request: Request) -> Matter:
        return await _call_async(_store(request).patch, matter_id, patch)

    @router.get("/{matter_id}/history", response_model=list[Matter])
    async def history(matter_id: str, request: Request) -> list[Matter]:
        return await _call_async(_store(request).history, matter_id)

    @router.get("/{matter_id}/context", response_model=MatterContext)
    async def context(matter_id: str, request: Request) -> MatterContext:
        return await _call_async(_store(request).context, matter_id)

    @router.get("/{matter_id}/evidence/{evidence_id}", response_model=SampleRecord)
    async def evidence(matter_id: str, evidence_id: str, request: Request) -> SampleRecord:
        return await _call_async(_store(request).evidence, matter_id, evidence_id)

    @router.post("/{matter_id}/notes", response_model=SampleRecord, status_code=201)
    async def add_note(matter_id: str, note: MatterNote, request: Request) -> SampleRecord:
        store = _store(request)
        record = await _call_async(
            _store(request).add_note,
            matter_id,
            note.text,
            expected_version=note.expected_version,
        )
        if getattr(store, "authority", "legacy") == "sdk":
            return record
        return next(
            note
            for note in store.notes(matter_id)
            if note.evidence_id == record.evidence_id
        )

    @router.get("/{matter_id}/notes", response_model=list[SampleRecord])
    async def notes(matter_id: str, request: Request) -> list[SampleRecord]:
        return await _call_async(_store(request).notes, matter_id)

    @router.post("/{matter_id}/refresh", response_model=DiscoveryStatus, status_code=202)
    async def refresh(matter_id: str, options: DiscoveryInput, request: Request) -> DiscoveryStatus:
        try:
            return _discovery(request).start(options, matter_id=matter_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "matter_not_found") from error

    return router


def _store(request: Request):
    return request.app.state.matter_store


def _legacy_store(request: Request) -> MatterStore:
    return cast(
        MatterStore,
        getattr(request.app.state, "legacy_matter_store", request.app.state.matter_store),
    )


def _discovery(request: Request) -> MatterDiscovery:
    return cast(MatterDiscovery, request.app.state.matter_discovery)


def _call[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except MatterConflict as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, "matter_not_found") from error
    except ValueError as error:
        raise HTTPException(409, "matter_source_or_version_changed") from error


async def _call_async[**P, T](
    function: Callable[P, T | Awaitable[T]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    try:
        value = function(*args, **kwargs)
        return await value if inspect.isawaitable(value) else value
    except MatterConflict as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, "matter_not_found") from error
    except ValueError as error:
        raise HTTPException(409, "matter_source_or_version_changed") from error
    except RuntimeError as error:
        if str(error).startswith("sdk_matter_"):
            raise HTTPException(503, str(error)) from error
        raise
