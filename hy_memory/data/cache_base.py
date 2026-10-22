"""
Agent Memory V2 - CacheBase

审计/观测落库层的抽象基类。
定义所有后端（SQLite / MySQL）必须实现的公共接口：
memory_operations（变动日志）、pipeline_logs（LLM 调用链）、system_metrics（指标）。
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any


class CacheBase(ABC):
    """
    审计/观测落库抽象基类。

    所有后端（SqliteCache / MysqlCache）均继承此类，
    上层代码只依赖 CacheBase 类型。
    """

    # ================================================================
    # 生命周期
    # ================================================================

    @abstractmethod
    async def initialize(self) -> None:
        """初始化后端连接/数据库。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接，释放资源。"""
        ...

    # ================================================================
    # 统计
    # ================================================================

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        ...

    # ================================================================
    # Memory Operations Log
    # ================================================================

    @abstractmethod
    async def store_memory_operation(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        op: str,
        memory_id: str,
        content: str,
        layer: str = "",
        old_memory_id: Optional[str] = None,
        reason: str = "",
        supersedes: Optional[List[str]] = None,
    ) -> bool:
        """记录一条知识库变动操作（ADD / EVOLVE）"""
        ...

    @abstractmethod
    async def get_memory_operations(
        self,
        request_id: Optional[str] = None,
        memory_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询知识库变动记录，支持按 request_id / memory_id / user_id 过滤"""
        ...

    # ================================================================
    # Pipeline Logs (LLM 调用链中间结果)
    # ================================================================

    @abstractmethod
    async def store_pipeline_log(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        step: str,
        prompt: str,
        response: str,
        parsed: str = "",
        memory_ids: Optional[List[str]] = None,
        elapsed_ms: float = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        session_id: str = "",
        event_at_ms: int = 0,
    ) -> bool:
        """记录一条 pipeline 中间结果（EXTRACT / SEARCH_QUERY / RECONCILE / SUMMARY）

        session_id 与 user_id/agent_id 同为隔离维度，由调用方显式传入。
        实现侧会把 user_id 按 "__" 拆出 namespace 单独落库。

        event_at_ms: step 发生时刻（epoch 毫秒，可读由消费方自行转换），由上层统一捕获，
        与 JSONL log 的 timestamp 同源。缺省（0）时实现侧回落到入库时刻。区别于 created_at（入库时刻）。"""
        ...

    @abstractmethod
    async def get_pipeline_logs(
        self,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        step: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询 pipeline 中间结果日志"""
        ...

    # ================================================================
    # Durable write outcomes
    # ================================================================

    @abstractmethod
    async def claim_write_outcome(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        operation: str,
    ) -> Optional[Dict[str, Any]]:
        """Claim one idempotent write; return the existing row if already claimed."""
        ...

    @abstractmethod
    async def finish_write_outcome(
        self,
        request_id: str,
        status: str,
        result: Dict[str, Any],
    ) -> bool:
        """Persist the terminal/unknown result for a claimed write."""
        ...

    @abstractmethod
    async def get_write_outcome(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Read one durable write outcome by exact request id."""
        ...

    # ================================================================
    # Summary Buffer（rolling summary 的未消费 turn 暂存）
    #
    # 默认 no-op，便于无需该能力的后端直接继承。
    # 持久化后端（sqlite/mysql）覆盖为真实实现。
    # ================================================================




    async def get_episode_state(self, episode_key: str) -> Optional[Dict[str, Any]]:
        """读取某 episode_key 的当前 episode 状态。默认返回 None（无状态 → 开新的）。"""
        return None

    async def upsert_episode_state(
        self,
        episode_key: str,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        episode_id: str,
        last_write_ts: float,
        memory_count: int,
    ) -> bool:
        """写入/更新 episode 状态。默认 no-op。"""
        return False
