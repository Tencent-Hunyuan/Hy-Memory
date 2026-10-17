"""Hermes memory plugin entry point."""

from .provider import HyMemoryProvider


def register(ctx):
    """Register the native memory provider with Hermes."""
    ctx.register_memory_provider(HyMemoryProvider())
