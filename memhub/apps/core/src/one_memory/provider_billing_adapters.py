"""Read-only adapters for documented balances, key quotas and official costs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx

from one_memory.model_profiles import ModelProfile
from one_memory.provider_billing_catalog import BillingProvider
from one_memory.provider_finance_types import (
    BalanceInfo,
    ProviderBalance,
    ProviderSpend,
    SpendPeriod,
)

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"


class BillingPermissionError(ValueError):
    """A provider rejected billing authorization; no provider text is retained."""


def _refused(code: object) -> None:
    value = str(code or "")
    if value.startswith(("AuthFailure", "UnauthorizedOperation")) or value in {
        "NoPermission",
        "NotAuthorized",
        "Forbidden",
        "SignatureDoesNotMatch",
        "InvalidAccessKeyId.NotFound",
        "InvalidSecurityToken.Expired",
    }:
        raise BillingPermissionError("billing_authorization_required")
    raise ValueError("provider_refused")


def _money(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("missing_financial_value")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid_financial_value") from None
    if not amount.is_finite():
        raise ValueError("invalid_financial_value")
    return amount


def _object(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    value = json.loads(response.content, parse_float=Decimal)
    if not isinstance(value, dict):
        raise ValueError("invalid_provider_response")
    return value


def _ready(
    profile: ModelProfile,
    provider: BillingProvider,
    *,
    source_url: str | None = None,
    **values: Any,
) -> ProviderBalance:
    return ProviderBalance(
        profile_id=profile.profile_id,
        provider=provider.name,
        state="ready",
        checked_at=datetime.now(UTC),
        source_url=source_url or provider.endpoint,
        **values,
    )


def _standard(
    client: httpx.Client, profile: ModelProfile, provider: BillingProvider, key: str
) -> ProviderBalance:
    body = _object(client.get(provider.endpoint or "", headers={"Authorization": f"Bearer {key}"}))
    if provider.name == "deepseek":
        if type(body.get("is_available")) is not bool or not isinstance(
            body.get("balance_infos"), list
        ):
            raise ValueError("invalid_provider_response")
        return _ready(
            profile,
            provider,
            is_available=body["is_available"],
            balances=tuple(BalanceInfo.model_validate(item) for item in body["balance_infos"]),
        )
    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError("invalid_provider_response")
    if provider.name.startswith("moonshot"):
        if body.get("status") is not True:
            raise ValueError("provider_refused")
        return _ready(
            profile,
            provider,
            balances=(
                BalanceInfo(
                    currency="CNY" if provider.name == "moonshot_cn" else "USD",
                    total_balance=_money(data.get("available_balance")),
                    granted_balance=_money(data.get("voucher_balance")),
                    cash_balance=_money(data.get("cash_balance")),
                ),
            ),
        )
    if body.get("status") is not True or body.get("code") != 20000:
        raise ValueError("provider_refused")
    return _ready(
        profile,
        provider,
        balances=(
            BalanceInfo(
                currency="CNY",
                total_balance=_money(data.get("totalBalance")),
                topped_up_balance=_money(data.get("chargeBalance")),
            ),
        ),
    )


def _openrouter(
    client: httpx.Client,
    profile: ModelProfile,
    provider: BillingProvider,
    fields: dict[str, str],
    model_key: str | None,
) -> ProviderBalance:
    management = fields.get("api_key")
    key = management or model_key
    if not key:
        raise ValueError("missing_credentials")
    endpoint = "https://openrouter.ai/api/v1/credits" if management else provider.endpoint
    body = _object(client.get(endpoint or "", headers={"Authorization": f"Bearer {key}"}))
    data = body["data"]
    if management:
        total = _money(data.get("total_credits")) - _money(data.get("total_usage"))
        return _ready(
            profile,
            provider,
            source_url=endpoint,
            scope="account",
            balances=(BalanceInfo(currency="USD", total_balance=total),),
        )
    if "limit" not in data or "limit_remaining" not in data:
        raise ValueError("missing_key_quota_fields")
    remaining = data.get("limit_remaining")
    effective = data.get("effective_budget")
    if isinstance(effective, dict) and effective.get("remaining_usd") is not None:
        effective_remaining = _money(effective["remaining_usd"])
        remaining = (
            min(_money(remaining), effective_remaining)
            if remaining is not None
            else effective_remaining
        )
    usage_fields: tuple[tuple[str, SpendPeriod], ...] = (
        ("usage_daily", "day"),
        ("usage_weekly", "week"),
        ("usage_monthly", "month"),
        ("usage", "all_time"),
    )
    spend = tuple(
        ProviderSpend(period=period, currency="USD", amount=_money(data[field]))
        for field, period in usage_fields
        if data.get(field) is not None
    )
    return _ready(
        profile,
        provider,
        scope="api_key",
        quota_unlimited=data.get("limit") is None and remaining is None,
        balances=(
            BalanceInfo(
                currency="USD", total_balance=_money(remaining) if remaining is not None else None
            ),
        ),
        spending=spend,
    )


def _month_spending(
    totals: dict[str, Decimal], daily: dict[str, Decimal]
) -> tuple[ProviderSpend, ...]:
    return tuple(
        ProviderSpend(period="month_to_date", currency=c, amount=a) for c, a in totals.items()
    ) + tuple(ProviderSpend(period="day", currency=c, amount=a) for c, a in daily.items())


def _openai(
    client: httpx.Client, profile: ModelProfile, provider: BillingProvider, fields: dict[str, str]
) -> ProviderBalance:
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    params: dict[str, Any] = {
        "start_time": int(start.timestamp()),
        "end_time": int(now.timestamp()),
        "bucket_width": "1d",
        "limit": 31,
    }
    for name, query_name in (("project_id", "project_ids[]"), ("api_key_id", "api_key_ids[]")):
        if fields.get(name):
            params[query_name] = fields[name]
    totals: dict[str, Decimal] = {}
    daily: dict[str, Decimal] = {}
    seen: set[str] = set()
    for _ in range(6):
        body = _object(
            client.get(
                provider.endpoint or "",
                params=params,
                headers={"Authorization": f"Bearer {fields['api_key']}"},
            )
        )
        for bucket in body["data"]:
            current_day = (
                datetime.fromtimestamp(int(bucket["start_time"]), UTC).date() == now.date()
            )
            for result in bucket["results"]:
                amount = result["amount"]
                currency = str(amount["currency"]).upper()
                value = _money(amount["value"])
                totals[currency] = totals.get(currency, Decimal(0)) + value
                if current_day:
                    daily[currency] = daily.get(currency, Decimal(0)) + value
        if not body.get("has_more"):
            break
        cursor = body.get("next_page")
        if not isinstance(cursor, str) or not cursor or cursor in seen or len(cursor) > 4096:
            raise ValueError("invalid_cost_pagination")
        seen.add(cursor)
        params["page"] = cursor
    else:
        raise ValueError("cost_pagination_incomplete")
    spending = _month_spending(totals, daily)
    return _ready(profile, provider, scope="organization", spending=spending)


def _aliyun(
    client: httpx.Client, profile: ModelProfile, provider: BillingProvider, fields: dict[str, str]
) -> ProviderBalance:
    params = {
        "Action": "QueryAccountBalance",
        "Version": "2017-12-14",
        "Format": "JSON",
        "AccessKeyId": fields["access_key_id"],
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureNonce": uuid4().hex,
    }
    if fields.get("security_token"):
        params["SecurityToken"] = fields["security_token"]

    def encode(value: str) -> str:
        return quote(value, safe="-_.~")

    canonical = "&".join(f"{encode(k)}={encode(v)}" for k, v in sorted(params.items()))
    signed = "POST&%2F&" + encode(canonical)
    params["Signature"] = base64.b64encode(
        hmac.new(
            (fields["access_key_secret"] + "&").encode(), signed.encode(), hashlib.sha1
        ).digest()
    ).decode()
    # RPC parameters go in the POST body so security tokens never enter URL logs.
    body = _object(client.post(provider.endpoint or "", data=params))
    if body.get("Success") is not True:
        _refused(body.get("Code"))
    data = body["Data"]
    return _ready(
        profile,
        provider,
        balances=(
            BalanceInfo(
                currency=data["Currency"],
                total_balance=_money(data.get("AvailableAmount")),
                cash_balance=_money(data.get("AvailableCashAmount")),
            ),
        ),
    )


def _tencent(
    client: httpx.Client, profile: ModelProfile, provider: BillingProvider, fields: dict[str, str]
) -> ProviderBalance:
    now = datetime.now(UTC)
    stamp = str(int(now.timestamp()))
    date = now.strftime("%Y-%m-%d")
    payload = b"{}"
    host = "billing.tencentcloudapi.com"
    content_type = "application/json; charset=utf-8"
    canonical = (
        f"POST\n/\n\ncontent-type:{content_type}\nhost:{host}\n"
        f"\ncontent-type;host\n{hashlib.sha256(payload).hexdigest()}"
    )
    scope = f"{date}/billing/tc3_request"
    signed = f"TC3-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    def sign(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode(), hashlib.sha256).digest()

    signing_key = sign(
        sign(sign(("TC3" + fields["secret_key"]).encode(), date), "billing"), "tc3_request"
    )
    signature = hmac.new(signing_key, signed.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": content_type,
        "X-TC-Action": "DescribeAccountBalance",
        "X-TC-Version": "2018-07-09",
        "X-TC-Timestamp": stamp,
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={fields['secret_id']}/{scope}, "
            f"SignedHeaders=content-type;host, Signature={signature}"
        ),
    }
    if fields.get("session_token"):
        headers["X-TC-Token"] = fields["session_token"]
    data = _object(client.post(provider.endpoint or "", headers=headers, content=payload))[
        "Response"
    ]
    if data.get("Error"):
        _refused(data["Error"].get("Code"))
    cents = Decimal(100)
    return _ready(
        profile,
        provider,
        balances=(
            BalanceInfo(
                currency="CNY",
                total_balance=_money(data.get("RealBalance")) / cents,
                granted_balance=_money(data.get("PresentAccountBalance")) / cents,
                cash_balance=_money(data.get("CashAccountBalance")) / cents,
            ),
        ),
    )


def _azure(
    client: httpx.Client, profile: ModelProfile, provider: BillingProvider, fields: dict[str, str]
) -> ProviderBalance:
    resource = fields["resource_id"]
    match = re.fullmatch(
        r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/([^/?#\\]{1,90})/providers/Microsoft.CognitiveServices/accounts/([A-Za-z0-9-]{1,64})",
        resource,
        re.I,
    )
    if not match:
        raise ValueError("invalid_azure_resource")
    subscription = str(UUID(match[1]))
    if (
        not (urlsplit(profile.base_url or "").hostname or "")
        .lower()
        .startswith(match[3].lower() + ".")
    ):
        raise ValueError("azure_resource_endpoint_mismatch")
    if match[2] in (".", ".."):
        raise ValueError("invalid_azure_resource_group")
    group = quote(match[2], safe="")
    endpoint = (
        f"https://management.azure.com/subscriptions/{subscription}/"
        f"resourceGroups/{group}/providers/Microsoft.CostManagement/query"
    )
    query = {
        "type": "ActualCost",
        "timeframe": "MonthToDate",
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "filter": {
                "dimensions": {"name": "ResourceId", "operator": "In", "values": [resource]}
            },
        },
    }
    body = _object(
        client.post(
            endpoint,
            params={"api-version": "2026-06-01"},
            json=query,
            headers={"Authorization": f"Bearer {fields['access_token']}"},
        )
    )
    properties = body["properties"]
    # Do not present a partial page as a complete official cost.
    if properties.get("nextLink"):
        raise ValueError("azure_cost_pagination_incomplete")
    columns = [column["name"] for column in properties["columns"]]
    cost_index = next(
        i for i, name in enumerate(columns) if name in ("Cost", "PreTaxCost", "CostUSD")
    )
    currency_index = columns.index("Currency")
    day_index = columns.index("UsageDate")
    totals: dict[str, Decimal] = {}
    daily: dict[str, Decimal] = {}
    today = datetime.now(UTC).strftime("%Y%m%d")
    for row in properties["rows"]:
        currency = str(row[currency_index]).upper()
        amount = _money(row[cost_index])
        totals[currency] = totals.get(currency, Decimal(0)) + amount
        if str(row[day_index]) == today:
            daily[currency] = daily.get(currency, Decimal(0)) + amount
    return _ready(
        profile,
        provider,
        scope="resource",
        source_url=endpoint,
        spending=_month_spending(totals, daily),
    )


def query_financials(
    client: httpx.Client,
    profile: ModelProfile,
    provider: BillingProvider,
    fields: dict[str, str],
    model_key: str | None,
) -> ProviderBalance:
    if provider.name in ("deepseek", "moonshot_cn", "moonshot_global", "siliconflow"):
        if not model_key:
            raise ValueError("missing_credentials")
        return _standard(client, profile, provider, model_key)
    if provider.name == "openrouter":
        return _openrouter(client, profile, provider, fields, model_key)
    if provider.name == "openai":
        return _openai(client, profile, provider, fields)
    if provider.name == "aliyun":
        return _aliyun(client, profile, provider, fields)
    if provider.name == "tencent":
        return _tencent(client, profile, provider, fields)
    if provider.name == "azure":
        return _azure(client, profile, provider, fields)
    raise ValueError("billing_adapter_unavailable")
