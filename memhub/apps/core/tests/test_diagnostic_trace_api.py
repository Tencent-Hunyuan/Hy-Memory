import json
from uuid import uuid4

from fastapi.testclient import TestClient

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.diagnostic_log import (
    configure_diagnostics,
    diagnostic_event,
    flush_diagnostics,
    reset_diagnostics,
)
from one_memory.hy_runtime_config import set_detailed_logging


def test_trace_lookup_is_disabled_until_detailed_logging_is_enabled(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="trace-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer trace-token"}) as client:
        response = client.post("/api/v1/diagnostics/trace", json={"request_id": str(uuid4())})
    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "state": "disabled", "events": []}


def test_model_usage_route_returns_content_free_totals(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="trace-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer trace-token"}) as client:
        response = client.get("/api/v1/diagnostics/model-usage")
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "summary": {
            "request_count": 0,
            "succeeded": 0,
            "failed": 0,
            "in_flight": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
            "reasoning_tokens": 0,
            "avg_prompt_tokens": 0,
            "avg_completion_tokens": 0,
        },
        "requests": [],
        "by_minute": [],
    }


def test_trace_lookup_returns_only_safe_structured_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    set_detailed_logging(tmp_path, True)
    request_id = str(uuid4())
    configure_diagnostics(tmp_path)
    try:
        diagnostic_event(
            level="error",
            component="memory.task",
            event="memory_task.finished",
            outcome="failed",
            request_id=request_id,
            duration_ms=17,
            error_code="synthetic_failure",
            details={"phase": "hy_ingest"},
        )
        assert flush_diagnostics()
    finally:
        reset_diagnostics()
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="trace-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer trace-token"}) as client:
        response = client.post("/api/v1/diagnostics/trace", json={"request_id": request_id})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ok"
    assert body["events"] == [
        {
            "timestamp": body["events"][0]["timestamp"],
            "component": "memory.task",
            "event": "memory_task.finished",
            "outcome": "failed",
            "duration_ms": 17,
            "error_code": "synthetic_failure",
            "details": {"phase": "hy_ingest"},
        }
    ]
    assert "private" not in json.dumps(body)


def test_log_level_route_applies_current_process_level(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="trace-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer trace-token"}) as client:
        initial = client.get("/api/v1/diagnostics/settings")
        changed = client.put("/api/v1/diagnostics/log-level", json={"level": "debug"})
        current = client.get("/api/v1/diagnostics/settings")

    assert initial.status_code == 200
    assert initial.json() == {
        "schema_version": 1,
        "detailed_logging": False,
        "log_level": "info",
    }
    assert changed.status_code == 200
    assert changed.json() == {
        "schema_version": 1,
        "detailed_logging": True,
        "log_level": "debug",
    }
    assert current.json() == changed.json()


def test_log_level_route_rejects_unknown_levels(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="trace-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer trace-token"}) as client:
        response = client.put("/api/v1/diagnostics/log-level", json={"level": "verbose"})

    assert response.status_code == 422


def test_daily_usage_and_vendor_pricing_are_authenticated_and_validated(tmp_path):
    from one_memory.model_profiles import ModelProfileInput
    from one_memory.secrets import InMemorySecretStore

    secrets = InMemorySecretStore()
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="usage-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
        secret_store=secrets,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/model-usage").status_code == 401
        assert client.get("/api/v1/model-usage/balance/missing").status_code == 401
        client.headers.update(
            {"Authorization": "Bearer usage-token", "Origin": "http://127.0.0.1:5173"}
        )
        for params in ({"timezone": "Invalid/Zone"}, {"timezone": "../etc"}, {"days": 90}):
            assert client.get("/api/v1/model-usage", params=params).status_code == 422
        report = client.get("/api/v1/model-usage", params={"days": 7, "timezone": "Asia/Shanghai"})
        assert report.status_code == 200
        assert len(report.json()["days"]) == 7
        assert report.json()["summary"]["request_count"] == 0
        profile = app.state.model_profile_store.create(
            ModelProfileInput(
                name="Another vendor",
                provider_kind="openai_compatible",
                model="vendor-chat",
                base_url="https://vendor.test/v1",
            ),
            secrets,
        )
        url = f"/api/v1/model-usage/pricing/{profile.profile_id}"
        payload = {
            "model": "vendor-chat",
            "currency": "USD",
            "input_per_million": "2",
            "output_per_million": "8",
            "billing_url": "https://vendor.test/billing",
        }
        assert client.put(url, json=payload).status_code == 200
        prices = client.get("/api/v1/model-usage/pricing").json()["pricing"]
        assert prices[profile.profile_id]["billing_url"] == payload["billing_url"]
        assert client.put(url, json={**payload, "model": "wrong-model"}).status_code == 409
        assert (
            client.put(url, json={**payload, "billing_url": "javascript:alert(1)"}).status_code
            == 422
        )


def test_provider_balance_api_uses_secure_profile_credentials_and_returns_official_amounts(
    tmp_path,
):
    import httpx

    from one_memory.model_profiles import ModelProfileInput
    from one_memory.provider_balance import ProviderBalanceService
    from one_memory.secrets import InMemorySecretStore

    secrets = InMemorySecretStore()
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="balance-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
        secret_store=secrets,
    )
    with TestClient(app, headers={"Authorization": "Bearer balance-token"}) as client:
        profile = app.state.model_profile_store.create(
            ModelProfileInput(
                name="DeepSeek",
                provider_kind="openai_compatible",
                base_url="https://api.deepseek.com",
                model="deepseek-flash",
                api_key="balance-test-key",
            ),
            secrets,
        )

        def provider(request):
            assert str(request.url) == "https://api.deepseek.com/user/balance"
            assert request.headers["Authorization"] == "Bearer balance-test-key"
            return httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "CNY",
                            "total_balance": "12.34",
                            "granted_balance": "2.34",
                            "topped_up_balance": "10.00",
                        }
                    ],
                },
            )

        app.state.provider_balance = ProviderBalanceService(
            app.state.model_profile_store, secrets, transport=httpx.MockTransport(provider)
        )
        result = client.get(f"/api/v1/model-usage/balance/{profile.profile_id}")
        assert result.status_code == 200
        assert result.json()["balances"][0]["total_balance"] == "12.34"
        assert result.json()["state"] == "ready"
        assert "balance-test-key" not in result.text
        assert app.state.model_request_audit_store.report()["summary"]["request_count"] == 0
