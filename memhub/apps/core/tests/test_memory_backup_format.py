"""The v2 backup format without the SDK: what is copied, named, verified, rolled back.

The end-to-end round trip over real stores is `test_backup_includes_memories`;
this file covers the rules that need no store: which files a backup takes,
where it may be written, how a manifest is checked, and that an interrupted
restore is rolled back to the previous data by the next start.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from one_memory import memory_backup
from one_memory.memory_backup import (
    BackendIdentity,
    BackupError,
    copy_files,
    finish_backup,
    locate,
    plan_files,
    recover_interrupted_restore,
    resolve_target,
    restore_pending,
    verify_files,
)
from one_memory.storage_backends import GraphBackend, StorageBackends, VectorBackend


def _sqlite(path: Path, rows: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Closed here, not whenever it is collected: closing a WAL database
    # checkpoints it, and a late close would change the files under a test.
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        database.executemany("INSERT INTO t VALUES (?)", [(str(n),) for n in range(rows)])


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    _sqlite(home / "one-memory.sqlite3")
    root = home / "hy-memory" / "governed-v2"
    _sqlite(root / "cache.sqlite3", rows=3)
    _sqlite(root / "vector" / "chroma.sqlite3", rows=2)
    (root / "vector" / "segment").mkdir(parents=True)
    (root / "vector" / "segment" / "data_level0.bin").write_bytes(b"\x01" * 4096)
    (root / "graph").write_bytes(b"kuzu" * 1024)
    (root / "graph-1a2b3c4d").write_bytes(b"retained space graph")
    (root / "graph.wal").write_bytes(b"")
    # Never copied: quarantined and archived graphs, logs, temporaries, locks.
    (root / "graph.quarantined-20261101T000000Z").write_bytes(b"bad")
    (root / "graph.dim512.20261101T000000Z.bak").write_bytes(b"old width")
    (root / "logs").mkdir()
    (root / "logs" / "hy_memory.log").write_text("log")
    (root / "graph.lock").write_text("")
    (root / "scratch.tmp").write_text("")
    (root / ".governed-v2.restore-staging").mkdir()
    return home


def test_a_backup_takes_every_sdk_file_and_nothing_temporary(tmp_path) -> None:
    home = _home(tmp_path)
    planned = {item.relative: item for item in plan_files(home)}
    assert set(planned) == {
        "management/one-memory.sqlite3",
        "sdk/governed-v2/cache.sqlite3",
        "sdk/governed-v2/graph",
        "sdk/governed-v2/graph-1a2b3c4d",
        "sdk/governed-v2/graph.wal",
        "sdk/governed-v2/vector/chroma.sqlite3",
        "sdk/governed-v2/vector/segment/data_level0.bin",
    }
    assert planned["sdk/governed-v2/cache.sqlite3"].sqlite is True
    assert planned["sdk/governed-v2/graph"].sqlite is False


def test_a_symlink_in_the_sdk_tree_is_refused(tmp_path) -> None:
    home = _home(tmp_path)
    (home / "hy-memory" / "governed-v2" / "elsewhere").symlink_to(tmp_path)
    with pytest.raises(BackupError, match="backup_sdk_root_symlink"):
        plan_files(home)


def test_the_target_is_a_new_directory_inside_the_home(tmp_path) -> None:
    home = _home(tmp_path)
    assert resolve_target(home, "backups/a").name == "a.memhub-backup"
    assert resolve_target(home, "backups/a.sqlite3").name == "a.memhub-backup"
    assert resolve_target(home, "backups/a.memhub-backup").name == "a.memhub-backup"
    with pytest.raises(BackupError, match="backup_target_outside_data_dir"):
        resolve_target(home, str(tmp_path / "outside"))
    with pytest.raises(BackupError, match="backup_target_outside_data_dir"):
        resolve_target(home, "../escape")
    # Only a direct entry of `backups/`: not the home itself, not deeper.
    for elsewhere in ("one", "hy-memory/x", "backups/a/b", "backups", "backups/.."):
        with pytest.raises(BackupError, match="backup_target_outside_data_dir"):
            resolve_target(home, elsewhere)
    (home / "backups" / "b.memhub-backup").mkdir(parents=True)
    with pytest.raises(BackupError, match="backup_target_exists"):
        resolve_target(home, "backups/b")


def _backup(home: Path, name: str = "one", **extra: object) -> Path:
    target = resolve_target(home, f"backups/{name}")
    staging = memory_backup.staging_for(target)
    staging.mkdir(parents=True)
    inventory = copy_files(plan_files(home), staging)
    manifest = {
        "format": "memhub-backup",
        "schema_version": 2,
        "sdk_namespace": "governed-v2",
        "backend": BackendIdentity().document(),
        "users": ["local"],
        "counts": {"layers": None, "vector_store": {}, "graph": None},
        **extra,
    }
    finish_backup(staging=staging, target=target, inventory=inventory, manifest=manifest)
    return target


def test_sqlite_files_are_copied_whole_and_owner_only(tmp_path) -> None:
    home = _home(tmp_path)
    backup = _backup(home)
    copy = backup / "sdk" / "governed-v2" / "cache.sqlite3"
    with closing(sqlite3.connect(copy)) as database:
        assert database.execute("SELECT count(*) FROM t").fetchone() == (3,)
        assert database.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert not (copy.parent / "cache.sqlite3-wal").exists()
    for path in [backup, *backup.rglob("*")]:
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["file_count"] == len(manifest["files"]) == 7


def test_a_manifest_and_its_files_are_verified(tmp_path) -> None:
    home = _home(tmp_path)
    backup = _backup(home)
    source = locate(home, "backups/one.memhub-backup")
    assert source.schema_version == 2
    verify_files(backup, source.manifest["files"])  # type: ignore[arg-type]
    (backup / "sdk" / "governed-v2" / "graph").write_bytes(b"changed")
    with pytest.raises(BackupError, match="backup_restore_verify_failed"):
        verify_files(backup, source.manifest["files"])  # type: ignore[arg-type]
    (backup / "sdk" / "governed-v2" / "graph").unlink()
    with pytest.raises(BackupError, match="backup_restore_verify_failed"):
        verify_files(backup, source.manifest["files"])  # type: ignore[arg-type]


def test_a_newer_or_unknown_manifest_is_refused(tmp_path) -> None:
    home = _home(tmp_path)
    backup = _backup(home)
    document = json.loads((backup / "manifest.json").read_text())
    document["schema_version"] = 3
    (backup / "manifest.json").write_text(json.dumps(document))
    with pytest.raises(BackupError, match="backup_restore_source_too_new"):
        locate(home, "backups/one.memhub-backup")
    document["format"] = "something-else"
    (backup / "manifest.json").write_text(json.dumps(document))
    with pytest.raises(BackupError, match="backup_restore_manifest_invalid"):
        locate(home, "backups/one.memhub-backup")


def test_a_source_outside_the_backups_directory_is_refused(tmp_path) -> None:
    home = _home(tmp_path)
    (tmp_path / "loose.memhub-backup").mkdir()
    with pytest.raises(BackupError, match="backup_restore_source_outside_home"):
        locate(home, str(tmp_path / "loose.memhub-backup"))
    with pytest.raises(BackupError, match="backup_restore_source_outside_home"):
        locate(home, "backups/missing.memhub-backup")


def test_backend_kinds() -> None:
    assert BackendIdentity.of(StorageBackends()).kind == "local"
    neo4j = StorageBackends(graph=GraphBackend(provider="neo4j", url="bolt://127.0.0.1:7687"))
    assert BackendIdentity.of(neo4j).kind == "remote_graph"
    qdrant = StorageBackends(
        vector=VectorBackend(provider="qdrant", host="qdrant.internal"), namespace="mh12345678"
    )
    identity = BackendIdentity.of(qdrant)
    assert identity.kind == "remote_vector"
    assert identity.vector_prefix == "mh12345678"
    assert BackendIdentity.from_document(identity.document()) == identity


def _prepared(home: Path) -> memory_backup.PreparedRestore:
    source = locate(home, "backups/one.memhub-backup")
    original = memory_backup._check_database
    memory_backup._check_database = lambda _path: frozenset({"t"})  # type: ignore[assignment]
    try:
        return memory_backup.prepare_restore(home, source, current=BackendIdentity())
    finally:
        memory_backup._check_database = original  # type: ignore[assignment]


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_restore_killed_mid_swap_is_rolled_back_by_the_next_start(tmp_path) -> None:
    home = _home(tmp_path)
    _backup(home)
    root = home / "hy-memory" / "governed-v2"
    (root / "graph").write_bytes(b"newer graph than the backup")
    before = _tree(root)
    prepared = _prepared(home)
    memory_backup._write_journal(
        home,
        {
            "schema_version": 1,
            "stamp": prepared.stamp,
            "previous": prepared.previous.relative_to(home).as_posix(),
            "staged": prepared.staged_sdk.relative_to(home).as_posix(),
        },
    )
    # The process dies right after moving the live tree aside.
    (prepared.previous / "sdk").mkdir(parents=True)
    root.replace(prepared.previous / "sdk" / "governed-v2")
    assert restore_pending(home)
    assert not root.exists()

    assert recover_interrupted_restore(home) is True
    assert not restore_pending(home)
    assert _tree(root) == before
    assert not prepared.staged_sdk.exists()
    assert recover_interrupted_restore(home) is False


def test_a_cross_backend_restore_is_refused_before_staging(tmp_path) -> None:
    home = _home(tmp_path)
    _backup(home)
    source = locate(home, "backups/one.memhub-backup")
    qdrant = BackendIdentity(vector="qdrant", vector_prefix="mh12345678")
    with pytest.raises(BackupError, match="backup_restore_backend_mismatch"):
        memory_backup.prepare_restore(home, source, current=qdrant)
    assert not list((home / "hy-memory").glob(".governed-v2.backup-restore-*"))


QDRANT_EXPORT = {
    "path": "export/memories.jsonl",
    "format": "jsonl",
    "node_format": "hy-memory-node-v1",
    "embeddings_included": True,
    "sha256": "0" * 64,
}
LIVE = {"space_id": "space-live"}


def _qdrant_backup(home: Path, **counts: object) -> BackendIdentity:
    qdrant = BackendIdentity(vector="qdrant", vector_prefix="mh12345678")
    _backup(
        home,
        backend=qdrant.document(),
        export=QDRANT_EXPORT,
        embedding_space=LIVE,
        counts={"layers": None, "vector_store": {}, "graph": None, **counts},
    )
    return qdrant


def test_a_qdrant_backup_onto_another_namespace_is_refused(tmp_path) -> None:
    home = _home(tmp_path)
    _qdrant_backup(home)
    source = locate(home, "backups/one.memhub-backup")
    other = BackendIdentity(vector="qdrant", vector_prefix="mh87654321")
    with pytest.raises(BackupError, match="backup_restore_backend_mismatch"):
        memory_backup.prepare_restore(home, source, current=other, current_space_id="space-live")


@pytest.mark.parametrize(
    ("manifest", "space", "current", "code"),
    [
        # Written before 2.0.16: list items without vectors or node ids.
        (
            {"export": {**QDRANT_EXPORT, "embeddings_included": False}},
            "space-live",
            None,
            "backup_restore_export_without_vectors",
        ),
        (
            {"export": {k: v for k, v in QDRANT_EXPORT.items() if k != "node_format"}},
            "space-live",
            None,
            "backup_restore_export_without_vectors",
        ),
        # The embedding space changed since: other collections, other vectors.
        ({}, "space-new", None, "backup_restore_qdrant_space_changed"),
        ({}, None, None, "backup_restore_qdrant_space_changed"),
        # Coding/Working experiences: no SDK export or import of their vectors.
        (
            {"counts": {"layers": {"local": {"scene:coding": 2}}}},
            "space-live",
            None,
            "backup_restore_qdrant_experiences_unsupported",
        ),
        (
            {"counts": {"vector_store": {"memhub_x|512": {"working_facts": {"local": 1}}}}},
            "space-live",
            None,
            "backup_restore_qdrant_experiences_unsupported",
        ),
        # ... on the server now, too: their SQLite rows would not match.
        (
            {},
            "space-live",
            {"memhub_x|512": {"coding_keys_cv": {"local": 3}}},
            "backup_restore_qdrant_experiences_unsupported",
        ),
        # The retrieval-clause index is neither exported nor rebuilt.
        (
            {"counts": {"vector_store": {"memhub_x|512": {"retrieval_clauses": {"local": 4}}}}},
            "space-live",
            None,
            "backup_restore_qdrant_retrieval_clauses_unsupported",
        ),
        (
            {},
            "space-live",
            {"memhub_x|512": {"retrieval_clauses": {"local": 1}}},
            "backup_restore_qdrant_retrieval_clauses_unsupported",
        ),
    ],
)
def test_a_qdrant_restore_refuses_what_the_sdk_cannot_write_back(
    tmp_path, manifest, space, current, code
) -> None:
    base: dict[str, object] = {
        "export": QDRANT_EXPORT,
        "embedding_space": LIVE,
        "counts": {
            "layers": {"local": {"l2_fact": 3, "scene:coding": 0}},
            "vector_store": {"memhub_x|512": {"memory": {"local": 3}, "tag_index": {"local": 2}}},
        },
    }
    document = {**base, **manifest}
    if "counts" in manifest:
        document["counts"] = {**base["counts"], **manifest["counts"]}  # type: ignore[dict-item]
    assert (
        memory_backup.qdrant_restore_refusal(
            document, current_space_id=space, current_vectors=current
        )
        == code
    )


def test_a_qdrant_restore_of_memories_alone_is_allowed(tmp_path) -> None:
    manifest = {
        "export": QDRANT_EXPORT,
        "embedding_space": LIVE,
        # The tag index is not written back, and MemHub's retrieval does not read it.
        "counts": {
            "layers": {"local": {"l1_raw": 4, "l2_fact": 3, "scene:coding": 0, "scene:working": 0}},
            "vector_store": {"memhub_x|512": {"memory": {"local": 7}, "tag_index": {"local": 2}}},
        },
    }
    current = {"memhub_x|512": {"memory": {"local": 9}, "entities": {"local": 1}}}
    assert (
        memory_backup.qdrant_restore_refusal(
            manifest, current_space_id="space-live", current_vectors=current
        )
        is None
    )


def test_an_offline_restore_onto_qdrant_says_it_needs_a_client(tmp_path) -> None:
    home = _home(tmp_path)
    qdrant = _qdrant_backup(home)
    source = locate(home, "backups/one.memhub-backup")
    with pytest.raises(BackupError, match="backup_restore_vector_client_unavailable"):
        asyncio.run(memory_backup.run_restore(home, source, current=qdrant))
    assert not list((home / "hy-memory").glob(".governed-v2.backup-restore-*"))


def test_write_export_keeps_every_node_with_its_vector(tmp_path) -> None:
    pages: list[dict[str, object]] = []
    nodes = [
        {"node_id": f"r{n}", "user_id": "local", "layer": "l1_raw", "embedding": [0.5, 0.5]}
        for n in range(3)
    ] + [
        {
            "node_id": "f1",
            "user_id": "local",
            "layer": "l2_fact",
            "status": "superseded",
            "superseded_by": "f2",
            "embedding": [1.0, 0.0],
        }
    ]

    class Client:
        def export_user_memories(self, user_id, *, include_embeddings):
            assert (user_id, include_embeddings) == ("local", True)
            return {
                "success": True,
                "nodes": nodes,
                "embedding_dims": 2,
                "embedding_model": "m",
            }

        def list_memories(self, **kwargs):
            pages.append(kwargs)
            if kwargs["scene"] == "coding" and kwargs["offset"] == 0:
                return {"experiences": [{"memory_id": "c1", "content": "use uv"}]}
            return {"experiences": []}

    target = tmp_path / "export" / "memories.jsonl"
    export = memory_backup.write_export(Client(), ["local"], target)
    assert export["counts"] == {
        "local": {
            "l0_basic_info": 0,
            "l1_raw": 3,
            "l2_fact": 1,
            "l3_summary": 0,
            "l4_identity": 0,
            "scene:coding": 1,
            "scene:working": 0,
        }
    }
    assert export["embeddings_included"] is True
    assert export["node_format"] == "hy-memory-node-v1"
    assert (export["embedding_dims"], export["embedding_model"]) == (2, "m")
    assert len(target.read_text().splitlines()) == 5
    assert target.stat().st_mode & 0o777 == 0o600
    assert all(page["statuses"] == list(memory_backup.ALL_STATUSES) for page in pages)
    assert export["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    # Read back: the node lines only, per user, exactly as exported.
    assert memory_backup.read_export(target, str(export["sha256"])) == {"local": nodes}
    with pytest.raises(BackupError, match="backup_restore_verify_failed"):
        memory_backup.read_export(target, "0" * 64)


def test_write_export_refuses_an_sdk_without_the_export(tmp_path) -> None:
    class Client:
        def export_user_memories(self, user_id, *, include_embeddings):
            return {"success": False, "error_message": "nope"}

    with pytest.raises(BackupError, match="backup_sdk_unavailable"):
        memory_backup.write_export(Client(), ["local"], tmp_path / "export" / "m.jsonl")


def test_old_backup_note_names_the_missing_memories() -> None:
    assert memory_backup.old_backup_note() == "management_database_only_no_memories"


def test_run_restore_refuses_a_pending_journal(tmp_path) -> None:
    home = _home(tmp_path)
    _backup(home)
    (home / ".memhub-restore.json").write_text("{}")
    source = locate(home, "backups/one.memhub-backup")
    with pytest.raises(BackupError, match="backup_restore_pending"):
        asyncio.run(memory_backup.run_restore(home, source, current=BackendIdentity()))


ESCAPES = (
    "backups/../one-memory.sqlite3",
    "backups/../../outside",
    "../outside",
    "/etc/passwd",
    "backups/./../hy-memory",
    "backups/sub/../../outside",
    "",
    "backups/\x00name",
)


@pytest.mark.parametrize("value", ESCAPES)
def test_a_traversal_never_leaves_the_backups_directory(tmp_path, value) -> None:
    home = _home(tmp_path)
    (home / "backups").mkdir(exist_ok=True)
    (tmp_path / "outside").mkdir()
    with pytest.raises(BackupError, match="backup_restore_source_outside_home"):
        locate(home, value)
    with pytest.raises(BackupError, match="backup_target_outside_data_dir"):
        resolve_target(home, value)


def test_a_symlink_never_leads_out_of_the_backups_directory(tmp_path) -> None:
    home = _home(tmp_path)
    backups = home / "backups"
    backups.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}")
    _backup(home, "real")
    # A link to a directory outside, and a link to a real backup inside: both refused.
    (backups / "escape.memhub-backup").symlink_to(outside)
    (backups / "alias.memhub-backup").symlink_to(backups / "real.memhub-backup")
    for name in ("escape.memhub-backup", "alias.memhub-backup"):
        with pytest.raises(BackupError, match="backup_restore_source_outside_home"):
            locate(home, f"backups/{name}")
        with pytest.raises(BackupError, match="backup_target_outside_data_dir"):
            resolve_target(home, f"backups/{name}")
    assert locate(home, "backups/real.memhub-backup").schema_version == 2


def test_a_backups_directory_that_is_a_link_is_refused(tmp_path) -> None:
    home = _home(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "x.memhub-backup").mkdir(parents=True)
    (home / "backups").symlink_to(elsewhere)
    with pytest.raises(BackupError, match="backup_restore_source_outside_home"):
        locate(home, "backups/x.memhub-backup")
    with pytest.raises(BackupError, match="backup_target_outside_data_dir"):
        resolve_target(home, "backups/new")


def test_an_absolute_path_inside_the_backups_directory_is_accepted(tmp_path) -> None:
    home = _home(tmp_path)
    _backup(home, "abs")
    source = locate(home, str(home / "backups" / "abs.memhub-backup"))
    assert source.path == (home / "backups" / "abs.memhub-backup").resolve()
