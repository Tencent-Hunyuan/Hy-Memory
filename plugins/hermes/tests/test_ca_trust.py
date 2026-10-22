"""Scoped CA configuration and real ephemeral localhost TLS regressions."""

import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import threading

import pytest
import hermes_hy_memory.provider as provider_module
from hermes_hy_memory import HyMemoryProvider
from hermes_hy_memory.provider import ProviderError, _deadline_handlers


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    for key in os.environ:
        if key.startswith("HY_MEMORY_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HY_MEMORY_USER_ID", "owner")
    monkeypatch.setenv("HY_MEMORY_AGENT_ID", "agent")


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("openssl required for ephemeral TLS certificates")
    directory = tmp_path_factory.mktemp("tls")
    pairs = []
    for name in ("localhost", "other"):
        cert, key = directory / (name + ".crt"), directory / (name + ".key")
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            check=True,
            capture_output=True,
        )
        pairs.append((cert, key))
    return pairs


@pytest.mark.parametrize("kind", ["missing", "malformed", "permission", "empty"])
def test_invalid_ca_is_sanitized(tmp_path, monkeypatch, kind):
    path = tmp_path / "PRIVATE-path.pem"
    if kind == "malformed":
        path.write_text("PRIVATE malformed PEM")
    if kind == "permission":

        def denied(*args, **kwargs):
            raise PermissionError("PRIVATE path or PEM")

        monkeypatch.setattr(ssl, "create_default_context", denied)
    monkeypatch.setenv("HY_MEMORY_CA_FILE", "" if kind == "empty" else str(path))
    provider = HyMemoryProvider()
    assert not provider.is_available()
    assert provider._config_error == "invalid_configuration"
    with pytest.raises(ProviderError, match="^invalid_configuration$"):
        provider.initialize("session")
    assert "PRIVATE" not in json.dumps(provider.identity_signature())
    assert "PRIVATE" not in provider.unavailable_reason()


def test_context_is_scoped_and_initialized_once(certificates, monkeypatch):
    cert = certificates[0][0]
    original = ssl.create_default_context
    calls = []

    def create(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", create)
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(cert))
    monkeypatch.setenv("SSL_CERT_FILE", "/does/not/exist/ambient.pem")
    global_factory = ssl._create_default_https_context
    provider = HyMemoryProvider()
    assert provider.is_available()
    context = provider._ssl_context
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert _deadline_handlers(100, context)[1]._context is context
    provider.is_available()
    provider.identity_signature()
    assert calls == [{"cadata": cert.read_text()}]
    assert ssl._create_default_https_context is global_factory
    expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    assert provider.identity_signature()["hy_memory_ca_sha256"] == expected
    assert str(cert) not in json.dumps(provider.identity_signature())


def test_rotation_at_same_path_changes_identity(certificates, tmp_path, monkeypatch):
    path = tmp_path / "ca.pem"
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(path))
    path.write_bytes(certificates[0][0].read_bytes())
    first = HyMemoryProvider()
    path.write_bytes(certificates[1][0].read_bytes())
    second = HyMemoryProvider()
    assert first.is_available() and second.is_available()
    assert first.identity_signature() != second.identity_signature()


