# -*- coding: utf-8 -*-
"""HTTP server signal shutdown must not deadlock the serving thread."""

import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hy_memory.server import _request_shutdown


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # pragma: no cover - the test only needs a live server
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # pragma: no cover - silence test output
        pass


def test_request_shutdown_returns_promptly():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        started = time.monotonic()
        _request_shutdown(server)
        assert time.monotonic() - started < 1.0
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        server.server_close()


def test_signal_handler_does_not_deadlock_the_serving_thread():
    """A signal handler calling shutdown() directly hangs forever.

    Run the serving loop on the main thread (as run_server does), fire an
    alarm, and require the process to exit on its own.
    """
    script = textwrap.dedent(
        """
        import signal
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from hy_memory.server import _request_shutdown

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), H)

        def _handler(signum, frame):
            _request_shutdown(server)

        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(1)
        server.serve_forever()
        server.server_close()
        print("exited")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "exited" in result.stdout
