"""A capture the hook hands off outlives that hook; it has to end itself.

The hook exits once the prompt is in the pipe, and the host only ever killed
the hook's own shell. Nothing is left to stop a capture that gets stuck, so
the process carries its own limit.
"""

import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.serial,
    pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX alarm; capture on Windows is not covered"
    ),
]


def _stuck_capture(tmp_path, deadline: str | None) -> subprocess.Popen[bytes]:
    env = {
        key: value for key, value in os.environ.items() if key != "ONE_MEMORY_CAPTURE_DEADLINE_S"
    }
    env.update(HOME=str(tmp_path), ONE_MEMORY_DATA_DIR=str(tmp_path / "data"))
    if deadline is not None:
        env["ONE_MEMORY_CAPTURE_DEADLINE_S"] = deadline
    # Stdin is held open and never written: the capture blocks on its read,
    # which is what one that is stuck looks like from outside.
    return subprocess.Popen(
        [sys.executable, "-m", "one_memory.cli", "capture", "workbuddy"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _alive_after(processes: list[subprocess.Popen[bytes]], seconds: float) -> int:
    until = time.monotonic() + seconds
    while time.monotonic() < until and any(p.poll() is None for p in processes):
        time.sleep(0.05)
    return sum(p.poll() is None for p in processes)


def _reap(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait()
        assert process.stdin is not None
        process.stdin.close()


def test_a_stuck_capture_stays_until_its_deadline(tmp_path) -> None:
    # The hang is real: this is what every round would leave behind.
    stuck = [_stuck_capture(tmp_path, None) for _ in range(3)]
    try:
        assert _alive_after(stuck, 3) == 3
    finally:
        _reap(stuck)


def test_stuck_captures_with_a_deadline_leave_nothing_behind(tmp_path) -> None:
    stuck = [_stuck_capture(tmp_path, "1") for _ in range(3)]
    try:
        assert _alive_after(stuck, 6) == 0
        assert {p.returncode for p in stuck} == {-signal.SIGALRM}
    finally:
        _reap(stuck)


def _armed_seconds(tmp_path, deadline: str | None) -> int:
    """What `main` leaves on the alarm clock, read back by the process itself."""

    env = {
        key: value for key, value in os.environ.items() if key != "ONE_MEMORY_CAPTURE_DEADLINE_S"
    }
    env.update(HOME=str(tmp_path), ONE_MEMORY_DATA_DIR=str(tmp_path / "data"))
    if deadline is not None:
        env["ONE_MEMORY_CAPTURE_DEADLINE_S"] = deadline
    probe = (
        "import signal, sys\n"
        "sys.argv = ['one-memory', 'capture', 'workbuddy']\n"
        "import one_memory.cli as cli\n"
        "cli.run = lambda arguments: print(signal.alarm(0)) or 0\n"
        "cli.main()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], env=env, capture_output=True, text=True, check=False
    )
    return int(result.stdout.strip())


@pytest.mark.parametrize("deadline", [None, "0", "-1", "soon", "", "²", "9" * 40, "1000000"])
def test_a_capture_the_hook_did_not_time_still_ends_itself(tmp_path, deadline) -> None:
    # The Stop hook and every other host's capture hook pass nothing.
    assert 110 <= _armed_seconds(tmp_path, deadline) <= 120


def test_the_deadline_the_hook_hands_over_is_the_one_armed(tmp_path) -> None:
    assert _armed_seconds(tmp_path, "7") == 7


def test_run_as_the_entry_it_ends_itself_while_still_importing(tmp_path) -> None:
    # Importing the rest of Core is where a frozen build or a cold disk
    # stalls; here that import never returns.
    entry = (
        "import runpy, sys, time\n"
        "class Stall:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'one_memory.config':\n"
        "            time.sleep(30)\n"
        "sys.meta_path.insert(0, Stall())\n"
        "sys.argv = ['one-memory', 'capture', 'workbuddy']\n"
        "runpy.run_module('one_memory.cli', run_name='__main__')\n"
    )
    env = {**os.environ, "HOME": str(tmp_path), "ONE_MEMORY_DATA_DIR": str(tmp_path / "data")}
    env["ONE_MEMORY_CAPTURE_DEADLINE_S"] = "1"
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", entry], stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    try:
        assert _alive_after([process], 10) == 0
    finally:
        _reap([process])
    assert process.returncode == -signal.SIGALRM
    assert time.monotonic() - started < 10


def test_with_no_core_a_real_capture_exits_at_once_and_writes_nothing(tmp_path) -> None:
    # The process a host actually starts: no Core is running for this home,
    # so the turn is dropped. Nothing may fall back to writing the database.
    data_dir = tmp_path / "data"
    env = {
        key: value for key, value in os.environ.items() if key != "ONE_MEMORY_CAPTURE_DEADLINE_S"
    }
    env.update(HOME=str(tmp_path), ONE_MEMORY_DATA_DIR=str(data_dir))
    payload = b'{"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "hi"}'
    result = subprocess.run(
        [sys.executable, "-m", "one_memory.cli", "capture", "workbuddy"],
        input=payload,
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    assert not (data_dir / "one-memory.sqlite3").exists()
