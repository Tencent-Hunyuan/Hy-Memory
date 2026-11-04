"""Daily, content-free usage and explicit current-price estimates for memory models."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from one_memory.database_access import main_database

PRICING_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICING_VERSION = "deepseek-cny-2026-11-04"
_RATE = Decimal("1000000")
_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_prompt_tokens",
    "reasoning_tokens",
)


class ModelPricing(BaseModel):
    """User-provided per-million rates. Kept separate from secrets and model configuration."""

    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=512)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    input_per_million: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)
    output_per_million: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)
    billing_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def paired_rates(self) -> ModelPricing:
        if (self.input_per_million is None) != (self.output_per_million is None):
            raise ValueError("input_and_output_rates_required_together")
        return self

    @field_validator("billing_url")
    @classmethod
    def validate_billing_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("billing_url_must_be_https_without_credentials")
        return value


class UsageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    recording_started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)


class ModelUsageService:
    def __init__(self, database: Path, data_dir: Path) -> None:
        self.database = database
        self.path = data_dir / "model-usage.json"
        self._lock = threading.RLock()
        self._settings = (
            UsageSettings.model_validate_json(self.path.read_text())
            if self.path.exists()
            else UsageSettings()
        )
        if not self.path.exists():
            self._save(self._settings)

    def _save(self, settings: UsageSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(settings.model_dump_json(indent=2))
                handle.close()
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        self._settings = settings

    def settings(self) -> UsageSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def set_pricing(self, profile_id: str, pricing: ModelPricing) -> ModelPricing:
        with self._lock:
            updated = self._settings.model_copy(deep=True)
            updated.pricing[profile_id] = pricing
            self._save(updated)
        return pricing

    def report(
        self,
        *,
        days: int,
        timezone: str,
        profile_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        zone = ZoneInfo(timezone)
        now = now or datetime.now(UTC)
        today = now.astimezone(zone).date()
        first_day = today - timedelta(days=days - 1)
        since = datetime.combine(first_day, time.min, zone).astimezone(UTC).isoformat()
        until = now.astimezone(UTC).isoformat()
        settings = self.settings()
        # Read every matching attempt, never the diagnostic view's truncated detail page.
        with main_database(self.database).read(row_factory=sqlite3.Row) as db:
            rows = db.execute(
                """SELECT a.*, v.base_url FROM model_request_audit a
                   LEFT JOIN model_profile_versions v
                   ON a.profile_id=v.profile_id AND a.profile_revision=v.revision
                   WHERE a.started_at>=? AND a.started_at<=?
                   AND (? IS NULL OR a.profile_id=?) ORDER BY a.started_at""",
                (since, until, profile_id, profile_id),
            ).fetchall()
        recorded_from = settings.recording_started_at
        daily = {
            (first_day + timedelta(days=offset)).isoformat(): _totals() for offset in range(days)
        }
        models: dict[tuple[str, str], dict[str, Any]] = {}
        summary = _totals()
        for row in rows:
            day = datetime.fromisoformat(row["started_at"]).astimezone(zone).date().isoformat()
            identity = (row["profile_id"], row["model"])
            model = models.setdefault(
                identity,
                {
                    "profile_id": identity[0],
                    "model": identity[1],
                    **_totals(),
                },
            )
            price = settings.pricing.get(identity[0])
            if price is not None and price.model != identity[1]:
                price = None
            estimate = _estimate(row, price)
            for totals in (summary, daily[day], model):
                _include(totals, row, estimate)
        return {
            "schema_version": 1,
            "timezone": timezone,
            "recorded_from": recorded_from.isoformat(),
            "generated_at": now.isoformat(),
            "pricing_version": PRICING_VERSION,
            "pricing_source": PRICING_SOURCE,
            "summary": _wire_totals(summary),
            "days": [
                {
                    "date": day,
                    "coverage": (
                        "unrecorded"
                        if day < recorded_from.astimezone(zone).date().isoformat()
                        and not totals["request_count"]
                        else "partial"
                        if day <= recorded_from.astimezone(zone).date().isoformat()
                        else "recorded"
                    ),
                    **_wire_totals(totals),
                }
                for day, totals in reversed(daily.items())
            ],
            "models": [_wire_totals(model) for model in models.values()],
        }


def _totals() -> dict[str, Any]:
    return {
        "request_count": 0,
        "succeeded": 0,
        "failed": 0,
        "in_flight": 0,
        "unknown_usage_requests": 0,
        "unpriced_requests": 0,
        **{field: 0 for field in _TOKEN_FIELDS},
        "costs": {},
    }


def _include(
    totals: dict[str, Any], row: sqlite3.Row, estimate: tuple[str, Decimal, Decimal] | None
) -> None:
    totals["request_count"] += 1
    totals[row["outcome"]] += 1
    if any(row[field] is None for field in ("prompt_tokens", "completion_tokens", "total_tokens")):
        totals["unknown_usage_requests"] += 1
    for field in _TOKEN_FIELDS:
        totals[field] += row[field] or 0
    if estimate is None:
        totals["unpriced_requests"] += 1
        return
    currency, lower, upper = estimate
    cost = totals["costs"].setdefault(currency, [Decimal(0), Decimal(0), 0])
    cost[0] += lower
    cost[1] += upper
    cost[2] += 1


def _wire_totals(totals: dict[str, Any]) -> dict[str, Any]:
    return {
        **totals,
        "costs": [
            {
                "currency": currency,
                "estimated_min": str(amounts[0].quantize(Decimal("0.00000001"))),
                "estimated_max": str(amounts[1].quantize(Decimal("0.00000001"))),
                "priced_requests": amounts[2],
            }
            for currency, amounts in sorted(totals["costs"].items())
        ],
    }


def _estimate(row: sqlite3.Row, custom: ModelPricing | None) -> tuple[str, Decimal, Decimal] | None:
    prompt, output, cached = (
        row[field] for field in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens")
    )
    if prompt is None or output is None or (cached is not None and cached > prompt):
        return None
    if (
        custom is not None
        and custom.input_per_million is not None
        and custom.output_per_million is not None
    ):
        cached_rate = custom.cached_input_per_million
        if cached_rate is None:
            cached_rate = custom.input_per_million
        low, high = _cost_range(
            prompt, output, cached, custom.input_per_million, cached_rate, custom.output_per_million
        )
        return custom.currency, low, high
    # Model names alone do not identify a provider: proxies may charge different rates.
    if urlsplit(row["base_url"] or "").hostname != "api.deepseek.com":
        return None
    rates = {
        "deepseek-flash": ("1", "0.02", "4"),
        "deepseek-v4-flash": ("1", "0.02", "4"),
        "deepseek-v4-flash-vision-exp": ("1", "0.02", "4"),
        "deepseek-v4-pro": ("4.5", "0.15", "13.5"),
    }.get(row["model"])
    if rates is None:
        return None
    low, high = _cost_range(prompt, output, cached, *(Decimal(rate) for rate in rates))
    local = datetime.fromisoformat(row["started_at"]).astimezone(ZoneInfo("Asia/Shanghai"))
    # Peak weekdays exclude Chinese statutory holidays. Without an authoritative
    # holiday calendar, show the off-peak/peak bounds instead of inventing a bill.
    peak_candidate = local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18)
    return "CNY", low, high * (2 if peak_candidate else 1)


def _cost_range(
    prompt: int,
    output: int,
    cached: int | None,
    input_rate: Decimal,
    cached_rate: Decimal,
    output_rate: Decimal,
) -> tuple[Decimal, Decimal]:
    def cost(hits: int) -> Decimal:
        return ((prompt - hits) * input_rate + hits * cached_rate + output * output_rate) / _RATE

    if cached is not None:
        value = cost(cached)
        return value, value
    bounds = (cost(0), cost(prompt))
    return min(bounds), max(bounds)
