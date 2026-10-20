"""Startup rollback runs on-owner in fresh, bounded, credential-free processes."""
import asyncio
from concurrent.futures import CancelledError
from contextlib import ExitStack
import gc
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import traceback
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    'solo_failure', 'runtime_failure',
    'solo_second_loop', 'runtime_second_loop',
    'solo_thread_start', 'runtime_thread_start',
    'solo_vector', 'solo_graph', 'solo_cache', 'solo_writer', 'solo_reader',
    'solo_history', 'solo_metrics', 'runtime_cache', 'runtime_metrics',
    'runtime_observability', 'cleanup_failure', 'cleanup_cancelled',
    'solo_cancelled', 'shared_ownership', 'previous_metrics',
    'runtime_cancelled', 'runtime_cancelled_twice', 'runtime_cancelled_after_init',
    'runtime_cancelled_native_init', 'registry_system2',
]


@pytest.mark.parametrize('case', CASES)
def test_initialization_rollback(case, tmp_path):
    for name in ('home', 'data', 'logs', 'cache', 'tmp'):
        (tmp_path / name).mkdir()
    env = {
        'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
        'HOME': str(tmp_path / 'home'), 'TMPDIR': str(tmp_path / 'tmp'),
        'XDG_CACHE_HOME': str(tmp_path / 'cache'),
        'MEMORY_DATA_DIR': str(tmp_path / 'data'),
        'MEMORY_LOG_DIR': str(tmp_path / 'logs'), 'MEMORY_CACHE_BACKEND': 'sqlite',
        'MEMORY_LOG_LEVEL': 'WARNING', 'PYTHONPATH': str(ROOT),
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        'ANONYMIZED_TELEMETRY': 'false', 'HF_HUB_OFFLINE': '1',
    }
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).resolve()), case],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Task exception was never retrieved' not in result.stderr, result.stderr
    assert 'Task was destroyed but it is pending' not in result.stderr, result.stderr


def config():
    from hy_memory import MemoryConfig
    cfg = MemoryConfig()
    cfg.history.enable = False
    cfg.history.db_path = str(Path(os.environ['MEMORY_DATA_DIR']) / 'history.db')
    cfg.recall.local_rerank_enabled = False
    cfg.cache.backend = 'sqlite'
    cfg.cache.db_path = str(Path(os.environ['MEMORY_DATA_DIR']) / 'synthetic.db')
    cfg.embedder.api_key = ''
    cfg.llm.api_key = ''
    return cfg


def assert_released(created):
    gc.collect()
    states = [(lt._thread.is_alive(), lt._loop.is_closed()) for lt in created]
    print(f'LOOPS (alive, closed)={states}', flush=True)
    assert all(not alive and closed for alive, closed in states), states


def original_failures(cm, created, case):
    from hy_memory import SharedRuntime
    cfg = config()
    if case == 'solo_failure':
        cfg.vector_store.provider = 'review-invalid-provider'
        expected = ValueError
    else:
        cfg.cache.db_path = os.environ['MEMORY_DATA_DIR']
        expected = sqlite3.OperationalError

    async def create_runtime():
        await asyncio.wait_for(SharedRuntime.create(cfg), timeout=3)

    try:
        if case == 'solo_failure':
            cm.HyMemoryClient(cfg, mode='lite')
        else:
            asyncio.run(create_runtime())
    except expected:
        traceback.print_exc()
    else:
        raise AssertionError('Expected initialization failure was not raised')
    assert len(created) == 2
    assert_released(created)


def loop_creation_failures(cm, created, case, tracked):
    from hy_memory import SharedRuntime
    failure = RuntimeError('synthetic second loop creation failure')
    count = 0
    start = threading.Thread.start

    def factory():
        nonlocal count
        count += 1
        if count == 2:
            raise failure
        return tracked()

    def start_thread(thread):
        nonlocal count
        count += 1
        if count == 2:
            raise failure
        start(thread)

    target = patch.object(threading.Thread, 'start', start_thread) if case.endswith('thread_start') else patch.object(cm, '_LoopThread', factory)
    with target:
        try:
            if case.startswith('solo'):
                cm.HyMemoryClient(config(), mode='lite')
            else:
                asyncio.run(SharedRuntime.create(config()))
        except RuntimeError as exc:
            assert exc is failure
        else:
            raise AssertionError('Second loop creation did not fail')
    assert len(created) == (2 if case.endswith('thread_start') else 1)
    assert_released(created)


