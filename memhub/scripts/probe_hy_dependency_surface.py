"""Report the public HY SDK surface required by MemHub."""

from __future__ import annotations

import tempfile
from pathlib import Path

_SANDBOX = Path(tempfile.mkdtemp(prefix="hy-probe-"))
from one_memory.hy_runtime_config import (
    HY_MEMORY_DISTRIBUTION,
    HY_MEMORY_READER,
    HY_MEMORY_VERSION,
    configure_hy_runtime_environment,
    hy_memory_version,
)

configure_hy_runtime_environment(_SANDBOX)

import hy_memory
from hy_memory import HyMemoryClient, MemoryConfig

results: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        value = fn()
    except Exception as error:  # noqa: BLE001 - reporting, not handling
        results.append((name, "MISSING", f"{type(error).__name__}: {error}"))
    else:
        results.append((name, "ok", repr(value)[:60]))


print(f"hy_memory.__version__ = {hy_memory.__version__}")
print(f"distribution/version  = {HY_MEMORY_DISTRIBUTION} {hy_memory_version()}")
print(f"loaded from           = {Path(hy_memory.__file__).parent}")
print(f"MemHub mode/reader    = pro / {HY_MEMORY_READER}")
print()

config = MemoryConfig.from_env()

# Config fields the bridge assigns. A field that does not exist is the whole
# risk: on a plain dataclass the assignment silently invents an attribute that
# nothing reads, so presence is checked before assignment, never after.
CONFIG_FIELDS = {
    "": ["debug", "metrics_enabled"],
    "llm": [
        "provider",
        "model",
        "api_key",
        "base_url",
        "timeout",
        "max_retries",
        "few_shot_enabled",
        "enable_query_rewrite",
        "enable_intention",
        "extract_system_prompt_zh",
    ],
    "embedder": [
        "provider",
        "model",
        "api_key",
        "base_url",
        "timeout",
        "max_retries",
        "embedding_dims",
    ],
    "vector_store": [
        "provider",
        "collection_name",
        "persist_directory",
        "embedding_dims",
        "host",
        "port",
        "api_key",
    ],
    "graph_store": ["provider", "db_path", "graph_rerank_enabled", "temporal_spine"],
    "recall": [
        "episode_recall_enabled",
        "dynamic_episode_enabled",
        "episode_capsule_context_enabled",
        "local_rerank_enabled",
    ],
}

# Negative control. This field exists in no HY build, so a report that shows it
# as `ok` means hasattr is not discriminating here and every other `ok` on the
# page is worthless. Same for the method control below.
CONFIG_FIELDS["llm"].append("__om_probe_field_that_cannot_exist__")

for section, fields in CONFIG_FIELDS.items():
    holder = config if section == "" else getattr(config, section, None)
    label = section or "config"
    if holder is None:
        results.append((f"{label}.*", "MISSING", "section absent"))
        continue
    for field in fields:
        present = hasattr(holder, field)
        results.append(
            (
                f"{label}.{field}",
                "ok" if present else "MISSING",
                type(holder).__name__,
            )
        )

# Public client methods MemHub calls.
for method in (
    "add",
    "add_extracted",
    "search",
    "get",
    "update",
    "delete",
    "list_memories",
    "evolve",
    "close",
    "get_user_basic_info",
    "__om_probe_method_that_cannot_exist__",
):
    results.append(
        (
            f"HyMemoryClient.{method}",
            "ok" if hasattr(HyMemoryClient, method) else "MISSING",
            "",
        )
    )

# The Chinese-writing override reads HY's own prompt module.
check("hy_memory.agent.prompts_zh", lambda: __import__("hy_memory.agent.prompts_zh"))
check("hy_memory.agent.extractor", lambda: __import__("hy_memory.agent.extractor"))
check("hy_memory.utils.lang_detect", lambda: __import__("hy_memory.utils.lang_detect"))

# Constructing the client is where a config the SDK dislikes would surface.
with tempfile.TemporaryDirectory() as sandbox:
    root = Path(sandbox)
    config.vector_store.provider = "chroma"
    config.vector_store.persist_directory = str(root / "vector")
    config.graph_store.provider = "kuzu"
    config.graph_store.db_path = str(root / "graph")
    config.llm.provider = "openai"
    config.llm.api_key = "probe-not-used"
    config.llm.base_url = "http://127.0.0.1:1/v1"
    config.embedder.provider = "openai"
    config.embedder.api_key = "probe-not-used"
    config.embedder.base_url = "http://127.0.0.1:1/v1"
    try:
        client = HyMemoryClient(config=config, mode="pro")
    except Exception as error:  # noqa: BLE001 - reporting, not handling
        results.append(("HyMemoryClient(config, mode='ultra')", "MISSING", str(error)))
    else:
        results.append(("HyMemoryClient(config, mode='ultra')", "ok", type(client).__name__))
        client.close()

width = max(len(name) for name, _, _ in results)
missing = 0
for name, state, detail in results:
    if state == "MISSING":
        missing += 1
    print(f"{name.ljust(width)}  {state:8}  {detail}")

print()
print(f"total {len(results)} checks, {missing} MISSING")
if hy_memory_version() != HY_MEMORY_VERSION:
    raise SystemExit(1)
