# -*- coding: utf-8 -*-
"""
HY Memory - 日志系统配置

标准 Python logging + TimedRotatingFileHandler，
每天一个文件，轮转后 gzip 压缩，保留 N 天（默认 15，可用
MEMORY_LOG_RETENTION_DAYS 配置）。支持 request_id 注入，
可按 request_id grep 日志过滤完整请求链路。

Usage:
    from hy_memory.utils.log_setup import setup_logging, set_request_id

    setup_logging()  # 应用启动时调用一次
    set_request_id("abc123def456")  # 每个请求入口设置

日志文件位置:
    {MEMORY_DATA_DIR}/logs/hy_memory_{date}.log
    默认: ~/.hy_memory/logs/hy_memory_{date}.log
"""

import contextvars
import gzip
import logging
import os
import re
import shutil
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

# ================================================================
# request_id 上下文变量 (协程安全)
# ================================================================

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def set_request_id(request_id: str) -> None:
    """设置当前协程/线程的 request_id"""
    _request_id_var.set(request_id)


def get_request_id() -> str:
    """获取当前协程/线程的 request_id"""
    return _request_id_var.get()


class request_id_scope:
    """
    临时把 request_id 设为指定值（默认哨兵 "-"），退出时恢复原值。

    用于「进程级 / 初始化」日志：即便外层（如 App 请求上下文）已 set 了业务
    request_id，初始化期间的日志也不应继承它，否则按 request_id 过滤会混入
    一次性 init 噪音。

    用法：
        with request_id_scope():   # 默认 "-"
            ... 初始化、打 init 日志 ...
    """

    def __init__(self, request_id: str = "-"):
        self._rid = request_id
        self._token = None

    def __enter__(self):
        self._token = _request_id_var.set(self._rid)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _request_id_var.reset(self._token)
        return False


# ================================================================
# 自定义 Filter: 将 request_id 注入 LogRecord
# ================================================================

class RequestIdFilter(logging.Filter):
    """
    从 contextvars 读取 request_id 并注入 LogRecord。

    这样所有 handler 的 formatter 都可以用 %(request_id)s。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        return True


class _StdoutLevelFilter(logging.Filter):
    """
    stdout handler 专用：只放 DEBUG/INFO 通过，WARNING 及以上拒绝。

    配合独立的 stderr handler（setLevel=WARNING）实现 Unix 惯例的
    双流分离，避免 INFO 日志被监控系统（如 OpenClaw）按 stderr=WARN 误标。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


# ================================================================
# 日志配置
# ================================================================

_LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 轮转文件名: hy_memory.log.YYYY-MM-DD，或 gzip 压缩后的 ...YYYY-MM-DD.gz。
# TimedRotatingFileHandler 自身的 extMatch（`(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)`
# fullmatch）认不出 `.gz` 后缀，外部 gzip 过的轮转文件因此永远不进
# backupCount 清理列表（issue #158 实测：越压越涨）。
_ROTATED_SUFFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:\.gz)?$")

# 全局标记，避免重复配置
_logging_configured = False


def _gzip_namer(default_name: str) -> str:
    """轮转文件名追加 .gz（Python logging howto 的 namer 用法）"""
    return default_name + ".gz"


def _gzip_rotator(source: str, dest: str) -> None:
    """把刚轮转出去的文件压缩成 gzip，再删掉原文件。"""
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def _env_int(name: str, default: int) -> int:
    """读取正整数环境变量；未设置/非法时用默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


class _DailyRotatingFileHandler(TimedRotatingFileHandler):
    """每天午夜轮转 + 可选 gzip 压缩 + 保留 N 天（.gz 一并计入）。

    覆写 getFilesToDelete：不再依赖基类的 extMatch（认不出 `.gz`），
    而是自行清点 `hy_memory.log.YYYY-MM-DD` 与 `.gz` 两种轮转文件，
    文件名即 ISO 日期，字符串排序即时间顺序；超出 backupCount 的最旧
    文件删除。旧版本留下的未压缩轮转文件同样被清点，升级后不会逃逸。
    """

    def getFilesToDelete(self):
        dir_name, base_name = os.path.split(self.baseFilename)
        prefix = base_name + "."
        rotated = [
            f for f in os.listdir(dir_name)
            if f.startswith(prefix) and _ROTATED_SUFFIX_RE.fullmatch(f[len(prefix):])
        ]
        if len(rotated) < self.backupCount:
            return []
        rotated.sort()
        return [
            os.path.join(dir_name, f)
            for f in rotated[: len(rotated) - self.backupCount]
        ]


class SingleLineFormatter(logging.Formatter):
    """
    纯文本 formatter，但保证每条日志输出为单行。

    问题：message / exception 里可能含真实换行符（如用户 query 带 \\n），
    单行模板被换行符截断后，下游按行采集的平台会拿到半截记录并报解析错误。
    解决：渲染完成后把 \\r\\n 等控制换行转义为字面 \\n / \\r。
    """

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        if "\n" in s or "\r" in s:
            s = s.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
        return s


class JsonLineFormatter(logging.Formatter):
    """
    结构化 JSON Lines formatter（每条日志一行合法 JSON）。

    供按 JSON 解析每一行的日志采集平台使用：message 里的换行会被 json
    自然转义，不会截断，也不会出现 "[json] error field" 之类的解析报错。

    字段：ts / level / request_id / logger / msg（+ exc 异常栈，如有）。
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "ts": self.formatTime(record, _DATE_FORMAT),
            "level": record.levelname,
            "request_id": getattr(record, "request_id", "-"),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _make_formatter() -> logging.Formatter:
    """
    根据 MEMORY_LOG_FORMAT 选择 formatter：
    - "json"          → JsonLineFormatter（结构化，供 JSON 采集平台）
    - 其它/未设置(默认) → SingleLineFormatter（纯文本，但保证单行不被换行截断）
    """
    fmt = os.getenv("MEMORY_LOG_FORMAT", "text").strip().lower()
    if fmt == "json":
        return JsonLineFormatter()
    return SingleLineFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)


