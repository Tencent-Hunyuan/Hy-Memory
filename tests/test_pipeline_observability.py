"""Pipeline log（默认开，可关）与 trace（可关）分离。"""

import asyncio
import json
from pathlib import Path

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.cache_sqlite import SqliteCache
from hy_memory.utils.pipeline_observability import (
    is_llm_content_logging_enabled,
    is_pipeline_log_enabled,
    is_pipeline_trace_enabled,
    resolve_pipeline_log_dir,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    log_dir = str(tmp_path / "pipeline_logs")
    db_path = str(tmp_path / "cache.db")
    monkeypatch.setenv("MEMORY_LOG_DIR", log_dir)
    monkeypatch.setenv("MEMORY_CACHE_DB_PATH", db_path)
    return log_dir


class TestPipelineObservabilityConfig:
    def test_resolve_log_dir_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMORY_LOG_DIR", raising=False)
        monkeypatch.delenv("MEMORY_TRACE_LOG_DIR", raising=False)
        monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "data"))
        assert resolve_pipeline_log_dir() == str(tmp_path / "data" / "logs" / "pipeline")

    def test_log_enabled_default(self, monkeypatch):
        monkeypatch.delenv("MEMORY_PIPELINE_LOG_ENABLED", raising=False)
        assert is_pipeline_log_enabled() is True

    def test_log_disabled(self, monkeypatch):
        monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "false")
        assert is_pipeline_log_enabled() is False

    def test_trace_enabled_default(self, monkeypatch):
        monkeypatch.delenv("MEMORY_PIPELINE_TRACE_ENABLED", raising=False)
        assert is_pipeline_trace_enabled() is True

    def test_trace_disabled(self, monkeypatch):
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")
        assert is_pipeline_trace_enabled() is False

    def test_llm_content_logging_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MEMORY_LOG_LLM_CONTENT", raising=False)
        assert is_llm_content_logging_enabled() is False

    def test_llm_content_logging_explicitly_enabled(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LOG_LLM_CONTENT", "true")
        assert is_llm_content_logging_enabled() is True


class TestObservabilityHookBehavior:
    def test_log_always_trace_off(self, env_setup, monkeypatch):
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")

        async def _test():
            from datetime import date
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                install_pipeline_observability,
            )

            config = MemoryConfig.from_dict({"cache": {"backend": "sqlite"}})
            cache = SqliteCache(config)
            await cache.initialize()
            install_pipeline_observability(cache, log_label="test")
            await cache.store_pipeline_log(
                request_id="req-1",
                user_id="u1",
                agent_id="a1",
                step="S1_EMBED",
                parsed='{"dims": 128}',
            )
            await drain_pipeline_log_tasks()

            log_file = Path(env_setup) / "default" / f"{date.today().isoformat()}.log"
            assert log_file.exists()
            row = json.loads(log_file.read_text(encoding="utf-8").strip())
            assert row["step"] == "S1_EMBED"

            logs = await cache.get_pipeline_logs(request_id="req-1")
            assert logs == []
            await cache.close()

        run(_test())

    def test_log_flag_on_writes_jsonl(self, env_setup, monkeypatch):
        """显式开启与默认一致：照常写 JSONL。"""
        monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "true")
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")

        async def _test():
            from datetime import date
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                install_pipeline_observability,
            )

            config = MemoryConfig.from_dict({"cache": {"backend": "sqlite"}})
            cache = SqliteCache(config)
            await cache.initialize()
            install_pipeline_observability(cache, log_label="test")
            await cache.store_pipeline_log(
                request_id="req-on", user_id="u1", agent_id="a1", step="S1_EMBED",
            )
            await drain_pipeline_log_tasks()

            log_file = Path(env_setup) / "default" / f"{date.today().isoformat()}.log"
            assert json.loads(log_file.read_text(encoding="utf-8").strip())["step"] == "S1_EMBED"
            await cache.close()

        run(_test())

    def test_log_off_trace_off_is_noop(self, env_setup, monkeypatch):
        """两者都关：不包装、不写文件、不写库，store_pipeline_log 直接返回 True。"""
        monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "false")
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")

        async def _test():
            from hy_memory.utils.pipeline_observability import (
                _noop_store_pipeline_log,
                drain_pipeline_log_tasks,
                install_pipeline_observability,
            )

            config = MemoryConfig.from_dict({"cache": {"backend": "sqlite"}})
            cache = SqliteCache(config)
            await cache.initialize()
            original_store = cache.store_pipeline_log
            install_pipeline_observability(cache, log_label="test")
            assert cache.store_pipeline_log is _noop_store_pipeline_log

            ok = await cache.store_pipeline_log(
                request_id="req-off", user_id="ns__u1", agent_id="a1",
                step="S1_EMBED", prompt="p", response="r",
            )
            await drain_pipeline_log_tasks()

            assert ok is True
            assert not Path(env_setup).exists()
            # 原始 store 仍能写库，说明表本身可用；空操作确实没写
            assert await cache.get_pipeline_logs(request_id="req-off") == []
            await original_store(
                request_id="req-probe", user_id="u1", agent_id="a1",
                step="PROBE", prompt="", response="",
            )
            assert len(await cache.get_pipeline_logs(request_id="req-probe")) == 1
            await cache.close()

        run(_test())

    def test_log_off_trace_on_writes_db_only(self, env_setup, monkeypatch):
        """只关 Log：不写 JSONL，Trace 照常落库。"""
        monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "false")
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "true")

        async def _test():
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                install_pipeline_observability,
            )

            config = MemoryConfig.from_dict({"cache": {"backend": "sqlite"}})
            cache = SqliteCache(config)
            await cache.initialize()
            install_pipeline_observability(cache, log_label="test")
            await cache.store_pipeline_log(
                request_id="req-db", user_id="u1", agent_id="a1", step="S1_EMBED",
            )
            await drain_pipeline_log_tasks()

            assert not Path(env_setup).exists()
            logs = await cache.get_pipeline_logs(request_id="req-db")
            assert [row["step"] for row in logs] == ["S1_EMBED"]
            await cache.close()

        run(_test())

    def test_store_returns_before_slow_persist(self, env_setup, monkeypatch):
        """慢 DB / 慢磁盘不得堵住 await store_pipeline_log。"""
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "true")

        async def _test():
            import time as _time
            from hy_memory.utils.pipeline_log_writer import PipelineLogWriter
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                wrap_store_pipeline_log,
            )

            started = asyncio.Event()
            release = asyncio.Event()

            async def _slow_store(**kwargs):
                started.set()
                await release.wait()
                return True

            wrapped = wrap_store_pipeline_log(
                _slow_store,
                log_writer=PipelineLogWriter(env_setup),
                trace_enabled=True,
            )

            t0 = _time.perf_counter()
            ok = await wrapped(
                request_id="req-fast",
                user_id="u1",
                agent_id="a1",
                step="READ_EMBED_QUERY",
                parsed="{}",
                elapsed_ms=12.3,
            )
            elapsed_ms = (_time.perf_counter() - t0) * 1000
            assert ok is True
            # 主路径应在慢 persist 完成前返回（给一点调度余量）
            assert elapsed_ms < 50.0

            # 给事件循环一次调度机会，确认后台任务已启动并卡在 release 上
            for _ in range(20):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()

            release.set()
            await drain_pipeline_log_tasks()

        run(_test())

    def test_step_to_step_elapsed_ignores_persist_latency(self, env_setup, monkeypatch):
        """step-to-step 在入队瞬间打点，不被后台 IO 拉长。"""
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")

        async def _test():
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                wrap_store_pipeline_log,
            )

            recorded = []

            class _CaptureWriter:
                def write_step(self, **kwargs):
                    recorded.append(dict(kwargs))

            async def _noop_store(**kwargs):
                return True

            wrapped = wrap_store_pipeline_log(
                _noop_store,
                log_writer=_CaptureWriter(),
                trace_enabled=False,
            )

            await wrapped(request_id="r", user_id="u", agent_id="a", step="A")
            await asyncio.sleep(0.05)
            await wrapped(request_id="r", user_id="u", agent_id="a", step="B")
            await drain_pipeline_log_tasks()

            assert len(recorded) == 2
            # 第二步 elapsed ≈ 50ms 量级（业务间隔），不应被 JSONL 写放大到秒级
            assert 30.0 <= recorded[1]["elapsed_ms"] <= 200.0

        run(_test())

    def test_caller_event_at_ms_is_reused(self, env_setup, monkeypatch):
        """调用方显式传入非 0 event_at_ms → 直接复用，不被本地 time.time() 覆盖。"""
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "true")

        async def _test():
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                wrap_store_pipeline_log,
            )

            recorded_writer = []
            recorded_store = []

            class _CaptureWriter:
                def write_step(self, **kwargs):
                    recorded_writer.append(dict(kwargs))

            async def _capture_store(**kwargs):
                recorded_store.append(dict(kwargs))
                return True

            wrapped = wrap_store_pipeline_log(
                _capture_store,
                log_writer=_CaptureWriter(),
                trace_enabled=True,
            )

            # 模拟 App 在真正事件发生时刻同步打点，比这次 SDK 调用早很多
            caller_event_at_ms = 1700000000123

            ok = await wrapped(
                request_id="r-caller-ts",
                user_id="u1",
                agent_id="a1",
                step="A",
                event_at_ms=caller_event_at_ms,
            )
            await drain_pipeline_log_tasks()

            assert ok is True
            assert recorded_writer[0]["event_at_ms"] == caller_event_at_ms
            assert recorded_store[0]["event_at_ms"] == caller_event_at_ms

        run(_test())

    def test_missing_event_at_ms_falls_back_to_now(self, env_setup, monkeypatch):
        """调用方不传 / 传 0 → 回落本地 time.time()（毫秒级，且非 0）。"""
        monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")

        async def _test():
            import time as _time
            from hy_memory.utils.pipeline_observability import (
                drain_pipeline_log_tasks,
                wrap_store_pipeline_log,
            )

            recorded = []

            class _CaptureWriter:
                def write_step(self, **kwargs):
                    recorded.append(dict(kwargs))

            async def _noop_store(**kwargs):
                return True

            wrapped = wrap_store_pipeline_log(
                _noop_store,
                log_writer=_CaptureWriter(),
                trace_enabled=False,
            )

            t0_ms = int(_time.time() * 1000)
            await wrapped(request_id="r-no-ts", user_id="u1", agent_id="a1", step="A")
            await wrapped(
                request_id="r-no-ts-2", user_id="u1", agent_id="a1", step="A",
                event_at_ms=0,
            )
            await drain_pipeline_log_tasks()
            t1_ms = int(_time.time() * 1000)

            assert len(recorded) == 2
            for row in recorded:
                assert t0_ms - 1000 <= row["event_at_ms"] <= t1_ms + 1000

        run(_test())


