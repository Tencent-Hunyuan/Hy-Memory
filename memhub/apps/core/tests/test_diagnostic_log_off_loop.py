"""诊断日志不得在调用线程上做磁盘 I/O。

真机证据：`~/.one-memory/logs/core.log` 六次 `loop_watchdog event loop blocked`
里有三次 MainThread 栈顶就在 `diagnostic_log.write` —— 两次卡在那把全局锁上，
一次卡在 `os.open`。事件是从事件循环上发出来的，写一条日志要走一遍目录扫描
加一次整盘刷新，磁盘一忙，整个进程就没人应答。

这些守卫按「先证明它本该发生」写：每条都先在旧实现上跑红，再翻成断言。
其中前三条是行为红（旧实现确实会做 I/O、确实会卡住调用方），
不是靠新 API 缺席才红的。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import threading
import time
from pathlib import Path

import pytest

import one_memory.diagnostic_log as module
from one_memory.diagnostic_log import DiagnosticLog

# 注入的「慢盘」单次写入耗时。比任何合理的事件循环预算都大，
# 所以只要还在调用线程上写，测量就一定看得见。
SLOW_WRITE_SECONDS = 0.5
# 事件循环允许的最大滞后。真机 watchdog 的门限是 2.0s，这里收紧一个量级，
# 留给 CI 抖动的余量仍然远大于一次慢写。
MAX_LOOP_LAG_SECONDS = 0.3
LINE_PREFIX = b'{"schema_version"'


class FilesystemSpy:
    """记录每一次落在日志目录上的文件系统动作是由哪个线程发起的。

    只认日志目录下的路径，避免把 pytest 自己的 stat 算进来 —— 否则调用线程
    永远「有 I/O」，断言就成了永远红的假守卫。
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.calls: list[tuple[str, int]] = []
        self._descriptors: set[int] = set()
        self._lock = threading.Lock()

    def _mine(self, path: object) -> bool:
        try:
            resolved = Path(os.fspath(path))  # type: ignore[arg-type]
        except TypeError:
            return False
        try:
            return self.root in resolved.parents or resolved == self.root
        except (OSError, ValueError):
            return False

    def _record(self, name: str) -> None:
        with self._lock:
            self.calls.append((name, threading.get_ident()))

    def names_for_thread(self, ident: int) -> list[str]:
        with self._lock:
            return sorted({name for name, caller in self.calls if caller == ident})

    def count(self, *names: str) -> int:
        wanted = set(names) if names else None
        with self._lock:
            return sum(1 for name, _ in self.calls if wanted is None or name in wanted)

    def install(self, monkeypatch: pytest.MonkeyPatch, *, slow_write: bool = False) -> None:
        real_open = os.open
        real_write = os.write
        real_fsync = os.fsync
        real_chmod = os.chmod
        real_close = os.close
        real_glob = pathlib.Path.glob
        real_stat = pathlib.Path.stat

        def spy_open(path, flags, mode=0o777, **kwargs):  # type: ignore[no-untyped-def]
            descriptor = real_open(path, flags, mode, **kwargs)
            if self._mine(path):
                self._record("os.open")
                with self._lock:
                    self._descriptors.add(descriptor)
            return descriptor

        def spy_write(descriptor, data):  # type: ignore[no-untyped-def]
            with self._lock:
                mine = descriptor in self._descriptors
            if mine:
                self._record("os.write")
                if slow_write and data.startswith(LINE_PREFIX):
                    time.sleep(SLOW_WRITE_SECONDS)
            return real_write(descriptor, data)

        def spy_fsync(descriptor):  # type: ignore[no-untyped-def]
            with self._lock:
                mine = descriptor in self._descriptors
            if mine:
                self._record("os.fsync")
            return real_fsync(descriptor)

        def spy_close(descriptor):  # type: ignore[no-untyped-def]
            # Descriptor numbers get reused. Forgetting to untrack them here
            # would let an unrelated write be attributed to the diagnostic file.
            with self._lock:
                self._descriptors.discard(descriptor)
            return real_close(descriptor)

        def spy_chmod(path, mode, **kwargs):  # type: ignore[no-untyped-def]
            if self._mine(path):
                self._record("os.chmod")
            return real_chmod(path, mode, **kwargs)

        def spy_glob(this, pattern, **kwargs):  # type: ignore[no-untyped-def]
            if self._mine(this):
                self._record("Path.glob")
            return real_glob(this, pattern, **kwargs)

        def spy_stat(this, **kwargs):  # type: ignore[no-untyped-def]
            if self._mine(this):
                self._record("Path.stat")
            return real_stat(this, **kwargs)

        monkeypatch.setattr(os, "open", spy_open)
        monkeypatch.setattr(os, "write", spy_write)
        monkeypatch.setattr(os, "fsync", spy_fsync)
        monkeypatch.setattr(os, "chmod", spy_chmod)
        monkeypatch.setattr(os, "close", spy_close)
        monkeypatch.setattr(pathlib.Path, "glob", spy_glob)
        monkeypatch.setattr(pathlib.Path, "stat", spy_stat)