def partial_components(cm, created, case):
    from hy_memory import SharedRuntime
    from hy_memory.data.cache_sqlite import SqliteCache
    from hy_memory.data.history_store import HistoryStore
    from hy_memory.metrics import MetricsCollector
    from hy_memory.pipelines.reader import MemoryReader
    from hy_memory.pipelines.writer import MemoryWriter

    stage = case.split('_', 1)[1]
    if case.startswith('cleanup'):
        stage = 'reader'
    failure = RuntimeError(f'synthetic {stage} initialization failure')
    if case == 'solo_cancelled':
        stage = 'vector'
        failure = asyncio.CancelledError('synthetic startup cancellation')
    events, resources, tasks, diagnostics = [], {}, [], []
    failed_objects = []
    cfg = config()
    cfg.history.enable = stage == 'history'
    is_runtime = case.startswith('runtime')

    class Capture(logging.Handler):
        def emit(self, record):
            diagnostics.append(record.getMessage())

    handler = Capture()
    cleanup_logger = logging.getLogger('hy_memory.utils.init_rollback')
    cleanup_logger.addHandler(handler)

    class Vector:
        _collection_name = 'synthetic'

        async def initialize(self):
            resources['owner'] = asyncio.get_running_loop()
            resources['vector'] = self
            if stage == 'vector':
                raise failure

        async def close(self):
            assert asyncio.get_running_loop() is resources['owner']
            events.append('vector')
            if case == 'cleanup_failure':
                raise RuntimeError('synthetic vector close failure')
            if case == 'cleanup_cancelled':
                raise asyncio.CancelledError('synthetic close cancellation')

    class Graph(Vector):
        async def initialize(self):
            resources['graph'] = self
            raise failure

        async def close(self):
            assert asyncio.get_running_loop() is resources['owner']
            events.append('graph')

    class Client(cm.HyMemoryClient):
        def __new__(cls, *args, **kwargs):
            obj = super().__new__(cls)
            failed_objects.append(obj)
            return obj

    class Runtime(SharedRuntime):
        def __init__(self, cfg):
            super().__init__(cfg)
            failed_objects.append(self)

    def wrap_init(cls, name):
        original = cls.initialize

        async def initialize(obj):
            resources.setdefault('owner', asyncio.get_running_loop())
            resources[name] = obj
            await original(obj)
            if stage == name:
                raise failure
        return initialize

    def wrap_close(cls, name):
        original = cls.close

        async def close(obj):
            assert asyncio.get_running_loop() is resources['owner']
            events.append(name)
            await original(obj)
        return close

    original_start = MetricsCollector.start_background_tasks

    async def start_metrics(metrics):
        resources['metrics'] = metrics
        await original_start(metrics)
        tasks.extend([metrics._flush_task, metrics._cleanup_task])
        if stage == 'metrics':
            raise failure

    def fail_observability(rt):
        raise failure

    with ExitStack() as stack:
        stack.enter_context(patch.object(cm, 'create_vector_store', return_value=Vector()))
        for cls, name in [(SqliteCache, 'cache'), (MemoryWriter, 'writer'),
                          (MemoryReader, 'reader'), (HistoryStore, 'history')]:
            stack.enter_context(patch.object(cls, 'initialize', wrap_init(cls, name)))
            stack.enter_context(patch.object(cls, 'close', wrap_close(cls, name)))
        stack.enter_context(patch.object(MetricsCollector, 'start_background_tasks', start_metrics))
        if stage == 'graph':
            stack.enter_context(patch('hy_memory.data.graph_store.create_graph_store', return_value=Graph()))
        if stage == 'observability':
            stack.enter_context(patch.object(Runtime, '_install_pipeline_observability', fail_observability))
        try:
            if is_runtime:
                asyncio.run(Runtime.create(cfg))
            else:
                Client(cfg, mode='ultra' if stage == 'graph' else 'lite')
        except (RuntimeError, CancelledError) as exc:
            if case != 'solo_cancelled':
                assert exc is failure, repr(exc)
            traceback.print_exc()
        else:
            raise AssertionError('Initialization unexpectedly succeeded')

        assert_released(created)
        before = list(events)
        # These private failure-only operations are idempotent, including after
        # the owning loops have already been disposed.
        asyncio.run(failed_objects[0]._rollback_initialization())
        from hy_memory.utils.init_rollback import stop_failed_loops
        stop_failed_loops(*created)
        assert events == before

    cleanup_logger.removeHandler(handler)
    assert len(events) == len(set(events)), events
    if 'vector' in resources:
        assert 'vector' in events, events
    if 'graph' in resources:
        assert 'graph' in events, events
    for name in ('cache', 'history'):
        if name in resources:
            assert resources[name]._conn is None, name
            assert name in events, events
    for name in ('writer', 'reader'):
        if name in resources:
            assert name in events, events
            assert events.index(name) < events.index('vector'), events
    if 'cache' in events and 'vector' in events:
        assert events.index('vector') < events.index('cache'), events
    if tasks:
        assert all(task.done() for task in tasks)
        assert not resources['metrics']._background_started
        assert resources['metrics']._cache is None
    if case.startswith('cleanup'):
        assert any('_vector_store close failed' in line for line in diagnostics), diagnostics
    print(f'CLOSE_ORDER={events}\nDIAGNOSTICS={diagnostics}', flush=True)


