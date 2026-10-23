import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from one_memory.imports.access import source_root_identity
from one_memory.imports.candidates import SourceCandidateError, SourceCandidateStore
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    SourceRootCandidate,
    build_source_catalog,
)
from one_memory.imports.storage import EvidenceStore


@pytest.fixture
def setup(tmp_path):
    tmp_path = tmp_path.resolve()
    home = tmp_path / "home"
    home.mkdir()
    database = tmp_path / "data.sqlite"
    grants = SourceGrantStore(database, home=home)
    jobs = ImportJobStore(database)
    evidence = EvidenceStore(database)
    store = SourceCandidateStore(database, home=home)
    catalog = build_source_catalog(home=home, environment={})
    selected = home / "custom"
    selected.mkdir()
    return store, catalog, selected, database, grants, jobs, evidence


def save(store, registration, path, revision=0):
    return store.select(
        registration,
        path,
        expected_revision=revision,
        expected_root_id=source_root_identity(path),
        user_confirmed=True,
    )


@pytest.mark.parametrize("source", ["workbuddy", "cursor", "claude-code", "codex", "hermes"])
def test_preview_is_metadata_only_and_cancel_changes_nothing(setup, source):
    store, catalog, selected, database, grants, jobs, evidence = setup
    with (
        patch.object(Path, "open", side_effect=AssertionError("no file reads")),
        patch.object(Path, "iterdir", side_effect=AssertionError("no enumeration")),
        patch.object(Path, "mkdir", side_effect=AssertionError("no source mkdir")),
    ):
        preview = store.preview(catalog[source], selected)
    assert preview.path == str(selected)
    assert preview.root_id == source_root_identity(selected)
    assert preview.candidate_revision == 0
    assert store.resolve(catalog[source]) == catalog[source]
    assert store.allowed(source)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM source_candidates").fetchone()[0] == 0
    assert grants.receipt(user_id="local", source_id=source) is None
    assert jobs.list_jobs() == [] and evidence.count() == 0


def test_hermes_profile_directory_can_be_selected(setup):
    """Hermes is multi-profile, so its source root has to be pickable.

    The default candidate is `~/.hermes`, which also contains every
    `profiles/<name>/state.db`. Without a picker there is no way to import one
    profile without the others, so `hermes` joins the picker allowlist and a
    profile directory behaves like any other selected root.
    """
    store, catalog, selected, *_ = setup
    profile = selected / ".hermes" / "profiles" / "luo-bo"
    profile.mkdir(parents=True)
    (profile / "state.db").write_bytes(b"")
    assert store.allowed("hermes")
    saved = save(store, catalog["hermes"], profile)
    assert saved.path == str(profile)
    assert saved.candidate_revision == 1
    assert store.resolve(catalog["hermes"]).candidate == SourceRootCandidate(
        profile, RootCandidateKind.USER_SELECTED, 1
    )


def test_select_persists_only_candidate_and_restarts_with_revision(setup):
    store, catalog, selected, database, grants, jobs, evidence = setup
    original = catalog["cursor"]
    first = save(store, original, selected)
    assert first.candidate_revision == 1
    resolved = store.resolve(original)
    assert resolved.candidate == SourceRootCandidate(selected, RootCandidateKind.USER_SELECTED, 1)
    assert resolved.create_adapter is original.create_adapter
    assert save(store, original, selected, 1) == first
    restarted = SourceCandidateStore(database, home=store.home)
    assert restarted.resolve_catalog(catalog)["cursor"] == resolved
    assert grants.receipt(user_id="local", source_id="cursor") is None
    assert jobs.list_jobs() == [] and evidence.count() == 0


def test_compare_and_swap_and_aba_cannot_reuse_old_preview(setup):
    store, catalog, a, _, _, _, _ = setup
    reg = catalog["cursor"]
    b = a.with_name("other")
    b.mkdir()
    assert save(store, reg, a).candidate_revision == 1
    with pytest.raises(SourceCandidateError, match="source_candidate_revision_conflict"):
        save(store, reg, b, 0)
    assert save(store, reg, b, 1).candidate_revision == 2
    assert save(store, reg, a, 2).candidate_revision == 3
    with pytest.raises(SourceCandidateError, match="source_candidate_revision_conflict"):
        save(store, reg, b, 1)


