import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from one_memory.database_access import main_database_transaction
from one_memory.imports.access import (
    DEFAULT_MAX_SOURCE_FILE_BYTES,
    AuthorizedSourceRoot,
    SourceAccessError,
    SourceAccessErrorCode,
    root_identity_matches,
)
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.memory_server.migrations import source_grants_ddl

CURSOR_MAX_SOURCE_FILE_BYTES = 64 * 1024 * 1024 * 1024
GRANT_SCHEMA = source_grants_ddl("sqlite")


class SourceAuthorizationState(StrEnum):
    """Why a source cannot be read right now, or `ok` when it can.

    The API used to answer `authorized=false` for all of these at once, which
    is how a revoked grant, a rebuilt history directory and a moved candidate
    all looked like the same bug and all got the same advice. The values carry
    no paths and no user content, because they are written to diagnostics.
    """

    OK = "ok"
    MISSING_GRANT = "missing_grant"
    REVOKED_GRANT = "revoked_grant"
    SOURCE_IDENTITY_CHANGED = "source_identity_changed"
    SOURCE_ROOT_UNAVAILABLE = "source_root_unavailable"
    CANDIDATE_CHANGED = "candidate_changed"


class SourceGrantReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    source_id: str
    root_id: str
    root_label: str
    candidate_kind: RootCandidateKind
    granted_at: datetime
    revoked_at: datetime | None
    revision: int = 0


