"""Switching the embedding model, and merging stray spaces, without losing a memory.

The switch worker, the startup merge and the registry, driven against an
in-memory re-embed backend (`tests/embedding_fakes.py`). The SDK's own
re-embed functions are exercised in `test_embedding_switch_sdk_adapter.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from one_memory.embedding_spaces import (
    EmbeddingSpaceStore,
    derived_space,
    probe_width_allowed,
)
from one_memory.embedding_switch import (
    EmbeddingSwitchError,
    EmbeddingSwitchService,
)
from one_memory.hy_sdk_runtime import (
    build_sdk_config,
    builtin_retrieval_snapshot,
)
from one_memory.hy_sdk_types import HySdkRuntimeError
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from tests.embedding_fakes import Crash, FakeReembedBackend

pytestmark = pytest.mark.anyio

ENDPOINT = "https://models.example/v1"


class Home:
    """One MemHub home: profiles, registry, an in-memory vector store, a switch service."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database = root / "one-memory.sqlite3"
        self.secrets = InMemorySecretStore()
        self.profiles = ModelProfileStore(self.database)
        self.spaces = EmbeddingSpaceStore(self.database)
        self.backend = FakeReembedBackend()
        self.commits: list[str] = []

    def service(self, *, hold: bool = False, **options: object) -> EmbeddingSwitchService:
        """`hold`: keep the startup gate closed until `recover()`, as Core does."""

        async def on_commit(job: object) -> None:
            self.commits.append(job.job_id)  # type: ignore[attr-defined]

        service = EmbeddingSwitchService(
            database_path=self.database,
            data_dir=self.root,
            profiles=self.profiles,
            spaces=self.spaces,
            secrets=self.secrets,
            backend=self.backend,
            on_commit=on_commit,
            **options,  # type: ignore[arg-type]
        )
        if not hold:
            service.release_scan_hold()
        return service

    def profile(
        self,
        *,
        model: str | None = "bge-m3",
        dims: int | None = 1024,
        endpoint: str = ENDPOINT,
        name: str = "custom",
        activate: bool = False,
        probed: bool = True,
    ):
        remote = model is not None
        created = self.profiles.create(
            ModelProfileInput(
                name=name,
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                base_url=endpoint,
                model="chat",
                embedding_provider_kind=ProviderKind.OPENAI_COMPATIBLE if remote else None,
                embedding_base_url=endpoint if remote else None,
                embedding_model=model,
                embedding_dimensions=dims if remote else None,
                api_key=SecretStr("sk-test"),
            ),
            self.secrets,
        )
        if probed:
            self.profiles.record_capabilities(
                created.profile_id,
                expected_revision=created.revision,
                capabilities=ModelCapabilities(
                    endpoint_reachable=True,
                    model_available=True,
                    structured_output=True,
                    embedding_available=True,
                ),
                secrets=self.secrets,
            )
        if activate:
            self.profiles.activate(created.profile_id, self.secrets)
        return self.profiles.snapshot(created.profile_id, created.revision)

    def bootstrap(self):
        active = self.profiles.active_identity()
        return self.spaces.bootstrap(
            self.profiles.snapshot(*active) if active is not None else None
        )


def legacy_name(provider: str, endpoint: str | None, model: str, dims: object) -> str:
    """The four-attribute hash every build before the registry used, spelled out."""
    identity = "\0".join((provider, endpoint or "local", model, str(dims or "auto")))
    return "memhub_" + hashlib.sha256(identity.encode()).hexdigest()[:16]


async def run(service: EmbeddingSwitchService, job_id: str) -> None:
    await service.run(job_id)


# ----------------------------------------------------------------------
# Registry bootstrap: existing homes keep their collections byte-for-byte
# ----------------------------------------------------------------------


def test_a_builtin_home_registers_its_existing_collection(tmp_path: Path) -> None:
    home = Home(tmp_path)
    space = home.bootstrap()

    assert space.collection_base == legacy_name("local_onnx", None, "bge-small-zh-v1.5", 512)
    assert space.name_dims == 512
    assert space.naming == "legacy"
    assert space.graph_dir == "graph"
    config = build_sdk_config(builtin_retrieval_snapshot(), space, api_key=None, data_dir=tmp_path)
    # Exactly what the pre-registry build opened.
    assert config.vector_store.collection_name == space.collection_base
    assert config.vector_store.embedding_dims == 512
    assert config.graph_store.db_path.endswith("governed-v2/graph")
    assert (
        build_sdk_config(
            builtin_retrieval_snapshot(), api_key=None, data_dir=tmp_path
        ).vector_store.collection_name
        == space.collection_base
    )