def shared_ownership(cm, created, case):
    from hy_memory import SharedRuntime
    from hy_memory.metrics import MetricsCollector
    from hy_memory.utils.init_rollback import stop_failed_loops
    runtime = asyncio.run(SharedRuntime.create(config()))
    borrowed = list(created)
    cache = runtime.cache
    metrics = MetricsCollector.get()
    tasks = [metrics._flush_task, metrics._cleanup_task]
    failure = RuntimeError('synthetic borrowed-resource startup failure')
    closed = []

    class Vector:
        _collection_name = 'synthetic'

        async def initialize(self):
            pass

        async def close(self):
            closed.append('vector')
            assert asyncio.get_running_loop() is runtime.loop_thread._loop

    async def fail_reader(registry):
        raise failure

    def fail_hook(rt):
        raise failure

    try:
        try:
            if case == 'shared_ownership':
                with patch.object(cm, 'create_vector_store', return_value=Vector()), patch.object(cm.ComponentFactory, 'get_reader', fail_reader):
                    cm.HyMemoryClient(config(), mode='lite', runtime=runtime)
            else:
                with patch.object(SharedRuntime, '_install_pipeline_observability', fail_hook):
                    asyncio.run(SharedRuntime.create(config()))
        except RuntimeError as exc:
            assert exc is failure
        else:
            raise AssertionError('Expected initialization failure')
        if case == 'shared_ownership':
            assert closed == ['vector']
            assert len(created) == 2
        else:
            assert len(created) == 4
            assert_released(created[2:])
        assert all(lt._thread.is_alive() and lt._loop.is_running() for lt in borrowed)
        assert runtime.cache is cache and cache._conn is not None
        assert metrics._cache is cache and metrics._background_started
        assert tasks == [metrics._flush_task, metrics._cleanup_task]
        assert all(not task.done() for task in tasks)
        assert runtime.loop_thread.run(asyncio.sleep(0, result='usable')) == 'usable'
    finally:
        runtime.close()
        stop_failed_loops(*created)


def cancelled_runtime(cm, created, case):
    from hy_memory import SharedRuntime
    from hy_memory.data.cache_sqlite import SqliteCache
    from hy_memory.metrics import MetricsCollector

    async def scenario():
        parent_loop = asyncio.get_running_loop()
        entered, closing = asyncio.Event(), asyncio.Event()
        release = None
        instances, caches, closed, tasks = [], [], [], []
        original_close = SqliteCache.close

        async def close(cache):
            nonlocal release
            assert asyncio.get_running_loop() is created[0]._loop
            assert instances[0].init_finished
            release = asyncio.Event()
            parent_loop.call_soon_threadsafe(closing.set)
            if case == 'runtime_cancelled_twice':
                await release.wait()
            await original_close(cache)
            closed.append(cache)

        class Runtime(SharedRuntime):
            init_finished = False

            def __init__(self, cfg):
                super().__init__(cfg)
                instances.append(self)

            async def _init_on_loop(self):
                try:
                    await super()._init_on_loop()
                    caches.append(self._cache)
                    tasks.extend([self._metrics._flush_task, self._metrics._cleanup_task])
                    if case == 'runtime_cancelled_after_init':
                        parent_loop.call_soon_threadsafe(outer.cancel)
                    else:
                        parent_loop.call_soon_threadsafe(entered.set)
                        await asyncio.Event().wait()
                finally:
                    self.init_finished = True

        with patch.object(SqliteCache, 'close', close):
            outer = asyncio.create_task(Runtime.create(config()))
            if case != 'runtime_cancelled_after_init':
                await asyncio.wait_for(entered.wait(), timeout=3)
                outer.cancel()
            if case == 'runtime_cancelled_twice':
                await asyncio.wait_for(closing.wait(), timeout=3)
                outer.cancel()
                created[0]._loop.call_soon_threadsafe(release.set)
            try:
                await asyncio.wait_for(outer, timeout=3)
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError('Caller cancellation was swallowed')
        assert closed == caches and len(closed) == 1
        assert all(cache._conn is None for cache in caches)
        assert all(task.done() for task in tasks)
        assert not MetricsCollector.get()._background_started
        assert MetricsCollector.get()._cache is None
        await instances[0].aclose()  # already rolled back: no second close
        assert closed == caches
        assert_released(created)

    asyncio.run(scenario())


