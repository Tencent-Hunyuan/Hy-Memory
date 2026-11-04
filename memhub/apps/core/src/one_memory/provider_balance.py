"""Read the provider's account balance, independently of local usage estimates."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from one_memory.http_client import create_client
from one_memory.model_profiles import ModelProfile, ModelProfileStore
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"


class BalanceInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)
    currency: Literal["CNY", "USD"]
    total_balance: Decimal
    granted_balance: Decimal
    topped_up_balance: Decimal


class _DeepSeekBalance(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    is_available: bool
    balance_infos: list[BalanceInfo]


class ProviderBalance(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    profile_id: str
    state: Literal["ready", "unsupported", "credentials_unavailable", "unavailable"]
    is_available: bool | None = None
    balances: tuple[BalanceInfo, ...] = ()
    checked_at: datetime | None = None
    source_url: str | None = None


def supports_official_balance(profile: ModelProfile) -> bool:
    try:
        parsed = urlsplit(profile.base_url or "")
        return (
            profile.provider_kind == "openai_compatible"
            and parsed.scheme == "https"
            and parsed.hostname == "api.deepseek.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


class ProviderBalanceService:
    def __init__(
        self,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profiles = profiles
        self.secrets = secrets
        self.transport = transport
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: dict[tuple[str, int], tuple[float, ProviderBalance]] = {}

    def get(self, profile: ModelProfile, *, refresh: bool = False) -> ProviderBalance:
        if not supports_official_balance(profile):
            # Never send credentials to a custom billing URL or a look-alike domain.
            return ProviderBalance(profile_id=profile.profile_id, state="unsupported")
        identity = (profile.profile_id, profile.revision)
        with self._lock:
            cached = self._cached.get(identity)
            if cached is not None:
                age = self.clock() - cached[0]
                ttl = 60 if cached[1].state == "ready" else 10
                if age < (5 if refresh else ttl):
                    return cached[1]
            balance = self._fetch(profile)
            if len(self._cached) >= 64:
                self._cached.clear()
            self._cached[identity] = (self.clock(), balance)
            return balance

    def _fetch(self, profile: ModelProfile) -> ProviderBalance:
        try:
            secret = self.profiles.resolve_snapshot_secret(
                profile.profile_id, profile.revision, self.secrets
            )
        except (SecretStoreUnavailableError, KeyError):
            secret = None
        if secret is None:
            return ProviderBalance(profile_id=profile.profile_id, state="credentials_unavailable")
        try:
            with create_client(
                timeout=8, follow_redirects=False, transport=self.transport
            ) as client:
                response = client.get(
                    DEEPSEEK_BALANCE_URL, headers={"Authorization": f"Bearer {secret}"}
                )
                response.raise_for_status()
                payload = _DeepSeekBalance.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError):
            # Error bodies and exception strings may contain credentials or provider detail.
            return ProviderBalance(profile_id=profile.profile_id, state="unavailable")
        return ProviderBalance(
            profile_id=profile.profile_id,
            state="ready",
            is_available=payload.is_available,
            balances=tuple(payload.balance_infos),
            checked_at=datetime.now(UTC),
            source_url=DEEPSEEK_BALANCE_URL,
        )