def test_replaced_directory_is_rejected_at_save(setup):
    store, catalog, selected, _, _, _, _ = setup
    before = store.preview(catalog["cursor"], selected)
    selected.rename(selected.with_name("old"))
    selected.mkdir()
    with pytest.raises(SourceCandidateError, match="source_identity_changed"):
        store.select(
            catalog["cursor"],
            selected,
            expected_revision=0,
            expected_root_id=before.root_id,
            user_confirmed=True,
        )
    assert store.resolve(catalog["cursor"]).candidate.revision == 0


@pytest.mark.parametrize(
    "target",
    [
        "root",
        "home",
        "ancestor",
        "database",
        "Documents",
        "Downloads",
        "Desktop",
        "missing",
        "file",
        "relative",
        "symlink",
    ],
)
def test_rejects_unsafe_root_without_creating_directories(setup, target):
    store, catalog, selected, database, *_ = setup
    if target == "root":
        path = Path("/")
    elif target == "home":
        path = store.home
    elif target in {"ancestor", "database"}:
        path = database.parent
    elif target == "missing":
        path = selected / "missing"
    elif target == "file":
        path = selected / "file"
        path.write_text("not a directory")
    elif target == "relative":
        path = Path("relative")
    elif target == "symlink":
        path = selected.with_name("alias")
        path.symlink_to(selected, target_is_directory=True)
    else:
        path = store.home / target
        path.mkdir()
    with pytest.raises(SourceCandidateError):
        store.preview(catalog["cursor"], path)
    assert store.resolve(catalog["cursor"]).candidate.revision == 0


def test_rejects_database_containment_not_just_home_ancestors(tmp_path):
    tmp_path = tmp_path.resolve()
    home = tmp_path / "home"
    home.mkdir()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    store = SourceCandidateStore(candidate / "private.sqlite", home=home)
    reg = build_source_catalog(home=home, environment={})["cursor"]
    with pytest.raises(SourceCandidateError, match="source_candidate_contains_database"):
        store.preview(reg, candidate)


def test_env_override_and_nonpriority_sources_cannot_save(setup):
    store, catalog, selected, *_ = setup
    for reg in [
        catalog["pi"],
        replace(
            catalog["cursor"],
            candidate=SourceRootCandidate(selected, RootCandidateKind.ENVIRONMENT_OVERRIDE),
        ),
    ]:
        assert store.change_reason(reg)
        with pytest.raises(SourceCandidateError):
            save(store, reg, selected)
    save(store, catalog["cursor"], selected)
    override = replace(
        catalog["cursor"],
        candidate=SourceRootCandidate(
            selected.with_name("env"), RootCandidateKind.ENVIRONMENT_OVERRIDE
        ),
    )
    assert store.resolve(override) == override


@pytest.mark.parametrize(
    "kind", ["grant", "revoked", "evidence", *[status.value for status in ImportStatus]]
)
def test_any_prior_source_state_blocks_new_candidate(setup, kind):
    store, catalog, selected, database, grants, jobs, evidence = setup
    reg = catalog["cursor"]
    if kind in {"grant", "revoked"}:
        grants.grant(
            user_id="local",
            source_id="cursor",
            root=selected,
            candidate_kind=RootCandidateKind.DEFAULT,
        )
        if kind == "revoked":
            grants.revoke(user_id="local", source_id="cursor")
    elif kind == "evidence":
        evidence.put(
            EvidenceInput(
                user_id="local",
                source_id="cursor",
                conversation_id="c",
                message_id="m",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim="fixture",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )
    else:
        job = jobs.create(user_id="local", source_id="cursor")
        with sqlite3.connect(database) as db:
            db.execute("UPDATE import_jobs SET status=? WHERE job_id=?", (kind, job.job_id))
    assert store.change_reason(reg) == "source_candidate_locked"
    with pytest.raises(SourceCandidateError, match="source_candidate_locked"):
        save(store, reg, selected)
    assert store.resolve(reg).candidate.revision == 0


def test_confirmation_required_and_unrelated_source_does_not_block(setup):
    store, catalog, selected, _, grants, *_ = setup
    grants.grant(
        user_id="local", source_id="codex", root=selected, candidate_kind=RootCandidateKind.DEFAULT
    )
    with pytest.raises(SourceCandidateError, match="source_candidate_confirmation_required"):
        store.select(
            catalog["cursor"],
            selected,
            expected_revision=0,
            expected_root_id=source_root_identity(selected),
            user_confirmed=False,
        )
    assert save(store, catalog["cursor"], selected).candidate_revision == 1
