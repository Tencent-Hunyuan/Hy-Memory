"""Context propagated to host-provided HTTP transports for cost accounting."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_model_call_stage: ContextVar[str] = ContextVar(
    "hy_memory_model_call_stage",
    default="other",
)


def current_model_call_stage() -> str:
    return _model_call_stage.get()


@contextmanager
def model_call_stage(stage: str | None) -> Iterator[None]:
    normalized = (stage or "other").strip().lower() or "other"
    token: Token[str] = _model_call_stage.set(normalized)
    try:
        yield
    finally:
        _model_call_stage.reset(token)