def _emit(log: DiagnosticLog, index: int) -> None:
    log.write(
        level="info",
        component="memory.scheduler",
        event="memory_scheduler.idle",
        outcome="succeeded",
        details={"count": index},
    )


def _read_events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _wait_for_events(path: Path, expected: int, timeout: float = 10.0) -> list[dict[str, object]]:
    """等到磁盘上出现 expected 条为止。

    不用它来「猜」时间，只用它避免把守卫写成「不写就永远绿」：
    没落盘就是失败，落盘慢也是失败。
    """
    deadline = time.monotonic() + timeout
    events: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        events = _read_events(path)
        if len(events) >= expected:
            return events
        time.sleep(0.01)
    return events


def test_emitting_an_event_does_no_disk_io_on_the_caller_thread(tmp_path, monkeypatch):
    """旧实现下必红：write() 就在调用线程上 glob / stat / open / fsync。"""
    log = DiagnosticLog(tmp_path)
    spy = FilesystemSpy(tmp_path / "logs")
    spy.install(monkeypatch)
    caller = threading.get_ident()

    for index in range(8):
        _emit(log, index)
    # 在本测试自己去读文件之前取样，否则读取动作会被算进调用线程头上。
    on_caller = spy.names_for_thread(caller)

    events = _wait_for_events(log.path, 8)
    # 先证明确实写出去了，否则「调用线程没有 I/O」只是因为什么都没干。
    assert len(events) == 8, f"只落盘 {len(events)} 条，守卫会变成永远绿"
    assert spy.count("os.write") >= 1

    assert not on_caller, f"调用线程仍在做日志磁盘 I/O：{on_caller}"
    log.close()


def test_no_fsync_per_event_on_any_thread(tmp_path, monkeypatch):
    """每条事件一次整盘刷新是这条路径最贵的一步，诊断日志不需要这种耐久性。"""
    log = DiagnosticLog(tmp_path)
    spy = FilesystemSpy(tmp_path / "logs")
    spy.install(monkeypatch)

    for index in range(20):
        _emit(log, index)

    events = _wait_for_events(log.path, 20)
    assert len(events) == 20
    assert spy.count("os.fsync") < 20, "仍然是每条事件一次 fsync"
    log.close()


def test_directory_scan_is_not_repeated_for_every_event(tmp_path, monkeypatch):
    """_prune() 的 glob 原本每条事件跑一次，目录一大就是每条事件一次全目录扫描。"""
    log = DiagnosticLog(tmp_path)
    spy = FilesystemSpy(tmp_path / "logs")
    spy.install(monkeypatch)

    for index in range(20):
        _emit(log, index)

    assert len(_wait_for_events(log.path, 20)) == 20
    assert spy.count("Path.glob") < 20, "仍然是每条事件一次目录扫描"
    log.close()


def test_an_event_emitted_on_the_event_loop_does_not_stall_it(tmp_path, monkeypatch):
    """旧实现下必红：注入慢盘后，循环上每发一条事件就停 0.5s。"""
    log = DiagnosticLog(tmp_path)
    spy = FilesystemSpy(tmp_path / "logs")
    spy.install(monkeypatch, slow_write=True)

    async def scenario() -> float:
        worst = 0.0
        running = True

        async def heartbeat() -> None:
            nonlocal worst
            last = time.monotonic()
            while running:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                worst = max(worst, now - last - 0.01)
                last = now

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)
        for index in range(5):
            _emit(log, index)
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        running = False
        await beat
        return worst

    worst = asyncio.run(scenario())
    assert len(_wait_for_events(log.path, 5, timeout=20.0)) == 5
    assert worst < MAX_LOOP_LAG_SECONDS, f"事件循环被日志写入卡了 {worst:.2f}s"
    log.close()