class SourceGrantStore:
    """Root authorization only; Harness sync lives in Core's JSON config."""

    def __init__(self, database_path: Path, *, home: Path) -> None:
        self.database_path = database_path
        self.home = home
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(initialize=True) as connection:
            connection.executescript(GRANT_SCHEMA)

    def grant(
        self,
        *,
        user_id: str,
        source_id: str,
        root: Path,
        candidate_kind: RootCandidateKind,
        expected_root_id: str | None = None,
    ) -> SourceGrantReceipt:
        authorized = AuthorizedSourceRoot.grant(
            source_id=source_id,
            root=root,
            user_confirmed=True,
            home=self.home,
            max_file_bytes=_max_source_file_bytes(source_id),
        )
        if expected_root_id is not None and not root_identity_matches(root, expected_root_id):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED)
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            candidate_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_candidates'"
            ).fetchone()
            if (
                candidate_table
                and connection.execute(
                    "SELECT 1 FROM source_candidates WHERE user_id=? AND source_id=?",
                    (user_id, source_id),
                ).fetchone()
            ):
                raise SourceAccessError(
                    SourceAccessErrorCode.SOURCE_CANDIDATE_CONFIRMATION_REQUIRED
                )
            self.write_grant(
                connection,
                user_id=user_id,
                source_id=source_id,
                authorized=authorized,
                candidate_kind=candidate_kind,
                now=now,
            )
        return self.require_receipt(user_id=user_id, source_id=source_id)

    @staticmethod
    def write_grant(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        source_id: str,
        authorized: AuthorizedSourceRoot,
        candidate_kind: RootCandidateKind,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO source_grants (
                user_id, source_id, root_path, requested_path, root_identity,
                candidate_kind, granted_at, revoked_at, revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1)
            ON CONFLICT(user_id, source_id) DO UPDATE SET
                root_path=excluded.root_path,
                requested_path=excluded.requested_path,
                root_identity=excluded.root_identity,
                candidate_kind=excluded.candidate_kind,
                granted_at=excluded.granted_at,
                revoked_at=NULL,
                revision=source_grants.revision+1
            """,
            (
                user_id,
                source_id,
                str(authorized.root),
                str(authorized.requested_root),
                authorized.root_identity,
                candidate_kind,
                now,
            ),
        )

    def authorized_root(
        self,
        *,
        user_id: str,
        source_id: str,
    ) -> AuthorizedSourceRoot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_grants WHERE user_id=? AND source_id=?",
                (user_id, source_id),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        authorized = AuthorizedSourceRoot.grant(
            source_id=source_id,
            root=Path(row["requested_path"]),
            user_confirmed=True,
            home=self.home,
            max_file_bytes=_max_source_file_bytes(source_id),
        )
        if not root_identity_matches(Path(row["requested_path"]), row["root_identity"]):
            raise SourceAccessError(SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED)
        return authorized

    def authorization_state(
        self,
        *,
        user_id: str,
        source_id: str,
        candidate: Path,
    ) -> SourceAuthorizationState:
        """Why reading this source would be refused.

        Read-only and fails the same way `authorized_root` does: this names a
        state for the UI and for diagnostics, it does not widen access. The
        candidate is compared by path, so a grant that still points somewhere
        else reads as a moved candidate rather than as a rebuilt directory —
        the two need different fixes.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_grants WHERE user_id=? AND source_id=?",
                (user_id, source_id),
            ).fetchone()
        if row is None:
            return SourceAuthorizationState.MISSING_GRANT
        if row["revoked_at"] is not None or row["revision"] <= 0:
            return SourceAuthorizationState.REVOKED_GRANT
        granted = Path(row["requested_path"])
        if not granted.exists():
            return SourceAuthorizationState.SOURCE_ROOT_UNAVAILABLE
        try:
            if not root_identity_matches(granted, row["root_identity"]):
                return SourceAuthorizationState.SOURCE_IDENTITY_CHANGED
        except SourceAccessError:
            # Unreadable or gone between the two calls: there is no grant to
            # repair, only a directory to make available again.
            return SourceAuthorizationState.SOURCE_ROOT_UNAVAILABLE
        # Same directory, but the app now looks somewhere else. Re-authorizing
        # the old grant would not help; the candidate has to be granted.
        if granted != candidate:
            return SourceAuthorizationState.CANDIDATE_CHANGED
        return SourceAuthorizationState.OK

    def revoke(self, *, user_id: str, source_id: str) -> SourceGrantReceipt:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            connection.execute(
                "UPDATE source_grants SET revoked_at=? "
                "WHERE user_id=? AND source_id=? AND revoked_at IS NULL",
                (now, user_id, source_id),
            )
        return self.require_receipt(user_id=user_id, source_id=source_id)

    def follow_default_home(
        self,
        *,
        user_id: str,
        source_id: str,
        root: Path,
        default_homes: tuple[Path, ...],
    ) -> SourceGrantReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_grants WHERE user_id=? AND source_id=?",
                (user_id, source_id),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        granted = Path(row["requested_path"])
        if granted == root or granted not in default_homes or root not in default_homes:
            return None
        return self.grant(
            user_id=user_id,
            source_id=source_id,
            root=root,
            candidate_kind=RootCandidateKind.DEFAULT,
        )

    def receipt(
        self,
        *,
        user_id: str,
        source_id: str,
    ) -> SourceGrantReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_grants WHERE user_id=? AND source_id=?",
                (user_id, source_id),
            ).fetchone()
        return self._to_receipt(row) if row else None

    def require_receipt(self, *, user_id: str, source_id: str) -> SourceGrantReceipt:
        receipt = self.receipt(user_id=user_id, source_id=source_id)
        if receipt is None:
            raise KeyError(source_id)
        return receipt

    def _connect(
        self,
        *,
        initialize: bool = False,
        writing: bool = False,
    ) -> AbstractContextManager[sqlite3.Connection]:
        return main_database_transaction(self.database_path, initialize=initialize, writing=writing)

    @staticmethod
    def _to_receipt(row: sqlite3.Row) -> SourceGrantReceipt:
        root = Path(row["root_path"])
        return SourceGrantReceipt(
            user_id=row["user_id"],
            source_id=row["source_id"],
            root_id=row["root_identity"],
            root_label=root.name,
            candidate_kind=RootCandidateKind(row["candidate_kind"]),
            granted_at=datetime.fromisoformat(row["granted_at"]),
            revoked_at=(datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None),
            revision=row["revision"],
        )


def _max_source_file_bytes(source_id: str) -> int:
    if source_id == "cursor":
        return CURSOR_MAX_SOURCE_FILE_BYTES
    return DEFAULT_MAX_SOURCE_FILE_BYTES
