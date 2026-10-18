<p align="center">
  <img src="docs/images/hunyuanlogo.png" alt="Tencent Hunyuan" width="260">
</p>

<h1 align="center">Hy Memory</h1>

<p align="center">Production-grade dual-system cognitive memory for LLM agents.</p>

<p align="center">
  <a href="#latest-news"><img src="https://img.shields.io/badge/news-latest-e65100?style=flat-square" alt="Latest News"></a>
  <a href="#papers"><img src="https://img.shields.io/badge/papers-research-68448b?style=flat-square" alt="Papers"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/get_started-quickstart-149954?style=flat-square" alt="Get Started"></a>
  <a href="#system-architecture"><img src="https://img.shields.io/badge/architecture-overview-007ec6?style=flat-square" alt="System Architecture"></a>
  <a href="#support"><img src="https://img.shields.io/badge/support-docs-00897b?style=flat-square" alt="Support"></a>
  <a href="https://huggingface.co/tencent/Hy-Memory-30B-A3B"><img src="https://img.shields.io/badge/model-Hugging%20Face-ffd21e?style=flat-square" alt="Hugging Face Model"></a>
  <a href="memhub/README.md"><img src="https://img.shields.io/badge/MemHub-README-f28c45?style=flat-square" alt="MemHub"></a>
  <a href="https://memory.hunyuan.tencent.com/"><img src="https://img.shields.io/badge/website-Hy%20Memory-0052d9?style=flat-square" alt="Website"></a>
  <a href="https://discord.gg/x56wEb2PX"><img src="https://img.shields.io/badge/Discord-memory%20channel-5865f2?style=flat-square&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-4c6ef5?style=flat-square" alt="Apache-2.0 License"></a>
</p>

English | [中文](README.zh.md)

