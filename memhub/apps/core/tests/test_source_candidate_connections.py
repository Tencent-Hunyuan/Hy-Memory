import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from one_memory.imports.access import SourceAccessError, source_root_identity
from one_memory.imports.candidates import SourceCandidateError, SourceCandidateStore
from one_memory.imports.connections import ConnectionIntentError, SourceConnectionStore
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.sources.registry import RootCandidateKind, build_source_catalog


@pytest.fixture
def setup(tmp_path):
    tmp_path = tmp_path.resolve()
    home = tmp_path / "home"
    default = home / ".claude/projects"
    default.mkdir(parents=True)
    custom = home / "selected"
    custom.mkdir()
    database = tmp_path / "data.sqlite"
    grants = SourceGrantStore(database, home=home)
    jobs = ImportJobStore(database)
    candidates = SourceCandidateStore(database, home=home)
    connections = SourceConnectionStore(grants, jobs, home=home, candidates=candidates)
    reg = build_source_catalog(home=home, environment={})["claude-code"]
    return candidates, connections, reg, custom, database


def select(store, reg, path, revision=0):
    return store.select(
        reg,
        path,
        expected_revision=revision,
        expected_root_id=source_root_identity(path),
        user_confirmed=True,
    )


def options(reg, root, request="confirmed"):
    return dict(
        request_id=request,
        registrations=(reg,),
        source_roots={reg.descriptor.source_id: source_root_identity(root)},
        confirm_overrides=root != reg.candidate.path or reg.candidate.revision > 0,
        full_rescan=False,
    )


def test_empty_candidate_revisions_preserve_default_fingerprint(setup):
    _, store, reg, _, database = setup
    kwargs = options(reg, reg.candidate.path)
    first = store.connect(**kwargs)
    assert store.connect(**kwargs, source_candidate_revisions={}) == first
    expected = hashlib.sha256(
        json.dumps(
            {
                "sources": ("claude-code",),
                "roots": kwargs["source_roots"],
                "overrides": False,
                "full_rescan": False,
                "onboarding": False,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with sqlite3.connect(database) as db:
        assert (
            db.execute("SELECT request_hash FROM source_connection_intents").fetchone()[0]
            == expected
        )


@pytest.mark.parametrize("revisions", [None, {}, {"claude-code": 0}, {"claude-code": 2}])
def test_persisted_candidate_requires_exact_revision(setup, revisions):
    candidates, store, reg, custom, _ = setup
    select(candidates, reg, custom)
    result = store.connect(**options(reg, custom), source_candidate_revisions=revisions)
    assert result.accepted_source_ids == ()
    assert result.failed_sources == {"claude-code": "source_candidate_revision_conflict"}
    assert store.jobs.list_jobs() == []
    assert store.grants.receipt(user_id="local", source_id="claude-code") is None


def test_connection_resolves_candidate_in_transaction_and_prior_replay_never_regrants(setup):
    candidates, store, reg, custom, database = setup
    select(candidates, reg, custom)
    kwargs = {**options(reg, custom), "source_candidate_revisions": {"claude-code": 1}}
    result = store.connect(**kwargs)
    assert result.accepted_source_ids == ("claude-code",)
    assert store.grants.authorized_root(user_id="local", source_id="claude-code").root == custom
    store.jobs.claim(result.jobs[0].job_id)
    store.jobs.transition(result.jobs[0].job_id, ImportStatus.COMPLETED)
    before = store.grants.revoke(user_id="local", source_id="claude-code")
    replay = store.connect(**kwargs)
    assert replay.jobs[0].job_id == result.jobs[0].job_id
    assert store.grants.receipt(user_id="local", source_id="claude-code") == before
    with pytest.raises(ConnectionIntentError, match="source_intent_conflict"):
        store.connect(**{**kwargs, "source_candidate_revisions": {"claude-code": 2}})
    assert len(store.jobs.list_jobs()) == 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT revision FROM source_candidates").fetchone()[0] == 1


def test_stale_resolved_registration_cannot_bypass_aba_revision(setup):
    candidates, store, reg, a, _ = setup
    b = a.with_name("second")
    b.mkdir()
    select(candidates, reg, a)
    stale = candidates.resolve(reg)
    select(candidates, reg, b, 1)
    select(candidates, reg, a, 2)
    result = store.connect(**options(stale, a), source_candidate_revisions={"claude-code": 1})
    assert result.failed_sources == {"claude-code": "source_candidate_revision_conflict"}
    assert store.jobs.list_jobs() == []


def test_save_and_connect_serialize_on_the_same_sqlite_writer(setup, monkeypatch):
    candidates, store, reg, a, _ = setup
    b = a.with_name("second")
    b.mkdir()
    select(candidates, reg, a)
    entered, release = Event(), Event()
    resolve = candidates.resolve

    def held(registration, *, user_id="local", db=None):
        assert db is not None and db.in_transaction
        value = resolve(registration, user_id=user_id, db=db)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(candidates, "resolve", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        connecting = pool.submit(
            store.connect, **options(reg, a), source_candidate_revisions={"claude-code": 1}
        )
        assert entered.wait(5)
        saving = pool.submit(select, candidates, reg, b, 1)
        release.set()
        assert connecting.result(timeout=5).accepted_source_ids == ("claude-code",)
        with pytest.raises(SourceCandidateError, match="source_candidate_locked"):
            saving.result(timeout=5)
    assert resolve(reg).candidate.path == a


def test_legacy_grant_rejects_persisted_candidate_but_confirmed_connect_succeeds(setup):
    candidates, store, reg, selected, _ = setup
    select(candidates, reg, selected)
    with pytest.raises(SourceAccessError, match="source_candidate_confirmation_required"):
        store.grants.grant(
            user_id="local",
            source_id="claude-code",
            root=reg.candidate.path,
            candidate_kind=RootCandidateKind.DEFAULT,
        )
    assert store.grants.receipt(user_id="local", source_id="claude-code") is None
    denied = store.connect(
        **{**options(reg, selected, "no-extra-confirm"), "confirm_overrides": False},
        source_candidate_revisions={"claude-code": 1},
    )
    assert denied.failed_sources == {"claude-code": "environment_override_confirmation_required"}
    allowed = store.connect(
        **options(reg, selected),
        source_candidate_revisions={"claude-code": 1},
    )
    assert allowed.accepted_source_ids == ("claude-code",)


@pytest.mark.parametrize("candidate_table", [False, True])
def test_legacy_grant_without_candidate_retains_previous_behavior(tmp_path, candidate_table):
    home = tmp_path.resolve() / "home"
    source = home / ".claude/projects"
    source.mkdir(parents=True)
    database = tmp_path / "legacy.sqlite"
    grants = SourceGrantStore(database, home=home)
    if candidate_table:
        SourceCandidateStore(database, home=home)
    granted = grants.grant(
        user_id="local",
        source_id="claude-code",
        root=source,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    assert granted.revision == 1