def cancelled_native_init(cm, created):
    """A real SQLite worker has not assigned _conn when cancellation arrives."""
    from hy_memory import SharedRuntime
    from hy_memory.data.cache_sqlite import SqliteCache

    async def scenario():
        parent = asyncio.get_running_loop()
        entered = asyncio.Event()
        release, worker_done = threading.Event(), threading.Event()
        caches, closed, owner_tasks, callback_errors = [], [], [], []
        original_init = SqliteCache._init_sync
        original_close = SqliteCache.close

        class OwnerTask(asyncio.Task):
            def cancel(self, *args, **kwargs):
                result = super().cancel(*args, **kwargs)
                if result and self in owner_tasks:
                    def release_after_cancel():
                        if worker_done.is_set() or closed:
                            callback_errors.append('startup was not drained before rollback')
                        release.set()
                    self.get_loop().call_soon(release_after_cancel)
                return result

        class Runtime(SharedRuntime):
            async def _init_on_loop(self):
                owner_tasks.append(asyncio.current_task())
                await super()._init_on_loop()

        def initialize(cache):
            caches.append(cache)
            parent.call_soon_threadsafe(entered.set)
            assert release.wait(5), 'Native worker watchdog expired'
            try:
                original_init(cache)
            finally:
                worker_done.set()

        async def close(cache):
            assert worker_done.is_set(), 'Closed before native allocation finished'
            await original_close(cache)
            closed.append(cache)

        # Install the observing Task factory before the startup task is created.
        original_run = cm._LoopThread._run

        def run(loop_thread):
            loop_thread._loop.set_task_factory(lambda loop, coro, **kw: OwnerTask(coro, loop=loop, **kw))
            original_run(loop_thread)

        try:
            with patch.object(cm._LoopThread, '_run', run), patch.object(SqliteCache, '_init_sync', initialize), patch.object(SqliteCache, 'close', close):
                outer = asyncio.create_task(Runtime.create(config()))
                await asyncio.wait_for(entered.wait(), timeout=3)
                outer.cancel()
                try:
                    await asyncio.wait_for(outer, timeout=3)
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError('Cancellation was swallowed')
            assert not callback_errors, callback_errors
            assert len(caches) == 1 and closed == caches
            assert caches[0]._conn is None
            assert_released(created)
        finally:
            release.set()

    asyncio.run(scenario())


def partial_system2():
    from hy_memory.pipelines.registry import ComponentFactory
    from hy_memory.pipelines.system2_writer import System2Writer

    async def scenario():
        registry = ComponentFactory(config())
        failure = RuntimeError('synthetic partial System2 initialization')
        original = System2Writer.initialize
        close = System2Writer.close
        closed = []

        async def initialize(writer):
            await original(writer)
            raise failure

        async def close_writer(writer):
            closed.append(writer)
            await close(writer)

        # The injected vector is only consulted by writer initialization.
        registry._shared_vector_store = object()
        with patch.object(System2Writer, 'initialize', initialize), patch.object(System2Writer, 'close', close_writer):
            try:
                await registry.get_system2_writer()
            except RuntimeError as exc:
                assert exc is failure
            else:
                raise AssertionError('Expected partial System2 failure')
        assert len(closed) == 1
        assert registry._system2_writer is None

    asyncio.run(scenario())


def run_case(case):
    attempts = []

    def offline(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
            attempts.append(event)
            raise AssertionError(f'Network access forbidden: {event}')

    sys.addaudithook(offline)
    import hy_memory.client as cm
    assert Path(cm.__file__).resolve() == ROOT / 'hy_memory/client.py'
    print(f'SOURCE={cm.__file__}', flush=True)
    created = []

    class TrackedLoop(cm._LoopThread):
        def __init__(self):
            try:
                super().__init__()
            finally:
                created.append(self)

    try:
        with patch.object(cm, '_LoopThread', TrackedLoop):
            if case in ('solo_failure', 'runtime_failure'):
                original_failures(cm, created, case)
            elif case.endswith(('second_loop', 'thread_start')):
                loop_creation_failures(cm, created, case, TrackedLoop)
            elif case in ('shared_ownership', 'previous_metrics'):
                shared_ownership(cm, created, case)
            elif case == 'registry_system2':
                partial_system2()
            elif case == 'runtime_cancelled_native_init':
                cancelled_native_init(cm, created)
            elif case.startswith('runtime_cancelled'):
                cancelled_runtime(cm, created, case)
            else:
                partial_components(cm, created, case)
        assert not attempts, attempts
    finally:
        for lt in created:
            if lt._thread.is_alive():
                lt.stop()
            if not lt._thread.is_alive() and not lt._loop.is_closed():
                lt._loop.close()


if __name__ == '__main__':
    run_case(sys.argv[1])
