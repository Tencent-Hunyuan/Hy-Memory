"""A task refused before it was sent must come back, not stay unknown forever."""

from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryGovernanceStore
from one_memory.memory_tasks import MemoryTaskKind, MemoryTaskStatus, MemoryTaskStore
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore


def _stores(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    evidence = EvidenceStore(database)
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    governance = MemoryGovernanceStore(database)
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            model="hy-bot-phi4",
            embedding_model="nomic-embed-text",
            embedding_base_url="http://127.0.0.1:11434",
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
    profile = profiles.activate(profile.profile_id, secrets)
    return evidence, profiles, tasks, governance, secrets, profile


def test_a_transport_refusal_is_retryable_not_unknown(tmp_path):
    """D85: the request never left, so nothing about it is unknown.

    `_CLAIMABLE_TASK_STATUSES` has no UNKNOWN, so a task recorded that way is
    never picked up again: on Kristin's machine 85 of them sat there after the
    model was swapped, and those conversations never got a memory.
    """
    _evidence, _profiles, tasks, _governance, _secrets, profile = _stores(tmp_path)
    task = tasks.create(
        request_id="transport-refused",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    _claimed, token = tasks.claim(task.task_id)

    finished = tasks.mark_unknown(
        task.task_id,
        runner_token=token,
        error_code="model_content_transport_headers_mismatch",
    )

    assert finished.status is MemoryTaskStatus.FAILED
    assert finished.last_error_code == "model_content_transport_headers_mismatch"


def test_opening_the_store_requeues_stranded_transport_refusals(tmp_path):
    """D85's second half: the ones already stranded by an older build.

    Reopening the store must put them back, with their attempts cleared so the
    first try is not already spent.
    """
    database = tmp_path / "one-memory.sqlite3"
    _evidence, _profiles, tasks, _governance, _secrets, profile = _stores(tmp_path)
    task = tasks.create(
        request_id="transport-refused",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    _claimed, token = tasks.claim(task.task_id)
    tasks.mark_unknown(
        task.task_id,
        runner_token=token,
        error_code="model_content_transport_headers_mismatch",
    )
    # Simulate the stranded row an older build left behind.
    with tasks._connect(writing=True) as connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=3 WHERE task_id=?",
            (MemoryTaskStatus.UNKNOWN, task.task_id),
        )

    reopened = MemoryTaskStore(database)

    restored = reopened.get(task.task_id)
    assert restored.status is MemoryTaskStatus.PENDING
    assert restored.attempts == 0


def test_a_network_failure_stays_unknown(tmp_path):
    """The code that can arrive after HY wrote must not be requeued blind."""
    _evidence, _profiles, tasks, _governance, _secrets, profile = _stores(tmp_path)
    task = tasks.create(
        request_id="transport-network",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    _claimed, token = tasks.claim(task.task_id)

    finished = tasks.mark_unknown(
        task.task_id,
        runner_token=token,
        error_code="model_content_transport_request_failed",
    )

    assert finished.status is MemoryTaskStatus.UNKNOWN


def test_a_failure_after_the_send_stays_unknown_everywhere(tmp_path):
    """D88: codes that can arrive after HY wrote must survive both paths.

    `mark_unknown` and the startup migration agree, so a build that starts
    recording them correctly cannot have its older rows requeued behind its
    back.
    """
    _evidence, _profiles, tasks, _governance, _secrets, profile = _stores(tmp_path)
    for code in (
        "model_content_transport_request_failed",
        "model_content_transport_lease_denied_hy_store_write_mismatch",
        "model_content_transport_redirect",
        "model_content_transport_budget_exceeded",
    ):
        task = tasks.create(
            request_id=f"after-send:{code}",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref="evidence-1",
            profile=profile,
        )
        _claimed, token = tasks.claim(task.task_id)
        finished = tasks.mark_unknown(task.task_id, runner_token=token, error_code=code)
        assert finished.status is MemoryTaskStatus.UNKNOWN, code

        # And the migration must not move it either.
        with tasks._connect(writing=True) as connection:
            connection.execute(
                "UPDATE memory_tasks SET status=? WHERE task_id=?",
                (MemoryTaskStatus.UNKNOWN, task.task_id),
            )
        assert tasks.migrate_unknown_transport_refusals() == 0
        assert tasks.get(task.task_id).status is MemoryTaskStatus.UNKNOWN, code


def test_the_migration_reaches_the_refusal_beyond_one_batch(tmp_path):
    """D88: the filter belongs in the query, not after a blind LIMIT.

    With more unknown tasks than a batch, filtering in Python spends every
    pass on the first rows and never reaches the ones that qualify.
    """
    _evidence, _profiles, tasks, _governance, _secrets, profile = _stores(tmp_path)
    seed = tasks.create(
        request_id="seed",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    _claimed, token = tasks.claim(seed.task_id)
    tasks.mark_unknown(
        seed.task_id,
        runner_token=token,
        error_code="model_content_transport_headers_mismatch",
    )
    # Far more unknown rows than one batch, none of them qualifying; the
    # target is inserted after them, so a pass that filters after a blind
    # LIMIT never reaches it.
    with tasks._connect(writing=True) as connection:
        template = connection.execute(
            "SELECT * FROM memory_tasks WHERE task_id=?", (seed.task_id,)
        ).fetchone()
        # Column names from the schema, not from iterating the row: the row
        # factory can yield integer positions alongside names.
        columns = [
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(memory_tasks)")
            if str(row["name"]) != "task_id"
        ]
        inserts = ",".join(columns)
        placeholders = ",".join("?" for _ in columns)
        rows = []
        for index in range(2100):
            values = []
            for column in columns:
                value = template[column]
                if column == "request_id":
                    value = f"req-noise{index}"
                elif column in {"status", "last_error_code"}:
                    value = (
                        MemoryTaskStatus.UNKNOWN
                        if column == "status"
                        else "model_content_outcome_unknown"
                    )
                elif column == "attempts":
                    value = 1
                values.append(value)
            rows.append((f"noise{index}", *values))
        connection.executemany(
            f"INSERT INTO memory_tasks (task_id,{inserts}) VALUES (?," + placeholders + ")",
            rows,
        )

    with tasks._connect(writing=True) as connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=1, last_error_code=? WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "model_content_transport_headers_mismatch",
                "noise2099",
            ),
        )

    # A batch of one: the pass may look at a single unknown row, and can only
    # pick the qualifying one if the filter runs in the query. (Insertion
    # order does not decide SQLite's scan order, so pinning the batch small
    # is what makes this deterministic.)
    assert tasks.migrate_unknown_transport_refusals(limit=1) == 1
    assert tasks.get("noise2099").status is MemoryTaskStatus.PENDING
    assert tasks.get("noise2099").attempts == 0