class TestPipelineLogSubdirSplit:
    """pipeline JSONL 按 user_id 的 "__" namespace 前缀分子目录。"""

    def test_subdir_from_user_id_prefix(self, tmp_path):
        from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

        w = PipelineLogWriter(str(tmp_path), "default")
        w.write_step(user_id="project_alpha__Donald Smith", step="EXTRACT", response="x")
        w.write_step(user_id="acme__alice", step="RECONCILE", response="y")
        w.write_step(user_id="no_prefix_user", step="S1_EMBED", response="z")

        dirs = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
        assert "project_alpha" in dirs
        assert "acme" in dirs
        # 无前缀 → 回落构造时 default_subdir（这里 default）
        assert "default" in dirs

    def test_first_double_underscore_is_split_point(self, tmp_path):
        from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

        w = PipelineLogWriter(str(tmp_path), "fallback")
        # 只按第一个 __ 切分；user 部分自带 __ 不影响 subdir
        w.write_step(user_id="app1__user__with__underscores", step="X", response="r")
        assert (tmp_path / "app1").is_dir()

    def test_user_id_prefix_cannot_escape_log_root(self, tmp_path):
        from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

        log_root = tmp_path / "logs"
        outside = tmp_path / "outside"
        w = PipelineLogWriter(str(log_root), "default")
        for user_id in (
            f"{outside / 'evil'}__u1",
            f"../../{outside.name}__u1",
            "..__u1",
            f"{outside.name}/nested__u1",
        ):
            w.write_step(user_id=user_id, step="X", response="r")

        assert not outside.exists()
        files = list(log_root.rglob("*.log"))
        assert files
        assert all(path.is_relative_to(log_root) for path in files)

    def test_default_subdir_is_sanitized(self, tmp_path):
        from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

        log_root = tmp_path / "logs"
        w = PipelineLogWriter(str(log_root), "../../evil")
        w.write_step(user_id="no_prefix", step="X", response="r")

        assert not (tmp_path / "evil").exists()
        assert (log_root / "default").is_dir()


