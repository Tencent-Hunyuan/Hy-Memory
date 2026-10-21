"""删除发件箱：一条删除同一时刻只归一个执行者，结果只由持有领取凭证的那个写。

时间一律走注入的虚拟时钟：退避 30 秒起、翻倍、6 小时封顶是精确断言，
不靠真实 sleep 去碰运气。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from one_memory.hy_memory_bridge import HyMemoryBridge, HyMemoryBridgeError
from one_memory.memory_governance import DeletionStatus, MemoryGovernanceStore
from one_memory.model_profiles import ProviderKind
from tests.test_hy_memory_bridge import FakeHyMemoryClient, snapshot
from tests.test_memory_no_candidate import _deleted, _Hy, _supervisor
from tests.test_memory_processing import _setup


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _clocked(tmp_path):
    stores = _setup(tmp_path)
    clock = _Clock()
    governance = MemoryGovernanceStore(tmp_path / "one-memory.sqlite3", clock=clock)
    return stores, clock, governance


def test_a_failed_deletion_is_due_again_exactly_after_its_backoff(tmp_path) -> None:
    stores, clock, governance = _clocked(tmp_path)
    (memory_id,) = _deleted(stores, 1)

    waits = []
    for _attempt in range(12):
        (claim,) = governance.claim_deletions(user_id="local")
        failed = governance.settle_deletion(
            claim, user_id="local", error_code="hy_memory_delete_failed"
        )
        assert failed.status is DeletionStatus.FAILED
        retry_at = governance.deletion_retry_at(memory_id, user_id="local")
        waits.append((retry_at - clock()).total_seconds())
        clock.now = retry_at - timedelta(microseconds=1)
        assert governance.claim_deletions(user_id="local") == []
        clock.now = retry_at

    assert waits == [30, 60, 120, 240, 480, 960, 1920, 3840, 7680, 15360, 21600, 21600]


class _Stop(Exception):
    pass


def test_the_deletion_sync_tries_again_exactly_when_each_wait_runs_out(tmp_path) -> None:
    stores, clock, governance = _clocked(tmp_path)
    (memory_id,) = _deleted(stores, 1)
    supervisor = _supervisor(stores, governance)
    tried: list[datetime] = []

    class _Down(_Hy):
        async def delete(self, memory_id: str) -> dict[str, object]:
            tried.append(clock())
            return await super().delete(memory_id)

    supervisor._bridges[(stores[5].profile_id, stores[5].revision, None)] = _Down(available=False)

    async def wait(timeout: float) -> None:
        if len(tried) == 13:
            raise _Stop
        clock.advance(timeout)

    supervisor._wait_for_deletions = wait

    with pytest.raises(_Stop):
        asyncio.run(supervisor.keep_deletions_in_sync())

    gaps = [
        (later - earlier).total_seconds() for earlier, later in zip(tried, tried[1:], strict=False)
    ]
    assert gaps == [30, 60, 120, 240, 480, 960, 1920, 3840, 7680, 15360, 21600, 21600]
    assert governance.deletion(memory_id, user_id="local").attempts == 13


class _GatedHy:
    """First call lands late and succeeds; a second one, if made, fails later still."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = [asyncio.Event(), asyncio.Event()]
        self.gates = [asyncio.Event(), asyncio.Event()]

    async def delete(self, memory_id: str) -> dict[str, object]:
        index = self.calls
        self.calls += 1
        self.entered[index].set()
        await self.gates[index].wait()
        if index:
            raise HyMemoryBridgeError("hy_memory_delete_failed")
        return {"success": True}

    async def close(self) -> None:
        return None


def test_the_api_and_the_sync_never_work_the_same_deletion_at_once(tmp_path) -> None:
    stores = _setup(tmp_path)
    (memory_id,) = _deleted(stores, 1)
    supervisor = _supervisor(stores)
    hy = _GatedHy()
    supervisor._bridges[(stores[5].profile_id, stores[5].revision, None)] = hy

    async def scenario() -> None:
        immediate = asyncio.create_task(supervisor.finish_deletion(memory_id))
        await hy.entered[0].wait()
        sweep = asyncio.create_task(supervisor.finish_deletions())
        for _ in range(500):
            if sweep.done() or hy.entered[1].is_set():
                break
            await asyncio.sleep(0.005)
        hy.gates[0].set()
        await immediate
        hy.gates[1].set()
        await sweep

    asyncio.run(scenario())

    assert hy.calls == 1
    assert stores[3].deletion(memory_id, user_id="local").status is DeletionStatus.COMPLETED


def test_a_claim_that_was_taken_over_cannot_overwrite_the_outcome(tmp_path) -> None:
    from one_memory.memory_governance import DELETION_LEASE

    stores, clock, governance = _clocked(tmp_path)
    (memory_id,) = _deleted(stores, 1)

    (first,) = governance.claim_deletions(user_id="local")
    assert governance.claim_deletions(user_id="local") == []
    assert governance.deletion(memory_id, user_id="local").status is DeletionStatus.RUNNING
    # Its holder died: once the lease runs out someone else may take it.
    clock.advance(DELETION_LEASE.total_seconds())
    (second,) = governance.claim_deletions(user_id="local")
    assert second.token != first.token

    done = governance.settle_deletion(second, user_id="local", error_code=None)
    assert done.status is DeletionStatus.COMPLETED
    assert governance.settle_deletion(first, user_id="local", error_code="late") is None
    assert governance.deletion(memory_id, user_id="local").status is DeletionStatus.COMPLETED


