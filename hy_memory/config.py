"""
Agent Memory - 配置管理

提供灵活的配置选项，支持环境变量和配置文件
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List
from pathlib import Path


def _load_dotenv():
    """加载 .env 文件（如果存在），向上逐级查找"""
    # 从当前文件向上逐级查找 .env
    cur = Path(__file__).resolve().parent
    env_path = None
    for _ in range(5):
        candidate = cur / ".env"
        if candidate.exists():
            env_path = candidate
            break
        cur = cur.parent
    if env_path is None:
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 不覆盖已存在的环境变量
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _get_env_float(key: str, default: float) -> float:
    """获取浮点型环境变量"""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_env_int(key: str, default: int) -> int:
    """获取整型环境变量"""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    """获取布尔型环境变量"""
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def _default_data_dir() -> str:
    """
    返回数据根目录。

    优先级:
      1. 环境变量 MEMORY_DATA_DIR（云端部署时指定挂载路径，如 /data/memory）
      2. ~/.hy_memory（本地 pip install 用户的默认路径）

    所有本地存储（向量数据库、图数据库、SQLite、日志）均基于此目录派生。
    当配置了远程服务（如 Qdrant host、Neo4j url）时，对应的本地路径不会被使用。
    """
    return os.getenv("MEMORY_DATA_DIR", os.path.join(Path.home(), ".hy_memory"))


@dataclass
class VectorStoreConfig:
    """向量存储配置"""

    provider: str = None  # qdrant, chroma, faiss
    collection_name: str = None  # 集合名称
    tag_index_collection_name: Optional[str] = None  # env: MEMORY_TAG_INDEX_COLLECTION_NAME
    persist_directory: str = None  # 持久化目录
    embedding_dims: int = None  # 向量维度
    on_disk: bool = True  # 是否持久化

    # 连接配置（远程存储）
    host: Optional[str] = None
    port: Optional[int] = None
    api_key: Optional[str] = None

    def __post_init__(self):
        if self.provider is None:
            self.provider = os.getenv("MEMORY_VECTOR_STORE", "chroma")
        if self.collection_name is None:
            self.collection_name = os.getenv("MEMORY_COLLECTION_NAME", "agent_memories")
        if self.tag_index_collection_name is None:
            self.tag_index_collection_name = os.getenv(
                "MEMORY_TAG_INDEX_COLLECTION_NAME"
            )
        if self.persist_directory is None:
            self.persist_directory = os.getenv(
                "MEMORY_PERSIST_DIR",
                os.path.join(_default_data_dir(), "data", "vector_db"),
            )
        if self.embedding_dims is None:
            self.embedding_dims = _get_env_int("MEMORY_EMBEDDING_DIMS", 1536)
        if self.host is None:
            self.host = os.getenv("MEMORY_VECTOR_HOST")
        if self.port is None:
            port_str = os.getenv("MEMORY_VECTOR_PORT")
            self.port = int(port_str) if port_str else None
        if self.api_key is None:
            self.api_key = os.getenv("MEMORY_VECTOR_API_KEY")


@dataclass
class LLMConfig:
    """LLM 配置（用于记忆提取和处理）"""

    # provider 目前统一使用 openai；第三方服务通过兼容的 base_url 接入。
    provider: str = None
    model: str = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    # 生成参数
    temperature: float = None
    # True = 请求里**完全不带 temperature**。
    # 有些网关（如 Opus 5）只接受 temperature=1 或干脆不传，传 0/0.1/0.3 会 400。
    # env: MEMORY_LLM_OMIT_TEMPERATURE=true，或 MEMORY_LLM_TEMPERATURE=none/omit
    omit_temperature: bool = None
    max_tokens: int = None  # 默认 max_tokens（通用）
    agent_max_tokens: int = None  # Agent 场景统一 max_tokens

    # 调用参数
    timeout: int = None  # LLM 请求超时（秒）
    max_retries: int = None  # 最大重试次数
    retry_delay: float = None  # 重试间隔（秒）

    # 功能开关
    few_shot_enabled: bool = None  # 是否在 extractor / reconcile prompt 中附加 few-shot 示例（默认关闭，env: MEMORY_FEW_SHOT_ENABLED）
    structure_metadata_enabled: bool = None  # env: MEMORY_STRUCTURE_METADATA_ENABLED

    # default 模式下，仅当调用方显式传 queries=[...] 时，此开关控制是否把旧的
    # 多-query 路由升级为“分类 + rewrite + 时间解析”。单 query 永远不受它影响。
    # search_mode="agentic" 则不看此开关，单/多 query 都执行 judge + rewrite。
    enable_query_rewrite: bool = None
    query_rewrite_max_chars: int = None  # queries 合并长度超过此值则跳过 rewrite（env MEMORY_QUERY_REWRITE_MAX_CHARS，默认 1000）

    # Intention（L7）抽取开关：关闭后 extractor prompt 不再要求 intentions，
    # writer 也不落 L7。未来计划若值得记，应进 memory（L2）。默认开。
    enable_intention: bool = None  # env MEMORY_ENABLE_INTENTION，默认 true

    # 自定义 extractor system prompt（client 级，仅通过 from_config/LLMConfig 配置，无 env）。
    # None = 用内置默认；非空则该语言 extractor 直接用此 system prompt（不再追加内置 few-shot）。
    # user 模板（数据注入段：content/last_messages/memory_at/current_date）仍用内置。
    extract_system_prompt_zh: Optional[str] = None  # 中文输入时的 extractor system prompt 覆盖
    extract_system_prompt_en: Optional[str] = None  # 英文输入时的 extractor system prompt 覆盖

    # OpenAI-compatible 扩展（extra_headers / extra_body 原样透传）
    extra_headers: Optional[Dict[str, str]] = None
    extra_body: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.provider is None:
            self.provider = os.getenv("MEMORY_LLM_PROVIDER", "openai")
        if self.model is None:
            self.model = os.getenv("MEMORY_LLM_MODEL", "gpt-4.1-nano")
        if self.api_key is None:
            self.api_key = os.getenv(
                "MEMORY_LLM_API_KEY",
                os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY")),
            )
        if self.base_url is None:
            self.base_url = os.getenv("MEMORY_LLM_BASE_URL", os.getenv("LLM_BASE_URL"))
        if self.temperature is None:
            # MEMORY_LLM_TEMPERATURE=none/omit/null/空 → 不发这个参数
            _raw_temp = os.getenv("MEMORY_LLM_TEMPERATURE")
            if _raw_temp is not None and _raw_temp.strip().lower() in (
                "", "none", "null", "omit", "unset",
            ):
                if self.omit_temperature is None:
                    self.omit_temperature = True
                self.temperature = 0.1   # 占位；omit_temperature 为真时不会被发送
            else:
                self.temperature = _get_env_float("MEMORY_LLM_TEMPERATURE", 0.1)
        if self.omit_temperature is None:
            self.omit_temperature = _get_env_bool("MEMORY_LLM_OMIT_TEMPERATURE", False)
        if self.max_tokens is None:
            self.max_tokens = _get_env_int("MEMORY_LLM_MAX_TOKENS", 1024)
        if self.agent_max_tokens is None:
            self.agent_max_tokens = _get_env_int("MEMORY_AGENT_MAX_TOKENS", 2000)
        if self.timeout is None:
            self.timeout = _get_env_int("MEMORY_LLM_TIMEOUT", 180)
        if self.max_retries is None:
            self.max_retries = _get_env_int("MEMORY_LLM_MAX_RETRIES", 5)
        if self.retry_delay is None:
            self.retry_delay = _get_env_float("MEMORY_LLM_RETRY_DELAY", 1.0)
        if self.few_shot_enabled is None:
            # 默认关闭 few-shot；开启后 extractor / reconcile prompt 会附加示例。
            self.few_shot_enabled = _get_env_bool("MEMORY_FEW_SHOT_ENABLED", False)
        if self.structure_metadata_enabled is None:
            self.structure_metadata_enabled = _get_env_bool(
                "MEMORY_STRUCTURE_METADATA_ENABLED", False
            )
        if self.enable_query_rewrite is None:
            self.enable_query_rewrite = _get_env_bool("MEMORY_ENABLE_QUERY_REWRITE", False)
        if self.query_rewrite_max_chars is None:
            self.query_rewrite_max_chars = _get_env_int("MEMORY_QUERY_REWRITE_MAX_CHARS", 1000)
        if self.enable_intention is None:
            self.enable_intention = _get_env_bool("MEMORY_ENABLE_INTENTION", True)
        # extra_headers / extra_body：透传给 OpenAI 兼容 API。
        # 若 env 未真正读取，会导致
        # MEMORY_LLM_EXTRA_BODY='{"reasoning_effort":"none"}' 这类「关思考」配置
        # 静默失效——推理模型的思维链照样吃掉 max_tokens。
        for _attr, _env in (
            ("extra_headers", "MEMORY_LLM_EXTRA_HEADERS"),
            ("extra_body", "MEMORY_LLM_EXTRA_BODY"),
        ):
            if getattr(self, _attr) is not None:
                continue
            _val = os.getenv(_env)
            if not _val:
                continue
            import json as _json

            try:
                _parsed = _json.loads(_val)
            except (ValueError, TypeError):
                _parsed = None
            # 非法 JSON 或非 dict 一律忽略（保持 None），不要让配置错误炸掉 client 构造
            if isinstance(_parsed, dict):
                setattr(self, _attr, _parsed)


@dataclass
class EmbedderConfig:
    """Embedder 配置（统一走 OpenAI 兼容协议）"""

    provider: str = None  # openai（兼容 DashScope、DeepSeek 等）
    model: str = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    embedding_dims: int = None

    # 重试参数
    max_retries: int = None  # 最大重试次数
    retry_delay: float = None  # 重试间隔（秒）
    timeout: int = None  # 请求超时（秒）

    # 内部平台扩展（extra_headers / extra_body 会透传给 OpenAI client）
    extra_headers: Optional[Dict[str, str]] = None
    extra_body: Optional[Dict[str, Any]] = None
    # Some compatible providers have a fixed output width but reject dimensions.
    send_dimensions: Optional[bool] = None

    def __post_init__(self):
        if self.provider is None:
            self.provider = os.getenv("MEMORY_EMBEDDER_PROVIDER", "openai")
        if self.model is None:
            self.model = os.getenv("MEMORY_EMBEDDER_MODEL", "text-embedding-3-small")
        if self.api_key is None:
            self.api_key = os.getenv(
                "MEMORY_EMBEDDER_API_KEY",
                os.getenv("EMBEDDING_API_KEY", os.getenv("OPENAI_API_KEY")),
            )
        if self.base_url is None:
            self.base_url = os.getenv(
                "MEMORY_EMBEDDER_BASE_URL", os.getenv("EMBEDDING_BASE_URL")
            )
        if self.embedding_dims is None:
            val = os.getenv("MEMORY_EMBEDDING_DIMS")
            self.embedding_dims = int(val) if val else None
        if self.send_dimensions is None:
            self.send_dimensions = _get_env_bool("MEMORY_EMBEDDER_SEND_DIMENSIONS", True)
        if self.max_retries is None:
            # 重试次数：仅对 timeout/连接错误重试，业务错立即抛。默认 2 次。
            # 总耗时上限 ≈ timeout × (max_retries+1) + retry_delay × max_retries
            #           = 3 × 3 + 0.3 × 2 ≈ 9.6s
            self.max_retries = _get_env_int("MEMORY_EMBEDDER_MAX_RETRIES", 2)
        if self.retry_delay is None:
            self.retry_delay = _get_env_float("MEMORY_EMBEDDER_RETRY_DELAY", 0.3)
        if self.timeout is None:
            # 单次 embed 请求超时。线上长跑样本 p50 已到约 8s；3s 会把本可成功的
            # 请求切断并制造重试风暴。SDK 内层重试已关闭，15s 是每个显式 attempt
            # 的真实上限；部署可通过 MEMORY_EMBEDDER_TIMEOUT 覆盖。
            self.timeout = _get_env_int("MEMORY_EMBEDDER_TIMEOUT", 15)
        if self.extra_headers is None:
            val = os.getenv("MEMORY_EMBEDDER_EXTRA_HEADERS")
            if val:
                import json as _json

                try:
                    self.extra_headers = _json.loads(val)
                except Exception:
                    pass
        if self.extra_body is None:
            val = os.getenv("MEMORY_EMBEDDER_EXTRA_BODY")
            if val:
                import json as _json

                try:
                    self.extra_body = _json.loads(val)
                except Exception:
                    pass


@dataclass
class RecallConfig:
    """召回配置"""

    # 默认召回数量
    default_limit: int = 10
    max_limit: int = 100
    default_limit_per_layer: int = 5

    # 评分权重
    semantic_weight: float = 0.5
    recency_weight: float = 0.3
    importance_weight: float = 0.15
    access_weight: float = 0.05

    # 时间衰减
    recency_decay_days: int = 30  # 衰减周期（天）
    recency_decay_factor: float = 0.9  # 衰减因子

    # Memory Strength（基于闲置时长的时间衰减排序）
    # strength = (1 + log(access_count)) * exp(-idle_days / tau)，乘进 normal 通道分数
    strength_enabled: bool = False        # 总开关（默认关闭）：关闭时不做 strength 重排，也不回写 access
    strength_tau: float = 180.0           # 衰减时间常数（天）
    access_tracking_enabled: bool = True  # 搜索后是否回写 access_count / last_accessed_at（受总开关约束）

    # Entity Store（独立 {collection}_entities collection）
    # 此开关**只控制写入侧**：开启后写入 L2_FACT 落库时自动抽 entity 刷 store。
    # 读侧共享流程可结合 graph entity 命中做候选加成；底层 entity collection 同时
    # 保留给写侧与迁移工具。
    entity_store_enabled: bool = False    # 写入侧开关（默认关闭），env: MEMORY_ENTITY_STORE_ENABLED

    # ── Episode（内部会话）──
    # 把跨 write 批次的"一件事"归到同一个 episode_id，读侧用它框出候选做加权。
    episode_gap_seconds: float = None   # env: MEMORY_EPISODE_GAP_SECONDS
    episode_max_memories: int = None    # env: MEMORY_EPISODE_MAX_MEMORIES
    episode_recall_enabled: bool = None # env: MEMORY_EPISODE_RECALL_ENABLED（读侧开关）
    episode_recall_weight: float = None # env: MEMORY_EPISODE_RECALL_WEIGHT
    # episode 关闭时总结一次（替代 rolling summary 的每 N 轮重写，省 token）
    episode_summary_enabled: bool = None      # env: MEMORY_EPISODE_SUMMARY_ENABLED
    episode_summary_min_memories: int = None  # env: MEMORY_EPISODE_SUMMARY_MIN_MEMORIES
    # 一等 session episode：由调用方显式 finalize，直接从该 session 的 L1_RAW
    # 生成完整叙事；读侧以 episode 为主，并附少量 atomic facts。
    session_episode_enabled: bool = None       # env: MEMORY_SESSION_EPISODE_ENABLED
    session_episode_context_enabled: bool = None  # env: MEMORY_SESSION_EPISODE_CONTEXT_ENABLED
    session_episode_limit: int = None          # env: MEMORY_SESSION_EPISODE_LIMIT
    session_episode_fact_limit: int = None     # env: MEMORY_SESSION_EPISODE_FACT_LIMIT
    dynamic_episode_enabled: bool = None       # env: MEMORY_DYNAMIC_EPISODE_ENABLED
    dynamic_episode_summary_concurrency: int = None  # env: MEMORY_DYNAMIC_EPISODE_SUMMARY_CONCURRENCY
    series_episode_enabled: bool = None        # env: MEMORY_SERIES_EPISODE_ENABLED
    episode_capsule_context_enabled: bool = None  # env: MEMORY_EPISODE_CAPSULE_CONTEXT_ENABLED
    episode_capsule_query_min: float = None    # env: MEMORY_EPISODE_CAPSULE_QUERY_MIN
    episode_capsule_assignment_min: float = None  # env: MEMORY_EPISODE_CAPSULE_ASSIGNMENT_MIN
    episode_capsule_first_rank_max: int = None # env: MEMORY_EPISODE_CAPSULE_FIRST_RANK_MAX
    episode_capsule_member_limit: int = None   # env: MEMORY_EPISODE_CAPSULE_MEMBER_LIMIT
    episode_capsule_render_mode: str = None    # env: MEMORY_EPISODE_CAPSULE_RENDER_MODE
    # Compact L3 is a retrieval-only container: retrieve its referenced L2
    # members into the local-rerank pool, but never return the L3 body itself.
    episode_member_expansion_enabled: bool = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_ENABLED
    episode_member_expansion_mode: str = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_MODE
    episode_member_expansion_episode_limit: int = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_EPISODE_LIMIT
    episode_member_expansion_member_limit: int = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_MEMBER_LIMIT
    episode_member_expansion_per_episode: int = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_PER_EPISODE
    episode_member_expansion_episode_min_similarity: float = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_EPISODE_MIN_SIMILARITY
    episode_member_expansion_min_similarity: float = None  # env: MEMORY_EPISODE_MEMBER_EXPANSION_MIN_SIMILARITY
    same_series_timeline_enabled: bool = None  # env: MEMORY_SAME_SERIES_TIMELINE_ENABLED
    local_rerank_enabled: bool = None          # env: MEMORY_LOCAL_RERANK_ENABLED
    local_rerank_model: str = None             # env: MEMORY_LOCAL_RERANK_MODEL
    local_rerank_pool_factor: int = None        # env: MEMORY_LOCAL_RERANK_POOL_FACTOR
    local_rerank_max_length: int = None         # env: MEMORY_LOCAL_RERANK_MAX_LENGTH
    local_rerank_batch_size: int = None         # env: MEMORY_LOCAL_RERANK_BATCH_SIZE
    local_rerank_device: str = None             # env: MEMORY_LOCAL_RERANK_DEVICE
    dynamic_entity_tag_embed_enabled: bool = None  # env: MEMORY_HYBRID_V2_DYNAMIC_ENTITY_TAG_EMBED_ENABLED
    tag_index_nominee_enabled: bool = None       # env: MEMORY_TAG_INDEX_NOMINEE_ENABLED
    tag_index_nominee_topk: int = None           # env: MEMORY_TAG_INDEX_NOMINEE_TOPK
    tag_index_nominee_min_score: float = None    # env: MEMORY_TAG_INDEX_NOMINEE_MIN_SCORE
    tag_index_nominee_min_matches: int = None    # env: MEMORY_TAG_INDEX_NOMINEE_MIN_MATCHES
    tag_index_nominee_search_limit: int = None   # env: MEMORY_TAG_INDEX_NOMINEE_SEARCH_LIMIT
    tag_index_nominee_limit: int = None          # env: MEMORY_TAG_INDEX_NOMINEE_LIMIT
    tag_index_nominee_weight: float = None       # env: MEMORY_TAG_INDEX_NOMINEE_WEIGHT
    tag_index_nominee_max_tag_df: int = None     # env: MEMORY_TAG_INDEX_NOMINEE_MAX_TAG_DF
    hierarchy_bundle_enabled: bool = None       # env: MEMORY_HIERARCHY_BUNDLE_ENABLED
    hierarchy_bundle_max_members: int = None    # env: MEMORY_HIERARCHY_BUNDLE_MAX_MEMBERS
    hierarchy_bundle_token_budget: int = None   # env: MEMORY_HIERARCHY_BUNDLE_TOKEN_BUDGET
    hierarchy_bundle_topic_limit: int = None    # env: MEMORY_HIERARCHY_BUNDLE_TOPIC_LIMIT
    hierarchy_bundle_node_beam: int = None      # env: MEMORY_HIERARCHY_BUNDLE_NODE_BEAM
    hierarchy_bundle_member_pool: int = None    # env: MEMORY_HIERARCHY_BUNDLE_MEMBER_POOL
    hierarchy_bundle_allowed_node_types: str = None  # env: MEMORY_HIERARCHY_BUNDLE_ALLOWED_NODE_TYPES
    hierarchy_bundle_novel_floor_margin: float = None  # env: MEMORY_HIERARCHY_BUNDLE_NOVEL_FLOOR_MARGIN
    multi_seed_bundle_enabled: bool = None     # env: MEMORY_MULTI_SEED_BUNDLE_ENABLED
    multi_seed_bundle_mode: str = None         # env: MEMORY_MULTI_SEED_BUNDLE_MODE
    multi_seed_bundle_alpha: float = None      # env: MEMORY_MULTI_SEED_BUNDLE_ALPHA
    multi_seed_bundle_beta: float = None       # env: MEMORY_MULTI_SEED_BUNDLE_BETA
    multi_seed_bundle_gamma: float = None      # env: MEMORY_MULTI_SEED_BUNDLE_GAMMA
    multi_seed_bundle_member_floor: float = None  # env: MEMORY_MULTI_SEED_BUNDLE_MEMBER_FLOOR
    multi_seed_bundle_group_floor: float = None   # env: MEMORY_MULTI_SEED_BUNDLE_GROUP_FLOOR
    multi_seed_bundle_tag_s1_floor: float = None  # env: MEMORY_MULTI_SEED_BUNDLE_TAG_S1_FLOOR
    multi_seed_bundle_tau: float = None        # env: MEMORY_MULTI_SEED_BUNDLE_TAU
    multi_seed_bundle_lexical_weight: float = None  # env: MEMORY_MULTI_SEED_BUNDLE_LEXICAL_WEIGHT
    multi_seed_bundle_max_tag_df: int = None   # env: MEMORY_MULTI_SEED_BUNDLE_MAX_TAG_DF
    multi_seed_bundle_scan_limit: int = None   # env: MEMORY_MULTI_SEED_BUNDLE_SCAN_LIMIT
    multi_seed_bundle_max_members: int = None  # env: MEMORY_MULTI_SEED_BUNDLE_MAX_MEMBERS
    multi_seed_bundle_max_bundles: int = None  # env: MEMORY_MULTI_SEED_BUNDLE_MAX_BUNDLES
    multi_seed_bundle_min_episode_seeds: int = None  # env: MEMORY_MULTI_SEED_BUNDLE_MIN_EPISODE_SEEDS
    multi_seed_bundle_single_tag_seed_ceiling: float = None  # env: MEMORY_MULTI_SEED_BUNDLE_SINGLE_TAG_SEED_CEILING
    multi_seed_bundle_token_budget: int = None  # env: MEMORY_MULTI_SEED_BUNDLE_TOKEN_BUDGET
    retrieval_clauses_enabled: bool = None       # env: MEMORY_RETRIEVAL_CLAUSES_ENABLED
    retrieval_clauses_min_words: int = None      # env: MEMORY_RETRIEVAL_CLAUSES_MIN_WORDS
    retrieval_clauses_min_score: float = None    # env: MEMORY_RETRIEVAL_CLAUSES_MIN_SCORE
    retrieval_clauses_margin: float = None       # env: MEMORY_RETRIEVAL_CLAUSES_MARGIN
    retrieval_clauses_cap: int = None            # env: MEMORY_RETRIEVAL_CLAUSES_CAP
    retrieval_clauses_require_keyword: bool = None  # env: MEMORY_RETRIEVAL_CLAUSES_REQUIRE_KEYWORD
    retrieval_clauses_min_keyword_score: float = None  # normalized BM25 floor
    supplemental_propositions_enabled: bool = None  # env: MEMORY_SUPPLEMENTAL_PROPOSITIONS_ENABLED

    # 最小分数阈值
    min_score_threshold: float = 0.0

    def __post_init__(self):
        self.default_limit = _get_env_int("MEMORY_RECALL_DEFAULT_LIMIT", 10)
        self.max_limit = _get_env_int("MEMORY_RECALL_MAX_LIMIT", 100)
        self.semantic_weight = _get_env_float("MEMORY_RECALL_SEMANTIC_WEIGHT", 0.5)
        self.recency_weight = _get_env_float("MEMORY_RECALL_RECENCY_WEIGHT", 0.3)
        self.importance_weight = _get_env_float("MEMORY_RECALL_IMPORTANCE_WEIGHT", 0.15)
        self.access_weight = _get_env_float("MEMORY_RECALL_ACCESS_WEIGHT", 0.05)
        self.recency_decay_days = _get_env_int("MEMORY_RECALL_DECAY_DAYS", 30)
        self.strength_enabled = _get_env_bool("MEMORY_RECALL_STRENGTH_ENABLED", False)
        self.strength_tau = _get_env_float("MEMORY_RECALL_STRENGTH_TAU", 180.0)
        self.access_tracking_enabled = _get_env_bool("MEMORY_RECALL_ACCESS_TRACKING", True)
        self.entity_store_enabled = _get_env_bool("MEMORY_ENTITY_STORE_ENABLED", False)
        if self.episode_gap_seconds is None:
            # 30 分钟：跨 write 但间隔不长 → 同一次讨论
            self.episode_gap_seconds = _get_env_float("MEMORY_EPISODE_GAP_SECONDS", 1800.0)
        if self.episode_max_memories is None:
            # 上限防止"一直开着的会话"变成一个巨型 episode
            self.episode_max_memories = _get_env_int("MEMORY_EPISODE_MAX_MEMORIES", 50)
        if self.episode_recall_enabled is None:
            self.episode_recall_enabled = _get_env_bool("MEMORY_EPISODE_RECALL_ENABLED", False)
        if self.episode_recall_weight is None:
            self.episode_recall_weight = _get_env_float("MEMORY_EPISODE_RECALL_WEIGHT", 0.7)
        if self.episode_summary_enabled is None:
            self.episode_summary_enabled = _get_env_bool("MEMORY_EPISODE_SUMMARY_ENABLED", False)
        if self.episode_summary_min_memories is None:
            self.episode_summary_min_memories = _get_env_int(
                "MEMORY_EPISODE_SUMMARY_MIN_MEMORIES", 3)
        if self.session_episode_enabled is None:
            self.session_episode_enabled = _get_env_bool(
                "MEMORY_SESSION_EPISODE_ENABLED", False
            )
        if self.session_episode_context_enabled is None:
            self.session_episode_context_enabled = _get_env_bool(
                "MEMORY_SESSION_EPISODE_CONTEXT_ENABLED", False
            )
        if self.session_episode_limit is None:
            self.session_episode_limit = _get_env_int(
                "MEMORY_SESSION_EPISODE_LIMIT", 5
            )
        if self.session_episode_fact_limit is None:
            self.session_episode_fact_limit = _get_env_int(
                "MEMORY_SESSION_EPISODE_FACT_LIMIT", 2
            )
        if self.dynamic_episode_enabled is None:
            self.dynamic_episode_enabled = _get_env_bool(
                "MEMORY_DYNAMIC_EPISODE_ENABLED", False
            )
        if self.dynamic_episode_summary_concurrency is None:
            self.dynamic_episode_summary_concurrency = max(1, min(16, _get_env_int(
                "MEMORY_DYNAMIC_EPISODE_SUMMARY_CONCURRENCY", 3
            )))
        if self.series_episode_enabled is None:
            self.series_episode_enabled = _get_env_bool(
                "MEMORY_SERIES_EPISODE_ENABLED", False
            )
        if self.episode_capsule_context_enabled is None:
            self.episode_capsule_context_enabled = _get_env_bool(
                "MEMORY_EPISODE_CAPSULE_CONTEXT_ENABLED", False
            )
        if self.episode_capsule_query_min is None:
            self.episode_capsule_query_min = _get_env_float(
                "MEMORY_EPISODE_CAPSULE_QUERY_MIN", 0.65
            )
        if self.episode_capsule_assignment_min is None:
            self.episode_capsule_assignment_min = _get_env_float(
                "MEMORY_EPISODE_CAPSULE_ASSIGNMENT_MIN", 0.60
            )
        if self.episode_capsule_first_rank_max is None:
            self.episode_capsule_first_rank_max = _get_env_int(
                "MEMORY_EPISODE_CAPSULE_FIRST_RANK_MAX", 3
            )
        if self.episode_capsule_member_limit is None:
            self.episode_capsule_member_limit = _get_env_int(
                "MEMORY_EPISODE_CAPSULE_MEMBER_LIMIT", 3
            )
        if self.episode_capsule_render_mode is None:
            self.episode_capsule_render_mode = os.getenv(
                "MEMORY_EPISODE_CAPSULE_RENDER_MODE", "full"
            ).strip().lower()
        if self.episode_capsule_render_mode not in {"full", "l2_visible"}:
            self.episode_capsule_render_mode = "full"
        if self.episode_member_expansion_enabled is None:
            self.episode_member_expansion_enabled = _get_env_bool(
                "MEMORY_EPISODE_MEMBER_EXPANSION_ENABLED", False
            )
        if self.episode_member_expansion_mode is None:
            self.episode_member_expansion_mode = os.getenv(
                "MEMORY_EPISODE_MEMBER_EXPANSION_MODE", "anchored_attach"
            ).strip().lower()
        if self.episode_member_expansion_mode not in {
            "anchored_attach", "anchored", "direct", "series_direct",
        }:
            self.episode_member_expansion_mode = "anchored_attach"
        if self.episode_member_expansion_episode_limit is None:
            self.episode_member_expansion_episode_limit = _get_env_int(
                "MEMORY_EPISODE_MEMBER_EXPANSION_EPISODE_LIMIT", 3
            )
        if self.episode_member_expansion_member_limit is None:
            self.episode_member_expansion_member_limit = _get_env_int(
                "MEMORY_EPISODE_MEMBER_EXPANSION_MEMBER_LIMIT", 6
            )
        if self.episode_member_expansion_per_episode is None:
            self.episode_member_expansion_per_episode = _get_env_int(
                "MEMORY_EPISODE_MEMBER_EXPANSION_PER_EPISODE", 3
            )
        if self.episode_member_expansion_episode_min_similarity is None:
            self.episode_member_expansion_episode_min_similarity = _get_env_float(
                "MEMORY_EPISODE_MEMBER_EXPANSION_EPISODE_MIN_SIMILARITY", 0.35
            )
        if self.episode_member_expansion_min_similarity is None:
            self.episode_member_expansion_min_similarity = _get_env_float(
                "MEMORY_EPISODE_MEMBER_EXPANSION_MIN_SIMILARITY", 0.20
            )
        if self.same_series_timeline_enabled is None:
            self.same_series_timeline_enabled = _get_env_bool(
                "MEMORY_SAME_SERIES_TIMELINE_ENABLED", False
            )
        if self.local_rerank_enabled is None:
            self.local_rerank_enabled = _get_env_bool(
                "MEMORY_LOCAL_RERANK_ENABLED", False
            )
        if self.local_rerank_model is None:
            self.local_rerank_model = os.getenv(
                "MEMORY_LOCAL_RERANK_MODEL",
                "nreimers/mmarco-mMiniLMv2-L6-H384-v1",
            )
        if self.local_rerank_pool_factor is None:
            self.local_rerank_pool_factor = _get_env_int(
                "MEMORY_LOCAL_RERANK_POOL_FACTOR", 3
            )
        if self.local_rerank_max_length is None:
            self.local_rerank_max_length = _get_env_int(
                "MEMORY_LOCAL_RERANK_MAX_LENGTH", 512
            )
        if self.local_rerank_batch_size is None:
            self.local_rerank_batch_size = _get_env_int(
                "MEMORY_LOCAL_RERANK_BATCH_SIZE", 16
            )
        if self.local_rerank_device is None:
            self.local_rerank_device = os.getenv(
                "MEMORY_LOCAL_RERANK_DEVICE", "auto"
            )
        if self.dynamic_entity_tag_embed_enabled is None:
            self.dynamic_entity_tag_embed_enabled = _get_env_bool(
                "MEMORY_HYBRID_V2_DYNAMIC_ENTITY_TAG_EMBED_ENABLED", False
            )
        if self.tag_index_nominee_enabled is None:
            self.tag_index_nominee_enabled = _get_env_bool(
                "MEMORY_TAG_INDEX_NOMINEE_ENABLED", False
            )
        if self.tag_index_nominee_topk is None:
            self.tag_index_nominee_topk = _get_env_int(
                "MEMORY_TAG_INDEX_NOMINEE_TOPK", 10
            )
        if self.tag_index_nominee_min_score is None:
            self.tag_index_nominee_min_score = _get_env_float(
                "MEMORY_TAG_INDEX_NOMINEE_MIN_SCORE", 0.4
            )
        if self.tag_index_nominee_min_matches is None:
            self.tag_index_nominee_min_matches = _get_env_int(
                "MEMORY_TAG_INDEX_NOMINEE_MIN_MATCHES", 2
            )
        if self.tag_index_nominee_search_limit is None:
            self.tag_index_nominee_search_limit = _get_env_int(
                "MEMORY_TAG_INDEX_NOMINEE_SEARCH_LIMIT", 40
            )
        if self.tag_index_nominee_limit is None:
            self.tag_index_nominee_limit = _get_env_int(
                "MEMORY_TAG_INDEX_NOMINEE_LIMIT", 1
            )
        if self.tag_index_nominee_weight is None:
            self.tag_index_nominee_weight = _get_env_float(
                "MEMORY_TAG_INDEX_NOMINEE_WEIGHT", 0.3
            )
        if self.tag_index_nominee_max_tag_df is None:
            self.tag_index_nominee_max_tag_df = _get_env_int(
                "MEMORY_TAG_INDEX_NOMINEE_MAX_TAG_DF", 8
            )
        if self.hierarchy_bundle_enabled is None:
            self.hierarchy_bundle_enabled = _get_env_bool(
                "MEMORY_HIERARCHY_BUNDLE_ENABLED", False
            )
        if self.hierarchy_bundle_max_members is None:
            self.hierarchy_bundle_max_members = _get_env_int(
                "MEMORY_HIERARCHY_BUNDLE_MAX_MEMBERS", 8
            )
        if self.hierarchy_bundle_token_budget is None:
            self.hierarchy_bundle_token_budget = _get_env_int(
                "MEMORY_HIERARCHY_BUNDLE_TOKEN_BUDGET", 400
            )
        if self.hierarchy_bundle_topic_limit is None:
            self.hierarchy_bundle_topic_limit = _get_env_int(
                "MEMORY_HIERARCHY_BUNDLE_TOPIC_LIMIT", 12
            )
        if self.hierarchy_bundle_node_beam is None:
            self.hierarchy_bundle_node_beam = _get_env_int(
                "MEMORY_HIERARCHY_BUNDLE_NODE_BEAM", 4
            )
        if self.hierarchy_bundle_member_pool is None:
            self.hierarchy_bundle_member_pool = _get_env_int(
                "MEMORY_HIERARCHY_BUNDLE_MEMBER_POOL", 48
            )
        if self.hierarchy_bundle_allowed_node_types is None:
            self.hierarchy_bundle_allowed_node_types = os.getenv(
                "MEMORY_HIERARCHY_BUNDLE_ALLOWED_NODE_TYPES", ""
            )
        if self.hierarchy_bundle_novel_floor_margin is None:
            raw_novel_floor_margin = os.getenv(
                "MEMORY_HIERARCHY_BUNDLE_NOVEL_FLOOR_MARGIN", ""
            ).strip()
            self.hierarchy_bundle_novel_floor_margin = (
                float(raw_novel_floor_margin)
                if raw_novel_floor_margin else None
            )
        if self.multi_seed_bundle_enabled is None:
            self.multi_seed_bundle_enabled = _get_env_bool(
                "MEMORY_MULTI_SEED_BUNDLE_ENABLED", False
            )
        if self.multi_seed_bundle_mode is None:
            self.multi_seed_bundle_mode = os.getenv(
                "MEMORY_MULTI_SEED_BUNDLE_MODE", "episode_single"
            )
        if self.multi_seed_bundle_alpha is None:
            self.multi_seed_bundle_alpha = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_ALPHA", 0.6
            )
        if self.multi_seed_bundle_beta is None:
            self.multi_seed_bundle_beta = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_BETA", 0.4
            )
        if self.multi_seed_bundle_gamma is None:
            self.multi_seed_bundle_gamma = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_GAMMA", 0.4
            )
        if self.multi_seed_bundle_member_floor is None:
            self.multi_seed_bundle_member_floor = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_MEMBER_FLOOR", 0.20
            )
        if self.multi_seed_bundle_group_floor is None:
            self.multi_seed_bundle_group_floor = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_GROUP_FLOOR", 0.35
            )
        if self.multi_seed_bundle_tag_s1_floor is None:
            self.multi_seed_bundle_tag_s1_floor = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_TAG_S1_FLOOR", 0.0
            )
        if self.multi_seed_bundle_tau is None:
            self.multi_seed_bundle_tau = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_TAU", 0.45
            )
        if self.multi_seed_bundle_lexical_weight is None:
            self.multi_seed_bundle_lexical_weight = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_LEXICAL_WEIGHT", 0.35
            )
        if self.multi_seed_bundle_max_tag_df is None:
            self.multi_seed_bundle_max_tag_df = _get_env_int(
                "MEMORY_MULTI_SEED_BUNDLE_MAX_TAG_DF", 8
            )
        if self.multi_seed_bundle_scan_limit is None:
            self.multi_seed_bundle_scan_limit = _get_env_int(
                "MEMORY_MULTI_SEED_BUNDLE_SCAN_LIMIT", 50
            )
        if self.multi_seed_bundle_max_members is None:
            self.multi_seed_bundle_max_members = _get_env_int(
                "MEMORY_MULTI_SEED_BUNDLE_MAX_MEMBERS", 8
            )
        if self.multi_seed_bundle_max_bundles is None:
            self.multi_seed_bundle_max_bundles = _get_env_int(
                "MEMORY_MULTI_SEED_BUNDLE_MAX_BUNDLES", 0
            )
        if self.multi_seed_bundle_min_episode_seeds is None:
            self.multi_seed_bundle_min_episode_seeds = _get_env_int(
                "MEMORY_MULTI_SEED_BUNDLE_MIN_EPISODE_SEEDS", 1
            )
        if self.multi_seed_bundle_single_tag_seed_ceiling is None:
            self.multi_seed_bundle_single_tag_seed_ceiling = _get_env_float(
                "MEMORY_MULTI_SEED_BUNDLE_SINGLE_TAG_SEED_CEILING", 1.01
            )
        if self.multi_seed_bundle_token_budget is None:
            self.multi_seed_bundle_token_budget = _get_env_int(
                "MEMORY_MULTI_SEED_BUNDLE_TOKEN_BUDGET", 400
            )
        if self.retrieval_clauses_enabled is None:
            self.retrieval_clauses_enabled = _get_env_bool(
                "MEMORY_RETRIEVAL_CLAUSES_ENABLED", False
            )
        if self.retrieval_clauses_min_words is None:
            self.retrieval_clauses_min_words = _get_env_int(
                "MEMORY_RETRIEVAL_CLAUSES_MIN_WORDS", 70
            )
        if self.retrieval_clauses_min_score is None:
            self.retrieval_clauses_min_score = _get_env_float(
                "MEMORY_RETRIEVAL_CLAUSES_MIN_SCORE", 0.50
            )
        if self.retrieval_clauses_margin is None:
            self.retrieval_clauses_margin = _get_env_float(
                "MEMORY_RETRIEVAL_CLAUSES_MARGIN", 0.07
            )
        if self.retrieval_clauses_cap is None:
            self.retrieval_clauses_cap = _get_env_int(
                "MEMORY_RETRIEVAL_CLAUSES_CAP", 3
            )
        if self.retrieval_clauses_require_keyword is None:
            self.retrieval_clauses_require_keyword = _get_env_bool(
                "MEMORY_RETRIEVAL_CLAUSES_REQUIRE_KEYWORD", True
            )
        if self.retrieval_clauses_min_keyword_score is None:
            self.retrieval_clauses_min_keyword_score = _get_env_float(
                "MEMORY_RETRIEVAL_CLAUSES_MIN_KEYWORD_SCORE", 0.10
            )
        if self.supplemental_propositions_enabled is None:
            self.supplemental_propositions_enabled = _get_env_bool(
                "MEMORY_SUPPLEMENTAL_PROPOSITIONS_ENABLED", False
            )


@dataclass
class TimeWindowConfig:
    """时间窗口配置"""

    # 滚动删除
    enable_rolling_delete: bool = False
    rolling_window_days: int = 90  # 滚动窗口（天）
    rolling_check_interval: int = 3600  # 检查间隔（秒）

    # 按层配置不同的窗口
    layer_windows: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.enable_rolling_delete = _get_env_bool("MEMORY_ROLLING_DELETE", False)
        self.rolling_window_days = _get_env_int("MEMORY_ROLLING_WINDOW_DAYS", 90)

        # 默认各层窗口
        if not self.layer_windows:
            self.layer_windows = {
                "raw": _get_env_int("MEMORY_RAW_WINDOW_DAYS", 30),
                "dialogue": _get_env_int("MEMORY_DIALOGUE_WINDOW_DAYS", 60),
                "summary": _get_env_int("MEMORY_SUMMARY_WINDOW_DAYS", 180),
                "profile": _get_env_int("MEMORY_PROFILE_WINDOW_DAYS", 365),
                "knowledge": _get_env_int("MEMORY_KNOWLEDGE_WINDOW_DAYS", 365),
            }


@dataclass
class ExtractorConfig:
    """记忆提取器配置"""

    # 自动路由
    enable_auto_routing: bool = True

    # 自动提取
    enable_auto_extract: bool = False
    extract_entities: bool = True  # 提取实体
    extract_summary: bool = True  # 提取摘要
    extract_profile: bool = True  # 提取画像

    # 提取阈值
    min_content_length: int = 10  # 最小内容长度
    summary_trigger_length: int = 500  # 触发摘要的长度

    # extract 时注入的历史上下文轮数（k 轮对话 = k*2 条 message）。
    # client 可覆盖；env: MEMORY_EXTRACT_HISTORY_TURNS
    history_turns: int = None

    def __post_init__(self):
        self.enable_auto_routing = _get_env_bool("MEMORY_AUTO_ROUTING", True)
        self.enable_auto_extract = _get_env_bool("MEMORY_AUTO_EXTRACT", False)
        self.min_content_length = _get_env_int("MEMORY_MIN_CONTENT_LENGTH", 10)
        if self.history_turns is None:
            self.history_turns = _get_env_int("MEMORY_EXTRACT_HISTORY_TURNS", 5)


@dataclass
class GraphStoreConfig:
    """图存储配置 (Kuzu / Neo4j / Memgraph)"""

    provider: str = None  # "kuzu" | "neo4j" | "memgraph"
    db_path: str = None  # Kuzu 数据库路径
    url: str = None  # Neo4j/Memgraph bolt URL
    username: str = None  # Neo4j/Memgraph 用户名
    password: str = None  # Neo4j/Memgraph 密码
    database: str = None  # Neo4j 数据库名

    # ── 记忆关系图（pro 模式）──
    # 写侧：把 L2_FACT 投影进图并建 RELATED_TO / TAGGED_WITH 边
    memory_graph_write: bool = None   # env: MEMORY_GRAPH_WRITE，默认 True（graph 开了才生效）
    # 显式 Subject→Topic→Subtopic 层级投影。默认关，建模验证后才接 Search。
    hierarchy_topic_write: bool = None  # env: MEMORY_HIERARCHY_TOPIC_WRITE
    # 读侧总开关：默认 **关**，需要灰度验证后再开
    graph_rerank_enabled: bool = None  # env: MEMORY_GRAPH_RERANK_ENABLED

    # 用法 A：池内支持度传播
    propagate_beta: float = None       # env: MEMORY_GRAPH_PROPAGATE_BETA
    propagate_iters: int = None        # env: MEMORY_GRAPH_PROPAGATE_ITERS
    support_cap: float = None          # env: MEMORY_GRAPH_SUPPORT_CAP
    # 用法 B：阈值松弛
    relax_gamma: float = None          # env: MEMORY_GRAPH_RELAX_GAMMA，0 表示关闭用法 B
    seed_floor: float = None           # env: MEMORY_GRAPH_SEED_FLOOR
    max_expand: int = None             # env: MEMORY_GRAPH_MAX_EXPAND
    # 用法 C：关系路径
    path_enabled: bool = None          # env: MEMORY_GRAPH_PATH_ENABLED
    path_high_floor: float = None      # env: MEMORY_GRAPH_PATH_HIGH_FLOOR
    # 写侧建边阈值
    edge_min_score: float = None       # env: MEMORY_GRAPH_EDGE_MIN_SCORE
    co_occurs_max_batch: int = None    # env: MEMORY_GRAPH_CO_OCCURS_MAX_BATCH

    # ── 阶段 5（均默认关，都要额外成本）──
    # 时序脊：同 Topic 内按 memory_at 串有向 TEMPORAL_NEXT 边（写侧，无 LLM）
    temporal_spine: bool = None        # env: MEMORY_GRAPH_TEMPORAL_SPINE
    # Topic-scoped summary：按主题切摘要而非按时间窗（写侧，**要 LLM**）
    topic_summary_enabled: bool = None      # env: MEMORY_TOPIC_SUMMARY_ENABLED
    topic_summary_min_memories: int = None  # env: MEMORY_TOPIC_SUMMARY_MIN_MEMORIES
    topic_summary_step: int = None          # env: MEMORY_TOPIC_SUMMARY_STEP
    topic_summary_concurrency: int = None   # env: MEMORY_TOPIC_SUMMARY_CONCURRENCY
    # 用法 B：扩展 + 重新打分（漏斗，见 _graph/rerank.py）
    expand_hops: int = None            # env: MEMORY_GRAPH_EXPAND_HOPS
    expand_prefilter_top_n: int = None # env: MEMORY_GRAPH_EXPAND_PREFILTER_TOP_N
    expand_boost_beta: float = None    # env: MEMORY_GRAPH_EXPAND_BOOST_BETA
    # 用法 B 硬闸：sim(cand) ≥ seed_sim − delta（替代旧 gamma 松弛门槛的准入判定）
    seed_sim_delta: float = None       # env: MEMORY_GRAPH_SEED_SIM_DELTA，默认 0.2
    # 通过闸的扩展是否按时间并进 seed（\\n\\n）；True 时不单独占 top-k
    expand_merge_into_seed: bool = None  # env: MEMORY_GRAPH_EXPAND_MERGE_SEED
    # 多通道打分权重（语义 / BM25 / tag∩keyword / 时序 / co_occur / 其它结构边）
    expand_w_sem: float = None         # env: MEMORY_GRAPH_EXPAND_W_SEM
    expand_w_bm25: float = None        # env: MEMORY_GRAPH_EXPAND_W_BM25
    expand_w_tag: float = None         # env: MEMORY_GRAPH_EXPAND_W_TAG（建议最高）
    expand_w_temporal: float = None    # env: MEMORY_GRAPH_EXPAND_W_TEMPORAL
    expand_w_co_occur: float = None    # env: MEMORY_GRAPH_EXPAND_W_CO_OCCUR（建议偏低）
    expand_w_struct: float = None      # env: MEMORY_GRAPH_EXPAND_W_STRUCT（typed 边）
    # episode 支持度叠加到图边支持度上的折扣系数（有界叠加，非取 max）
    episode_blend_lambda: float = None  # env: MEMORY_GRAPH_EPISODE_BLEND_LAMBDA
    multi_seed_bonus: float = None     # env: MEMORY_GRAPH_MULTI_SEED_BONUS
    temporal_tau_days: float = None    # env: MEMORY_GRAPH_TEMPORAL_TAU_DAYS
    temporal_affinity_floor: float = None  # env: MEMORY_GRAPH_TEMPORAL_AFFINITY_FLOOR

    def __post_init__(self):
        if self.provider is None:
            self.provider = os.getenv("MEMORY_GRAPH_PROVIDER", "kuzu")
        if self.db_path is None:
            self.db_path = os.getenv(
                "MEMORY_GRAPH_DB_PATH",
                os.path.join(_default_data_dir(), "data", "kuzu_db"),
            )
        if self.url is None:
            self.url = os.getenv(
                "NEO4J_URL", os.getenv("MEMGRAPH_URL", "bolt://localhost:7687")
            )
        if self.username is None:
            self.username = os.getenv(
                "NEO4J_USERNAME", os.getenv("MEMGRAPH_USERNAME", "")
            )
        if self.password is None:
            self.password = os.getenv(
                "NEO4J_PASSWORD", os.getenv("MEMGRAPH_PASSWORD", "")
            )
        if self.database is None:
            self.database = os.getenv("NEO4J_DATABASE", "neo4j")

        # ── 记忆关系图参数（默认值见 docs/pro_graph_design.md）──
        if self.memory_graph_write is None:
            self.memory_graph_write = _get_env_bool("MEMORY_GRAPH_WRITE", True)
        if self.hierarchy_topic_write is None:
            self.hierarchy_topic_write = _get_env_bool(
                "MEMORY_HIERARCHY_TOPIC_WRITE", False
            )
        if self.graph_rerank_enabled is None:
            # 读侧默认关：图为空/稀疏时接进来没有收益，要先灰度验证（含 null test）
            self.graph_rerank_enabled = _get_env_bool("MEMORY_GRAPH_RERANK_ENABLED", False)
        if self.propagate_beta is None:
            self.propagate_beta = _get_env_float("MEMORY_GRAPH_PROPAGATE_BETA", 0.15)
        if self.propagate_iters is None:
            self.propagate_iters = _get_env_int("MEMORY_GRAPH_PROPAGATE_ITERS", 1)
        if self.support_cap is None:
            self.support_cap = _get_env_float("MEMORY_GRAPH_SUPPORT_CAP", 1.0)
        if self.relax_gamma is None:
            self.relax_gamma = _get_env_float("MEMORY_GRAPH_RELAX_GAMMA", 0.3)
        if self.seed_floor is None:
            # 0.5 而非 0.6：实测 15 个真实召回池，向量最高分中位数才 0.636，
            # 0.6 时"可当 seed 的命中"中位数是 **0 个** → 扩展根本不触发，
            # 多 seed 佐证从未 engage。0.5 时中位 18 个可达候选、80% 查询有 >=2 seed。
            self.seed_floor = _get_env_float("MEMORY_GRAPH_SEED_FLOOR", 0.5)
        if self.max_expand is None:
            self.max_expand = _get_env_int("MEMORY_GRAPH_MAX_EXPAND", 20)
        if self.path_enabled is None:
            self.path_enabled = _get_env_bool("MEMORY_GRAPH_PATH_ENABLED", True)
        if self.path_high_floor is None:
            # 同 seed_floor 重标定。路径要求**两端**都过线，0.6 时典型只有 top-1
            # 能过线、凑不出两个 → 实测 262 次检索产出 0 条路径。
            self.path_high_floor = _get_env_float("MEMORY_GRAPH_PATH_HIGH_FLOOR", 0.5)
        if self.edge_min_score is None:
            self.edge_min_score = _get_env_float("MEMORY_GRAPH_EDGE_MIN_SCORE", 0.55)
        if self.co_occurs_max_batch is None:
            self.co_occurs_max_batch = _get_env_int("MEMORY_GRAPH_CO_OCCURS_MAX_BATCH", 8)

        # 阶段 5：都要额外成本（LLM 或额外检索），一律默认关
        if self.temporal_spine is None:
            self.temporal_spine = _get_env_bool("MEMORY_GRAPH_TEMPORAL_SPINE", False)
        if self.topic_summary_enabled is None:
            self.topic_summary_enabled = _get_env_bool("MEMORY_TOPIC_SUMMARY_ENABLED", False)
        if self.topic_summary_min_memories is None:
            self.topic_summary_min_memories = _get_env_int(
                "MEMORY_TOPIC_SUMMARY_MIN_MEMORIES", 5
            )
        if self.topic_summary_step is None:
            self.topic_summary_step = _get_env_int("MEMORY_TOPIC_SUMMARY_STEP", 5)
        if self.topic_summary_concurrency is None:
            self.topic_summary_concurrency = max(1, min(16, _get_env_int(
                "MEMORY_TOPIC_SUMMARY_CONCURRENCY", 3
            )))
        if self.expand_hops is None:
            self.expand_hops = _get_env_int("MEMORY_GRAPH_EXPAND_HOPS", 2)
        if self.expand_prefilter_top_n is None:
            self.expand_prefilter_top_n = _get_env_int(
                "MEMORY_GRAPH_EXPAND_PREFILTER_TOP_N", 50
            )
        if self.expand_boost_beta is None:
            self.expand_boost_beta = _get_env_float("MEMORY_GRAPH_EXPAND_BOOST_BETA", 0.1)
        if self.seed_sim_delta is None:
            self.seed_sim_delta = _get_env_float("MEMORY_GRAPH_SEED_SIM_DELTA", 0.2)
        if self.expand_merge_into_seed is None:
            self.expand_merge_into_seed = _get_env_bool(
                "MEMORY_GRAPH_EXPAND_MERGE_SEED", True
            )
        if self.expand_w_sem is None:
            self.expand_w_sem = _get_env_float("MEMORY_GRAPH_EXPAND_W_SEM", 1.0)
        if self.expand_w_bm25 is None:
            self.expand_w_bm25 = _get_env_float("MEMORY_GRAPH_EXPAND_W_BM25", 0.3)
        if self.expand_w_tag is None:
            self.expand_w_tag = _get_env_float("MEMORY_GRAPH_EXPAND_W_TAG", 0.5)
        if self.expand_w_temporal is None:
            self.expand_w_temporal = _get_env_float(
                "MEMORY_GRAPH_EXPAND_W_TEMPORAL", 0.2
            )
        if self.expand_w_co_occur is None:
            self.expand_w_co_occur = _get_env_float(
                "MEMORY_GRAPH_EXPAND_W_CO_OCCUR", 0.15
            )
        if self.expand_w_struct is None:
            self.expand_w_struct = _get_env_float("MEMORY_GRAPH_EXPAND_W_STRUCT", 0.25)
        if self.episode_blend_lambda is None:
            self.episode_blend_lambda = _get_env_float(
                "MEMORY_GRAPH_EPISODE_BLEND_LAMBDA", 0.3)
        if self.multi_seed_bonus is None:
            self.multi_seed_bonus = _get_env_float("MEMORY_GRAPH_MULTI_SEED_BONUS", 0.5)
        if self.temporal_tau_days is None:
            self.temporal_tau_days = _get_env_float("MEMORY_GRAPH_TEMPORAL_TAU_DAYS", 7.0)
        if self.temporal_affinity_floor is None:
            self.temporal_affinity_floor = _get_env_float(
                "MEMORY_GRAPH_TEMPORAL_AFFINITY_FLOOR", 0.3
            )


@dataclass
class CacheConfig:
    """
    缓存后端配置

    支持的后端:
    - "sqlite"   : SQLite 本地数据库（默认，零依赖）
    - "mysql"    : 标准 MySQL 持久化（需 aiomysql 包）

    细粒度控制：可以启用/禁用特定的缓存功能
    - enable_profile_cache   : Profile 热点缓存（读优化）
    - enable_system2_queue   : System2 任务队列（后台任务）
    - enable_intention_cache : Intention 队列（触发器）
    - enable_write_records   : Write record（状态查询）
    """

    backend: str = None  # "sqlite" | "mysql"
    db_path: str = None  # SQLite 路径

    # MySQL 连接参数（backend="mysql" 时使用）
    mysql_host: str = None
    mysql_port: int = None
    mysql_user: str = None
    mysql_password: str = None
    mysql_database: str = None
    mysql_pool_size: int = None
    mysql_pool_recycle: int = None

    # 细粒度缓存控制（所有后端均支持）
    enable_profile_cache: bool = None  # Profile 缓存
    enable_system2_queue: bool = None  # System2 任务队列
    enable_intention_cache: bool = None  # Intention 队列
    enable_write_records: bool = None  # Write 记录

    def __post_init__(self):
        if self.backend is None:
            self.backend = os.getenv("MEMORY_CACHE_BACKEND", "sqlite")
        backend_norm = str(self.backend).lower().strip()
        if backend_norm not in ("sqlite", "mysql"):
            raise ValueError(
                f"Unsupported cache backend {self.backend!r}; "
                f"must be one of: sqlite / mysql"
            )
        self.backend = backend_norm
        if self.db_path is None:
            self.db_path = os.getenv(
                "MEMORY_CACHE_DB_PATH",
                os.path.join(_default_data_dir(), "data", "cache.db"),
            )

        # MySQL 连接参数
        if self.mysql_host is None:
            self.mysql_host = os.getenv("MEMORY_MYSQL_HOST", "localhost")
        if self.mysql_port is None:
            self.mysql_port = _get_env_int("MEMORY_MYSQL_PORT", 3306)
        if self.mysql_user is None:
            self.mysql_user = os.getenv("MEMORY_MYSQL_USER", "root")
        if self.mysql_password is None:
            self.mysql_password = os.getenv("MEMORY_MYSQL_PASSWORD", "")
        if self.mysql_database is None:
            self.mysql_database = os.getenv("MEMORY_MYSQL_DATABASE", "hy_memory")
        if self.mysql_pool_size is None:
            self.mysql_pool_size = _get_env_int("MEMORY_MYSQL_POOL_SIZE", 10)
        if self.mysql_pool_recycle is None:
            self.mysql_pool_recycle = _get_env_int("MEMORY_MYSQL_POOL_RECYCLE", 3600)

        # 细粒度缓存控制 - 默认均为 True（启用所有功能）
        if self.enable_profile_cache is None:
            self.enable_profile_cache = _get_env_bool(
                "MEMORY_CACHE_ENABLE_PROFILE", True
            )
        if self.enable_system2_queue is None:
            self.enable_system2_queue = _get_env_bool(
                "MEMORY_CACHE_ENABLE_SYSTEM2_QUEUE", True
            )
        if self.enable_intention_cache is None:
            self.enable_intention_cache = _get_env_bool(
                "MEMORY_CACHE_ENABLE_INTENTION_CACHE", True
            )
        if self.enable_write_records is None:
            self.enable_write_records = _get_env_bool(
                "MEMORY_CACHE_ENABLE_WRITE_RECORDS", True
            )


@dataclass
class APIConfig:
    """API 配置"""

    host: str = "0.0.0.0"
    port: int = 8000
    prefix: str = "/api/v1/memory"

    # 认证
    enable_auth: bool = False
    api_keys: List[str] = field(default_factory=list)

    # 限流
    enable_rate_limit: bool = False
    rate_limit_per_minute: int = 100

    # CORS
    enable_cors: bool = True
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

    def __post_init__(self):
        self.host = os.getenv("MEMORY_API_HOST", "0.0.0.0")
        self.port = _get_env_int("MEMORY_API_PORT", 8000)
        self.prefix = os.getenv("MEMORY_API_PREFIX", "/api/v1/memory")
        self.enable_auth = _get_env_bool("MEMORY_API_AUTH", False)

        # API Keys
        api_keys_str = os.getenv("MEMORY_API_KEYS", "")
        if api_keys_str:
            self.api_keys = [k.strip() for k in api_keys_str.split(",")]


@dataclass
class HistoryConfig:
    """历史记录配置（SQLite 审计追踪）"""

    enable: bool = None  # 是否启用历史记录
    db_path: str = None  # SQLite 数据库路径
    record_searches: bool = None  # 是否记录搜索操作

    def __post_init__(self):
        if self.enable is None:
            self.enable = _get_env_bool("MEMORY_HISTORY_ENABLE", True)
        if self.db_path is None:
            self.db_path = os.getenv(
                "MEMORY_HISTORY_DB_PATH",
                os.path.join(_default_data_dir(), "data", "history.db"),
            )
        if self.record_searches is None:
            self.record_searches = _get_env_bool(
                "MEMORY_HISTORY_RECORD_SEARCHES", False
            )


@dataclass
class CodingConfig:
    """
    Coding Memory 配置（生产力 / 编码场景独立链路）

    对外使用方式见 docs/scene_coding_usage.md。
    """

    enable: bool = None  # 是否启用 coding 链路（默认 True；关闭后所有 add 走 chat 链）
    db_path: str = None  # coding_memory.db 路径
    tool_result_max_bytes: int = None  # 单条 tool_result 截断字节上限
    # LLM 失败或没有抽出 fact 时，是否把对话原文存成 working fact。
    # 默认关闭，避免 assistant/tool 轨迹以 statement 形式直接暴露给消费方。
    verbatim_fallback_enabled: bool = None
    # search(scene="coding") 是否注入版本演进链块（diff / chain_block）。
    # 默认关闭（opt-in）：中重度 coding 用户可省下大量 prompt token；写入侧仍可 observe。
    chain_block_enabled: bool = None

    def __post_init__(self):
        if self.enable is None:
            self.enable = _get_env_bool("MEMORY_CODING_ENABLED", True)
        if self.db_path is None:
            self.db_path = os.getenv(
                "MEMORY_CODING_DB_PATH",
                os.path.join(_default_data_dir(), "data", "coding_memory.db"),
            )
        if self.tool_result_max_bytes is None:
            self.tool_result_max_bytes = _get_env_int(
                "MEMORY_CODING_TOOL_RESULT_MAX_BYTES", 2048
            )
        if self.verbatim_fallback_enabled is None:
            self.verbatim_fallback_enabled = _get_env_bool(
                "MEMORY_CODING_VERBATIM_FALLBACK_ENABLED", False
            )
        if self.chain_block_enabled is None:
            self.chain_block_enabled = _get_env_bool(
                "MEMORY_CODING_CHAIN_BLOCK_ENABLED", False
            )


@dataclass
class BasicProfileConfig:
    """
    用户基础画像（L0_BASIC_INFO）的字段 schema 配置。

    设计原则：
    - 不再使用 LLM function-calling tool（会让弱模型乱填字段）。
    - extractor 直接把 fields 字段表 format 到 EXTRACT prompt 里，要求 LLM
      在 JSON 输出中以 `basic_info` 字段返回（仅当对话明确出现时才填）。
    - 不同 client 可以配置不同的 schema（例如车机场景关注 vehicle_model，
      医疗场景关注 medical_history），SDK 默认提供 5 个通用字段。
    - 如果配置为空（fields == {}），SDK 回退到 default_factory 给出的默认 5 字段。

    用法：
        config.basic_profile.fields["hobby"] = "User's main hobby."
        # 或整体替换
        config.basic_profile.fields = {
            "name": "User's full or preferred name.",
            "vehicle_model": "User's primary vehicle model.",
        }
    """

    fields: Dict[str, str] = field(
        default_factory=lambda: {
            "name": "User's full or preferred name.",
            "age": "User's age in years (integer).",
            "location": "User's primary city or region of residence.",
            "occupation": "User's job title or role.",
            "employer": "User's employer / company name.",
        }
    )

    def effective_fields(self) -> Dict[str, str]:
        """配置为空时回落到默认；否则用配置值。"""
        if not self.fields:
            return BasicProfileConfig().fields
        return dict(self.fields)


@dataclass
class PipelineRouteConfig:
    """
    Pipeline 路由配置

    控制不同业务方/请求使用哪个版本的 Pipeline (Lite, Pro, ...)。
    """

    # 默认 Pipeline 版本
    default_version: str = None

    # 业务方 → Pipeline 版本映射
    # 例: {"business_a": "pro", "experiment_group": "pro"}
    business_version_map: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.default_version is None:
            self.default_version = os.getenv("MEMORY_PIPELINE_DEFAULT_VERSION", "lite")

        # 从环境变量加载业务方映射
        # 格式: MEMORY_PIPELINE_BUSINESS_MAP="business_a:pro,business_b:lite"
        map_str = os.getenv("MEMORY_PIPELINE_BUSINESS_MAP", "")
        if map_str and not self.business_version_map:
            for pair in map_str.split(","):
                pair = pair.strip()
                if ":" in pair:
                    biz, ver = pair.split(":", 1)
                    self.business_version_map[biz.strip()] = ver.strip()


@dataclass
class MemoryConfig:
    """
    Agent Memory 主配置

    整合所有子配置，提供统一的配置入口
    """

    # 子配置
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    graph_store: GraphStoreConfig = field(default_factory=GraphStoreConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)
    recall: RecallConfig = field(default_factory=RecallConfig)
    time_window: TimeWindowConfig = field(default_factory=TimeWindowConfig)
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
    api: APIConfig = field(default_factory=APIConfig)
    pipeline: PipelineRouteConfig = field(default_factory=PipelineRouteConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    coding: CodingConfig = field(default_factory=CodingConfig)
    basic_profile: BasicProfileConfig = field(default_factory=BasicProfileConfig)

    # 全局配置
    enable_graph: bool = False
    enable_agent: bool = False
    debug: bool = False
    metrics_enabled: bool = True
    # Runtime-only HTTP transport factory. MemHub uses this for request-cost
    # budgeting without restricting the embedded SDK's local store access.
    http_transport_factory: Optional[Callable[[], Any]] = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        # enable_graph 跟随 mode 走：mode=ultra 时自动启用，无需独立环境变量
        # 保留 from_dict() 可显式覆盖
        self.enable_agent = _get_env_bool("MEMORY_ENABLE_AGENT", False)
        self.debug = _get_env_bool("MEMORY_DEBUG", False)
        self.metrics_enabled = _get_env_bool("MEMORY_METRICS", True)

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        """从环境变量创建配置"""
        return cls()

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "MemoryConfig":
        """从字典创建配置"""
        config = cls()

        _sections = [
            "vector_store",
            "graph_store",
            "cache",
            "llm",
            "embedder",
            "recall",
            "time_window",
            "extractor",
            "api",
            "pipeline",
            "history",
            "coding",
        ]
        for section in _sections:
            if section in config_dict:
                sub = getattr(config, section)
                for key, value in config_dict[section].items():
                    if hasattr(sub, key):
                        setattr(sub, key, value)

        # basic_profile: fields 是 Dict[str, str]，整体替换（非空时）
        if "basic_profile" in config_dict:
            bp = config_dict["basic_profile"] or {}
            fields = bp.get("fields")
            if isinstance(fields, dict) and fields:
                # 强制 str→str；过滤掉空 description
                config.basic_profile.fields = {
                    str(k): str(v).strip()
                    for k, v in fields.items()
                    if str(k).strip() and str(v).strip()
                }

        config.enable_graph = config_dict.get("enable_graph", config.enable_graph)
        config.enable_agent = config_dict.get("enable_agent", config.enable_agent)
        config.debug = config_dict.get("debug", config.debug)
        config.metrics_enabled = config_dict.get(
            "metrics_enabled", config.metrics_enabled
        )

        return config

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "vector_store": {
                "provider": self.vector_store.provider,
                "collection_name": self.vector_store.collection_name,
                "persist_directory": self.vector_store.persist_directory,
                "embedding_dims": self.vector_store.embedding_dims,
            },
            "graph_store": {
                "provider": self.graph_store.provider,
                "db_path": self.graph_store.db_path,
                "url": self.graph_store.url,
                "database": self.graph_store.database,
            },
            "cache": {
                "backend": self.cache.backend,
                "db_path": self.cache.db_path,
            },
            "llm": {
                "provider": self.llm.provider,
                "model": self.llm.model,
                "base_url": self.llm.base_url,
            },
            "embedder": {
                "provider": self.embedder.provider,
                "model": self.embedder.model,
                "base_url": self.embedder.base_url,
                "embedding_dims": self.embedder.embedding_dims,
                "send_dimensions": self.embedder.send_dimensions,
            },
            "recall": {
                "default_limit": self.recall.default_limit,
                "semantic_weight": self.recall.semantic_weight,
                "recency_weight": self.recall.recency_weight,
                "importance_weight": self.recall.importance_weight,
                "supplemental_propositions_enabled": (
                    self.recall.supplemental_propositions_enabled
                ),
            },
            "pipeline": {
                "default_version": self.pipeline.default_version,
                "business_version_map": self.pipeline.business_version_map,
            },
            "history": {
                "enable": self.history.enable,
                "db_path": self.history.db_path,
                "record_searches": self.history.record_searches,
            },
            "basic_profile": {
                "fields": dict(self.basic_profile.fields),
            },
            "debug": self.debug,
            "enable_graph": self.enable_graph,
            "enable_agent": self.enable_agent,
            "metrics_enabled": self.metrics_enabled,
        }

    def print_config(self) -> None:
        """打印配置信息"""
        print("=" * 60)
        print("Agent Memory Configuration")
        print("=" * 60)
        print("[Vector Store]")
        print(f"  Provider: {self.vector_store.provider}")
        print(f"  Collection: {self.vector_store.collection_name}")
        print(f"  Persist Dir: {self.vector_store.persist_directory}")
        print(f"  Embedding Dims: {self.vector_store.embedding_dims}")
        print("[Graph Store]")
        print(f"  Provider: {self.graph_store.provider}")
        if self.graph_store.provider == "neo4j":
            print(f"  URL: {self.graph_store.url}")
            print(f"  Database: {self.graph_store.database}")
        else:
            print(f"  DB Path: {self.graph_store.db_path}")
        print("[Cache]")
        print(f"  Backend: {self.cache.backend}")
        if self.cache.backend == "sqlite":
            print(f"  DB Path: {self.cache.db_path}")
        else:
            print(f"  MySQL: {self.cache.mysql_host}:{self.cache.mysql_port}/{self.cache.mysql_database}")
        print("[LLM]")
        print(f"  Provider: {self.llm.provider}")
        print(f"  Model: {self.llm.model}")
        print(f"  Base URL: {self.llm.base_url}")
        print("[Embedder]")
        print(f"  Provider: {self.embedder.provider}")
        print(f"  Model: {self.embedder.model}")
        print(f"  Base URL: {self.embedder.base_url}")
        print("[Recall]")
        print(f"  Default Limit: {self.recall.default_limit}")
        print(
            f"  Weights: semantic={self.recall.semantic_weight}, "
            f"recency={self.recall.recency_weight}, "
            f"importance={self.recall.importance_weight}"
        )
        print("[Pipeline]")
        print(f"  Default Version: {self.pipeline.default_version}")
        print(f"  Business Map: {self.pipeline.business_version_map}")
        print("[API]")
        print(f"  Prefix: {self.api.prefix}")
        print(f"  Auth: {self.api.enable_auth}")
        print("[History]")
        print(f"  Enable: {self.history.enable}")
        print(f"  DB Path: {self.history.db_path}")
        print(f"  Record Searches: {self.history.record_searches}")
        print("=" * 60)
