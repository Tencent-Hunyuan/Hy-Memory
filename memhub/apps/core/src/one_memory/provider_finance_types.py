"""Provider financial facts retain their unit, period and account/key scope."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BalanceInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    total_balance: Decimal | None
    granted_balance: Decimal | None = None
    topped_up_balance: Decimal | None = None
    cash_balance: Decimal | None = None


SpendPeriod = Literal["day", "week", "month", "all_time", "month_to_date"]


class ProviderSpend(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    period: SpendPeriod
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProviderBalance(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    profile_id: str
    state: Literal[
        "ready", "unsupported", "authorization_required", "credentials_unavailable", "unavailable"
    ]
    provider: str | None = None
    scope: Literal["account", "api_key", "organization", "resource"] = "account"
    is_available: bool | None = None
    balances: tuple[BalanceInfo, ...] = ()
    spending: tuple[ProviderSpend, ...] = ()
    quota_unlimited: bool = False
    checked_at: datetime | None = None
    source_url: str | None = None
    billing_url: str | None = None
    docs_url: str | None = None
    authorization_supported: bool = False
    authorization_configured: bool = False