# ------------------------------------------------------------ HY 说没删成


class _DeleteClient(FakeHyMemoryClient):
    def __init__(self, result: dict[str, object]) -> None:
        super().__init__({"success": True})
        self.delete_result = result

    def delete(self, memory_id: str):
        self.calls.append({"delete_memory_id": memory_id})
        return self.delete_result


def _bridge(tmp_path, client) -> HyMemoryBridge:
    return HyMemoryBridge(
        profile=snapshot(ProviderKind.OLLAMA, base_url="http://127.0.0.1:11434", has_api_key=False),
        api_key=None,
        data_dir=tmp_path,
        client=client,
    )


def test_sdk_incomplete_delete_receipt_is_a_failure(tmp_path) -> None:
    bridge = _bridge(
        tmp_path,
        _DeleteClient(
            {
                "success": False,
                "deleted_count": 0,
                "partial": True,
                "error_code": "delete_incomplete",
            }
        ),
    )

    with pytest.raises(HyMemoryBridgeError) as error:
        asyncio.run(bridge.delete("still-there"))

    assert str(error.value) == "delete_incomplete"


@pytest.mark.parametrize(
    "result",
    [
        {"success": True, "deleted_count": 1, "partial": False, "error_code": None},
        {"success": False, "deleted_count": 0, "partial": False, "error_code": None},
    ],
    ids=["deleted-now", "already-absent"],
)
def test_complete_or_already_absent_receipt_counts_as_deleted(tmp_path, result) -> None:
    bridge = _bridge(tmp_path, _DeleteClient(result))

    asyncio.run(bridge.delete("gone"))


def test_an_unreadable_graph_keeps_the_deletion_failed_and_due_again(tmp_path) -> None:
    stores, clock, governance = _clocked(tmp_path)
    (memory_id,) = _deleted(stores, 1)
    supervisor = _supervisor(stores, governance)
    client = _DeleteClient(
        {
            "success": False,
            "deleted_count": 0,
            "partial": True,
            "error_code": "delete_incomplete",
        }
    )
    supervisor._bridges[(stores[5].profile_id, stores[5].revision, None)] = _bridge(
        tmp_path, client
    )

    asyncio.run(supervisor.finish_deletion(memory_id))

    failed = governance.deletion(memory_id, user_id="local")
    assert (failed.status, failed.last_error_code) == (
        DeletionStatus.FAILED,
        "delete_incomplete",
    )
    retry_at = governance.deletion_retry_at(memory_id, user_id="local")
    assert retry_at is not None

    client.delete_result = {
        "success": False,
        "deleted_count": 0,
        "partial": False,
        "error_code": None,
    }
    clock.now = retry_at
    asyncio.run(supervisor.finish_deletions())

    done = governance.deletion(memory_id, user_id="local")
    assert (done.status, done.attempts) == (DeletionStatus.COMPLETED, 2)


def test_sdk_error_retries_until_an_already_absent_receipt_arrives(tmp_path) -> None:
    stores, clock, governance = _clocked(tmp_path)
    (memory_id,) = _deleted(stores, 1)
    supervisor = _supervisor(stores, governance)

    class _Raises(FakeHyMemoryClient):
        def __init__(self):
            super().__init__({"success": True})

        def delete(self, memory_id: str):
            self.calls.append({"delete_memory_id": memory_id})
            raise RuntimeError("HY lost track of it")

    holding = _Raises()
    supervisor._bridges[(stores[5].profile_id, stores[5].revision, None)] = _bridge(
        tmp_path, holding
    )
    asyncio.run(supervisor.finish_deletion(memory_id))
    assert governance.deletion(memory_id, user_id="local").status is DeletionStatus.FAILED

    gone = _DeleteClient(
        {
            "success": False,
            "deleted_count": 0,
            "partial": False,
            "error_code": None,
        }
    )
    supervisor._bridges[(stores[5].profile_id, stores[5].revision, None)] = _bridge(tmp_path, gone)
    clock.now = governance.deletion_retry_at(memory_id, user_id="local")
    asyncio.run(supervisor.finish_deletions())
    done = governance.deletion(memory_id, user_id="local")
    assert (done.status, done.last_error_code) == (DeletionStatus.COMPLETED, None)


def test_a_deletion_that_keeps_failing_stops_for_attention(tmp_path) -> None:
    from one_memory.memory_governance import DELETION_MAX_ATTEMPTS

    stores, clock, governance = _clocked(tmp_path)
    (memory_id,) = _deleted(stores, 1)
    supervisor = _supervisor(stores, governance)
    tried: list[datetime] = []

    class _Counting(_Hy):
        async def delete(self, memory_id: str) -> dict[str, object]:
            tried.append(clock())
            return await super().delete(memory_id)

    supervisor._bridges[(stores[5].profile_id, stores[5].revision, None)] = _Counting(
        available=False
    )

    for _ in range(DELETION_MAX_ATTEMPTS + 3):
        due = governance.next_deletion_due(user_id="local")
        if due is None:
            break
        clock.now = max(clock.now, due)
        asyncio.run(supervisor.finish_deletions())

    assert len(tried) == DELETION_MAX_ATTEMPTS
    final = governance.deletion(memory_id, user_id="local")
    assert final.status is DeletionStatus.FAILED
    assert final.attempts == DELETION_MAX_ATTEMPTS
    assert final.last_error_code == "hy_memory_delete_needs_attention"
    assert governance.next_deletion_due(user_id="local") is None
    clock.advance(365 * 24 * 3600)
    assert governance.claim_deletions(user_id="local") == []
