from one_memory.input_sanitization import REDACTED_SECRET, sanitize_model_input


def test_redacts_cli_and_assignment_secrets_without_removing_context() -> None:
    value = (
        "docker login --username kristinhe --password 9dcb07f6687411f1ab6c525400bd71fc "
        "and token=abc123456789secret"
    )

    sanitized = sanitize_model_input(value)

    assert "docker login" in sanitized
    assert "--username kristinhe" in sanitized
    assert f"--password {REDACTED_SECRET}" in sanitized
    assert f"token={REDACTED_SECRET}" in sanitized
    assert "9dcb07f6687411f1ab6c525400bd71fc" not in sanitized
    assert "abc123456789secret" not in sanitized


def test_redacts_bearer_known_tokens_urls_and_private_keys() -> None:
    value = """
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature
OpenAI key sk-abcdefghijklmnopqrstuvwxyz
Clone https://alice:secret-password@example.com/repo
-----BEGIN PRIVATE KEY-----
secret-key-material
-----END PRIVATE KEY-----
"""

    sanitized = sanitize_model_input(value)

    assert sanitized.count(REDACTED_SECRET) == 4
    assert "eyJhbGci" not in sanitized
    assert "sk-abcdef" not in sanitized
    assert "secret-password" not in sanitized
    assert "secret-key-material" not in sanitized


def test_redacts_quoted_json_assignments_and_basic_authorization() -> None:
    value = (
        '{"api_key": "json-secret-value", "password": "space containing secret"}\n'
        "Authorization: Basic dXNlcjpwYXNzd29yZA=="
    )

    sanitized = sanitize_model_input(value)

    assert '"api_key": "<redacted-secret>"' in sanitized
    assert '"password": "<redacted-secret>"' in sanitized
    assert "Authorization: Basic <redacted-secret>" in sanitized
    assert "json-secret-value" not in sanitized
    assert "space containing secret" not in sanitized
    assert "dXNlcjpwYXNzd29yZA" not in sanitized


def test_does_not_redact_non_secret_ids() -> None:
    value = "Use cluster cls-antiv4ty and task f457e768-5339-47b2-9366-87aecae1f8f6"

    assert sanitize_model_input(value) == value


def test_redacts_a_private_key_cut_off_before_its_end_marker() -> None:
    value = "cat id_rsa\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA"

    sanitized = sanitize_model_input(value)

    assert sanitized == f"cat id_rsa\n{REDACTED_SECRET}"


def test_a_complete_private_key_keeps_the_text_after_it() -> None:
    value = "-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----\nafter"

    assert sanitize_model_input(value) == f"{REDACTED_SECRET}\nafter"


def test_tool_output_with_an_oversized_private_key_leaks_no_key_material() -> None:
    from one_memory.imports.sources.parsing import tool_evidence_text

    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 400
    key = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"

    text = tool_evidence_text({"name": "cat", "output": key})

    assert text is not None
    assert text.startswith("[cat] ")
    assert "MIIEvQ" not in text


def test_many_unterminated_key_headers_are_scanned_once() -> None:
    import time

    value = "x -----BEGIN PRIVATE KEY----- y\n" * 20_000

    started = time.monotonic()
    sanitized = sanitize_model_input(value)

    assert time.monotonic() - started < 2
    assert sanitized == f"x {REDACTED_SECRET}"


def test_redacts_credentials_in_urls_of_any_scheme() -> None:
    value = (
        "DATABASE_URL=postgres://alice:s3cr3t-pass@db.internal:5432/app\n"
        "MONGODB_URI=mongodb+srv://alice:s3cr3t-pass@cluster0.example.net/db\n"
        "BROKER=amqp://alice:s3cr3t-pass@mq.internal:5672/\n"
        "REDIS_URL=redis://:s3cr3t-pass@cache.internal:6379/0\n"
    )

    sanitized = sanitize_model_input(value)

    assert sanitized.count(REDACTED_SECRET) == 4
    assert "s3cr3t-pass" not in sanitized
    assert f"DATABASE_URL=postgres://alice:{REDACTED_SECRET}@db.internal:5432/app" in sanitized