def test_a_custom_home_registers_its_four_attribute_name(tmp_path: Path) -> None:
    home = Home(tmp_path)
    profile = home.profile(activate=True)
    space = home.bootstrap()

    assert space.collection_base == legacy_name("openai_compatible", ENDPOINT, "bge-m3", 1024)
    assert (space.model, space.dims, space.name_dims) == ("bge-m3", 1024, 1024)
    assert space.profile_id == profile.profile_id
    config = build_sdk_config(profile, space, api_key="sk-test", data_dir=tmp_path)
    assert config.vector_store.collection_name == space.collection_base
    assert config.embedder.embedding_dims == 1024
    # Bootstrap is once: a second start finds the same row.
    assert home.bootstrap() == space


def test_an_auto_width_home_keeps_its_unsuffixed_name_and_records_the_width(
    tmp_path: Path,
) -> None:
    home = Home(tmp_path)
    profile = home.profile(dims=None, activate=True)
    space = home.bootstrap()

    assert space.collection_base == legacy_name("openai_compatible", ENDPOINT, "bge-m3", None)
    assert space.name_dims is None and space.dims is None
    config = build_sdk_config(profile, space, api_key="sk-test", data_dir=tmp_path)
    assert config.vector_store.embedding_dims is None  # no `_<dims>` suffix
    assert config.embedder.embedding_dims is None  # no `dimensions` request field

    # The probe measured 1024 and nothing was stored yet: a relabel.
    with home.spaces.transaction() as connection:
        assert probe_width_allowed(connection, profile.profile_id, 1024, data_dir=tmp_path)
    relabelled = home.spaces.require_live()
    assert relabelled.dims == 1024
    assert relabelled.collection_base == space.collection_base
    assert relabelled.name_dims is None
    measured = profile.model_copy(update={"embedding_dimensions": 1024})
    config = build_sdk_config(measured, relabelled, api_key="sk-test", data_dir=tmp_path)
    assert config.vector_store.collection_name == space.collection_base
    assert config.vector_store.embedding_dims is None


def test_a_profile_whose_embedding_is_not_the_space_s_never_opens_a_new_collection(
    tmp_path: Path,
) -> None:
    home = Home(tmp_path)
    home.profile(activate=True)
    space = home.bootstrap()
    other = home.profile(model="text-embedding-v3", dims=768, name="other")

    with pytest.raises(HySdkRuntimeError, match="embedding_space_mismatch"):
        build_sdk_config(other, space, api_key="sk-test", data_dir=tmp_path)


def test_the_memory_mode_never_changes_a_collection_name(tmp_path: Path) -> None:
    home = Home(tmp_path)
    profile = home.profile(activate=True)
    space = home.bootstrap()
    names = {
        build_sdk_config(
            profile, space, mode, api_key="sk-test", data_dir=tmp_path
        ).vector_store.collection_name
        for mode in (None, "lite", "pro", "ultra")
    }
    assert names == {space.collection_base}


# ----------------------------------------------------------------------
# B2: a probe on the live space's profile never moves the space
# ----------------------------------------------------------------------


def test_a_probe_measuring_another_width_on_the_active_profile_is_not_recorded(
    tmp_path: Path,
) -> None:
    home = Home(tmp_path)
    profile = home.profile(activate=True)
    home.bootstrap()

    with home.spaces.transaction() as connection:
        assert not probe_width_allowed(connection, profile.profile_id, 768, data_dir=tmp_path)
        assert probe_width_allowed(connection, profile.profile_id, 1024, data_dir=tmp_path)
    assert home.spaces.require_live().dims == 1024


def test_a_probe_on_another_profile_records_what_it_measured(tmp_path: Path) -> None:
    home = Home(tmp_path)
    home.profile(activate=True)
    home.bootstrap()
    other = home.profile(model="text-embedding-v3", dims=None, name="other")

    with home.spaces.transaction() as connection:
        assert probe_width_allowed(connection, other.profile_id, 768, data_dir=tmp_path)


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------


async def test_an_endpoint_only_change_needs_no_re_embed(tmp_path: Path) -> None:
    home = Home(tmp_path)
    active = home.profile(activate=True)
    live = home.bootstrap()
    home.backend.store.put(live.collection_base, 1024, {"m1": ("a", "local")})
    moved = home.profile(endpoint="https://proxy.example/v1", name="proxy")

    plan = await home.service().plan(moved.profile_id)

    assert plan.needed is False
    assert plan.reason == "same_embedding"
    # Same collections, reached through the new endpoint.
    config = build_sdk_config(moved, live, api_key="sk-test", data_dir=tmp_path)
    assert config.vector_store.collection_name == live.collection_base
    assert config.embedder.base_url == "https://proxy.example/v1"
    assert active.embedding_base_url != moved.embedding_base_url