def test_unset_retains_default_handler_and_official_https(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unset CA must retain existing trust setup")

    monkeypatch.setattr(ssl, "create_default_context", forbidden)
    monkeypatch.setenv("HY_MEMORY_URL", "https://example.com")
    provider = HyMemoryProvider()
    assert provider.is_available()
    assert provider._ssl_context is None
    context = _deadline_handlers(100)[1]._context
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("mode", ["trusted", "untrusted", "hostname-mismatch"])
def test_real_localhost_tls(certificates, monkeypatch, mode):
    cert, key = certificates[0]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    failures = []

    def serve():
        try:
            conn, _ = listener.accept()
            with conn, context.wrap_socket(conn, server_side=True) as tls:
                tls.settimeout(2)
                # urllib can send headers and the POST body in separate TLS
                # records. Drain the complete request before closing, or the
                # unread body can cause a reset instead of a clean response.
                max_headers, max_body = 65536, 1024 * 1024
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = tls.recv(min(4096, max_headers + 1 - len(request)))
                    if not chunk:
                        raise ValueError("truncated HTTP headers")
                    request.extend(chunk)
                    if len(request) > max_headers and b"\r\n\r\n" not in request:
                        raise ValueError("oversized HTTP headers")
                headers, received_body = bytes(request).split(b"\r\n\r\n", 1)
                if len(headers) + 4 > max_headers:
                    raise ValueError("oversized HTTP headers")
                lengths = []
                for line in headers.split(b"\r\n")[1:]:
                    name, separator, value = line.partition(b":")
                    if not separator:
                        raise ValueError("malformed HTTP header")
                    if name.lower() == b"transfer-encoding":
                        raise ValueError("unsupported transfer encoding")
                    if name.lower() == b"content-length":
                        lengths.append(value.strip())
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError("invalid Content-Length")
                length = int(lengths[0])
                if length > max_body or len(received_body) > length:
                    raise ValueError("oversized HTTP body")
                remaining = length - len(received_body)
                while remaining:
                    chunk = tls.recv(min(4096, remaining))
                    if not chunk:
                        raise ValueError("truncated HTTP body")
                    remaining -= len(chunk)
                body = b'{"memories":[]}'
                tls.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
        except ssl.SSLError:
            pass  # Expected peer rejection in negative cases.
        except Exception as exc:
            failures.append(type(exc).__name__)

    host = "127.0.0.1" if mode == "hostname-mismatch" else "localhost"
    monkeypatch.setenv("HY_MEMORY_URL", f"https://{host}:{listener.getsockname()[1]}")
    if mode != "untrusted":
        monkeypatch.setenv("HY_MEMORY_CA_FILE", str(cert))
    provider = HyMemoryProvider()
    assert provider.is_available()
    provider.initialize("tls-session")
    worker = threading.Thread(target=serve)
    worker.start()
    try:
        result = json.loads(
            provider.handle_tool_call("hy_memory_search", {"query": "q"})
        )
        if mode == "trusted":
            assert result == {"success": True, "memories": []}
        else:
            assert result == {
                "success": False,
                "error": "transport_or_response_failure",
            }
    finally:
        provider.shutdown()
        listener.close()
        worker.join(3)
    assert not worker.is_alive()
    assert not failures


@pytest.mark.parametrize("scoped", [False, True])
def test_request_handler_context_and_legacy_argument_count(
    certificates, monkeypatch, scoped
):
    if scoped:
        monkeypatch.setenv("HY_MEMORY_CA_FILE", str(certificates[0][0]))
    provider = HyMemoryProvider()
    calls = []

    def handlers(*args):
        calls.append(args)
        return ()

    class Response:
        status = 200
        body = b'{"memories":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read1(self, size):
            body, self.body = self.body, b""
            return body

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(provider_module, "_deadline_handlers", handlers)
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    assert provider._request("search", {}) == {"memories": []}
    assert len(calls) == 1
    assert len(calls[0]) == (2 if scoped else 1)
    if scoped:
        assert calls[0][1] is provider._ssl_context


def test_context_without_ca_inspection(certificates, monkeypatch):
    original = ssl.create_default_context
    calls = []

    class Context:
        def __init__(self, context):
            self.context = context

        def get_ca_certs(self, *args, **kwargs):
            calls.append(True)
            raise NotImplementedError

        @property
        def check_hostname(self):
            return self.context.check_hostname

        @check_hostname.setter
        def check_hostname(self, value):
            self.context.check_hostname = value

        @property
        def verify_mode(self):
            return self.context.verify_mode

        @verify_mode.setter
        def verify_mode(self, value):
            self.context.verify_mode = value

    monkeypatch.setattr(
        ssl, "create_default_context", lambda **kw: Context(original(**kw))
    )
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(certificates[0][0]))
    provider = HyMemoryProvider()
    assert provider.is_available()
    assert provider._ssl_context.check_hostname
    assert provider._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert calls == []


