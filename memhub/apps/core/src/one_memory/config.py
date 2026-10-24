from pathlib import Path
from typing import Self
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ONE_MEMORY_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Path.home() / ".one-memory"
    source_home: Path = Field(default_factory=Path.home)
    host: str = "127.0.0.1"
    port: int = 8765
    instance_id: str = Field(default_factory=lambda: uuid4().hex)
    api_token: SecretStr = Field(default_factory=lambda: SecretStr(uuid4().hex))
    live_sync_enabled: bool = True
    automatic_memory_processing: bool = True
    imports_enabled: bool = True
    # Bounded metadata only: query, original text and answers are never stored.
    call_event_retention_days: int | None = Field(default=30, ge=1, le=365)
    call_event_max_events: int | None = Field(default=10_000, ge=1, le=100_000)
    # Temporary load-shedding while background imports contend with interactive
    # recall for the same small SQLite reader pool. This is deliberately 1; the
    # durable fix is a background admission controller, not a larger constant.
    import_max_concurrent_jobs: int = Field(default=1, ge=1, le=32)
    # How many precipitation tasks may run at once. `None` means "whatever the
    # active model profile allows", which is the default: the profile has
    # carried `max_concurrency` all along and is the natural owner of the
    # number, so hardcoding one here would contradict it and could not be
    # tuned per machine without a code change.
    #
    # Measured, not assumed: with a pool of 4 readers and readers actually
    # held, recall latency at concurrency 1 and 4 is indistinguishable
    # (median 272ms vs 272ms) and only degrades once it reaches 8 (522ms).
    # See `tests/measure_recall_under_precipitation.py`.
    precipitation_max_concurrent_tasks: int | None = Field(default=None, ge=1, le=32)
    development_origin: str = "http://127.0.0.1:5173"
    model_request_limit: int | None = Field(default=None, ge=1, le=100_000)
    model_request_body_byte_limit: int = Field(default=24_576, ge=1)
    model_request_output_token_limit: int = Field(default=16_384, ge=1)

    @field_validator("development_origin")
    @classmethod
    def validate_development_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1024 <= parsed.port <= 65535
        ):
            raise ValueError("development_origin must be an exact loopback HTTP origin")
        return value

    live_sync_interval_seconds: float = Field(default=60, ge=5)
    # Watching is layered on top of the timer, not a replacement for it: the
    # timer still covers bursts the rate limit skipped and sources with no
    # watchable root.
    live_sync_watch_enabled: bool = True
    live_sync_watch_debounce_seconds: float = Field(default=0.2, gt=0)
    # How long writes to one source collapse into a single scan. The freshness
    # a user actually feels is set by sedimentation, which measured 29–197s per
    # task on a real machine, so the difference between coalescing over a fifth
    # of a second and over five is not perceptible — while five seconds cuts
    # the jobs a source under active writing opens by nearly half.
    live_sync_watch_min_interval_seconds: float = Field(default=5, gt=0)
    # How long the loop may go without running anything before it is reported.
    # Two seconds is far longer than any legitimate pause here, and short
    # enough to catch the stalls that made hosts wait; lower it to hunt one.
    loop_stall_report_seconds: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def paired_call_event_retention(self) -> Self:
        if (self.call_event_retention_days is None) != (self.call_event_max_events is None):
            raise ValueError(
                "call_event_retention_days and call_event_max_events "
                "must both be set or both be None"
            )
        return self


def load_settings() -> Settings:
    return Settings()
