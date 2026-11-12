"""A profile's output-token field reaches every generation request (#287, #427).

Some OpenAI-compatible providers refuse `max_tokens` and require
`max_completion_tokens`. The field is a profile option, `output_token_parameter`,
and the probe, both memory-answer paths and the SDK configuration send the same
one. A probe refused over the field fails with a code that names the fix rather
than passing on a retry the rest of the pipeline would not make.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import fields

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from one_memory.hy_sdk_runtime import build_sdk_config
from one_memory.memory_qa import _answer_request
from one_memory.model_probe import ModelProber, ProbeCode, _structured_request
from one_memory.model_profiles import (
    ModelProfileInput,
    ModelProfilePatch,
    ModelProfileStore,
    ProviderKind,
    output_token_field,
)
from one_memory.sdk_memory_qa import _sdk_answer_request
from one_memory.sdk_migration_verify import read_active_profile
from one_memory.secrets import InMemorySecretStore

pytestmark = pytest.mark.contract


def _create(store, secrets, *, parameter: str | None = None, name: str = "openai"):
    extra = {} if parameter is None else {"output_token_parameter": parameter}
    return store.create(
        ModelProfileInput(
            name=name,
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            api_key=SecretStr("sk-test"),
            embedding_model="embedding-model",
            embedding_provider_kind=ProviderKind.LOCAL_ONNX,
            **extra,
        ),
        secrets,
    )


def test_profiles_default_to_max_tokens_and_keep_the_choice(tmp_path) -> None:
    secrets = InMemorySecretStore()
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    default = _create(store, secrets, name="default")
    chosen = _create(store, secrets, parameter="max_completion_tokens", name="chosen")

    assert default.output_token_parameter == "max_tokens"
    assert chosen.output_token_parameter == "max_completion_tokens"
    assert store.require(chosen.profile_id, secrets).output_token_parameter == (
        "max_completion_tokens"
    )
    snapshot = store.snapshot(chosen.profile_id, chosen.revision)
    assert snapshot.output_token_parameter == "max_completion_tokens"
    with pytest.raises(ValidationError):
        ModelProfileInput(
            name="bad",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.example.com/v1",
            model="gpt-test",
            output_token_parameter="max_output_tokens",  # type: ignore[arg-type]
        )


def test_a_patch_changes_it_in_a_new_revision_and_keeps_the_old_snapshot(tmp_path) -> None:
    secrets = InMemorySecretStore()
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    profile = _create(store, secrets)

    untouched = store.update(
        profile.profile_id,
        ModelProfilePatch(expected_revision=profile.revision, name="renamed"),
        secrets,
    )
    assert untouched.output_token_parameter == "max_tokens"
    changed = store.update(
        profile.profile_id,
        ModelProfilePatch(
            expected_revision=untouched.revision,
            output_token_parameter="max_completion_tokens",
        ),
        secrets,
    )

    assert changed.revision == untouched.revision + 1
    assert changed.output_token_parameter == "max_completion_tokens"
    assert store.snapshot(profile.profile_id, changed.revision).output_token_parameter == (
        "max_completion_tokens"
    )
    assert store.snapshot(profile.profile_id, untouched.revision).output_token_parameter == (
        "max_tokens"
    )


def test_a_store_from_before_the_option_gets_the_column_with_max_tokens(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    store = ModelProfileStore(database)
    profile = _create(store, secrets)
    with sqlite3.connect(database) as connection:
        for table in ("model_profiles", "model_profile_versions"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN output_token_parameter")

    # A read-only status probe beside a Core that has not reopened the store.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE model_profiles SET is_active=1")
    assert read_active_profile(database).output_token_parameter == "max_tokens"

    reopened = ModelProfileStore(database)
    with sqlite3.connect(database) as connection:
        for table in ("model_profiles", "model_profile_versions"):
            columns = {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}
            assert columns["output_token_parameter"][3] == 1  # NOT NULL
    assert reopened.require(profile.profile_id, secrets).output_token_parameter == "max_tokens"
    assert (
        reopened.snapshot(profile.profile_id, profile.revision).output_token_parameter
        == "max_tokens"
    )


def test_the_probe_and_both_answer_paths_send_the_profile_field(tmp_path) -> None:
    secrets = InMemorySecretStore()
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    default = _create(store, secrets, name="default")
    chosen = _create(store, secrets, parameter="max_completion_tokens", name="chosen")

    for profile, field, other in (
        (default, "max_tokens", "max_completion_tokens"),
        (chosen, "max_completion_tokens", "max_tokens"),
    ):
        assert output_token_field(profile) == field
        for force_plain in (False, True):
            *_, probe = _structured_request(profile, "sk-test", force_plain=force_plain)
            assert probe[field] == 256 and other not in probe
        *_, answer = _answer_request(profile, "sk-test", "我偏好什么？", ())
        assert answer[field] == 768 and other not in answer
        *_, sdk_answer = _sdk_answer_request(profile, "sk-test", "我偏好什么？", ())
        assert sdk_answer[field] == 768 and other not in sdk_answer


def test_ollama_and_anthropic_requests_keep_their_native_fields(tmp_path) -> None:
    secrets = InMemorySecretStore()
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    ollama = store.create(
        ModelProfileInput(
            name="ollama",
            provider_kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            model="qwen",
            output_token_parameter="max_completion_tokens",
        ),
        secrets,
    )
    anthropic = store.create(
        ModelProfileInput(
            name="anthropic",
            provider_kind=ProviderKind.ANTHROPIC,
            model="claude",
            api_key=SecretStr("sk-test"),
            output_token_parameter="max_completion_tokens",
        ),
        secrets,
    )
    *_, ollama_probe = _structured_request(ollama, None)
    assert "max_completion_tokens" not in ollama_probe and "max_tokens" not in ollama_probe
    *_, anthropic_probe = _structured_request(anthropic, "sk-test")
    assert anthropic_probe["max_tokens"] == 64
    assert "max_completion_tokens" not in anthropic_probe


def _probe_once(tmp_path, response: httpx.Response, payload: dict[str, object]):
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return response

    prober = ModelProber(
        store=ModelProfileStore(tmp_path / "probe.sqlite3"),
        secrets=InMemorySecretStore(),
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await prober._request_stage(
                client,
                "structured_output",
                "POST",
                "https://example.test/v1/chat/completions",
                {},
                payload,
                require_structured_json=True,
            )

    return asyncio.run(run()), requests


@pytest.mark.parametrize(
    "error",
    [
        # The OpenAI shape #427 matched.
        {
            "param": "max_tokens",
            "code": "unsupported_parameter",
            "message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.",
        },
        # No `param`, both fields named in the message.
        {"message": "max_tokens is not supported with this model; use max_completion_tokens"},
    ],
)
def test_a_refused_max_tokens_fails_with_a_code_that_names_the_fix(tmp_path, error) -> None:
    payload = {"model": "m", "max_tokens": 256, "messages": []}
    stage, requests = _probe_once(tmp_path, httpx.Response(400, json={"error": error}), payload)

    assert stage.ok is False
    assert stage.code is ProbeCode.OUTPUT_TOKEN_PARAMETER_REJECTED
    # Not retried with the other field: the SDK would still send this one.
    assert len(requests) == 1
    assert payload == {"model": "m", "max_tokens": 256, "messages": []}


def test_the_reverse_refusal_is_reported_too(tmp_path) -> None:
    error = {
        "param": "max_completion_tokens",
        "message": "Unrecognized request argument max_completion_tokens; use max_tokens",
    }
    stage, _ = _probe_once(
        tmp_path,
        httpx.Response(400, json={"error": error}),
        {"model": "m", "max_completion_tokens": 256, "messages": []},
    )
    assert stage.code is ProbeCode.OUTPUT_TOKEN_PARAMETER_REJECTED


@pytest.mark.parametrize(
    ("status", "error"),
    [
        # Names the replacement only: the refusal is about something else.
        (400, {"param": "response_format", "message": "use max_completion_tokens"}),
        # A max_tokens complaint that asks for no other field (a value range).
        (400, {"param": "max_tokens", "message": "max_tokens must be <= 4096"}),
        # Not a 400.
        (401, {"param": "max_tokens", "message": "Use max_completion_tokens instead."}),
    ],
)
def test_other_errors_keep_their_classification(tmp_path, status, error) -> None:
    stage, requests = _probe_once(
        tmp_path,
        httpx.Response(status, json={"error": error}),
        {"model": "m", "max_tokens": 256, "messages": []},
    )
    assert stage.code is not ProbeCode.OUTPUT_TOKEN_PARAMETER_REJECTED
    assert len(requests) == 1


def _sdk_takes_the_field() -> bool:
    from hy_memory.config import LLMConfig  # type: ignore[import-untyped]

    return "output_token_parameter" in {item.name for item in fields(LLMConfig)}


@pytest.mark.skipif(
    not _sdk_takes_the_field(),
    reason="the installed hy-memory predates llm.output_token_parameter (2.0.16)",
)
def test_the_sdk_config_carries_the_profile_field_and_ignores_the_env(
    tmp_path, monkeypatch
) -> None:
    from hy_memory.agent.llm_provider import LLMProvider  # type: ignore[import-untyped]

    secrets = InMemorySecretStore()
    store = ModelProfileStore(tmp_path / "one-memory.sqlite3")
    default = _create(store, secrets, name="default")
    chosen = _create(store, secrets, parameter="max_completion_tokens", name="chosen")

    # A stray environment value never wins over the profile's own field.
    monkeypatch.setenv("MEMORY_LLM_OUTPUT_TOKEN_PARAMETER", "max_completion_tokens")
    default_config = build_sdk_config(
        store.snapshot(default.profile_id, default.revision),
        api_key="sk-test",
        data_dir=tmp_path / "data",
    )
    monkeypatch.setenv("MEMORY_LLM_OUTPUT_TOKEN_PARAMETER", "max_tokens")
    chosen_config = build_sdk_config(
        store.snapshot(chosen.profile_id, chosen.revision),
        api_key="sk-test",
        data_dir=tmp_path / "data",
    )

    assert default_config.llm.output_token_parameter == "max_tokens"
    assert chosen_config.llm.output_token_parameter == "max_completion_tokens"
    # The SDK's own request builder reads the same field.
    provider = LLMProvider(chosen_config)
    assert provider._llm_config.output_token_parameter == "max_completion_tokens"
