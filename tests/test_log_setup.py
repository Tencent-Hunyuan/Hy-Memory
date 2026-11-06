# -*- coding: utf-8 -*-
"""
Logging stream split 测试 — 验证 INFO/DEBUG 走 stdout，WARNING/ERROR/CRITICAL 走 stderr。

修复 issue: 之前所有级别都走单 StreamHandler 默认 stderr，OpenClaw 等监控
平台会把整个 stderr 标 WARN，导致 INFO 日志被误标为告警。
"""

import io
import logging
import sys

import pytest

from hy_memory.utils import log_setup
from hy_memory.utils.log_setup import _StdoutLevelFilter

@pytest.fixture(autouse=True)
def _reset_logging_state(tmp_path, monkeypatch):
    """每个 case 把 hy_memory logger 和 _logging_configured 全部重置"""
    # 重置 module-level guard
    monkeypatch.setattr(log_setup, "_logging_configured", False)
    # 清掉已有 handlers
    hy_logger = logging.getLogger("hy_memory")
    old_handlers = hy_logger.handlers[:]
    old_propagate = hy_logger.propagate
    hy_logger.handlers.clear()

    # 重定向日志目录到 tmp（避免污染 ~/.hy_memory）
    monkeypatch.setenv("MEMORY_LOG_DIR", str(tmp_path / "logs"))

    yield

    hy_logger.handlers.clear()
    for h in old_handlers:
        hy_logger.addHandler(h)
    hy_logger.propagate = old_propagate
    monkeypatch.setattr(log_setup, "_logging_configured", False)


class TestStdoutLevelFilter:
    """直接验证 filter 类的行为"""

    def test_passes_debug(self):
        f = _StdoutLevelFilter()
        rec = logging.LogRecord("x", logging.DEBUG, "", 0, "msg", None, None)
        assert f.filter(rec) is True

    def test_passes_info(self):
        f = _StdoutLevelFilter()
        rec = logging.LogRecord("x", logging.INFO, "", 0, "msg", None, None)
        assert f.filter(rec) is True

    def test_blocks_warning(self):
        f = _StdoutLevelFilter()
        rec = logging.LogRecord("x", logging.WARNING, "", 0, "msg", None, None)
        assert f.filter(rec) is False

    def test_blocks_error(self):
        f = _StdoutLevelFilter()
        rec = logging.LogRecord("x", logging.ERROR, "", 0, "msg", None, None)
        assert f.filter(rec) is False

    def test_blocks_critical(self):
        f = _StdoutLevelFilter()
        rec = logging.LogRecord("x", logging.CRITICAL, "", 0, "msg", None, None)
        assert f.filter(rec) is False


class TestSetupLoggingHandlerRouting:
    """验证 setup_logging 配出的 handlers 是 stdout/stderr split 结构"""

    def test_two_stream_handlers_present(self):
        log_setup.setup_logging(level=logging.DEBUG)
        hy_logger = logging.getLogger("hy_memory")
        stream_handlers = [
            h for h in hy_logger.handlers if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and getattr(h, "stream", None) in {sys.stdout, sys.stderr}
        ]
        # stdout + stderr = 2 个 stream handler
        assert len(stream_handlers) == 2, f"expected 2 stream handlers, got {len(stream_handlers)}"

        targets = {h.stream for h in stream_handlers}
        assert sys.stdout in targets, "expected one handler targeting sys.stdout"
        assert sys.stderr in targets, "expected one handler targeting sys.stderr"

    def test_stdout_handler_has_level_filter(self):
        log_setup.setup_logging(level=logging.INFO)
        hy_logger = logging.getLogger("hy_memory")
        stdout_handler = next(
            h for h in hy_logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and h.stream is sys.stdout
        )
        # 必须有 _StdoutLevelFilter
        has_filter = any(isinstance(f, _StdoutLevelFilter) for f in stdout_handler.filters)
        assert has_filter, "stdout handler must have _StdoutLevelFilter"

    def test_stderr_handler_level_warning(self):
        log_setup.setup_logging(level=logging.DEBUG)  # 即使全局 DEBUG
        hy_logger = logging.getLogger("hy_memory")
        stderr_handler = next(
            h for h in hy_logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and h.stream is sys.stderr
        )
        # stderr handler 永远 WARNING+
        assert stderr_handler.level == logging.WARNING


