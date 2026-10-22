from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hy_memory.client import HyMemoryClient


class OutcomeCache:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def claim_write_outcome(
        self,
        request_id,
        user_id,
        agent_id,
        session_id,
        operation,
    ):
        existing = self.rows.get(request_id)
        if existing is not None:
            return existing
        self.rows[request_id] = {
            "request_id": request_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "operation": operation,
            "status": "running",
            "result": {},
        }
        return None

    async def finish_write_outcome(self, request_id, status, result):
        row = self.rows.get(request_id)
        if row is None:
            return False
        row.update(status=status, result=dict(result))
        return True

    async def get_write_outcome(self, request_id):
        return self.rows.get(request_id)


def _client():
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._cache = OutcomeCache()
    return client


@pytest.mark.asyncio
async def test_same_request_replays_terminal_result_without_writing_twice():
    client = _client()
    client._async_add_impl = AsyncMock(
        return_value={"success": True, "request_id": "r1", "memory_id": "m1"}
    )

    first = await HyMemoryClient.async_add.__wrapped__(
        client, "hello", user_id="u1", request_id="r1"
    )
    second = await HyMemoryClient.async_add.__wrapped__(
        client, "hello", user_id="u1", request_id="r1"
    )

    assert first["memory_id"] == "m1"
    assert second == {**first, "replayed": True}
    client._async_add_impl.assert_awaited_once()


@pytest.mark.asyncio
async def test_exception_marks_outcome_unknown_and_blocks_blind_retry():
    client = _client()
    client._async_add_impl = AsyncMock(side_effect=RuntimeError("after commit is unknown"))

    with pytest.raises(RuntimeError):
        await HyMemoryClient.async_add.__wrapped__(
            client, "hello", user_id="u1", request_id="r2"
        )

    replay = await HyMemoryClient.async_add.__wrapped__(
        client, "hello", user_id="u1", request_id="r2"
    )
    assert replay["success"] is False
    assert replay["outcome_status"] == "unknown"
    assert replay["error_code"] == "write_outcome_not_terminal"
    client._async_add_impl.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_id_cannot_cross_write_scope():
    client = _client()
    client._async_add_impl = AsyncMock(
        return_value={"success": True, "request_id": "r3", "memory_id": "m1"}
    )
    await HyMemoryClient.async_add.__wrapped__(
        client, "hello", user_id="u1", agent_id="a1", request_id="r3"
    )

    with pytest.raises(ValueError, match="another write scope"):
        await HyMemoryClient.async_add.__wrapped__(
            client, "hello", user_id="u2", agent_id="a1", request_id="r3"
        )


@pytest.mark.asyncio
async def test_add_extracted_uses_the_same_idempotent_outcome_contract():
    client = _client()
    client._async_add_extracted_impl = AsyncMock(
        return_value={
            "success": True,
            "request_id": "r4",
            "memory_id": "m1",
            "memory_ids": ["m1"],
        }
    )

    first = await HyMemoryClient.async_add_extracted.__wrapped__(
        client, ["fact"], user_id="u1", request_id="r4"
    )
    second = await HyMemoryClient.async_add_extracted.__wrapped__(
        client, ["fact"], user_id="u1", request_id="r4"
    )

    assert first["memory_ids"] == ["m1"]
    assert second == {**first, "replayed": True}
    client._async_add_extracted_impl.assert_awaited_once()
