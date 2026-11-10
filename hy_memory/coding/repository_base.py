"""Business storage contract; synchronous operations run in the SDK worker pool."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple

from .types import CodingMemory, WorkingFact


class MemoryRepository(ABC):
    """Authoritative experiences, facts, links and shared raw traces.

    Implementations must preserve input ordering, scope filters and idempotent
    trace insertion. They never create or query a vector store.
    """

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def list_by_workspace(
        self, user_id: str, workspace_id: str, branch: Optional[str],
        limit: int, offset: int,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List["CodingMemory"]:
        ...

    @abstractmethod
    def stats(
        self, user_id: str, workspace_id: Optional[str], branch: Optional[str],
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    def upsert_experience(self, memory: CodingMemory) -> None:
        ...

    @abstractmethod
    def delete_experience(self, experience_id: str, user_id: str) -> None:
        ...

    @abstractmethod
    def delete_fact(self, fact_id: str, user_id: str) -> None:
        ...

    @abstractmethod
    def get_experience(
        self, experience_id: str, user_id: Optional[str]
    ) -> Optional[CodingMemory]:
        ...

    @abstractmethod
    def get_experiences(
        self, experience_ids: List[str], user_id: Optional[str],
        include_sources: Optional[List[str]], exclude_sources: Optional[List[str]],
    ) -> List[CodingMemory]:
        ...

    @abstractmethod
    def scoped_experience_ids(
        self,
        user_id: str,
        agent_ids: Optional[List[str]],
        session_ids: Optional[List[str]],
        workspace_id: Optional[str],
        branch: Optional[str],
    ) -> Tuple[Set[str], int]:
        ...

    @abstractmethod
    def count_active_facts(
        self,
        user_id: str,
        agent_ids: Optional[List[str]],
        session_ids: Optional[List[str]],
        workspace_id: Optional[str],
        branch: Optional[str],
    ) -> Tuple[int, int]:
        ...

    @abstractmethod
    def upsert_fact(self, fact: WorkingFact) -> None:
        ...

    @abstractmethod
    def get_facts(
        self, fact_ids: List[str], user_id: Optional[str]
    ) -> List[WorkingFact]:
        ...

    @abstractmethod
    def get_fact_ancestors(
        self, fact_id: str, user_id: Optional[str], max_depth: int
    ) -> List[WorkingFact]:
        ...

    @abstractmethod
    def supersede_fact(self, old_fact_id: str, new_fact_id: str) -> bool:
        ...

    @abstractmethod
    def set_fact_derived_experiences(self, fact_id: str, experience_ids: List[str]) -> None:
        ...

    @abstractmethod
    def set_experience_source_facts(self, experience_id: str, fact_ids: List[str]) -> None:
        ...

    @abstractmethod
    def list_user_tasks(self, user_id: str, limit: int) -> List[str]:
        ...

    @abstractmethod
    def append_trace(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str],
        source: str,
        payload: List[Dict[str, Any]],
    ) -> None:
        ...

    @abstractmethod
    def recent_trace_payloads(
        self, user_id: str, agent_id: str, source: str, limit: int,
    ) -> List[List[Dict[str, Any]]]:
        ...
