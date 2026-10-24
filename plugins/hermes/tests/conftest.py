"""Minimal fallback contract only when Hermes is absent."""

import contextvars
import importlib.util
import os
from pathlib import Path
import sys
import threading
import types
from dataclasses import dataclass

snapshot = os.environ.get("HERMES_MEMORY_PROVIDER_SNAPSHOT")
if snapshot:
    real_spec = importlib.util.spec_from_file_location(
        "agent.memory_provider", snapshot
    )
    module = importlib.util.module_from_spec(real_spec)
    sys.modules[real_spec.name] = module
    real_spec.loader.exec_module(module)
else:
    try:
        from agent.memory_provider import MemoryProvider  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "agent",
            "agent.memory_provider",
        }:
            raise
        module = types.ModuleType("agent.memory_provider")

        class MemoryProvider:
            pass

        @dataclass
        class RecallStatus:
            provider_label: str
            count: int

        def ctx_bound(fn):
            ctx = contextvars.copy_context()
            return lambda *args, **kwargs: ctx.run(fn, *args, **kwargs)

        def spawn_context_thread(target, *, name, daemon=True):
            return threading.Thread(target=ctx_bound(target), name=name, daemon=daemon)

        module.MemoryProvider = MemoryProvider
        module.RecallStatus = RecallStatus
        module.ctx_bound = ctx_bound
        module.spawn_context_thread = spawn_context_thread
        sys.modules["agent.memory_provider"] = module

spec = importlib.util.spec_from_file_location(
    "hermes_hy_memory",
    Path(__file__).parents[1] / "hy_memory/__init__.py",
    submodule_search_locations=[str(Path(__file__).parents[1] / "hy_memory")],
)
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)
