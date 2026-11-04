"""Cached, content-free financial snapshots from documented provider adapters."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from one_memory.billing_authorization import BillingAuthorizationStore
from one_memory.http_client import create_client
from one_memory.model_profiles import ModelProfile, ModelProfileStore
from one_memory.provider_billing_adapters import BillingPermissionError, query_financials
from one_memory.provider_billing_catalog import BillingProvider, billing_provider
from one_memory.provider_finance_types import ProviderBalance
from one_memory.secrets import SecretStore, SecretStoreUnavailableError

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"


def supports_official_balance(profile: ModelProfile) -> bool:
    provider = billing_provider(profile)
    return provider is not None and provider.endpoint is not None


class ProviderBalanceService:
    def __init__(
        self,
        profiles: ModelProfileStore,
        secrets: SecretStore,
        *,
        authorizations: BillingAuthorizationStore | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profiles = profiles
        self.secrets = secrets
        self.authorizations = authorizations
        self.transport = transport
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: dict[tuple[str, int, str | None], tuple[float, ProviderBalance]] = {}

    def get(self, profile: ModelProfile, *, refresh: bool = False) -> ProviderBalance:
        provider = billing_provider(profile)
        if provider is None:
            return ProviderBalance(profile_id=profile.profile_id, state="unsupported")
        auth_revision = (
            self.authorizations.revision(profile.profile_id, provider)
            if self.authorizations
            else None
        )
        metadata: dict[str, Any] = {
            "provider": provider.name,
            "billing_url": provider.billing_url,
            "docs_url": provider.docs_url,
            "authorization_supported": bool(provider.fields),
            "authorization_configured": auth_revision is not None,
        }
        if provider.endpoint is None:
            return ProviderBalance(profile_id=profile.profile_id, state="unsupported", **metadata)
        if provider.requires_authorization and auth_revision is None:
            return ProviderBalance(
                profile_id=profile.profile_id, state="authorization_required", **metadata
            )
        identity = (profile.profile_id, profile.revision, auth_revision)
        with self._lock:
            cached = self._cached.get(identity)
            if cached is not None:
                age = self.clock() - cached[0]
                ttl = 60 if cached[1].state == "ready" else 10
                if age < (5 if refresh else ttl):
                    return cached[1]
            balance = self._fetch(profile, provider, auth_revision is not None).model_copy(
                update=metadata
            )
            if len(self._cached) >= 64:
                self._cached.clear()
            self._cached[identity] = (self.clock(), balance)
            return balance

    def _fetch(
        self, profile: ModelProfile, provider: BillingProvider, configured: bool
    ) -> ProviderBalance:
        try:
            fields = (
                self.authorizations.resolve(profile.profile_id, provider)
                if self.authorizations
                else None
            )
            if configured and fields is None:
                return ProviderBalance(
                    profile_id=profile.profile_id, state="credentials_unavailable"
                )
            # Cloud billing identities are separate: never reinterpret a generation key as IAM.
            needs_model_key = not provider.requires_authorization and not fields
            try:
                key = (
                    self.profiles.resolve_snapshot_secret(
                        profile.profile_id, profile.revision, self.secrets
                    )
                    if needs_model_key
                    else None
                )
            except KeyError:
                return ProviderBalance(
                    profile_id=profile.profile_id, state="credentials_unavailable"
                )
            if needs_model_key and key is None:
                return ProviderBalance(
                    profile_id=profile.profile_id, state="credentials_unavailable"
                )
            with create_client(
                timeout=8, follow_redirects=False, transport=self.transport
            ) as client:
                return query_financials(client, profile, provider, fields or {}, key)
        except SecretStoreUnavailableError:
            return ProviderBalance(profile_id=profile.profile_id, state="credentials_unavailable")
        except BillingPermissionError:
            return ProviderBalance(profile_id=profile.profile_id, state="authorization_required")
        except httpx.HTTPStatusError as error:
            return ProviderBalance(
                profile_id=profile.profile_id,
                state=(
                    "authorization_required"
                    if configured and error.response.status_code in (401, 403)
                    else "unavailable"
                ),
            )
        except (
            httpx.HTTPError,
            ValidationError,
            ValueError,
            TypeError,
            IndexError,
            KeyError,
            StopIteration,
        ):
            # Only stable states leave the adapter; response bodies and credentials never do.
            return ProviderBalance(profile_id=profile.profile_id, state="unavailable")
