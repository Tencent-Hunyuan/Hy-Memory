"""Active credentials are read once per call, never reused after a refusal."""

import asyncio
import threading

import pytest
from pydantic import SecretStr

from one_memory.background_work import shutdown_bulk_pool
from one_memory.hy_memory_bridge import HyMemoryBridgeError
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryGovernanceStore
from one_memory.memory_processing import MemoryProcessingSupervisor
from one_memory.memory_tasks import MemoryTaskStore
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfilePatch,
    ModelProfileStore,
)
from one_memory.secrets import InMemorySecretStore, SecretStoreUnavailableError


class CountingSecrets(InMemorySecretStore):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.denied = False
        self.after_read = None

    def has(self, secret_ref):
        # The OS store's has() resolves the secret; the in-memory default does not.
        return self.resolve(secret_ref) is not None

    def resolve(self, secret_ref):
        self.reads.append((secret_ref, threading.get_ident()))
        if self.denied:
            raise SecretStoreUnavailableError("synthetic reader denial, must not leak")
        value = super().resolve(secret_ref)
        callback, self.after_read = self.after_read, None
        if callback is not None:
            callback()
        return value


def create_profile(profiles, secrets, *, keyed=True):
    profile = profiles.create(
        ModelProfileInput(
            name="Synthetic credentials",
            provider_kind="openai_compatible" if keyed else "ollama",
            base_url="http://127.0.0.1:1/v1",
            model="synthetic-model",
            api_key=SecretStr("synthetic-credential") if keyed else None,
        ),
        secrets,
    )
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    return profiles.activate(profile.profile_id, secrets)


@pytest.fixture
def setup(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    secrets = CountingSecrets()
    profiles = ModelProfileStore(database)
    profile = create_profile(profiles, secrets)
    supervisor = MemoryProcessingSupervisor(
        tasks=MemoryTaskStore(database),
        profiles=profiles,
        secrets=secrets,
        evidence=EvidenceStore(database),
        governance=MemoryGovernanceStore(database),
        data_dir=tmp_path,
    )
    secrets.reads.clear()
    try:
        yield supervisor, profiles, secrets, profile
    finally:
        shutdown_bulk_pool()


def test_resolves_active_immutable_snapshot_once_off_loop(setup):
    supervisor, profiles, secrets, profile = setup
    expected = profiles.snapshot(profile.profile_id, profile.revision)
    caller = threading.get_ident()

    result = asyncio.run(supervisor._active_credentials())

    assert result == (expected, "synthetic-credential")
    assert secrets.reads == [(profiles.secret_ref(profile.profile_id), secrets.reads[0][1])]
    assert secrets.reads[0][1] != caller
    assert supervisor.tasks.list() == []
    assert supervisor._bridges == {}


@pytest.mark.parametrize("concurrent", [False, True])
def test_each_call_resolves_again_without_cross_request_cache(setup, concurrent):
    supervisor, _profiles, secrets, _profile = setup

    async def scenario():
        if concurrent:
            return await asyncio.gather(*(supervisor._active_credentials() for _ in range(4)))
        return [await supervisor._active_credentials() for _ in range(4)]

    results = asyncio.run(scenario())

    assert all(result is not None and result[1] == "synthetic-credential" for result in results)
    assert len(secrets.reads) == 4


@pytest.mark.parametrize("failure", ["missing", "denied"])
def test_prior_success_cannot_mask_current_reader_refusal(setup, failure):
    supervisor, profiles, secrets, profile = setup

    async def scenario():
        assert (await supervisor._active_credentials())[1] == "synthetic-credential"
        secrets.reads.clear()
        if failure == "missing":
            secrets.delete(profiles.secret_ref(profile.profile_id))
        else:
            secrets.denied = True
        code = "model_secret_unavailable" if failure == "missing" else "secure_storage_unavailable"
        with pytest.raises(HyMemoryBridgeError, match=f"^{code}$"):
            await supervisor._active_credentials()
        assert len(secrets.reads) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["deactivate", "switch", "revision"])
def test_profile_change_during_secret_read_rejects_old_credentials(setup, change):
    supervisor, profiles, secrets, profile = setup
    other = create_profile(profiles, secrets)
    profiles.activate(profile.profile_id, secrets)
    secrets.reads.clear()

    def change_identity():
        # Concurrent committed identity change while the original reader is in flight.
        with profiles._connect(writing=True) as db:
            if change == "revision":
                db.execute(
                    "UPDATE model_profiles SET revision=revision+1 WHERE profile_id=?",
                    (profile.profile_id,),
                )
            else:
                db.execute("UPDATE model_profiles SET is_active=0")
                if change == "switch":
                    db.execute(
                        "UPDATE model_profiles SET is_active=1 WHERE profile_id=?",
                        (other.profile_id,),
                    )

    secrets.after_read = change_identity
    with pytest.raises(HyMemoryBridgeError, match="^model_profile_changed$"):
        asyncio.run(supervisor._active_credentials())
    assert len(secrets.reads) == 1
    assert supervisor._bridges == {}


def test_rotation_between_calls_returns_new_snapshot_and_secret(setup):
    supervisor, profiles, secrets, profile = setup
    before = asyncio.run(supervisor._active_credentials())
    updated = profiles.update(
        profile.profile_id,
        ModelProfilePatch(expected_revision=profile.revision, api_key=SecretStr("synthetic-new")),
        secrets,
    )
    assert updated.is_active is False
    assert updated.capabilities == ModelCapabilities()
    assert profiles.active_identity() is None
    secrets.reads.clear()

    assert asyncio.run(supervisor._active_credentials()) is None
    assert secrets.reads == []
    assert supervisor.tasks.list() == []
    assert supervisor._bridges == {}

    # Synthetic probe evidence and explicit activation belong to the new revision.
    profiles.record_capabilities(
        updated.profile_id,
        expected_revision=updated.revision,
        capabilities=profile.capabilities,
        secrets=secrets,
    )
    updated = profiles.activate(updated.profile_id, secrets)
    assert updated.is_active is True
    secrets.reads.clear()

    after = asyncio.run(supervisor._active_credentials())

    assert before[0].revision == 1 and before[1] == "synthetic-credential"
    assert after == (profiles.snapshot(updated.profile_id, updated.revision), "synthetic-new")
    assert after[0].revision == 2
    assert len(secrets.reads) == 1


def test_no_active_profile_does_not_read_credentials(setup):
    supervisor, profiles, secrets, _profile = setup
    with profiles._connect(writing=True) as db:
        db.execute("UPDATE model_profiles SET is_active=0")

    assert asyncio.run(supervisor._active_credentials()) is None
    assert secrets.reads == []


def test_keyless_profile_does_not_require_or_resolve_a_secret(setup):
    supervisor, profiles, secrets, _profile = setup
    keyless = create_profile(profiles, secrets, keyed=False)
    secrets.reads.clear()

    result = asyncio.run(supervisor._active_credentials())

    assert result == (profiles.snapshot(keyless.profile_id, keyless.revision), None)
    assert result[0].has_api_key is False
    assert secrets.reads == []
