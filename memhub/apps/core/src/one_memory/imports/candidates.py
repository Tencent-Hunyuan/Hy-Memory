"""Metadata-only candidate selection, separate from source reading permission."""

import os
import sqlite3
import unicodedata
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

from one_memory.database_access import main_database_transaction
from one_memory.imports.access import (
    AuthorizedSourceRoot,
    SourceAccessError,
    root_identity_matches,
    source_root_identity,
)
from one_memory.imports.locations import inspect_source_location
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    SourceRegistration,
    SourceRootCandidate,
)
from one_memory.memory_server.migrations import source_candidates_ddl

SCHEMA = source_candidates_ddl("sqlite")
PICKER_SOURCE_IDS = frozenset({"workbuddy", "cursor", "claude-code", "codex", "hermes"})


class SourceCandidateError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SourceCandidateSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: str
    path: str
    root_id: str
    candidate_revision: int


class SourceCandidateStore:
    def __init__(self, database_path: Path, *, home: Path) -> None:
        self.database_path = database_path.resolve()
        self.home = home.resolve()
        with self._connect(initialize=True) as db:
            db.execute(SCHEMA)

    def _connect(
        self, *, initialize: bool = False, writing: bool = False
    ) -> AbstractContextManager[sqlite3.Connection]:
        return main_database_transaction(self.database_path, initialize=initialize, writing=writing)

    @staticmethod
    def allowed(source_id: str) -> bool:
        return source_id in PICKER_SOURCE_IDS

    def change_reason(
        self,
        registration: SourceRegistration,
        *,
        user_id: str = "local",
        db: sqlite3.Connection | None = None,
    ) -> str | None:
        if not self.allowed(registration.descriptor.source_id):
            return "source_candidate_not_supported"
        if registration.candidate.kind is RootCandidateKind.ENVIRONMENT_OVERRIDE:
            return "source_candidate_environment_override"
        if db is None:
            with self._connect() as connection:
                return self.change_reason(registration, user_id=user_id, db=connection)
        for table in ("source_grants", "evidence", "import_jobs"):
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if (
                exists
                and db.execute(
                    f"SELECT 1 FROM {table} WHERE user_id=? AND source_id=? LIMIT 1",
                    (user_id, registration.descriptor.source_id),
                ).fetchone()
            ):
                return "source_candidate_locked"
        return None

    def _require_change(
        self, registration: SourceRegistration, user_id: str, db: sqlite3.Connection
    ) -> None:
        reason = self.change_reason(registration, user_id=user_id, db=db)
        if reason:
            raise SourceCandidateError(reason)

    def _root(self, source_id: str, path: Path) -> tuple[str, str]:
        if (
            not path.is_absolute()
            or any(unicodedata.category(character) == "Cc" for character in str(path))
            or len(str(path)) > 8192
        ):
            raise SourceCandidateError("source_candidate_invalid_path")
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise SourceCandidateError("symlink_not_allowed")
            # Reuse existing sensitive-root checks without persisting a grant or
            # constructing a source reader. This object alone grants no access.
            root = AuthorizedSourceRoot.grant(
                source_id=source_id, root=path, user_confirmed=True, home=self.home
            )
            if root.root == Path(root.root.anchor) or self.home.is_relative_to(root.root):
                raise SourceCandidateError("sensitive_root")
            if self.database_path.is_relative_to(root.root):
                raise SourceCandidateError("source_candidate_contains_database")
            location = inspect_source_location(source_id, path)
            if location.state != "found" or location.root_id is None:
                raise SourceCandidateError(location.reason or "source_unavailable")
            if location.root_id != root.root_identity:
                raise SourceCandidateError("source_identity_changed")
            return os.path.abspath(path), location.root_id
        except SourceAccessError as error:
            raise SourceCandidateError(str(error.code)) from None
        except (OSError, RuntimeError) as error:
            if isinstance(error, SourceCandidateError):
                raise
            raise SourceCandidateError("source_unavailable") from None

    @staticmethod
    def _row(db: sqlite3.Connection, user_id: str, source_id: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            db.execute(
                "SELECT * FROM source_candidates WHERE user_id=? AND source_id=?",
                (user_id, source_id),
            ).fetchone(),
        )

    def preview(
        self, registration: SourceRegistration, path: Path, *, user_id: str = "local"
    ) -> SourceCandidateSelection:
        source_id = registration.descriptor.source_id
        with self._connect() as db:
            self._require_change(registration, user_id, db)
            row = self._row(db, user_id, source_id)
            requested, root_id = self._root(source_id, path)
            return SourceCandidateSelection(
                source_id=source_id,
                path=requested,
                root_id=root_id,
                candidate_revision=row["revision"] if row else 0,
            )

    def select(
        self,
        registration: SourceRegistration,
        path: Path,
        *,
        expected_revision: int,
        expected_root_id: str,
        user_confirmed: bool,
        user_id: str = "local",
    ) -> SourceCandidateSelection:
        if user_confirmed is not True:
            raise SourceCandidateError("source_candidate_confirmation_required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise SourceCandidateError("source_candidate_revision_conflict")
        source_id = registration.descriptor.source_id
        with self._connect(writing=True) as db:
            self._require_change(registration, user_id, db)
            row = self._row(db, user_id, source_id)
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise SourceCandidateError("source_candidate_revision_conflict")
            requested, root_id = self._root(source_id, path)
            if not root_identity_matches(path, expected_root_id):
                raise SourceCandidateError("source_identity_changed")
            if row and row["requested_path"] == requested and row["root_id"] == root_id:
                return SourceCandidateSelection(
                    source_id=source_id,
                    path=requested,
                    root_id=root_id,
                    candidate_revision=revision,
                )
            changed = db.execute(
                "INSERT INTO source_candidates "
                "(user_id,source_id,requested_path,root_id,revision,updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,source_id) DO UPDATE SET "
                "requested_path=excluded.requested_path,root_id=excluded.root_id,"
                "revision=excluded.revision,updated_at=excluded.updated_at "
                "WHERE source_candidates.revision=?",
                (
                    user_id,
                    source_id,
                    requested,
                    root_id,
                    revision + 1,
                    datetime.now(UTC).isoformat(),
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise SourceCandidateError("source_candidate_revision_conflict")
            if source_root_identity(path) != root_id:
                raise SourceCandidateError("source_identity_changed")
            return SourceCandidateSelection(
                source_id=source_id,
                path=requested,
                root_id=root_id,
                candidate_revision=revision + 1,
            )

    def resolve(
        self,
        registration: SourceRegistration,
        *,
        user_id: str = "local",
        db: sqlite3.Connection | None = None,
    ) -> SourceRegistration:
        if not self.allowed(registration.descriptor.source_id) or (
            registration.candidate.kind is RootCandidateKind.ENVIRONMENT_OVERRIDE
        ):
            return registration
        if db is None:
            with self._connect() as connection:
                return self.resolve(registration, user_id=user_id, db=connection)
        row = self._row(db, user_id, registration.descriptor.source_id)
        if row is None:
            if registration.candidate.kind is RootCandidateKind.USER_SELECTED:
                raise SourceCandidateError("source_candidate_not_found")
            return registration
        return replace(
            registration,
            candidate=SourceRootCandidate(
                path=Path(row["requested_path"]),
                kind=RootCandidateKind.USER_SELECTED,
                revision=row["revision"],
            ),
        )

    def resolve_catalog(
        self, catalog: dict[str, SourceRegistration], *, user_id: str = "local"
    ) -> dict[str, SourceRegistration]:
        with self._connect() as db:
            return {key: self.resolve(reg, user_id=user_id, db=db) for key, reg in catalog.items()}