def test_caller_never_waits_on_a_slow_disk_even_when_the_queue_overflows(tmp_path, monkeypatch):
    """旧实现下必红：调用方每条都要等满一次慢写。

    新实现下队列满了要丢，丢了要留痕，但任何情况下都不许把调用方按住。
    """
    monkeypatch.setattr(module, "MAX_QUEUED_EVENTS", 4, raising=False)
    log = DiagnosticLog(tmp_path)
    spy = FilesystemSpy(tmp_path / "logs")
    spy.install(monkeypatch, slow_write=True)

    slowest = 0.0
    for index in range(40):
        started = time.monotonic()
        _emit(log, index)
        slowest = max(slowest, time.monotonic() - started)
    assert slowest < 0.05, f"最慢的一次 write() 花了 {slowest:.3f}s，调用方被磁盘按住了"

    log.close()
    events = _read_events(log.path)
    assert len(events) < 40, "队列上限没有生效"
    dropped = [item for item in events if item["event"] == "diagnostic.events_dropped"]
    assert dropped, "丢了事件却没有留下任何记录"
    assert int(dropped[-1]["details"]["count"]) > 0  # type: ignore[index]


def test_dropped_record_says_how_many_and_over_what_window(tmp_path, monkeypatch):
    """只写条数不够：看日志的人得能判断「这段是空的」还是「这段被丢了」。

    否则下一轮排查又会以缺席为结论 —— 看不到某个事件，分不清是没发生还是被丢了。
    时间窗用 timestamp（窗口结束）减 duration_ms（窗口长度）表达，
    两个都是 schema 里已有的字段，磁盘上的字段集不变。
    """
    monkeypatch.setattr(module, "MAX_QUEUED_EVENTS", 4, raising=False)
    log = DiagnosticLog(tmp_path)
    spy = FilesystemSpy(tmp_path / "logs")
    spy.install(monkeypatch, slow_write=True)

    first_drop_at = None
    for index in range(40):
        _emit(log, index)
        if index == 8 and first_drop_at is None:
            first_drop_at = time.time()
    time.sleep(0.3)  # 让丢弃的时间窗有可测量的宽度
    for index in range(40, 60):
        _emit(log, index)
    log.close()

    dropped = [
        item for item in _read_events(log.path) if item["event"] == "diagnostic.events_dropped"
    ]
    assert dropped, "丢了事件却没有留下任何记录"
    record = dropped[-1]
    assert int(record["details"]["count"]) > 0  # type: ignore[index]
    assert "duration_ms" in record, "只说了丢多少，没说丢的是哪一段时间"
    window = int(record["duration_ms"])  # type: ignore[arg-type]
    assert window >= 300, f"时间窗 {window} ms 没覆盖到实际的丢弃区间"
    # 窗口结束点就是这条记录自己的时间戳，两者一起才定位得出区间。
    assert str(record["timestamp"]).endswith("Z")


def test_close_drains_everything_that_was_queued(tmp_path):
    """干净退出不许丢事件。"""
    log = DiagnosticLog(tmp_path)
    for index in range(200):
        _emit(log, index)
    log.close()

    events = _read_events(log.path)
    assert len(events) == 200


def test_order_and_line_format_are_unchanged(tmp_path):
    log = DiagnosticLog(tmp_path, session_id="11111111-1111-4111-8111-111111111111")
    for index in range(50):
        _emit(log, index)
    log.close()

    text = log.path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    events = [json.loads(line) for line in text.splitlines()]
    assert [int(item["details"]["count"]) for item in events] == list(range(50))  # type: ignore[index]
    # 仍然是原来的紧凑 ASCII JSON：没有空格分隔符，没有非 ASCII 字面量。
    for line in text.splitlines():
        assert ", " not in line and '": ' not in line
        assert line == line.encode("ascii", "strict").decode("ascii")
    assert events[0]["schema_version"] == 1
    assert events[0]["service"] == "core"
    assert events[0]["session_id"] == "11111111-1111-4111-8111-111111111111"


