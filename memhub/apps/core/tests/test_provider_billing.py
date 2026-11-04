"""Provider financial scopes, secure billing authorization and protocol adapters."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from one_memory.billing_authorization import BillingAuthorizationInput, BillingAuthorizationStore
from one_memory.provider_balance import ProviderBalanceService
from one_memory.provider_billing_catalog import billing_provider
from one_memory.secrets import InMemorySecretStore


def _service(tmp_path, url, handler, fields=None):
    profile = SimpleNamespace(
        profile_id="profile", revision=1, provider_kind="openai_compatible", base_url=url
    )

    class Profiles:
        calls = 0

        def resolve_snapshot_secret(self, *_args):
            self.calls += 1
            return "MODEL_KEY"

    profiles = Profiles()
    secrets = InMemorySecretStore()
    authorizations = BillingAuthorizationStore(tmp_path, secrets)
    provider = billing_provider(profile)
    assert provider is not None
    if fields:
        authorizations.put(profile.profile_id, provider, BillingAuthorizationInput(fields=fields))
    service = ProviderBalanceService(
        profiles, secrets, authorizations=authorizations, transport=httpx.MockTransport(handler)
    )
    return service, profile, profiles, authorizations, secrets


@pytest.mark.parametrize(
    "url,currency", [("https://api.moonshot.cn/v1", "CNY"), ("https://api.moonshot.ai/v1", "USD")]
)
def test_kimi_regional_balance_keeps_official_money_and_host(tmp_path, url, currency):
    def handler(request):
        assert request.url.host == httpx.URL(url).host
        assert request.url.path == "/v1/users/me/balance"
        assert request.headers["Authorization"] == "Bearer MODEL_KEY"
        return httpx.Response(
            200,
            json={
                "code": 123,
                "status": True,
                "data": {
                    "available_balance": 49.58894,
                    "voucher_balance": 46.58893,
                    "cash_balance": 3.00001,
                },
            },
        )

    service, profile, *_ = _service(tmp_path, url, handler)
    result = service.get(profile)
    assert result.state == "ready" and result.scope == "account"
    assert result.balances[0].currency == currency
    assert result.balances[0].total_balance == Decimal("49.58894")
    assert result.balances[0].cash_balance == Decimal("3.00001")


def test_siliconflow_does_not_guess_missing_gift_breakdown(tmp_path):
    service, profile, *_ = _service(
        tmp_path,
        "https://api.siliconflow.cn/v1",
        lambda request: httpx.Response(
            200,
            json={
                "code": 20000,
                "status": True,
                "data": {"totalBalance": "88.88", "chargeBalance": "88.00"},
            },
        ),
    )
    result = service.get(profile)
    assert result.state == "ready"
    assert result.balances[0].total_balance == Decimal("88.88")
    assert result.balances[0].granted_balance is None


def test_openrouter_shows_effective_key_quota_and_official_spend_not_account_balance(tmp_path):
    def handler(request):
        assert request.url.path == "/api/v1/key"
        return httpx.Response(
            200,
            json={
                "data": {
                    "limit": 100,
                    "limit_remaining": 74.5,
                    "effective_budget": {"remaining_usd": 17.5},
                    "usage_daily": 1.25,
                    "usage": 25.5,
                }
            },
        )

    service, profile, *_ = _service(tmp_path, "https://openrouter.ai/api/v1", handler)
    result = service.get(profile)
    assert result.scope == "api_key" and result.balances[0].total_balance == Decimal("17.5")
    assert [(s.period, s.amount) for s in result.spending] == [
        ("day", Decimal("1.25")),
        ("all_time", Decimal("25.5")),
    ]
    assert result.balances[0].topped_up_balance is None


def test_unlimited_key_is_not_a_zero_balance(tmp_path):
    service, profile, *_ = _service(
        tmp_path,
        "https://openrouter.ai/api/v1",
        lambda _: httpx.Response(
            200, json={"data": {"limit": None, "limit_remaining": None, "usage_daily": 0}}
        ),
    )
    result = service.get(profile)
    assert result.quota_unlimited and result.balances[0].total_balance is None
    assert result.spending[0].amount == 0


def test_openrouter_management_key_is_separate_and_account_credits_are_not_daily_spend(tmp_path):
    def handler(request):
        assert request.url.path == "/api/v1/credits"
        assert request.headers["Authorization"] == "Bearer MANAGEMENT_KEY"
        return httpx.Response(200, json={"data": {"total_credits": 100.5, "total_usage": 25.75}})

    service, profile, profiles, *_ = _service(
        tmp_path, "https://openrouter.ai/api/v1", handler, {"api_key": "MANAGEMENT_KEY"}
    )
    result = service.get(profile)
    assert profiles.calls == 0 and result.scope == "account"
    assert result.balances[0].total_balance == Decimal("74.75")
    assert not result.spending


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://api.hunyuan.cloud.tencent.com/v1",
        "https://my-resource.openai.azure.com/openai/v1",
    ],
)
def test_cloud_billing_requires_independent_authorization_before_reading_model_key(tmp_path, url):
    service, profile, profiles, *_ = _service(
        tmp_path, url, lambda _: pytest.fail("no authorized network call")
    )
    result = service.get(profile)
    assert result.state == "authorization_required" and profiles.calls == 0
    assert result.authorization_supported and not result.authorization_configured


def test_openai_costs_follow_pagination_and_never_appear_as_balance(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer ADMIN_KEY"
        assert request.url.params["project_ids[]"] == "proj_abc"
        assert request.url.params["api_key_ids[]"] == "key_abc"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "start_time": int(datetime.now(UTC).timestamp()),
                        "results": [
                            {"amount": {"value": 0.1 if len(seen) == 1 else 0.2, "currency": "usd"}}
                        ],
                    }
                ],
                "has_more": len(seen) == 1,
                "next_page": "cursor" if len(seen) == 1 else None,
            },
        )

    service, profile, profiles, *_ = _service(
        tmp_path,
        "https://api.openai.com/v1",
        handler,
        {"api_key": "ADMIN_KEY", "project_id": "proj_abc", "api_key_id": "key_abc"},
    )
    result = service.get(profile)
    assert result.state == "ready" and result.scope == "organization"
    assert not result.balances and profiles.calls == 0
    assert len(seen) == 2 and seen[1].url.params["page"] == "cursor"
    assert {s.period: s.amount for s in result.spending} == {
        "day": Decimal("0.3"),
        "month_to_date": Decimal("0.3"),
    }


def test_aliyun_uses_signed_post_body_and_never_puts_security_token_in_url(tmp_path):
    def handler(request):
        assert str(request.url) == "https://business.aliyuncs.com/" and request.method == "POST"
        params = parse_qs(request.content.decode())
        assert params["Action"] == ["QueryAccountBalance"] and params["Version"] == ["2017-12-14"]
        assert params["SecurityToken"] == ["TEMP_STS"] and params["AccessKeyId"] == ["AK_ID"]
        assert params["Signature"] and "CLOUD_SECRET" not in request.content.decode()
        assert "TEMP_STS" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "Success": True,
                "Data": {
                    "AvailableAmount": "1000.19",
                    "AvailableCashAmount": "999.19",
                    "Currency": "CNY",
                },
            },
        )

    service, profile, profiles, *_ = _service(
        tmp_path,
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        handler,
        {
            "access_key_id": "AK_ID",
            "access_key_secret": "CLOUD_SECRET",
            "security_token": "TEMP_STS",
        },
    )
    result = service.get(profile)
    assert result.state == "ready" and profiles.calls == 0
    assert result.balances[0].cash_balance == Decimal("999.19")
    assert result.balances[0].granted_balance is None


def test_tencent_tc3_and_cents_conversion_preserve_cash_vs_grant(tmp_path):
    def handler(request):
        assert request.url.host == "billing.tencentcloudapi.com" and request.content == b"{}"
        assert request.headers["X-TC-Action"] == "DescribeAccountBalance"
        assert request.headers["X-TC-Version"] == "2018-07-09"
        assert request.headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AK_ID/")
        assert "SECRET_KEY" not in request.headers["Authorization"]
        assert request.headers["X-TC-Token"] == "TEMP_TOKEN"
        return httpx.Response(
            200,
            json={
                "Response": {
                    "RealBalance": 12345,
                    "PresentAccountBalance": 200,
                    "CashAccountBalance": 12000,
                    "CreditBalance": 999999,
                }
            },
        )

    service, profile, *_ = _service(
        tmp_path,
        "https://api.hunyuan.cloud.tencent.com/v1",
        handler,
        {"secret_id": "AK_ID", "secret_key": "SECRET_KEY", "session_token": "TEMP_TOKEN"},
    )
    result = service.get(profile)
    assert result.state == "ready"
    assert result.balances[0].total_balance == Decimal("123.45")
    assert result.balances[0].granted_balance == Decimal("2")
    assert result.balances[0].cash_balance == Decimal("120")


def test_azure_queries_only_matching_resource_and_handles_column_order(tmp_path):
    resource = (
        "/subscriptions/11111111-1111-1111-1111-111111111111/"
        "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/my-resource"
    )

    def handler(request):
        assert request.url.host == "management.azure.com" and request.method == "POST"
        assert request.headers["Authorization"] == "Bearer ACCESS_TOKEN"
        body = json.loads(request.content)
        assert body["dataset"]["filter"]["dimensions"]["values"] == [resource]
        return httpx.Response(
            200,
            json={
                "properties": {
                    "columns": [{"name": "Currency"}, {"name": "UsageDate"}, {"name": "Cost"}],
                    "rows": [["USD", int(datetime.now(UTC).strftime("%Y%m%d")), 1.25]],
                    "nextLink": None,
                }
            },
        )

    service, profile, profiles, *_ = _service(
        tmp_path,
        "https://my-resource.openai.azure.com/openai/v1",
        handler,
        {"access_token": "ACCESS_TOKEN", "resource_id": resource},
    )
    result = service.get(profile)
    assert result.state == "ready" and result.scope == "resource" and profiles.calls == 0
    assert not result.balances and result.spending[0].amount == Decimal("1.25")


def test_billing_credentials_roundtrip_rotation_removal_and_never_on_disk(tmp_path):
    from one_memory.provider_billing_catalog import PROVIDERS

    secrets = InMemorySecretStore()
    provider = PROVIDERS["openai"]
    store = BillingAuthorizationStore(tmp_path, secrets)
    store.put("p", provider, BillingAuthorizationInput(fields={"api_key": "FIRST_PRIVATE_ADMIN"}))
    old = store.revision("p", provider)
    assert "FIRST_PRIVATE_ADMIN" not in store.path.read_text()
    store = BillingAuthorizationStore(tmp_path, secrets)
    assert store.resolve("p", provider) == {"api_key": "FIRST_PRIVATE_ADMIN"}
    store.put("p", provider, BillingAuthorizationInput(fields={"api_key": "SECOND_PRIVATE_ADMIN"}))
    assert store.revision("p", provider) != old and secrets.resolve(old) is None
    store.delete("p")
    assert store.resolve("p", provider) is None


def test_cloud_permission_failure_is_actionable_not_a_zero_balance(tmp_path):
    service, profile, *_ = _service(
        tmp_path,
        "https://api.openai.com/v1",
        lambda _: httpx.Response(403, json={"error": "private provider detail"}),
        {"api_key": "ADMIN_KEY"},
    )
    result = service.get(profile)
    assert result.state == "authorization_required" and result.authorization_configured
    assert not result.balances and not result.spending
    assert "private provider detail" not in result.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.minimax.io/v1",
        "https://api.minimaxi.com/v1",
        "https://open.bigmodel.cn/api/paas/v4",
        "https://api.z.ai/api/paas/v4",
    ],
)
def test_console_only_providers_do_not_claim_an_implemented_balance_api(tmp_path, url):
    service, profile, profiles, *_ = _service(
        tmp_path, url, lambda _: pytest.fail("no undocumented calls")
    )
    result = service.get(profile)
    assert result.state == "unsupported" and result.billing_url and result.docs_url
    assert profiles.calls == 0 and not result.balances


def test_billing_authorization_api_is_private_and_never_changes_model_key(tmp_path):
    from fastapi.testclient import TestClient

    from one_memory.api import create_app
    from one_memory.config import Settings
    from one_memory.model_profiles import ModelProfileInput

    secrets = InMemorySecretStore()
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            source_home=tmp_path / "home",
            api_token="billing-api-token",
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
        secret_store=secrets,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/model-usage/billing-auth/missing").status_code == 401
        client.headers.update(
            {"Authorization": "Bearer billing-api-token", "Origin": "http://127.0.0.1:5173"}
        )
        profile = app.state.model_profile_store.create(
            ModelProfileInput(
                name="OpenAI",
                provider_kind="openai_compatible",
                base_url="https://api.openai.com/v1",
                model="model",
                api_key="PRIVATE_MODEL_KEY",
            ),
            secrets,
        )
        path = f"/api/v1/model-usage/billing-auth/{profile.profile_id}"
        descriptor = client.get(path).json()
        assert not descriptor["configured"]
        assert {f["name"] for f in descriptor["fields"]} == {"api_key", "project_id", "api_key_id"}
        result = client.put(path, json={"fields": {"api_key": "PRIVATE_ADMIN_KEY"}})
        assert result.status_code == 200 and result.json()["configured"]
        assert "PRIVATE_ADMIN_KEY" not in result.text and "PRIVATE_MODEL_KEY" not in result.text
        assert "PRIVATE_ADMIN_KEY" not in (tmp_path / "billing-authorizations.json").read_text()
        assert (
            app.state.model_profile_store.resolve_snapshot_secret(
                profile.profile_id, profile.revision, secrets
            )
            == "PRIVATE_MODEL_KEY"
        )
        assert (
            client.put(path, json={"fields": {"unknown": "DO_NOT_ECHO_SECRET"}}).status_code == 422
        )
        result = client.put(path, json={"fields": {"api_key": "DO_NOT_ECHO_SECRET\n"}})
        assert result.status_code == 422 and "DO_NOT_ECHO_SECRET" not in result.text
        assert client.delete(path).json()["configured"] is False
        assert (
            client.get(f"/api/v1/model-usage/balance/{profile.profile_id}").json()["state"]
            == "authorization_required"
        )


def test_azure_wrong_resource_never_queries_another_resource(tmp_path):
    resource = (
        "/subscriptions/11111111-1111-1111-1111-111111111111/"
        "resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/wrong-resource"
    )
    service, profile, *_ = _service(
        tmp_path,
        "https://my-resource.openai.azure.com/openai/v1",
        lambda _: pytest.fail("wrong resource must not be queried"),
        {"access_token": "TOKEN", "resource_id": resource},
    )
    assert service.get(profile).state == "unavailable"


def test_permission_error_in_a_successful_cloud_http_response_is_not_zero(tmp_path):
    service, profile, *_ = _service(
        tmp_path,
        "https://api.hunyuan.cloud.tencent.com/v1",
        lambda _: httpx.Response(
            200,
            json={
                "Response": {
                    "Error": {"Code": "AuthFailure.SignatureFailure", "Message": "PRIVATE"}
                }
            },
        ),
        {"secret_id": "AK", "secret_key": "SK"},
    )
    result = service.get(profile)
    assert result.state == "authorization_required" and not result.balances
    assert "PRIVATE" not in result.model_dump_json()


def test_cloud_signatures_match_independent_openssl_vectors(tmp_path, monkeypatch):
    """Fixed RPC/TC3 vectors computed independently with OpenSSL HMAC, not the adapter."""
    import one_memory.provider_billing_adapters as adapters

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 4, 12, tzinfo=UTC)

    monkeypatch.setattr(adapters, "datetime", FrozenDatetime)
    monkeypatch.setattr(adapters, "uuid4", lambda: SimpleNamespace(hex="fixed_nonce"))

    def tencent(request):
        assert request.headers["X-TC-Timestamp"] == "1793793600"
        assert request.headers["Authorization"].endswith(
            "Signature=8ffeacd22bb16d5b7ceb56f8eb46ed1bfb1019357669331e25f8c2f7b65311e2"
        )
        return httpx.Response(
            200,
            json={
                "Response": {
                    "RealBalance": 100,
                    "PresentAccountBalance": 0,
                    "CashAccountBalance": 100,
                }
            },
        )

    service, profile, *_ = _service(
        tmp_path / "tc",
        "https://api.hunyuan.cloud.tencent.com/v1",
        tencent,
        {"secret_id": "AK_ID", "secret_key": "SECRET_KEY", "session_token": "TEMP_TOKEN"},
    )
    assert service.get(profile).state == "ready"

    def aliyun(request):
        assert parse_qs(request.content.decode())["Signature"] == ["2My2X4YAlat14EQ6HA0DrJ+9cbQ="]
        return httpx.Response(
            200,
            json={
                "Success": True,
                "Data": {"AvailableAmount": "100", "AvailableCashAmount": "100", "Currency": "CNY"},
            },
        )

    service, profile, *_ = _service(
        tmp_path / "ali",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        aliyun,
        {
            "access_key_id": "AK_ID",
            "access_key_secret": "CLOUD_SECRET",
            "security_token": "TEMP_STS",
        },
    )
    assert service.get(profile).state == "ready"


def test_missing_key_quota_fields_do_not_mean_unlimited_credits(tmp_path):
    service, profile, *_ = _service(
        tmp_path, "https://openrouter.ai/api/v1", lambda _: httpx.Response(200, json={"data": {}})
    )
    result = service.get(profile)
    assert result.state == "unavailable" and not result.quota_unlimited and not result.balances