def test_single_read_rotation_snapshot(certificates, tmp_path, monkeypatch):
    path = tmp_path / "ca.pem"
    snapshot = certificates[0][0].read_bytes()
    path.write_bytes(snapshot)
    original = ssl.create_default_context
    original_open = open
    reads = []

    def tracked_open(file, *args, **kwargs):
        if file == str(path):
            reads.append(file)
        return original_open(file, *args, **kwargs)

    def create(**kwargs):
        path.write_bytes(certificates[1][0].read_bytes())
        assert kwargs == {"cadata": snapshot.decode("ascii")}
        return original(**kwargs)

    monkeypatch.setattr("builtins.open", tracked_open)
    monkeypatch.setattr(ssl, "create_default_context", create)
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(path))
    provider = HyMemoryProvider()
    assert provider.is_available()
    assert reads == [str(path)]
    assert (
        provider.identity_signature()["hy_memory_ca_sha256"]
        == hashlib.sha256(
            ssl.PEM_cert_to_DER_cert(snapshot.decode("ascii"))
        ).hexdigest()
    )


def test_bundle_order_whitespace_and_der_identity(certificates, tmp_path, monkeypatch):
    path = tmp_path / "ca.pem"
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(path))
    first, second = [pair[0].read_text() for pair in certificates]
    path.write_text(first + second)
    bundle = HyMemoryProvider()
    path.write_text("\n" + second + "\n" + first + "\n")
    reordered = HyMemoryProvider()
    assert bundle.is_available() and reordered.is_available()
    assert bundle.identity_signature() == reordered.identity_signature()
    path.write_text(first)
    pem = HyMemoryProvider()
    path.write_bytes(ssl.PEM_cert_to_DER_cert(first))
    der = HyMemoryProvider()
    assert pem.is_available() and der.is_available()
    assert pem.identity_signature() == der.identity_signature()


@pytest.mark.parametrize(
    "contents",
    [
        b"",
        b"x" * 1048577,
        b"\xffPRIVATE",
        b"-----BEGIN CERTIFICATE-----\nPRIVATE\n-----END CERTIFICATE-----",
    ],
)
def test_invalid_ca_snapshot(tmp_path, monkeypatch, contents):
    path = tmp_path / "PRIVATE.pem"
    path.write_bytes(contents)
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(path))
    provider = HyMemoryProvider()
    assert not provider.is_available()
    assert provider._config_error == "invalid_configuration"
    with pytest.raises(ProviderError, match="^invalid_configuration$"):
        provider.initialize("session")


def test_real_truststore_context(certificates, monkeypatch):
    import importlib.util

    if importlib.util.find_spec("truststore") is None:
        return  # Optional dependency; deterministic regression always runs above.
    import truststore

    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise NotImplementedError

    monkeypatch.setattr(truststore.SSLContext, "get_ca_certs", forbidden)
    monkeypatch.setattr(ssl, "SSLContext", truststore.SSLContext)
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(certificates[0][0]))
    provider = HyMemoryProvider()
    assert provider.is_available()
    assert isinstance(provider._ssl_context, truststore.SSLContext)
    assert provider._ssl_context.check_hostname
    assert provider._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert calls == []


def test_maximum_size_bundle_is_accepted(certificates, tmp_path, monkeypatch):
    snapshot = certificates[0][0].read_bytes()
    path = tmp_path / "ca.pem"
    path.write_bytes(snapshot + b"\n" * (1048576 - len(snapshot)))
    monkeypatch.setenv("HY_MEMORY_CA_FILE", str(path))
    provider = HyMemoryProvider()
    assert provider.is_available()
    assert (
        provider.identity_signature()["hy_memory_ca_sha256"]
        == hashlib.sha256(
            ssl.PEM_cert_to_DER_cert(snapshot.decode("ascii"))
        ).hexdigest()
    )
