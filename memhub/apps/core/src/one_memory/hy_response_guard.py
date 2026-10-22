"""Transport validation for the pinned HY SDK; HY's prompts and decisions pass through unchanged."""

import logging
from contextlib import nullcontext
from time import monotonic
from typing import Any

from one_memory.diagnostic_log import diagnostic_event, diagnostic_exception, elapsed_ms
from one_memory.hy_source_context import HyWriteBindings
from one_memory.hy_step_timing import HyStepTimer


def _http_status(value: Any) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _exception_status(error: Exception) -> int | None:
    try:
        status = _http_status(getattr(error, "status_code", None))
        if status is not None:
            return status
        status = _http_status(getattr(getattr(error, "response", None), "status_code", None))
        if status is not None:
            return status
        # Native LLMRetryError retains sanitized attempt dictionaries. Only the
        # final attempt describes the terminal failure; never parse error text.
        attempts = getattr(error, "attempts", None)
        if isinstance(attempts, (list, tuple)) and attempts and isinstance(attempts[-1], dict):
            return _http_status(attempts[-1].get("status_code"))
    except Exception:
        # Diagnostic attribute access must not replace the provider exception.
        return None
    return None


def _model_failure_code(status: int | None) -> str:
    specific = {
        401: "hy_memory_model_authentication_failed",
        402: "hy_memory_model_payment_required",
        403: "hy_memory_model_access_denied",
        408: "hy_memory_model_timeout",
        429: "hy_memory_model_rate_limited",
    }
    if status in specific:
        return specific[status]
    if status is not None and 500 <= status <= 599:
        return "hy_memory_model_service_unavailable"
    if status is not None and 400 <= status <= 422:
        return "hy_memory_model_request_incompatible"
    return "hy_memory_model_request_failed"


def _terminal_client_timeout(error: Exception) -> bool:
    try:
        attempts = getattr(error, "attempts", None)
        if not isinstance(attempts, (list, tuple)) or not attempts:
            return False
        final = attempts[-1]
        if type(final) is not dict:
            return False
        kind = final.get("failure_kind")
        return type(kind) is str and kind == "timeout"
    except Exception:
        return False


