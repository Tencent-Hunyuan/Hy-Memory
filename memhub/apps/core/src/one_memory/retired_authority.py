"""Fail-closed facades used after legacy Evidence/Governance tables are gone."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any, Never


class RetiredAuthorityAccess(RuntimeError):
    pass


class RetiredEvidenceStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def ensure_imported_index_in_background(self) -> None:
        return None

    def release_completed_sdk_staging(self, *, limit: int = 2_048) -> int:
        del limit
        return 0

    def release_sdk_staging(self, evidence_ids: tuple[str, ...]) -> int:
        # The write drain releases staged Evidence after every SDK write. On
        # SDK authority nothing is staged; without this, `__getattr__` raised
        # past `getattr(..., None)` and every completed write logged an error.
        del evidence_ids
        return 0

    def get(self, _identifier: str) -> None:
        return None

    def known_workspaces(self, *, user_id: str) -> tuple[str, ...]:
        del user_id
        return ()

    def high_water_mark(self) -> int:
        return 0

    def source_totals_by_source(self, *, user_id: str) -> dict[str, tuple[int, int]]:
        del user_id
        return {}

    def __getattr__(self, name: str) -> Never:
        raise RetiredAuthorityAccess(f"evidence_authority_retired:{name}")


class RetiredPersonWrites:
    def __getattr__(self, name: str) -> Never:
        raise RetiredAuthorityAccess(f"governance_person_writes_retired:{name}")


class RetiredGovernanceStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def claim_person_writes(self, _owner: str) -> RetiredPersonWrites:
        return RetiredPersonWrites()

    def release_content_backfill_startup_gate(self) -> None:
        return None

    def content_candidate_backfill_state(self) -> str:
        return "idle"

    def content_candidate_backfill_failure(self) -> None:
        return None

    def get(self, *_args: Any, **_kwargs: Any) -> Never:
        raise RetiredAuthorityAccess("governance_authority_retired:get")

    def list(self, *_args: Any, **_kwargs: Any) -> Never:
        raise RetiredAuthorityAccess("governance_authority_retired:list")

    def hy_derivation(self) -> Never:
        raise RetiredAuthorityAccess("governance_authority_retired:hy_derivation")

    def __getattr__(self, name: str) -> Never:
        raise RetiredAuthorityAccess(f"governance_authority_retired:{name}")


class RetiredMemoryTaskStore:
    """No-op compatibility surface after write jobs become the sole queue."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def recover_running(self) -> int:
        return 0

    def retry_first_unknown_after_reconcile(self, **_kwargs: Any) -> int:
        return 0

    def retry_unknown_confirmed(self) -> int:
        return 0

    def park_source_for_permission(self, **_kwargs: Any) -> int:
        return 0

    def rebind_unstarted_profile(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    def resume_profile_after_probe(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    def pause_profile_for_model(self, *_args: Any, **_kwargs: Any) -> int:
        return 0

    def list(self, **_kwargs: Any) -> builtins.list[object]:
        return []

    def counts(self) -> dict[object, int]:
        return {}

    def task_for_evidence(self, _evidence_id: str) -> None:
        return None

    def unexecuted_policy_failures(self) -> int:
        return 0

    def unknown_error_counts(self) -> dict[str, int]:
        return {}

    def blocked_stages(self) -> builtins.list[tuple[str, str, int]]:
        return []

    def seconds_per_completion(self) -> None:
        return None

    def processing_metrics(self) -> dict[str, int]:
        return {}

    def __getattr__(self, name: str) -> Never:
        raise RetiredAuthorityAccess(f"memory_task_queue_retired:{name}")