async def test_a_generation_only_change_needs_no_re_embed(tmp_path: Path) -> None:
    home = Home(tmp_path)
    home.profile(model=None, activate=True)
    home.bootstrap()
    builtin_again = home.profile(model=None, name="other chat model")

    plan = await home.service().plan(builtin_again.profile_id)
    assert (plan.needed, plan.reason) == (False, "same_embedding")


async def test_an_empty_home_switches_without_a_job(tmp_path: Path) -> None:
    home = Home(tmp_path)
    old = home.bootstrap()
    target = home.profile()
    service = home.service()

    plan = await service.plan(target.profile_id)
    assert (plan.needed, plan.reason) == (False, "empty_home")

    space = await asyncio.to_thread(service.activate_empty_home, target.profile_id)
    assert space.state == "live"
    # The name every earlier build gives this profile: a downgrade still reads it.
    assert space.collection_base == legacy_name("openai_compatible", ENDPOINT, "bge-m3", 1024)
    assert home.profiles.active_identity() == (target.profile_id, target.revision)
    assert home.spaces.get(old.space_id).state == "dropped"
    assert service.jobs.open_job() is None


async def test_a_new_embedding_plans_every_vector_of_every_space(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = home.bootstrap()
    home.backend.store.put(live.collection_base, 512, {"a": ("one", "local"), "b": ("two", "bob")})
    home.backend.store.put(live.collection_base, 512, {"k": ("key", "local")}, role="coding_keys")
    stray_base = legacy_name("openai_compatible", ENDPOINT, "old-model", 768)
    home.backend.store.put(stray_base, 768, {"c": ("three", "carol")})
    target = home.profile()

    plan = await home.service().plan(target.profile_id)

    assert plan.needed and plan.reason == "embedding_identity_changed"
    assert plan.total_vectors == 4
    assert plan.users == 3
    assert [item.collection_base for item in plan.sources] == [live.collection_base, stray_base]
    assert plan.blockers == []
    assert plan.target.model == "bge-m3" and plan.target.dims == 1024


async def test_an_unprobed_target_blocks_the_plan(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = home.bootstrap()
    home.backend.store.put(live.collection_base, 512, {"a": ("one", "local")})
    target = home.profile(probed=False)

    plan = await home.service().plan(target.profile_id)

    assert plan.needed and "embedding_probe_required" in plan.blockers


async def test_an_sdk_without_re_embed_blocks_the_plan(tmp_path: Path, monkeypatch) -> None:
    home = Home(tmp_path)
    live = home.bootstrap()
    target = home.profile()
    home.backend.unavailable = "sdk_reembed_unavailable"
    # Without the SDK's scan, Chroma's own file says whether vectors exist.
    monkeypatch.setattr(
        "one_memory.sdk_vector_reattach.filled_collection_names",
        lambda _data_dir: (f"{live.collection_base}_512",),
    )

    plan = await home.service().plan(target.profile_id)

    assert plan.needed and "sdk_reembed_unavailable" in plan.blockers


# ----------------------------------------------------------------------
# The switch
# ----------------------------------------------------------------------


async def start(home: Home, service: EmbeddingSwitchService, target) -> str:
    plan = await service.plan(target.profile_id)
    with_worker = service.worker_enabled
    service.worker_enabled = False
    try:
        job = await service.start_switch(
            target.profile_id,
            expected_revision=target.revision,
            plan_token=plan.plan_token,
            requested_by="cli",
        )
    finally:
        service.worker_enabled = with_worker
    return job.job_id


def seed_builtin(home: Home) -> object:
    live = home.bootstrap()
    home.backend.store.put(
        live.collection_base,
        512,
        {"a": ("one", "local"), "b": ("two", "local"), "c": ("three", "hermes-1")},
        model="bge-small-zh-v1.5",
    )
    return live


async def test_a_switch_re_embeds_commits_and_activates_in_one_step(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    service = home.service()

    job_id = await start(home, service, target)
    assert not service.writes_open()
    await run(service, job_id)

    job = service.get_job(job_id)
    assert job.state == "done"
    assert job.progress.percent == 100.0
    new = home.spaces.require_live()
    assert new.space_id == job.target.space_id
    assert new.naming == "v2" and new.collection_base.startswith("memhub_e_")
    assert new.graph_dir == f"graph-{new.space_id[:8]}"
    records = home.backend.store.space(new.collection_base, 1024)["memory"]
    assert set(records) == {"a", "b", "c"}
    assert {record.model for record in records.values()} == {"bge-m3"}
    assert {record.width for record in records.values()} == {1024}
    old = home.spaces.get(live.space_id)
    assert old.state == "retired"
    keep = datetime.fromisoformat(old.keep_until) - datetime.fromisoformat(old.retired_at)
    assert keep == timedelta(days=14)
    # The source was never written.
    assert set(home.backend.store.space(live.collection_base, 512)["memory"]) == {"a", "b", "c"}
    assert home.profiles.active_identity() == (target.profile_id, target.revision)
    assert service.writes_open()
    assert home.commits == [job_id]


async def test_a_switch_folds_in_every_other_non_empty_space(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    stray = legacy_name("openai_compatible", ENDPOINT, "old-model", 768)
    home.backend.store.put(stray, 768, {"a": ("stale copy", "local"), "z": ("only here", "bob")})
    target = home.profile()
    service = home.service()

    job_id = await start(home, service, target)
    await run(service, job_id)

    assert service.get_job(job_id).state == "done"
    new = home.spaces.require_live()
    records = home.backend.store.space(new.collection_base, 1024)["memory"]
    assert set(records) == {"a", "b", "c", "z"}
    # The live space's record wins over the stray's.
    assert records["a"].document == "one"
    states = {space.collection_base: space.state for space in home.spaces.list_spaces()}
    assert states[stray] == "retired"
    assert states[live.collection_base] == "retired"


async def test_retired_spaces_are_never_folded_back_in(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    service = home.service()
    first = home.profile()
    await run(service, await start(home, service, first))
    # A memory deleted after the first switch must stay deleted.
    new = home.spaces.require_live()
    home.backend.store.collections[f"{new.collection_base}_1024"].pop("b")

    second = home.profile(model="text-embedding-v3", dims=768, name="second")
    job_id = await start(home, service, second)
    await run(service, job_id)

    latest = home.spaces.require_live()
    assert set(home.backend.store.space(latest.collection_base, 768)["memory"]) == {"a", "c"}


async def test_a_switch_back_gets_a_fresh_base_beside_the_kept_one(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    service = home.service()
    first = home.profile()
    await run(service, await start(home, service, first))
    kept = home.spaces.require_live()
    second = home.profile(model="text-embedding-v3", dims=768, name="second")
    await run(service, await start(home, service, second))
    again = home.profile(name="first again")

    job_id = await start(home, service, again)
    await run(service, job_id)

    latest = home.spaces.require_live()
    assert latest.collection_base == f"{kept.collection_base}_g1"
    assert home.spaces.get(kept.space_id).state == "retired"


async def test_a_second_switch_while_one_runs_is_refused(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    service = home.service()
    await start(home, service, home.profile())
    other = home.profile(model="text-embedding-v3", dims=768, name="other")
    plan = await service.plan(other.profile_id)

    assert "embedding_switch_running" in plan.blockers
    with pytest.raises(EmbeddingSwitchError, match="embedding_switch_running"):
        await service.start_switch(
            other.profile_id,
            expected_revision=other.revision,
            plan_token=plan.plan_token,
            requested_by="app",
        )


async def test_a_stale_plan_token_is_refused(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    service = home.service()
    plan = await service.plan(target.profile_id)
    home.backend.store.put(live.collection_base, 512, {"new": ("arrived", "local")})

    with pytest.raises(EmbeddingSwitchError, match="embedding_switch_plan_stale"):
        await service.start_switch(
            target.profile_id,
            expected_revision=target.revision,
            plan_token=plan.plan_token,
            requested_by="app",
        )


async def test_cancel_rolls_back_and_drops_the_partial_target(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    service = home.service()
    job_id = await start(home, service, target)

    def cancel_midway(_role: str, done: int) -> None:
        if done >= 2:
            service.cancel(job_id)

    home.backend.before_batch = cancel_midway
    await run(service, job_id)

    job = service.get_job(job_id)
    assert (job.state, job.error_code) == ("rolled_back", "embedding_switch_cancelled")
    assert home.spaces.require_live().space_id == live.space_id
    assert home.spaces.get(job.target.space_id).state == "dropped"
    assert not any(name.startswith("memhub_e_") for name in home.backend.store.collections)
    assert home.profiles.active_identity() is None
    assert service.writes_open()


async def test_a_width_mismatch_fails_before_writing_and_rolls_back(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    home.backend.returned_width = 768
    service = home.service()
    job_id = await start(home, service, home.profile())

    await run(service, job_id)

    job = service.get_job(job_id)
    assert (job.state, job.error_code) == ("rolled_back", "embedding_dimensions_mismatch")


async def test_a_lost_record_fails_verification(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    home.backend.lose_ids = {"c"}
    service = home.service()
    job_id = await start(home, service, home.profile())

    await run(service, job_id)

    job = service.get_job(job_id)
    assert (job.state, job.error_code) == ("rolled_back", "embedding_verification_failed")
    report = json.loads(service.jobs.get(job_id)["verification_json"])
    assert any("missing" in problem for problem in report["problems"])


async def test_a_target_edited_mid_switch_is_not_activated(tmp_path: Path) -> None:
    from one_memory.model_profiles import ModelProfilePatch

    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    service = home.service()
    job_id = await start(home, service, target)

    def edit(_role: str, done: int) -> None:
        if done == 0:
            home.profiles.update(
                target.profile_id,
                ModelProfilePatch(expected_revision=target.revision, name="renamed"),
                home.secrets,
            )

    home.backend.before_batch = edit
    await run(service, job_id)

    job = service.get_job(job_id)
    assert (job.state, job.error_code) == ("rolled_back", "target_profile_changed")
    assert home.spaces.require_live().space_id == live.space_id


async def test_writes_that_never_drain_roll_the_switch_back(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)

    async def busy() -> bool:
        return False

    service = home.service(drain_idle=busy, drain_timeout_s=0.2)
    job_id = await start(home, service, home.profile())

    await run(service, job_id)

    assert service.get_job(job_id).error_code == "writes_did_not_drain"


async def test_the_commit_is_one_transaction(tmp_path: Path, monkeypatch) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    service = home.service()
    job_id = await start(home, service, target)
    home.backend.before_batch = lambda *_: None
    # Run up to the commit, then fail inside its transaction.
    real_commit = service._commit_transaction

    def failing_activation(*_args, **_kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(
        "one_memory.embedding_switch.ModelProfileStore.activate_in_connection",
        staticmethod(failing_activation),
    )

    def commit_once(job: str) -> None:
        try:
            real_commit(job)
        finally:
            # Nothing of the transaction is visible.
            assert home.spaces.require_live().space_id == live.space_id
            assert home.spaces.get(service.jobs.get(job)["target_space_id"]).state == "building"
            assert service.jobs.get(job)["state"] == "committing"
            assert home.profiles.active_identity() is None

    service._commit_transaction = commit_once  # type: ignore[method-assign]
    await run(service, job_id)

    job = service.get_job(job_id)
    assert job.state == "rolled_back"
    assert home.spaces.require_live().space_id == live.space_id


# ----------------------------------------------------------------------
# Crash and resume, in every state
# ----------------------------------------------------------------------


@pytest.mark.parametrize("crash_at", ["preparing", "reembedding", "verifying", "committing"])
async def test_a_switch_resumes_after_a_crash_in_any_state(
    tmp_path: Path, crash_at: str, monkeypatch
) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    first = home.service()
    job_id = await start(home, first, target)

    if crash_at == "preparing":

        async def crash_idle() -> bool:
            raise Crash

        first.drain_idle = crash_idle
    elif crash_at == "reembedding":

        def crash_midway(_role: str, done: int) -> None:
            if done >= 2:
                raise Crash

        home.backend.before_batch = crash_midway
    elif crash_at == "verifying":

        async def crash_verify(*_args, **_kwargs):
            raise Crash

        monkeypatch.setattr(home.backend, "verify", crash_verify)
    else:

        def crash_commit(_job: str) -> None:
            raise Crash

        first._commit_transaction = crash_commit  # type: ignore[method-assign]

    with pytest.raises(Crash):
        await run(first, job_id)
    assert first.jobs.get(job_id)["state"] == crash_at
    monkeypatch.undo()
    home.backend.before_batch = lambda *_: None

    # The next process: the gate is closed before anything is decided.
    second = home.service(hold=True)
    assert not second.writes_open()
    await second.recover()
    await second.wait()

    job = second.get_job(job_id)
    assert job.state == "done"
    new = home.spaces.require_live()
    assert set(home.backend.store.space(new.collection_base, 1024)["memory"]) == {"a", "b", "c"}
    assert home.spaces.get(live.space_id).state == "retired"
    assert home.profiles.active_identity() == (target.profile_id, target.revision)
    assert second.writes_open()


async def test_a_crash_during_rollback_finishes_the_rollback(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    first = home.service()
    job_id = await start(home, first, home.profile())
    first.jobs.update(job_id, state="failed", error_code="embedding_service_failed")

    second = home.service()
    await second.recover()
    await second.wait()

    assert second.get_job(job_id).state == "rolled_back"
    assert home.spaces.require_live().space_id == live.space_id


async def test_an_app_update_mid_switch_resumes_on_the_new_build(
    tmp_path: Path, monkeypatch
) -> None:
    """The App may install while a switch runs; the next Core finishes it."""
    from one_memory import cli

    home = Home(tmp_path)
    seed_builtin(home)
    target = home.profile()
    old_build = home.service()
    job_id = await start(home, old_build, target)

    def crash_midway(_role: str, done: int) -> None:
        if done >= 2:
            raise Crash

    home.backend.before_batch = crash_midway
    with pytest.raises(Crash):
        await run(old_build, job_id)
    home.backend.before_batch = lambda *_: None

    # The upgrade asks whether the home needs migrating: the switch is
    # reported, never makes a migration required, and is left untouched.
    monkeypatch.setenv("ONE_MEMORY_DATA_DIR", str(tmp_path))
    from one_memory.sdk_data_plane_migration import SdkDataPlaneMigrationStore

    SdkDataPlaneMigrationStore(home.database)
    output = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=output) == 0
    status = json.loads(output.getvalue().strip().splitlines()[-1])
    assert status["embedding_switch"] == {
        "job_id": job_id,
        "kind": "switch",
        "state": "reembedding",
    }
    assert home.spaces.require_live().state == "live"

    new_build = home.service()
    await new_build.recover()
    await new_build.wait()

    assert new_build.get_job(job_id).state == "done"
    assert home.profiles.active_identity() == (target.profile_id, target.revision)


# ----------------------------------------------------------------------
# Multi-user
# ----------------------------------------------------------------------


async def test_every_user_is_re_embedded_and_counted(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = home.bootstrap()
    users = {
        "local": 3,
        "hermes-alice": 2,
        "hermes-bob": 1,
        # Revoked: no grant any more, but the data is still in the space.
        "hermes-revoked": 2,
    }
    records = {
        f"{user}-{index}": (f"memory {index} of {user}", user)
        for user, count in users.items()
        for index in range(count)
    }
    home.backend.store.put(live.collection_base, 512, records)
    home.backend.store.put(
        live.collection_base, 512, {"fact-1": ("fact", "hermes-alice")}, role="working_facts"
    )
    service = home.service()
    plan = await service.plan(home.profile().profile_id)
    assert plan.users == 4

    job_id = await start(home, service, home.profiles.snapshot(plan.target_profile_id, 1))
    await run(service, job_id)

    assert service.get_job(job_id).state == "done"
    report = json.loads(service.jobs.get(job_id)["verification_json"])
    assert report["per_user"] == {
        "local": 3,
        "hermes-alice": 3,
        "hermes-bob": 1,
        "hermes-revoked": 2,
    }
    new = home.spaces.require_live()
    assert set(home.backend.store.space(new.collection_base, 1024)["working_facts"]) == {"fact-1"}


# ----------------------------------------------------------------------
# Startup scan and merge
# ----------------------------------------------------------------------


async def test_startup_merges_a_stray_512_space_into_a_1024_live_space(tmp_path: Path) -> None:
    home = Home(tmp_path)
    profile = home.profile(activate=True)
    live = home.bootstrap()
    home.backend.store.put(
        live.collection_base,
        1024,
        {"shared": ("live version", "local"), "l1": ("only live", "local")},
        model="bge-m3",
    )
    stray = legacy_name("local_onnx", None, "bge-small-zh-v1.5", 512)
    home.backend.store.put(
        stray,
        512,
        {"shared": ("stale builtin copy", "local"), "s1": ("only stray", "hermes-1")},
        model="bge-small-zh-v1.5",
    )
    service = home.service(hold=True)

    assert not service.writes_open()  # held until the scan decides
    await service.recover()
    job = service.current()["job"]
    assert job is not None and job["kind"] == "merge"
    assert not service.writes_open()
    await service.wait()

    finished = service.get_job(job["job_id"])
    assert finished.state == "done"
    merged = home.backend.store.space(live.collection_base, 1024)["memory"]
    assert set(merged) == {"shared", "l1", "s1"}
    assert merged["shared"].document == "live version"  # live wins
    assert merged["s1"].model == "bge-m3" and merged["s1"].width == 1024
    stray_space = next(
        space for space in home.spaces.list_spaces() if space.collection_base == stray
    )
    assert stray_space.state == "retired" and stray_space.keep_until
    # A merge never changes the live space or the active profile.
    assert home.spaces.require_live().space_id == live.space_id
    assert home.profiles.active_identity() == (profile.profile_id, profile.revision)
    assert service.writes_open()

    # The next start finds nothing to merge.
    again = home.service()
    await again.recover()
    assert again.current()["job"] is None
    assert again.writes_open()


async def test_a_stray_holding_nothing_unmerged_is_retired_without_a_job(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    stray = legacy_name("openai_compatible", ENDPOINT, "old-model", 768)
    home.backend.store.put(stray, 768, {"a": ("copy", "local")})
    service = home.service()

    await service.recover()

    assert service.current()["job"] is None
    states = {space.collection_base: space.state for space in home.spaces.list_spaces()}
    assert states == {live.collection_base: "live", stray: "retired"}
    assert service.writes_open()


async def test_a_merge_resumes_after_a_crash(tmp_path: Path) -> None:
    home = Home(tmp_path)
    live = seed_builtin(home)
    stray = legacy_name("openai_compatible", ENDPOINT, "old-model", 768)
    home.backend.store.put(stray, 768, {f"s{i}": (f"stray {i}", "local") for i in range(5)})
    first = home.service()

    def crash_midway(_role: str, done: int) -> None:
        if done >= 2:
            raise Crash

    home.backend.before_batch = crash_midway
    await first.recover()
    with pytest.raises(Crash):
        await first._task
    home.backend.before_batch = lambda *_: None

    second = home.service()
    await second.recover()
    await second.wait()

    assert set(home.backend.store.space(live.collection_base, 512)["memory"]) == {
        "a",
        "b",
        "c",
        *(f"s{i}" for i in range(5)),
    }


async def test_an_old_sdk_skips_the_scan_and_opens_the_gate(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    home.backend.unavailable = "sdk_reembed_unavailable"
    service = home.service()

    await service.recover()

    assert service.scan_state == "unavailable"
    assert service.writes_open()


# ----------------------------------------------------------------------
# Retention
# ----------------------------------------------------------------------


async def test_retired_spaces_are_dropped_after_14_days_and_never_the_live_one(
    tmp_path: Path,
) -> None:
    home = Home(tmp_path)
    old = seed_builtin(home)
    service = home.service()
    await run(service, await start(home, service, home.profile()))
    live = home.spaces.require_live()

    assert await service.sweep_retired(now=datetime.now(UTC) + timedelta(days=13)) == []
    dropped = await service.sweep_retired(now=datetime.now(UTC) + timedelta(days=15))

    assert dropped == [old.space_id]
    assert home.spaces.get(old.space_id).state == "dropped"
    assert not home.backend.store.space(old.collection_base, 512)
    assert home.spaces.require_live().space_id == live.space_id


async def test_cleanup_drops_only_a_retired_space(tmp_path: Path) -> None:
    home = Home(tmp_path)
    old = seed_builtin(home)
    service = home.service()
    with pytest.raises(EmbeddingSwitchError, match="embedding_space_not_retired"):
        await service.drop_space(old.space_id)
    await run(service, await start(home, service, home.profile()))

    dropped = await service.drop_space(old.space_id)

    assert dropped.state == "dropped"


def test_derived_space_is_the_profile_s_pre_registry_space() -> None:
    builtin = derived_space(builtin_retrieval_snapshot())
    assert builtin.profile_id is None
    assert builtin.collection_base == legacy_name("local_onnx", None, "bge-small-zh-v1.5", 512)


def test_the_probe_never_records_a_width_that_would_move_the_live_space(tmp_path: Path) -> None:
    from one_memory.model_probe import ModelProber, ProbeCode, ProbeStage

    home = Home(tmp_path)
    profile = home.profile(activate=True)
    live = home.bootstrap()
    prober = ModelProber(store=home.profiles, secrets=home.secrets)
    current = home.profiles.require(profile.profile_id, home.secrets)

    def measured(width: int) -> tuple[ProbeStage, ...]:
        return (
            ProbeStage(
                stage="embedding",
                ok=True,
                code=ProbeCode.OK,
                latency_ms=1,
                embedding_dimensions=width,
            ),
        )

    after, switch_required = prober._record_embedding_width(current, measured(768))

    assert switch_required
    assert (after.embedding_dimensions, after.revision) == (1024, current.revision)
    assert home.spaces.require_live() == live
    assert home.profiles.active_identity() == (profile.profile_id, profile.revision)


def test_an_auto_width_profile_records_its_measured_width_without_renaming(
    tmp_path: Path,
) -> None:
    from one_memory.model_probe import ModelProber, ProbeCode, ProbeStage

    home = Home(tmp_path)
    profile = home.profile(dims=None, activate=True)
    live = home.bootstrap()
    prober = ModelProber(store=home.profiles, secrets=home.secrets)
    current = home.profiles.require(profile.profile_id, home.secrets)
    stage = ProbeStage(
        stage="embedding", ok=True, code=ProbeCode.OK, latency_ms=1, embedding_dimensions=1024
    )

    after, switch_required = prober._record_embedding_width(current, (stage,))

    assert not switch_required and after.embedding_dimensions == 1024
    relabelled = home.spaces.require_live()
    assert (relabelled.space_id, relabelled.collection_base, relabelled.name_dims) == (
        live.space_id,
        live.collection_base,
        None,
    )
    assert relabelled.dims == 1024
    snapshot = home.profiles.snapshot(*home.profiles.active_identity())
    config = build_sdk_config(snapshot, relabelled, api_key="sk-test", data_dir=tmp_path)
    assert config.vector_store.collection_name == live.collection_base
    assert config.vector_store.embedding_dims is None


async def test_ultra_mode_builds_a_new_graph_at_the_target_width(tmp_path: Path) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    graph = tmp_path / "hy-memory" / "governed-v2" / "graph"
    graph.mkdir(parents=True)
    quiesced: list[str] = []

    async def quiesce():
        quiesced.append("closed")
        return lambda: quiesced.append("released")

    service = home.service(graph_enabled=lambda: True, quiesce_clients=quiesce)
    target = home.profile()
    plan = await service.plan(target.profile_id)
    assert plan.graph_vectors == 3

    job_id = await start(home, service, target)
    await run(service, job_id)

    assert service.get_job(job_id).state == "done"
    new = home.spaces.require_live()
    built = tmp_path / "hy-memory" / "governed-v2" / new.graph_dir
    assert home.backend.graphs_built == [built]
    assert (built / "width").read_text() == "1024"
    assert graph.exists()  # the source graph is untouched, kept with the old space
    # Once to copy the graph, once to verify it.
    assert quiesced == ["closed", "released", "closed", "released"]


async def test_a_job_another_live_core_still_beats_for_is_not_taken_over(tmp_path: Path) -> None:
    import os

    home = Home(tmp_path)
    seed_builtin(home)
    first = home.service()
    job_id = await start(home, first, home.profile())
    # The parent process is alive and "beat" just now.
    first.jobs.update(job_id, owner_pid=os.getppid(), heartbeat_at=datetime.now(UTC).isoformat())

    second = home.service(hold=True)
    await second.recover()

    assert second._task is None
    assert not second.writes_open()
    assert second.jobs.get(job_id)["state"] == "preparing"


def test_a_live_space_whose_profile_was_deleted_says_so_on_read(tmp_path: Path) -> None:
    from one_memory.sdk_read_handle import ActiveProfileReadHandle

    home = Home(tmp_path)
    profile = home.profile(activate=True)
    home.bootstrap()
    home.profiles.delete(profile.profile_id, home.secrets)
    handle = ActiveProfileReadHandle(
        profiles=home.profiles, secrets=home.secrets, data_dir=tmp_path, spaces=home.spaces
    )

    with pytest.raises(HySdkRuntimeError, match="embedding_space_profile_missing"):
        handle._resolve()


async def test_legacy_collections_block_a_switch_until_they_are_reattached(
    tmp_path: Path, monkeypatch
) -> None:
    home = Home(tmp_path)
    seed_builtin(home)
    target = home.profile()
    monkeypatch.setattr(
        "one_memory.sdk_vector_reattach.legacy_vector_collections",
        lambda _data_dir, _profile: (("one_memory_abc", "memhub_abc"),),
    )

    plan = await home.service().plan(target.profile_id)

    assert "embedding_switch_requires_migration" in plan.blockers


def test_sdk_migrate_leaves_an_unfinished_switch_alone(tmp_path: Path) -> None:
    """The upgrade path runs the real migration entry on a home mid-switch."""
    from one_memory.sdk_migration_runner import run_startup_migration
    from one_memory.target_schema import bootstrap_fresh_management_schema

    assert bootstrap_fresh_management_schema(tmp_path / "one-memory.sqlite3")
    home = Home(tmp_path)
    live = seed_builtin(home)
    target = home.profile()
    service = home.service()
    job_id = asyncio.run(start(home, service, target))
    service.jobs.update(job_id, state="reembedding")
    before = (home.spaces.list_spaces(), dict(service.jobs.get(job_id)))

    result = run_startup_migration(
        data_dir=tmp_path,
        runtime_build="99",
        sdk_version="2.0.10",
        namespace="governed-v2",
    )

    assert result.state == "not_required"
    assert (home.spaces.list_spaces(), dict(service.jobs.get(job_id))) == before
    assert home.spaces.require_live().space_id == live.space_id
