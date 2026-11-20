"""Exercise real loop startup/rollback in offline, hard-deadline subprocesses."""
import asyncio
import faulthandler
import io
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    'ready_released', 'ready_permanently_blocked', 'stopped', 'closed',
    'stopped_thread_alive', 'closed_thread_alive', 'busy', 'submission_race',
    'cleanup_error', 'owner_thread', 'shutdown_error',
]


@pytest.mark.parametrize('case', CASES)
def test_loop_startup_rollback(case, tmp_path):
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
    try:
        result = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).resolve()), case],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b'').decode(errors='replace')
        output += (error.stderr or b'').decode(errors='replace')
        pytest.fail('Child exceeded hard deadline (15s):\n' + output)
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ('Task exception was never retrieved', 'Task was destroyed',
                    'was never awaited', 'Exception in callback', 'Exception ignored'):
        assert warning not in result.stderr, result.stderr


def run_case(case):
    def offline(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
            raise AssertionError('NETWORK_DISABLED: ' + event)
    sys.addaudithook(offline)
    import hy_memory.client as cm
    from hy_memory.utils import init_rollback as rb
    assert Path(cm.__file__).resolve() == ROOT / 'hy_memory/client.py'
    faulthandler.dump_traceback_later(13)
    messages = io.StringIO()
    handler = logging.StreamHandler(messages)
    rb.logger.addHandler(handler)
    release = threading.Event()
    entered = threading.Event()
    lt = cm._LoopThread.__new__(cm._LoopThread)

    # Only shorten rollback waits. The real constructor still waits five seconds
    # on its real threading.Event, and its real _run blocks before run_forever.
    timeout = 5.0 if case == 'ready_released' else 0.15
    with patch.object(rb, '_LOOP_CLEANUP_TIMEOUT', timeout, create=True):
        if case.startswith('ready_'):
            set_loop = asyncio.set_event_loop

            def blocked_start(loop):
                entered.set()
                release.wait()
                set_loop(loop)

            started = time.monotonic()
            with patch.object(asyncio, 'set_event_loop', blocked_start):
                try:
                    cm._LoopThread.__init__(lt)
                except RuntimeError as error:
                    assert str(error) == 'background event loop failed to start'
                    traceback.print_exc()
                else:
                    raise AssertionError('Real ready timeout was not raised')
                elapsed = time.monotonic() - started
                assert entered.is_set() and not lt._ready.is_set()
                assert 5 <= elapsed < 12, elapsed
                assert lt._thread.is_alive() and not lt._loop.is_running()
                assert not lt._loop.is_closed()
                assert 'not ready' in messages.getvalue(), messages.getvalue()
                assert 'did not stop' in messages.getvalue(), messages.getvalue()
                print(f'REAL_READY_TIMEOUT returned in {elapsed:.3f}s; live thread retained', flush=True)
                if case == 'ready_permanently_blocked':
                    # The daemon belongs exclusively to this child process. It
                    # cannot safely be killed or have its open loop closed here.
                    return
                release.set()
                lt._thread.join(timeout=2)
            assert not lt._thread.is_alive()
        elif case == 'cleanup_error':
            startup_error = RuntimeError('synthetic thread start failure')
            loop = asyncio.new_event_loop()
            with patch.object(threading.Thread, 'start', side_effect=startup_error), \
                 patch.object(asyncio, 'new_event_loop', return_value=loop), \
                 patch.object(loop, 'close', side_effect=ValueError('synthetic close failure')):
                try:
                    cm._LoopThread.__init__(lt)
                except RuntimeError as error:
                    assert error is startup_error
                else:
                    raise AssertionError('Startup exception was replaced')
            assert 'synthetic close failure' in messages.getvalue()
        else:
            original_run = cm._LoopThread._run

            def held_after_run(self):
                original_run(self)
                if case == 'closed_thread_alive':
                    self._loop.close()
                entered.set()
                release.wait()

            with patch.object(cm._LoopThread, '_run', held_after_run if case.endswith('thread_alive') else original_run):
                cm._LoopThread.__init__(lt)
            assert lt._ready.is_set() and lt._loop.is_running()
            if case in ('stopped', 'closed', 'stopped_thread_alive', 'closed_thread_alive'):
                lt._loop.call_soon_threadsafe(lt._loop.stop)
                if case.endswith('thread_alive'):
                    assert entered.wait(timeout=2)
                else:
                    lt._thread.join(timeout=2)
                    assert not lt._thread.is_alive()
                if case == 'closed':
                    lt._loop.close()
            elif case == 'busy':
                def block_loop():
                    entered.set()
                    release.wait()
                lt._loop.call_soon_threadsafe(block_loop)
                assert entered.wait(timeout=2)
            elif case == 'submission_race':
                submit = lt._loop.call_soon_threadsafe

                def stop_before_submission(callback, *args, **kwargs):
                    submit(lt._loop.stop)
                    lt._thread.join(timeout=2)
                    assert not lt._thread.is_alive()
                    return submit(callback, *args, **kwargs)

                lt._loop.call_soon_threadsafe = stop_before_submission

            started = time.monotonic()
            if case == 'owner_thread':
                def cleanup_on_owner():
                    rb.stop_failed_loops(lt)
                    entered.set()
                lt._loop.call_soon_threadsafe(cleanup_on_owner)
                assert entered.wait(timeout=2)
                lt._thread.join(timeout=2)
                assert not lt._thread.is_alive()
                assert 'cannot join itself' in messages.getvalue()
            elif case == 'shutdown_error':
                async def fail_shutdown():
                    raise ValueError('synthetic async generator shutdown failure')
                with patch.object(lt._loop, 'shutdown_asyncgens', fail_shutdown):
                    rb.stop_failed_loops(lt)
                assert 'synthetic async generator shutdown failure' in messages.getvalue()
            else:
                rb.stop_failed_loops(lt)
            assert time.monotonic() - started < 3
            if case.endswith('thread_alive') or case == 'busy':
                assert lt._thread.is_alive()
                if case != 'closed_thread_alive':
                    assert not lt._loop.is_closed()
                assert messages.getvalue(), 'Residual thread must be diagnosed'
                release.set()
                lt._thread.join(timeout=2)
                assert not lt._thread.is_alive()
            if case == 'submission_race':
                lt._loop.call_soon_threadsafe = submit

        rb.stop_failed_loops(lt)
        rb.stop_failed_loops(lt)  # Repeated failure cleanup is harmless.
        assert not lt._thread.is_alive() and lt._loop.is_closed()
        print('CASE=' + case + ' PASS; ' + messages.getvalue(), flush=True)
    faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    run_case(sys.argv[1])