class TestEndToEndStreamRouting:
    """端到端：把 stdout/stderr 重定向到 buffer，验证 record 真的去对了地方"""

    def _setup_with_redirected_streams(self, monkeypatch):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out_buf)
        monkeypatch.setattr(sys, "stderr", err_buf)
        log_setup.setup_logging(level=logging.DEBUG)
        return out_buf, err_buf

    def test_info_to_stdout(self, monkeypatch):
        out_buf, err_buf = self._setup_with_redirected_streams(monkeypatch)
        log = logging.getLogger("hy_memory.test_info")
        log.info("INFO_LINE_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()
        assert "INFO_LINE_TOKEN" in out_buf.getvalue()
        assert "INFO_LINE_TOKEN" not in err_buf.getvalue()

    def test_debug_to_stdout(self, monkeypatch):
        out_buf, err_buf = self._setup_with_redirected_streams(monkeypatch)
        log = logging.getLogger("hy_memory.test_debug")
        log.debug("DEBUG_LINE_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()
        assert "DEBUG_LINE_TOKEN" in out_buf.getvalue()
        assert "DEBUG_LINE_TOKEN" not in err_buf.getvalue()

    def test_warning_to_stderr(self, monkeypatch):
        out_buf, err_buf = self._setup_with_redirected_streams(monkeypatch)
        log = logging.getLogger("hy_memory.test_warn")
        log.warning("WARN_LINE_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()
        assert "WARN_LINE_TOKEN" not in out_buf.getvalue()
        assert "WARN_LINE_TOKEN" in err_buf.getvalue()

    def test_error_to_stderr(self, monkeypatch):
        out_buf, err_buf = self._setup_with_redirected_streams(monkeypatch)
        log = logging.getLogger("hy_memory.test_err")
        log.error("ERROR_LINE_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()
        assert "ERROR_LINE_TOKEN" not in out_buf.getvalue()
        assert "ERROR_LINE_TOKEN" in err_buf.getvalue()

    def test_critical_to_stderr(self, monkeypatch):
        out_buf, err_buf = self._setup_with_redirected_streams(monkeypatch)
        log = logging.getLogger("hy_memory.test_crit")
        log.critical("CRITICAL_LINE_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()
        assert "CRITICAL_LINE_TOKEN" not in out_buf.getvalue()
        assert "CRITICAL_LINE_TOKEN" in err_buf.getvalue()

    def test_no_duplicate_emission_per_line(self, monkeypatch):
        """同一条 log 不应同时出现在 stdout 和 stderr"""
        out_buf, err_buf = self._setup_with_redirected_streams(monkeypatch)
        log = logging.getLogger("hy_memory.test_dup")
        log.info("UNIQUE_INFO_TOKEN")
        log.warning("UNIQUE_WARN_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()

        out = out_buf.getvalue()
        err = err_buf.getvalue()
        # INFO 在 stdout，不在 stderr
        assert out.count("UNIQUE_INFO_TOKEN") == 1
        assert err.count("UNIQUE_INFO_TOKEN") == 0
        # WARN 在 stderr，不在 stdout
        assert out.count("UNIQUE_WARN_TOKEN") == 0
        assert err.count("UNIQUE_WARN_TOKEN") == 1


class TestSingleLineAndJsonFormat:
    """换行不应截断日志行；JSON 模式输出合法 JSON。"""

    def _record(self, msg):
        rec = logging.LogRecord(
            "hy_memory.test", logging.INFO, "f.py", 1, msg, None, None
        )
        rec.request_id = "req-xyz"
        return rec

    def test_text_formatter_escapes_newlines(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LOG_FORMAT", "text")
        fmt = log_setup._make_formatter()
        out = fmt.format(self._record("query='a\nb\rc'"))
        # 单行：渲染结果不含真实换行/回车
        assert "\n" not in out and "\r" not in out
        assert "\\n" in out and "\\r" in out
        assert "req-xyz" in out

    def test_json_formatter_is_valid_json_and_single_line(self, monkeypatch):
        import json as _json
        monkeypatch.setenv("MEMORY_LOG_FORMAT", "json")
        fmt = log_setup._make_formatter()
        out = fmt.format(self._record("query='a\nb\rc'"))
        assert "\n" not in out and "\r" not in out
        obj = _json.loads(out)  # 必须是合法 JSON
        assert obj["request_id"] == "req-xyz"
        assert obj["level"] == "INFO"
        assert obj["logger"] == "hy_memory.test"
        # 原始换行保留在字段值里（被 JSON 转义后存储，不截断行）
        assert obj["msg"] == "query='a\nb\rc'"

    def test_default_is_text(self, monkeypatch):
        monkeypatch.delenv("MEMORY_LOG_FORMAT", raising=False)
        fmt = log_setup._make_formatter()
        assert isinstance(fmt, log_setup.SingleLineFormatter)


class TestLogStreamOptIn:
    """MEMORY_LOG_STREAM=stderr：DEBUG/INFO 并入 stderr，stdout 保持纯净。

    stdio 传输宿主（如 MCP server over stdio）要求 stdout 只承载协议帧；
    默认行为（双流分离）必须保持不变。
    """

    def _redirect(self, monkeypatch):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out_buf)
        monkeypatch.setattr(sys, "stderr", err_buf)
        return out_buf, err_buf

    def _emit_and_flush(self):
        log = logging.getLogger("hy_memory.test_optin")
        log.info("OPTIN_INFO_TOKEN")
        log.warning("OPTIN_WARN_TOKEN")
        for h in logging.getLogger("hy_memory").handlers:
            h.flush()

    def test_stderr_mode_keeps_stdout_clean(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LOG_STREAM", "stderr")
        out_buf, err_buf = self._redirect(monkeypatch)
        log_setup.setup_logging(level=logging.DEBUG)
        self._emit_and_flush()
        assert out_buf.getvalue() == ""
        assert "OPTIN_INFO_TOKEN" in err_buf.getvalue()
        assert "OPTIN_WARN_TOKEN" in err_buf.getvalue()

    def test_stderr_mode_no_console_handler_targets_stdout(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LOG_STREAM", "stderr")
        log_setup.setup_logging(level=logging.DEBUG)
        hy_logger = logging.getLogger("hy_memory")
        stream_handlers = [
            h for h in hy_logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert stream_handlers, "expected console stream handlers"
        assert all(h.stream is not sys.stdout for h in stream_handlers)

    def test_default_unchanged_info_still_on_stdout(self, monkeypatch):
        monkeypatch.delenv("MEMORY_LOG_STREAM", raising=False)
        out_buf, err_buf = self._redirect(monkeypatch)
        log_setup.setup_logging(level=logging.DEBUG)
        self._emit_and_flush()
        assert "OPTIN_INFO_TOKEN" in out_buf.getvalue()
        assert "OPTIN_INFO_TOKEN" not in err_buf.getvalue()

    def test_explicit_stdout_value_behaves_like_default(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LOG_STREAM", "stdout")
        out_buf, err_buf = self._redirect(monkeypatch)
        log_setup.setup_logging(level=logging.DEBUG)
        self._emit_and_flush()
        assert "OPTIN_INFO_TOKEN" in out_buf.getvalue()
        assert "OPTIN_INFO_TOKEN" not in err_buf.getvalue()

    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MEMORY_LOG_STREAM", "not-a-stream")
        out_buf, err_buf = self._redirect(monkeypatch)
        log_setup.setup_logging(level=logging.DEBUG)
        self._emit_and_flush()
        assert "OPTIN_INFO_TOKEN" in out_buf.getvalue()
        assert "OPTIN_INFO_TOKEN" not in err_buf.getvalue()