def test_pipeline_jsonl_does_not_add_total_tokens_twice(tmp_path):
    from datetime import date

    from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

    writer = PipelineLogWriter(str(tmp_path), "default")
    writer.write_step(
        step="EXTRACT",
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
    )

    log_file = tmp_path / "default" / f"{date.today().isoformat()}.log"
    row = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert row["tokens"] == 150


class TestPipelineLogContentPrivacy:
    """Full LLM content is opt-in; default logs retain only length/hash metadata."""

    def test_prompt_and_response_redacted_by_default(self, tmp_path, monkeypatch):
        from datetime import date
        from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

        monkeypatch.delenv("MEMORY_LOG_LLM_CONTENT", raising=False)
        w = PipelineLogWriter(str(tmp_path), "default")
        recon_prompt = (
            "现有记忆 existing_memories:\n"
            '[{"memory_id":"m_old","content":"用户住在涩谷的公寓，月租18万日元"}]\n'
            "新记忆:\n用户现在住横滨"
        )
        w.write_step(
            user_id="u1", agent_id="a1", step="RECONCILE",
            prompt=recon_prompt,
            response='[{"op":"SUPERSEDE","memory_id":"m_old"}]',
            parsed='[{"op":"SUPERSEDE"}]',
        )
        log_file = tmp_path / "default" / f"{date.today().isoformat()}.log"
        row = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert row["prompt"].startswith("<redacted len=")
        assert row["content"].startswith("<redacted len=")
        assert "existing_memories" not in row["prompt"]
        assert "涩谷" not in row["prompt"]

    def test_prompt_and_response_persisted_only_when_enabled(self, tmp_path, monkeypatch):
        from datetime import date
        from hy_memory.utils.pipeline_log_writer import PipelineLogWriter

        monkeypatch.setenv("MEMORY_LOG_LLM_CONTENT", "true")
        w = PipelineLogWriter(str(tmp_path), "default")
        w.write_step(
            user_id="u1", step="EXTRACT",
            prompt="sensitive prompt", response="sensitive response",
        )
        log_file = tmp_path / "default" / f"{date.today().isoformat()}.log"
        row = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert row["prompt"] == "sensitive prompt"
        assert row["content"] == "sensitive response"