def test_events_from_several_threads_keep_each_thread_internal_order(tmp_path):
    log = DiagnosticLog(tmp_path)
    started = threading.Barrier(4)

    def emit_many(base: int) -> None:
        started.wait()
        for index in range(50):
            _emit(log, base + index)

    threads = [threading.Thread(target=emit_many, args=(base,)) for base in (0, 100, 200, 300)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    log.close()

    events = _read_events(log.path)
    assert len(events) == 200
    for base in (0, 100, 200, 300):
        mine = [
            int(item["details"]["count"])  # type: ignore[index]
            for item in events
            if base <= int(item["details"]["count"]) < base + 50  # type: ignore[index]
        ]
        assert mine == list(range(base, base + 50))


def test_symlinked_log_file_is_still_refused(tmp_path):
    log = DiagnosticLog(tmp_path)
    log.path.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("private")
    log.path.symlink_to(outside)

    _emit(log, 1)

    assert not log.close(timeout=5), "拒绝写入之后不该报告已落盘"
    assert outside.read_text() == "private"


def test_symlinked_log_directory_is_still_refused(tmp_path):
    log = DiagnosticLog(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "logs").symlink_to(elsewhere, target_is_directory=True)

    _emit(log, 1)

    assert not log.close(timeout=5), "拒绝写入之后不该报告已落盘"
    assert list(elsewhere.iterdir()) == []


def test_directory_and_file_permissions_are_still_owner_only(tmp_path):
    """先把权限放宽，否则 open/mkdir 的 mode 就已经够了，断言等于没在守。

    变异验证第一轮就是这么抓出来的：删掉两处 chmod，测试照样绿，
    因为新建文件本来就是 0600、新建目录本来就是 0700。
    """
    log = DiagnosticLog(tmp_path)
    log.path.parent.mkdir(parents=True)
    log.path.write_text("")
    os.chmod(log.path.parent, 0o755)
    os.chmod(log.path, 0o644)

    _emit(log, 1)
    log.close()

    assert log.path.stat().st_mode & 0o777 == 0o600
    assert log.path.parent.stat().st_mode & 0o777 == 0o700


def test_rotation_still_happens_when_the_file_grows_past_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 4096)
    log = DiagnosticLog(tmp_path)
    for index in range(250):
        _emit(log, index)
    log.close()

    rotated = log.path.with_suffix(log.path.suffix + ".1")
    assert rotated.exists(), "超过上限后没有轮转"
    assert log.path.stat().st_size <= 4096 + 1024


def test_old_logs_are_still_pruned_on_the_first_write(tmp_path):
    log = DiagnosticLog(tmp_path)
    log.path.parent.mkdir(parents=True)
    stale = log.path.parent / "diagnostic-runtime.jsonl"
    stale.write_text("{}\n")
    old = time.time() - 20 * 24 * 3600
    os.utime(stale, (old, old))

    _emit(log, 1)
    log.close()

    assert not stale.exists(), "过期日志没有被清理"


def test_invalid_input_is_still_rejected_on_the_caller_side(tmp_path):
    """校验必须留在调用线程，否则错误会被吞进后台线程，调用方看不到。"""
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValueError, match="diagnostic_detail_key_invalid"):
        log.write(
            level="error",
            component="hy.bridge",
            event="hy.failed",
            outcome="failed",
            details={"invalid key": "private"},
        )
    log.close()


def test_a_failing_disk_never_reaches_the_caller(tmp_path, monkeypatch):
    """调用方不该看见异常，但「什么都没做」不算通过。

    只验证 `_emit` 不抛异常的话，一个把所有事件直接扔掉的实现也能绿。
    所以这里还要求：写线程确实试过落盘，并且它没有谎称关停成功。
    """
    real_open = os.open
    attempts = []

    def refuse(path, flags, mode=0o777, **kwargs):  # type: ignore[no-untyped-def]
        if str(path).endswith("diagnostic-core.jsonl"):
            attempts.append(path)
            raise OSError("disk is full")
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", refuse)
    log = DiagnosticLog(tmp_path)
    for index in range(10):
        _emit(log, index)

    assert not log.close(timeout=5), "一个字节都没写进去，close() 却报告成功"
    assert attempts, "写线程根本没试过打开文件，这条守卫是空的"