**[Hy Memory](https://memory.hunyuan.tencent.com/)** ([Official Website](https://memory.hunyuan.tencent.com/)) is a production-grade dual-system cognitive memory engine and Python SDK (`hy-memory`) for self-evolving LLM agents, providing specialized memory pipelines across three task scenes: hierarchical long-term cognitive memory for **Chat**, two-layer fact and reusable-experience memory for **Working**, and version-evolution diff-chain tracking for **Coding**. Built on top of Hy Memory, **[MemHub](memhub/README.md)** is a local-first desktop and CLI product that unifies conversation history and coding traces from user-authorized AI applications (such as Claude Code, Cursor, Codex, OpenCode, and Qwen Code) into a single managed runtime and local data directory, enabling governed cross-application memory recall.

<div align="center">
  <video src="https://github.com/user-attachments/assets/94405890-8429-47c2-9084-38f7ed9f126f" controls width="640"></video>
  <p><b>MemHub Demo Video</b></p>
</div>

---

## Latest News

* *<mark>[new]</mark>* 🔥🔥🔥 **[2026-10]**: 📦 **Released [MemHub v1.0.4](https://github.com/Tencent-Hunyuan/Hy-Memory/releases/tag/v1.0.4)** and **[memhub-cli 1.0.5](https://www.npmjs.com/package/memhub-cli/v/1.0.5)**, with a signed macOS desktop app and verified macOS/Linux Runtimes!
* *<mark>[new]</mark>* 🔥🔥🔥 **[2026-10]**: 🚀 **Released** **Hy Memory 2.0 (`hy-memory==2.0.4`)** on [PyPI](https://pypi.org/project/hy-memory/2.0.4/) and open-sourced the **Hy-Memory** monorepo together with **[MemHub](memhub/README.md)**!
* *<mark>[new]</mark>* 🔥🔥🔥 **[2026-10]**: 🚀 **Open-sourced** **[Hy-Memory-30B-A3B](https://huggingface.co/tencent/Hy-Memory-30B-A3B)** on Hugging Face along with its [technical report](https://arxiv.org/abs/2610.22196) on attributed reinforcement learning for long-term memory writing!
* *<mark>[new]</mark>* 🔥🔥 **[2026-09]**: 📊 **Released** **[CMB: A Coding Memory Benchmark for Long-Horizon and Interleaved Coding Agents](docs/papers/CMB_A_Coding_Memory_Benchmark_for_Long_Horizon_and_Interleaved_Coding_Agents.pdf)** (submitted to **ICLR 2027**).
* *<mark>[new]</mark>* 🔥🔥 **[2026-08]**: 🎉 **Accepted** at **EMNLP 2026**: **[Memory Beyond Recall: A Dual-Process Cognitive Memory System for Self-Evolving LLM Agents](https://arxiv.org/abs/2606.09483)**!
* 🔥 **[2026-05]**: 🌟 **Initial Release** Released **[Hy Memory 1.0](https://mp.weixin.qq.com/s/dH5PGpxqArFZkSq3-QxWYA)**, our first-generation dual-system cognitive memory framework for self-evolving LLM agents.

---

## Papers

Welcome to the **Hy Memory Research Family**, covering cognitive chat memory, long-horizon coding memory, and RL-trained memory writing models:

> [**Memory Beyond Recall: A Dual-Process Cognitive Memory System for Self-Evolving LLM Agents**](https://arxiv.org/abs/2606.09483) *(Accepted at **EMNLP 2026**)*  
> **TL;DR:** Proposes the dual-process (System 1 + System 2) cognitive memory architecture for **Chat**, organizing personal memory into an 8-layer hierarchy (raw dialogues, atomic facts, summaries, schemas, and prospective intentions) with conflict reconciliation and cross-domain schema induction.  
> 📄 Paper: https://arxiv.org/abs/2606.09483

> [**CMB: A Coding Memory Benchmark for Long-Horizon and Interleaved Coding Agents**](docs/papers/CMB_A_Coding_Memory_Benchmark_for_Long_Horizon_and_Interleaved_Coding_Agents.pdf) *(Submitted to **ICLR 2027**)*  
> **TL;DR:** Introduces a benchmark and two-layer + version-evolution diff-chain memory method for long-horizon **Coding** agents, preserving both reusable engineering experiences and exact code revision trajectories across interleaved tasks.  
> 📄 Paper: [docs/papers/CMB_A_Coding_Memory_Benchmark_for_Long_Horizon_and_Interleaved_Coding_Agents.pdf](docs/papers/CMB_A_Coding_Memory_Benchmark_for_Long_Horizon_and_Interleaved_Coding_Agents.pdf)

> [**Hy-Memory-30B-A3B: Attributed Reinforcement Learning for Long-Term Memory Writing**](https://arxiv.org/abs/2610.22196) *(Open Model Technical Report)*  
> **TL;DR:** Trains a 30B-A3B MoE model via attributed reinforcement learning specifically for high-precision long-term memory extraction and conflict reconciliation.  
> 📄 Paper: https://arxiv.org/abs/2610.22196 · Model: https://huggingface.co/tencent/Hy-Memory-30B-A3B

---

## Quick Start

### 1. Installation

```bash
pip install hy-memory==2.0.3              # core (includes ChromaDB and Kuzu)
pip install "hy-memory[qdrant]==2.0.3"    # + Qdrant
pip install "hy-memory[faiss]==2.0.3"     # + FAISS
pip install "hy-memory[graph]==2.0.3"     # + Neo4j
pip install "hy-memory[mysql]==2.0.3"     # + MySQL cache
pip install "hy-memory[rerank]==2.0.3"    # + local multilingual cross-encoder rerank
pip install "hy-memory[all]==2.0.3"       # everything
```

### 2. Configuration

Only an LLM and an embedding endpoint are needed; any OpenAI-compatible service works:

```bash
export MEMORY_LLM_API_KEY="sk-your-key"
export MEMORY_LLM_BASE_URL="https://api.deepseek.com"
export MEMORY_LLM_MODEL="deepseek-chat"

export MEMORY_EMBEDDER_API_KEY="sk-your-key"
export MEMORY_EMBEDDER_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export MEMORY_EMBEDDER_MODEL="text-embedding-v3"
export MEMORY_EMBEDDING_DIMS=1024
```

Or use the OpenAI defaults with a single key:

```bash
export OPENAI_API_KEY="sk-your-key"
```

See the [Environment Variable Reference](docs/env_reference.md) for the full list.

### 3. Chat Memory

```python
from hy_memory import HyMemoryClient

client = HyMemoryClient(mode="pro")

# Write: plain text or OpenAI-format messages
client.add("I love sci-fi movies, especially Interstellar", user_id="user_1")
client.add([
    {"role": "user", "content": "Recommend a movie"},
    {"role": "assistant", "content": "Try Interstellar — a sci-fi masterpiece by Nolan"},
], user_id="user_1")

# Search: returns the profile / normal / proactive channels
results = client.search("What movies does the user like?", user_ids=["user_1"], limit=10)
for mem in results["memories"]["normal"]:
    print(f"  [{mem['score']:.2f}] {mem['content']}")

client.close()
```

### 4. Working & Coding Memory

Writes must include a tool-call trace (`tool_calls` or `role="tool"` messages) and take effect in `pro` / `ultra` mode only (`lite` ignores `scene` and stores everything as raw memory):

```python
messages = [
    {"role": "user", "content": "Fix the timeout in the search client."},
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "apply_patch", "arguments": '{"path":"src/search.py"}'},
    }]},
    {"role": "tool", "tool_call_id": "call_1", "name": "apply_patch",
     "content": "Patch applied successfully"},
]

client.add(
    messages,
    scene="coding",                              # or "working"
    user_id="user_123",
    workspace_id="github.com/example/service",   # optional: isolate by repository / project
    branch="fix/search-timeout",                 # optional: requires workspace_id
)

result = client.search(
    "Which timeout implementation was finally kept?",
    scene="coding",
    user_ids=["user_123"],
    workspace_id="github.com/example/service",
    branch="fix/search-timeout",
    limit=5,
)

result["facts"]          # fact layer
result["experiences"]    # experience layer
result["chain_block"]    # coding only: version-evolution context
```

### 5. Routing, Source Isolation & Advanced APIs

- **Automatic scene routing**: When `scene` is omitted, writes without tool calls go straight to `chat` (zero LLM calls), and writes with tool calls are classified once into `chat` / `working` / `coding`. For searches, `search_mode="default"` goes to `chat` with zero LLM calls, while `search_mode="agentic"` performs scene classification, query rewriting, and time-range parsing. See [Working/Coding Scenes](docs/scene_coding_usage.md).
- **Separate memories by source**: Pass `source="openclaw" | "cursor" | "codex"` on `add()` and filter with `include_sources` / `exclude_sources` on `search()` to isolate caller applications independently of `agent_id`.
- **CRUD & System 2 evolution**: `client.get(id)`, `client.update(id, text)`, `client.delete(id)`, `client.list_memories(user_id=...)`, and `client.evolve(user_id=...)` (triggers System 2 in `ultra` mode).
- **Per-call embedding endpoint override**: Pass `embed_override={"base_url": ..., "api_key": ...}` to `add()` / `search()` or use `with embed_endpoint_override(...):` for concurrency-safe endpoint switching.

For provider configuration, storage backends, scenes, and the complete API, see the [Usage Guide](docs/usage.md).

---

## System Architecture

<div align="center">
  <img src="docs/images/architecture.svg" alt="Hy Memory architecture" width="95%">
</div>

Hy Memory provides three memory scenes (`scene`), one per kind of task. `add()` / `search()` go through the scene router first, then into the Chat or the Working / Coding pipeline (orange parts in the diagram are coding-only; without them the pipeline is Working):

| `scene` | Tasks | Memory shape |
|---|---|---|
| `chat` | Small talk, personal information, preferences, general Q&A | Hierarchical memory (L0–L7) |
| `working` | Productivity work: documents, spreadsheets, research, financial analysis, content creation, computer operation | Two-layer memory: session facts + reusable experiences |
| `coding` | Software engineering: coding, debugging, testing, builds, APIs/SDKs, scripts, SQL | The same two-layer memory as `working`, plus a code version-evolution chain (diff chain) |

### 1. Chat: Memory Hierarchy

For long-term personal memory. Raw conversations are abstracted layer by layer into facts, summaries, a profile, and higher-level cognitive structures:

| Layer | Name | Content |
|---|---|---|
| L0 | `BASIC_INFO` | Basic information |
| L1 | `RAW` | Raw conversation (append-only) |
| L2 | `FACT` | Atomic facts (versioned, immutable records) |
| L3 | `SUMMARY` | Session summaries |
| L4 | `IDENTITY` | Reserved |
| L5 | `KNOWLEDGE` | Reserved |
| L6 | `SCHEMA` | Mental models (abstract narrative templates, graph layer) |
| L7 | `INTENTION` | Prospective intentions (with an expiry; demoted to ordinary facts once expired) |

**Modes**

| Mode | What it does | Graph DB | Use case |
|------|--------------|----------|----------|
| `lite` | Raw-only embedding writes and searches, no LLM calls; `scene` and `layers` are ignored | No | Vanilla vector RAG, fast ingestion, zero LLM cost |
| `pro` | + LLM extraction + reconcile | Optional (`MEMORY_ENABLE_GRAPH`, off by default) | Standard workloads |
| `ultra` | + System 2 schema induction + cross-domain sweeper (triggered by `evolve()`) | Yes (always on) | Full cognitive architecture |

### 2. Working: Two-Layer Productivity Memory

For getting things done. One session with tool calls (a trace) produces both layers at once:

| Layer | What it records | Per session | Structure |
|---|---|---|---|
| **Facts** (`WorkingFact`) | What this session did: the task, the approach, the result | Exactly 1, no threshold | `statement`, `subject`, `files`, `details` (verbatim source) |
| **Experiences** | Approaches that can be reused next time a similar task comes up | 0–N, must pass a value threshold | `task`, `search_keys`, `solution`, `boundary_envs`, `confidence` |

The fact layer guarantees that work which was done is remembered; the experience layer makes what was learned reusable. The two layers link to each other (`fact.derived_experience_ids` ↔ `experience.source_fact_ids`), and superseded facts stay traceable through a task-progress chain (`fact_chain_summary`).

### 3. Coding: Two-Layer Memory + Version-Evolution Chain

Coding **reuses the same two-layer pipeline and data schema** as Working. It differs in three ways:

| | Working | Coding |
|---|---|---|
| **Extraction spec** | Productivity-task spec | Software-engineering spec: the subject must be code or a decision; record exact coordinates (file / function / branch) instead of pasting code |
| **Storage** | `coding_memory.db` + `*_coding_keys` / `*_working_facts` collections | `coding_memory_cv.db` + `*_cv` collections, fully isolated from working |
| **Version-evolution chain** | None | **Yes**: how many times the same code was changed, and what each change was |

**Why a version-evolution chain**: fact extraction deliberately avoids pasting code (the budget goes to more informative coordinates), so a version that was later reverted never appears in a fact.
That version is usually not in the working tree or at HEAD either, so it cannot be read back afterwards. The chain stores the real diff of every version with zero LLM calls, filling in exactly the information a summary inevitably drops.

**Design highlights** (`hy_memory/coding/chain_store.py`):

- **A decorator, not a retriever**: the vector layer decides which facts are returned; the chain only attaches, under each fact, every revision of the code it touches (the user's words for that revision + the real diff).
- **Change sources**: local Git diffs (set `MEMORY_CODING_CHAIN_REPO_ROOT`), or deterministic replace actions with `path` / `old` / `new` in the tool trace.
- **Chain granularity & isolation**: one thread corresponds to one block of one function in one file on one branch, isolated by `user_id` + `workspace_id`.

---

## Support

### Storage Backends

- Vector stores: ChromaDB (default), Qdrant, FAISS
- Graph stores (ultra mode): Kuzu (default), Neo4j
- Working / Coding metadata: local SQLite

### Documentation

- [Usage guide](docs/usage.md) — full configuration reference, API details, deployment modes
- [Environment variables](docs/env_reference.md) — settings and defaults
- [Search and Reader guide](docs/hy_memory_search_skill.md) — reader selection, schema quotas, rerank, and return channels
- [Working/Coding Scenes](docs/scene_coding_usage.md) — productivity-memory routing, response shapes, and coding chain behavior
- [Graph memory design](docs/pro_graph_design.md) — graph projection, rerank, relation paths, and deployment limits
- [Environment template](.env.example) — a copy-ready configuration template
- [MemHub README](memhub/README.md) — local-first desktop & CLI product overview, runtime commands, and privacy boundaries
- [OpenClaw plugin](plugins/openclaw/README.md) — agent integrations live under `plugins/` (overview: [plugins/README.md](plugins/README.md))

### License

Hy-Memory is licensed under Apache-2.0. See [LICENSE](LICENSE); third-party
components remain subject to their respective licenses and notices.
