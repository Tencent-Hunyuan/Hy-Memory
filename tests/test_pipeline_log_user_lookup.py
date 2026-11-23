"""pipeline 日志按 user_id 查询必须和写入侧同一个 namespace 口径。"""

import asyncio


from hy_memory.config import MemoryConfig
from hy_memory.data.cache_sqlite import SqliteCache


def _config(tmp_path):
    return MemoryConfig.from_dict({
        "cache": {"backend": "sqlite", "db_path": str(tmp_path / "cache.db")},
    })


async def _store(cache, request_id, user_id, step="EXTRACT"):
    stored = await cache.store_pipeline_log(
        request_id=request_id, user_id=user_id, agent_id="a1",
        step=step, prompt="p", response="r",
    )
    assert stored


def test_namespaced_user_id_finds_its_own_logs(tmp_path):
    """写入把 proj__u1 落成 namespace=proj + user_id=u1，查询也要拆开。"""

    async def run():
        cache = SqliteCache(_config(tmp_path))
        await cache.initialize()
        try:
            await _store(cache, "req-1", "proj__u1")
            rows = await cache.get_pipeline_logs(user_id="proj__u1")
            assert [row["request_id"] for row in rows] == ["req-1"]
            assert (rows[0]["namespace"], rows[0]["user_id"]) == ("proj", "u1")
        finally:
            await cache.close()

    asyncio.run(run())


def test_namespaced_query_stays_inside_its_namespace(tmp_path):
    """两个 namespace 下同名用户不能互相看到对方的日志。"""

    async def run():
        cache = SqliteCache(_config(tmp_path))
        await cache.initialize()
        try:
            await _store(cache, "req-proj", "proj__u1")
            await _store(cache, "req-other", "other__u1")
            rows = await cache.get_pipeline_logs(user_id="proj__u1")
            assert [row["request_id"] for row in rows] == ["req-proj"]
        finally:
            await cache.close()

    asyncio.run(run())


def test_bare_user_id_still_finds_unnamespaced_logs(tmp_path):
    """无 namespace 的 user_id 行为不变。"""

    async def run():
        cache = SqliteCache(_config(tmp_path))
        await cache.initialize()
        try:
            await _store(cache, "req-bare", "u1")
            rows = await cache.get_pipeline_logs(user_id="u1")
            assert [row["request_id"] for row in rows] == ["req-bare"]
        finally:
            await cache.close()

    asyncio.run(run())


def test_rows_written_before_the_namespace_split_are_still_found(tmp_path):
    """旧库里的历史行是 namespace='' + 完整 user_id，不能因为改口径就查不到。"""

    async def run():
        cache = SqliteCache(_config(tmp_path))
        await cache.initialize()
        try:
            with cache._lock:
                cache._conn.execute(
                    """INSERT INTO pipeline_logs_v2
                       (request_id, namespace, user_id, agent_id, session_id,
                        step, prompt, response, created_at, created_date)
                       VALUES ('req-legacy', '', 'proj__u1', 'a1', '',
                               'EXTRACT', 'p', 'r', '2026-01-01T00:00:00', '2026-01-01')"""
                )
                cache._conn.commit()
            rows = await cache.get_pipeline_logs(user_id="proj__u1")
            assert [row["request_id"] for row in rows] == ["req-legacy"]
            assert rows[0]["namespace"] == ""
        finally:
            await cache.close()

    asyncio.run(run())


def test_step_filter_composes_with_a_namespaced_user_id(tmp_path):
    async def run():
        cache = SqliteCache(_config(tmp_path))
        await cache.initialize()
        try:
            await _store(cache, "req-1", "proj__u1", step="EXTRACT")
            await _store(cache, "req-1", "proj__u1", step="RECONCILE")
            rows = await cache.get_pipeline_logs(user_id="proj__u1", step="RECONCILE")
            assert [row["step"] for row in rows] == ["RECONCILE"]
        finally:
            await cache.close()

    asyncio.run(run())
