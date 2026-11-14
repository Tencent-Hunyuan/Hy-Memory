"""Late failed-owner exits must close real loops and FDs without caller retry."""
import asyncio
import errno
import faulthandler
import io
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ['ready_late_exit', 'shutdown_late_exit', 'normal_exit', 'late_close_error', 'owner_returned_before_failure']


@pytest.mark.parametrize('case', CASES)
def test_failed_loop_finalization(case, tmp_path):
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
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ('Task exception was never retrieved', 'Task was destroyed',
                    'was never awaited', 'Exception in callback', 'Exception ignored'):
        assert warning not in result.stderr, result.stderr


def fd_is_closed(fd):
    try:
        os.fstat(fd)
    except OSError as error:
        assert error.errno == errno.EBADF
        return True
    return False


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
    rb.logger.addHandler(logging.StreamHandler(messages))
    release = threading.Event()
    entered = threading.Event()
    shutdown_entered = threading.Event()
    close_calls = []
    close_original = None
    lt = cm._LoopThread.__new__(cm._LoopThread)

    def track_close():
        nonlocal close_original
        close_original = lt._loop.close

        def close():
            close_calls.append((threading.get_ident(), lt._loop.is_running()))
            if case == 'late_close_error':
                raise ValueError('synthetic late final close failure')
            close_original()
        lt._loop.close = close

    # Preserve the actual five-second constructor ready wait; shorten only the
    # failed-cleanup observation/join budgets. Neither case retries the helper.
    with patch.object(rb, '_LOOP_CLEANUP_TIMEOUT', 0.1):
        if case == 'owner_returned_before_failure':
            class HeldThread(threading.Thread):
                def run(self):
                    super().run()
                    entered.set()  # The SDK target has already returned.
                    release.wait()

            with patch.object(threading, 'Thread', HeldThread):
                cm._LoopThread.__init__(lt)
            track_close()
            selector = lt._loop._selector
            sockets = (lt._loop._ssock, lt._loop._csock)
            fds = [selector.fileno(), *(sock.fileno() for sock in sockets)]
            lt._loop.call_soon_threadsafe(lt._loop.stop)
            assert entered.wait(timeout=2)
            assert lt._thread.is_alive() and not lt._loop.is_running()
            try:
                rb.stop_failed_loops(lt)
                states = {'loop_closed': lt._loop.is_closed(),
                          'fd_closed': [fd_is_closed(fd) for fd in fds]}
                print(f'NO_RETRY case={case} target already returned; states={states}', flush=True)
            finally:
                release.set()
                lt._thread.join(timeout=2)
            assert not lt._thread.is_alive()
            assert states['loop_closed'] and all(states['fd_closed']), states
            assert close_calls == [(threading.get_ident(), False)], close_calls
            faulthandler.cancel_dump_traceback_later()
            return
        if case == 'normal_exit':
            with patch.object(rb, 'close_failed_loop') as finalize:
                cm._LoopThread.__init__(lt)
                track_close()
                lt.stop()
                assert not lt._thread.is_alive()
                assert not lt._close_on_exit
                finalize.assert_not_called()
            # Normal stop may close on its caller when PR #91 is overlaid. The
            # new failed-owner finalizer must stay inactive in either version.
            assert all(owner == threading.get_ident() and not running
                       for owner, running in close_calls), close_calls
            print(f'NORMAL_EXIT failed finalizer inactive; normal stop closed={lt._loop.is_closed()}', flush=True)
            if not lt._loop.is_closed():
                close_original()
            faulthandler.cancel_dump_traceback_later()
            return
        if case in ('ready_late_exit', 'late_close_error'):
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
                else:
                    raise AssertionError('Expected real startup timeout')
            elapsed = time.monotonic() - started
            assert entered.is_set() and 5 <= elapsed < 8, elapsed
            assert not lt._ready.is_set() and not lt._loop.is_running()
            print(f'ORIGINAL_READY_ERROR returned in {elapsed:.3f}s', flush=True)
            track_close()
        else:
            cm._LoopThread.__init__(lt)
            track_close()

            def worker():
                entered.set()
                release.wait()

            async def start_worker():
                asyncio.get_running_loop().run_in_executor(None, worker)
            lt.run(start_worker())
            assert entered.wait(timeout=2)
            shutdown_executor = lt._loop.shutdown_default_executor

            async def observed_shutdown():
                shutdown_entered.set()
                await shutdown_executor()
            lt._loop.shutdown_default_executor = observed_shutdown
            original_error = RuntimeError('synthetic initialization failure')
            started = time.monotonic()
            try:
                try:
                    raise original_error
                except BaseException:
                    rb.stop_failed_loops(lt)
                    raise
            except RuntimeError as error:
                assert error is original_error
            elapsed = time.monotonic() - started
            assert shutdown_entered.is_set() and elapsed < 2, elapsed
            assert 'shutdown timed out' in messages.getvalue(), messages.getvalue()
            assert lt._loop.is_running(), 'Shutdown must still be executing'
            print(f'ORIGINAL_INIT_ERROR returned in {elapsed:.3f}s during executor shutdown', flush=True)

        assert lt._thread.is_alive() and not lt._loop.is_closed()
        # Hold explicit loop, selector and socket references: GC must not be
        # mistaken for SDK-owned finalization after the failed constructor.
        loop = lt._loop
        selector = loop._selector
        sockets = (loop._ssock, loop._csock)
        fds = [selector.fileno(), *(sock.fileno() for sock in sockets)]
        assert all(not fd_is_closed(fd) for fd in fds)
        assert not close_calls, 'Do not close while startup/shutdown is blocked'
        release.set()
        lt._thread.join(timeout=2)
        assert not lt._thread.is_alive(), 'Owner did not exit after release'
        states = {'loop_closed': loop.is_closed(), 'fd_closed': [fd_is_closed(fd) for fd in fds]}
        print(f'NO_RETRY case={case} states={states}; close_calls={close_calls}', flush=True)
        assert close_calls == [(lt._thread.ident, False)], close_calls
        if case == 'late_close_error':
            assert not states['loop_closed'] and not any(states['fd_closed']), states
            assert 'synthetic late final close failure' in messages.getvalue()
            # Only this deliberately failing-close test releases its fixtures.
            close_original()
            assert all(fd_is_closed(fd) for fd in fds)
        else:
            assert states['loop_closed'] and all(states['fd_closed']), states
        print(messages.getvalue(), flush=True)
    faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    run_case(sys.argv[1])