class HyResponseGuard:
    def __init__(self, provider: Any, *, phase: str = "extract") -> None:
        self.provider = provider
        #: Diagnostic label only; the request passes through unchanged.
        self.phase = phase
        self.failures: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        return await self._complete("complete", args, kwargs)

    async def complete_messages(self, *args: Any, **kwargs: Any) -> Any:
        return await self._complete("complete_messages", args, kwargs)

    async def _complete(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        for attempt in range(2):
            started = monotonic()
            try:
                response = await getattr(self.provider, method)(*args, **kwargs)
            except Exception as error:
                status = _exception_status(error)
                code = (
                    "hy_memory_model_timeout"
                    if status is None and _terminal_client_timeout(error)
                    else _model_failure_code(status)
                )
                self.failures.append(code)
                logging.getLogger(__name__).warning(
                    "%s: status_code=%s", code, status if status is not None else "unknown"
                )
                diagnostic_event(
                    level="error",
                    component="hy.model",
                    event="hy.model_call.failed",
                    outcome="failed",
                    duration_ms=elapsed_ms(started),
                    error_code=code,
                    **diagnostic_exception(error),
                    details={"phase": self.phase, "attempt": attempt + 1, "call_count": 1},
                )
                raise
            truncated = getattr(response, "finish_reason", "") == "length"
            empty = not (response.content or "").strip() and not getattr(
                response, "tool_calls", None
            )
            if not truncated and not empty:
                diagnostic_event(
                    level="info",
                    component="hy.model",
                    event="hy.model_call.completed",
                    outcome="succeeded",
                    duration_ms=elapsed_ms(started),
                    details={"phase": self.phase, "attempt": attempt + 1, "call_count": 1},
                )
                return response
            code = (
                "hy_memory_model_output_truncated"
                if truncated
                else "hy_memory_model_empty_response"
            )
            if attempt:
                self.failures.append(code)
                raise ValueError(code)
            logging.getLogger(__name__).warning("%s: retrying the same HY model request", code)
            if truncated and "max_tokens" in kwargs:
                kwargs = {**kwargs, "max_tokens": min(16000, kwargs["max_tokens"] * 2)}
        raise AssertionError("unreachable")


class HyReconcileGuard:
    #: Set only for the span of one leased write.
    step_timer: HyStepTimer | None = None

    def __init__(self, reconciler: Any) -> None:
        self.reconciler = reconciler
        self.failures: list[str] = []
        self.bindings: HyWriteBindings | None = None
        self.imported_contents: frozenset[str] | None = None
        self.mutation_target_ids: set[str] = set()

    def begin_write(self) -> None:
        self.mutation_target_ids.clear()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reconciler, name)

    async def reconcile(self, *args: Any, **kwargs: Any) -> Any:
        timer = self.step_timer
        with timer.step("reconcile") if timer is not None else nullcontext():
            return await self._reconcile(args, kwargs)

    async def _reconcile(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        started = monotonic()
        if self.imported_contents is not None:
            metadata = kwargs.get("new_memories_with_meta") or []
            texts = kwargs.get("new_memories") or []
            if (
                not metadata
                or not texts
                or any(
                    not isinstance(item, dict) or item.get("content") not in self.imported_contents
                    for item in metadata
                )
                or any(text not in self.imported_contents for text in texts)
                or {item["content"] for item in metadata} != set(texts)
            ):
                code = "hy_memory_write_verification_import_input_mismatch"
                self.failures.append(code)
                raise ValueError(code)
        try:
            result = await self.reconciler.reconcile(*args, **kwargs)
        except Exception as error:
            diagnostic_event(
                level="error",
                component="hy.reconcile",
                event="hy.reconcile.failed",
                outcome="failed",
                duration_ms=elapsed_ms(started),
                error_code="hy_memory_reconcile_failed",
                **diagnostic_exception(error),
            )
            raise
        if not result.success:
            self.failures.append("hy_memory_reconcile_failed")
            diagnostic_event(
                level="error",
                component="hy.reconcile",
                event="hy.reconcile.failed",
                outcome="failed",
                duration_ms=elapsed_ms(started),
                error_code="hy_memory_reconcile_failed",
                details={"result_count": len(result.ops)},
            )
            return result
        try:
            await self._check_targets(result, kwargs)
        except ValueError as error:
            code = str(error)
            self.failures.append(code)
            raise
        except Exception as error:
            code = "hy_memory_write_verification_failed"
            self.failures.append(code)
            raise ValueError(code) from error
        owners: dict[str, set[str]] = {}
        for item in kwargs.get("new_memories_with_meta") or []:
            if item.get("owner") in {"user", "agent"}:
                owners.setdefault(item["content"], set()).add(item["owner"])
        # HY's no-candidate ADD path copies content verbatim but omits owner.
        # Restore only an unambiguous exact match, never infer from wording.
        for operation in result.ops:
            source_owners = owners.get(operation.content, set())
            if operation.op == "ADD" and operation.owner is None and len(source_owners) == 1:
                operation.owner = next(iter(source_owners))
        operation_counts = {"ADD": 0, "UPDATE": 0, "SUPERSEDE": 0, "DELETE": 0}
        for operation in result.ops:
            raw_name = getattr(operation, "op", "")
            name = str(getattr(raw_name, "value", raw_name)).upper()
            if name in {"UPDATE", "SUPERSEDE", "DELETE", "SHADOW"}:
                target = getattr(operation, "memory_id", None)
                if isinstance(target, str) and target:
                    self.mutation_target_ids.add(target)
                self.mutation_target_ids.update(
                    key
                    for key in getattr(operation, "memory_ids", ())
                    if isinstance(key, str) and key
                )
            if name == "ADD":
                operation_counts["ADD"] += 1
            elif name in {"UPDATE", "MERGE", "NOOP"}:
                operation_counts["UPDATE"] += 1
            elif name == "SUPERSEDE":
                operation_counts["SUPERSEDE"] += 1
            elif name in {"DELETE", "SHADOW"}:
                operation_counts["DELETE"] += 1
        diagnostic_event(
            level="info",
            component="hy.reconcile",
            event="hy.reconcile.completed",
            outcome="succeeded",
            duration_ms=elapsed_ms(started),
            details={
                "created_count": operation_counts["ADD"],
                "updated_count": operation_counts["UPDATE"],
                "superseded_count": operation_counts["SUPERSEDE"],
                "deleted_count": operation_counts["DELETE"],
                "result_count": len(result.ops),
            },
        )
        return result

    async def _check_targets(self, result: Any, kwargs: dict[str, Any]) -> None:
        """HY may only touch nodes of this user and scope that this write was allowed to see."""
        targets = {op.memory_id for op in result.ops if op.memory_id}
        targets.update(key for op in result.ops for key in getattr(op, "memory_ids", []))
        for key in targets:
            node = await kwargs["vector_store"].get_by_id(key)
            if (
                node is None
                or node.user_id != kwargs["user_id"]
                or node.agent_id != kwargs["agent_id"]
            ):
                raise ValueError("hy_memory_write_verification_target_invalid")
            if self.bindings is not None:
                known = self.bindings.known.get(key)
                if known is None or (known.content, known.owner) != (node.content, node.owner):
                    raise ValueError("hy_memory_write_verification_target_unbound")
        if self.bindings is None:
            return
        for op in result.ops:
            if not op.content:
                continue
            linked = {key for key in (op.memory_id, *getattr(op, "memory_ids", [])) if key}
            self.bindings.record_operation_support(op.content, op.owner, linked)
            if op.memory_id:
                self.bindings.verified_operations[op.memory_id] = (op.content, op.owner)


def install_hy_write_guards(client: Any) -> None:
    """The pinned HY SDK treats an empty reconcile response as a successful empty op list.

    Bind this client's real write paths. Pro/lite use the ordinary writer;
    ultra uses the System2 writer's inner writer. Keep HY prompts, extraction,
    retrieval, reconciliation and storage intact; never patch the shared SDK
    installation.
    """

    async def install() -> None:
        from one_memory.hy_write_verification import HyExtractionGuard

        ordinary = await client._registry.get_writer()
        system2 = await client._registry.get_system2_writer()
        guards: list[Any] = []
        seen_writers: set[int] = set()
        seen_guards: set[int] = set()
        for writer in (ordinary, system2._writer):
            if id(writer) in seen_writers:
                continue
            seen_writers.add(id(writer))
            agent = writer.mem_agent
            if not isinstance(agent.llm_provider, HyResponseGuard):
                agent.llm_provider = HyResponseGuard(agent.llm_provider)
                agent.extractor.llm = agent.llm_provider
            if not isinstance(agent.extractor, HyExtractionGuard):
                agent.extractor = HyExtractionGuard(agent.extractor)
            reconciler = writer.reconciler
            provider = reconciler._get_llm()
            if not isinstance(provider, HyResponseGuard):
                reconciler._llm = HyResponseGuard(provider, phase="reconcile")
            if not isinstance(reconciler, HyReconcileGuard):
                writer._reconciler = HyReconcileGuard(reconciler)
            for guard in (
                agent.llm_provider,
                reconciler._llm,
                writer._reconciler,
                agent.extractor,
            ):
                if id(guard) not in seen_guards:
                    seen_guards.add(id(guard))
                    guards.append(guard)
        client._one_memory_write_guards = tuple(guards)

    client._loop_thread.run(install())