def _default_log_dir() -> str:
    """
    确定日志目录:
    1. 环境变量 MEMORY_LOG_DIR
    2. {MEMORY_DATA_DIR}/logs/
    3. ~/.hy_memory/logs/
    """
    explicit = os.getenv("MEMORY_LOG_DIR")
    if explicit:
        return explicit
    data_dir = os.getenv(
        "MEMORY_DATA_DIR",
        os.path.join(str(Path.home()), ".hy_memory"),
    )
    return os.path.join(data_dir, "logs")


def setup_logging(
    log_dir: Optional[str] = None,
    retention_days: Optional[int] = None,
    level: Optional[int] = None,
) -> None:
    """
    配置 hy_memory 模块的日志。

    - 文件输出: _DailyRotatingFileHandler, 每天午夜滚动, gzip 压缩滚动文件,
      保留 retention_days 天（.gz 与旧版未压缩文件一并计入清理）
    - 控制台输出: 双流分离 — DEBUG/INFO 走 stdout，WARNING/ERROR/CRITICAL 走 stderr
      （遵循 Unix 惯例；OpenClaw 等监控平台会把 stderr 整体标 WARN，
       INFO 走 stdout 才不会被误归为告警）
    - 所有日志行包含 request_id 字段

    Args:
        log_dir:        日志目录。默认 {MEMORY_DATA_DIR}/logs/
        retention_days: 日志保留天数。默认从环境变量 MEMORY_LOG_RETENTION_DAYS
                        读取，缺省 15
        level:          日志级别。默认从环境变量 MEMORY_LOG_LEVEL 读取，缺省 INFO

    环境变量:
        MEMORY_LOG_RETENTION_DAYS: 保留天数，正整数，默认 15
        MEMORY_LOG_COMPRESS:      滚动文件是否 gzip 压缩，默认 1；设 0/false/no/off 关闭

    可多次调用，但只有首次生效。
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    if level is None:
        level = getattr(logging, os.getenv("MEMORY_LOG_LEVEL", "INFO").upper(), logging.INFO)
    if retention_days is None:
        retention_days = _env_int("MEMORY_LOG_RETENTION_DAYS", 15)
    compress = os.getenv("MEMORY_LOG_COMPRESS", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }

    # 确定日志目录并创建
    resolved_dir = log_dir or _default_log_dir()
    os.makedirs(resolved_dir, exist_ok=True)

    # hy_memory 根 logger
    root_logger = logging.getLogger("hy_memory")
    root_logger.setLevel(level)

    # request_id filter — 必须加在 handler 上而非 logger 上。
    # 原因: Python logging propagation 机制中，子 logger (如 hy_memory.client)
    # 的日志记录传播到父 logger 时，只调用父 logger 的 handlers，
    # 不经过父 logger 的 filters。如果 filter 只在 logger 上，
    # 子 logger 的记录到达 handler 时 record 上没有 request_id，
    # formatter 就会 KeyError crash。
    req_filter = RequestIdFilter()

    formatter = _make_formatter()

    # ---- 文件 Handler ----
    log_file = os.path.join(resolved_dir, "hy_memory.log")
    file_handler = _DailyRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
    )
    # 滚动后文件名: hy_memory.log.2026-03-21[.gz]
    file_handler.suffix = "%Y-%m-%d"
    if compress:
        file_handler.namer = _gzip_namer
        file_handler.rotator = _gzip_rotator
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    file_handler.addFilter(req_filter)
    root_logger.addHandler(file_handler)

    # ---- 控制台 Handler: 双流分离 ----
    # stdout: DEBUG / INFO（用 filter 排除 WARNING 及以上）
    # stderr: WARNING / ERROR / CRITICAL
    # 修复 Issue: 之前所有级别都走单 StreamHandler 默认 stderr，
    # OpenClaw 等监控平台会把整个 stderr 标 WARN，导致 INFO 日志
    # 被误标为告警，刷屏。
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(req_filter)
    stdout_handler.addFilter(_StdoutLevelFilter())
    root_logger.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    # stderr 永远只接 WARNING+，独立于全局 level（即使全局 level=DEBUG，
    # stderr 也不会拿到 INFO/DEBUG 行）
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.addFilter(req_filter)
    root_logger.addHandler(stderr_handler)

    # 避免日志向上传播到 root logger 导致重复输出
    root_logger.propagate = False

    # Suppress noisy third-party debug logs
    for noisy in ("openai", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root_logger.debug(
        f"Logging configured: dir={resolved_dir} "
        f"retention={retention_days}d compress={compress} level={logging.getLevelName(level)}"
    )
