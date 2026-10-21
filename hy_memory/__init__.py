"""
HY Memory - 核心 SDK

工业级智能体记忆系统核心框架。

快速开始:
    export OPENAI_API_KEY="sk-your-key-here"

    from hy_memory import HyMemoryClient

    # 最简用法 — 只需设置 OPENAI_API_KEY 环境变量
    client = HyMemoryClient(mode="pro")
    client.add("用户喜欢科幻电影", user_id="test_user")
    results = client.search("用户喜欢什么？", user_ids=["test_user"])
    client.close()

默认配置:
    - embedder: OpenAI text-embedding-3-small (1536 维)
    - llm: OpenAI gpt-4.1-nano
    - vector_store: Chroma 本地嵌入式 (零外部依赖)

    只需设置 OPENAI_API_KEY 环境变量即可运行。

    # 自定义配置
    client = HyMemoryClient.from_config({
        "vector_store": {"provider": "qdrant"},
        "graph_store": {
            "provider": "neo4j",
            "url": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "password",
        },
        "enable_graph": True,
    })

数据隔离 (三级):
    - user_id:  一级 key — 每个用户唯一的记忆库
    - agent_id: 二级 key — 同一用户下不同 Agent 场景的隔离
    - session_id: 三级 key — 同一 Agent 下不同会话的隔离

安装方式:
    pip install hy-memory          # 核心依赖 (含 Chroma，开箱即用)
    pip install hy-memory[qdrant]  # + Qdrant 向量库
    pip install hy-memory[faiss]   # + FAISS 向量库
    pip install hy-memory[all]     # 包含所有可选依赖
"""

try:
    from importlib.metadata import version as _get_version
    __version__ = _get_version("hy-memory")
except Exception:
    __version__ = "0.1.0"

# ====== 用户级 API（推荐） ======
from .client import HyMemoryClient
from .inspector import MemoryInspector
from .runtime import SharedRuntime
from .snapshot import (
    DataPlaneSnapshot,
    DataPlaneSnapshotRejected,
    snapshot_local_data_plane,
)

# ====== 配置 ======
from .config import MemoryConfig

# ====== Embedding 端点覆盖（per-call，不改 model/dims，只换接入端点）======
from .core.embed_service import embed_endpoint_override

# ====== 高级 API（按需使用） ======
from .pipelines import (
    ComponentFactory,
    PipelineConfig,
    WritePipeline,
    ReadPipeline,
    ChatMessage,
    WriteRequest,
    WriteResponse,
    ReadRequest,
    ReadResponse,
)

# ====== 数据模型 ======
from .models import (
    MemoryLayer,
    MemoryNode,
    MemoryEntry,
    MemoryMetadata,
    MemoryScore,
    MemoryInputType,
    AgentProcessMode,
    TaskStatus,
    DeleteScope,
    QAPair,
    AddRequest,
    AddResponse,
    AsyncAddResponse,
    RecallRequest,
    RecallResponse,
    UpdateRequest,
    UpdateResponse,
    DeleteRequest,
    DeleteResponse,
    BatchDeleteRequest,
    BatchDeleteResponse,
    GetRequest,
    GetResponse,
    ListRequest,
    ListResponse,
)


__all__ = [
    "__version__",
    # 用户级 API
    "HyMemoryClient",
    "MemoryInspector",
    "SharedRuntime",
    "DataPlaneSnapshot",
    "DataPlaneSnapshotRejected",
    "snapshot_local_data_plane",
    # 配置
    "MemoryConfig",
    # Embedding 端点覆盖
    "embed_endpoint_override",
    # 高级 API
    "ComponentFactory",
    "PipelineConfig",
    "WritePipeline",
    "ReadPipeline",
    "ChatMessage",
    "WriteRequest",
    "WriteResponse",
    "ReadRequest",
    "ReadResponse",
    # 数据模型
    "MemoryLayer",
    "MemoryNode",
    "MemoryEntry",
    "MemoryMetadata",
    "MemoryScore",
    "MemoryInputType",
    "AgentProcessMode",
    "TaskStatus",
    "DeleteScope",
    "QAPair",
    "AddRequest",
    "AddResponse",
    "AsyncAddResponse",
    "RecallRequest",
    "RecallResponse",
    "UpdateRequest",
    "UpdateResponse",
    "DeleteRequest",
    "DeleteResponse",
    "BatchDeleteRequest",
    "BatchDeleteResponse",
    "GetRequest",
    "GetResponse",
    "ListRequest",
    "ListResponse",
]
