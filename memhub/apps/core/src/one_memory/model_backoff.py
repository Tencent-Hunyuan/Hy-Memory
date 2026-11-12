"""One pause for every model request while the provider keeps failing (#245).

A broken model configuration (a revoked key, a wrong base URL, a model that
does not exist, a provider that is down) used to fail each queued job on its
own: the drain claimed the next job the moment the last one failed, so a
backlog of hundreds of jobs turned into requests as fast as the provider could
refuse them. The pause is global, not per job: a failure says something about
the provider, not about the conversation that happened to hit it.

After the first failure no further model work is claimed for 5 seconds; each
failed probe after that doubles the pause, up to 3 minutes. A pause admits one
probe job at a time. The first success, or any change the user makes to the
model configuration, ends it at once. Jobs not yet sent stay `pending`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BACKOFF_FIRST_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 180.0
#: How long one probe may hold the pause open before another is admitted. A
#: probe job that never reaches the provider (nothing to remember, a refusal
#: before the SDK) reports nothing, and the queue must not wait on it forever.
PROBE_HOLD_SECONDS = 30.0
#: Requests already in flight when the pause began are refused without going
#: out once this many failures in a row confirm it is not a one-off.
REFUSE_IN_FLIGHT_AFTER = 2

#: Statuses that say the configuration or the provider is wrong, not the one
#: request: authentication, a missing model or route, throttling, a timeout
#: and the provider's own failures. A 400/413/422 is about the request's
#: content and is left to that job.
_PROVIDER_STATUSES = {401: "model_auth_failed", 403: "model_auth_failed", 404: "model_not_found"}

#: Every code a provider failure is recorded under: a job that failed with one
#: of these waits for the provider instead of spending a retry.
MODEL_FAILURE_CODES = frozenset(
    {
        "model_auth_failed",
        "model_not_found",
        "model_timeout",
        "model_rate_limited",
        "model_server_error",
        "model_unreachable",
        "model_transport_failed",
    }
)


def backoff_seconds(level: int) -> float:
    """The pause after the `level`-th failed attempt in a row (1-based)."""
    if level < 1:
        return 0.0
    return min(BACKOFF_FIRST_SECONDS * float(2 ** min(level - 1, 16)), BACKOFF_CAP_SECONDS)


def failure_code(status: int) -> str | None:
    """The stable code for a provider HTTP status, or None if it is no provider failure."""
    if status in _PROVIDER_STATUSES:
        return _PROVIDER_STATUSES[status]
    if status in {408, 504}:
        return "model_timeout"
    if status == 429:
        return "model_rate_limited"
    if status >= 500:
        return "model_server_error"
    return None


def transport_failure_code(error: BaseException) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "model_timeout"
    if isinstance(error, httpx.ConnectError | httpx.NetworkError):
        return "model_unreachable"
    return "model_transport_failed"


def request_kind(request: httpx.Request) -> str:
    """`embedding` for the embeddings route, `chat` for everything else."""
    return "embedding" if request.url.path.rstrip("/").endswith("/embeddings") else "chat"


@dataclass(frozen=True)
class ModelBackoffState:
    #: When the pause ends; None when no pause is in force.
    until: datetime | None
    #: Failed attempts in a row.
    failures: int
    #: The last failure's stable code (`model_auth_failed`, ...).
    last_error: str | None
    profile_id: str | None


class ModelBackoffRefusal(httpx.TransportError):
    """A request refused locally because the provider is being given a rest."""


@dataclass(eq=False)
class ModelAttempt:
    """One job's write, as the provider saw it.

    `failure` is the code of the first provider failure (or local refusal)
    that happened while this write was in flight, None if there was none.
    """

    failure: str | None = None


class ModelBackoff:
    """Process-wide circuit breaker for the active model profile.

    Thread-safe: the transport records from the SDK's own loop thread, the
    drain asks from its claim thread, and status reads from the event loop.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        #: Writes in flight (`attempt`); never cleared by a reset.
        self._attempts: set[ModelAttempt] = set()
        self._reset_locked()

    def _reset_locked(self) -> None:
        #: Which kinds of request are failing (`chat`, `embedding`): a working
        #: embedding endpoint says nothing about a chat model that is gone.
        self._failing: set[str] = set()
        self._level = 0
        self._failures = 0
        self._until = 0.0
        self._probe_until = 0.0
        self._last_error: str | None = None
        self._profile_id: str | None = None

    def reset(self, reason: str = "model_configuration_changed") -> None:
        """Forget every failure: the user changed what is being called."""
        with self._lock:
            was_paused = self._failures > 0
            self._reset_locked()
        if was_paused:
            logger.info("model_backoff_reset reason=%s", reason)

    def record_success(self, profile_id: str | None = None, kind: str = "model") -> None:
        with self._lock:
            if self._failures == 0:
                return
            if profile_id is not None and self._profile_id not in {None, profile_id}:
                return
            self._failing.discard(kind)
            if self._failing:
                return
            self._reset_locked()
        logger.info("model_backoff_cleared profile_id=%s", profile_id)

    def record_failure(self, code: str, profile_id: str | None = None, kind: str = "model") -> None:
        now = self._clock()
        with self._lock:
            if profile_id is not None and self._profile_id not in {None, profile_id}:
                # Another profile's history says nothing about this one.
                self._reset_locked()
            self._profile_id = profile_id
            self._failing.add(kind)
            self._mark_attempts_locked(code)
            self._failures += 1
            self._last_error = code
            self._probe_until = 0.0
            # Requests that were already out when the pause began fail too;
            # they are the same outage, not a reason to wait longer.
            if now >= self._until:
                self._level += 1
                self._until = now + backoff_seconds(self._level)
            level, pause = self._level, self._until - now
        logger.warning(
            "model_backoff_failure error_code=%s level=%d pause_s=%.0f profile_id=%s",
            code,
            level,
            pause,
            profile_id,
        )

    @contextmanager
    def attempt(self) -> Iterator[ModelAttempt]:
        """Hold one job's write open, and learn whether the provider failed it.

        Every provider failure and every local refusal while the write is in
        flight is charged to it. A request cannot be traced to the job that
        made it: the SDK sends embeddings from one shared batching worker, so
        neither the calling thread nor a context variable identifies the job.
        Charging every write then in flight can name a write the provider did
        not fail (another job's request failed beside it); that write waits
        for the provider and is retried with the rest. It can never miss the
        write the provider did fail, which would spend that job's retries on
        an outage.
        """
        record = ModelAttempt()
        with self._lock:
            self._attempts.add(record)
        try:
            yield record
        finally:
            with self._lock:
                self._attempts.discard(record)

    def record_refusal(self) -> str:
        """A request refused locally; charged to the writes in flight like a failure."""
        with self._lock:
            code = self._last_error or "model_transport_failed"
            self._mark_attempts_locked(code)
        return code

    def _mark_attempts_locked(self, code: str) -> None:
        for record in self._attempts:
            if record.failure is None:
                record.failure = code

    def paused(self) -> bool:
        with self._lock:
            return self._failures > 0 and self._clock() < self._until

    def refuses_requests(self, kind: str = "model") -> bool:
        """Whether a request of this kind should not go out at all right now.

        Only the failing kind is refused: a chat model that is gone must not
        take recall's embedding calls down with it.
        """
        with self._lock:
            return (
                kind in self._failing
                and self._failures >= REFUSE_IN_FLIGHT_AFTER
                and self._clock() < self._until
            )

    def allowance(self, *, kind: str | None = None) -> int | None:
        """How many matching provider jobs may be claimed now.

        0 while paused; once the pause is over, one probe at a time until it
        reports or `PROBE_HOLD_SECONDS` pass. ``kind`` keeps a failed chat
        model from pausing raw-only work that needs embeddings, while an
        embedding outage still paces that work.
        """
        now = self._clock()
        with self._lock:
            if self._failures == 0 or (kind is not None and kind not in self._failing):
                return None
            if now < self._until or now < self._probe_until:
                return 0
            self._probe_until = now + PROBE_HOLD_SECONDS
            return 1

    def state(self) -> ModelBackoffState:
        now = self._clock()
        with self._lock:
            remaining = self._until - now if self._failures else 0.0
            return ModelBackoffState(
                until=(
                    self._wall_clock() + timedelta(seconds=remaining) if remaining > 0 else None
                ),
                failures=self._failures,
                last_error=self._last_error,
                profile_id=self._profile_id,
            )


def next_retry_at(app: Any, state: str, own: str | None) -> str | None:
    """When a queued job is tried next: its own retry wait, or the provider's pause.

    While the breaker pauses, nothing is claimed at all, so the pause holds
    every queued job, whatever it last failed with.
    """
    if state not in {"accepted", "pending"}:
        return None
    backoff = getattr(getattr(app, "state", None), "model_backoff", None)
    until = backoff.state().until if isinstance(backoff, ModelBackoff) else None
    paused = until.isoformat() if until is not None else None
    candidates = [value for value in (own, paused) if value]
    return max(candidates) if candidates else None


class BackedOffModelTransport(httpx.AsyncBaseTransport):
    """Report each provider exchange to the breaker; refuse while it confirms a pause."""

    def __init__(
        self,
        backoff: ModelBackoff,
        *,
        profile_id: str,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.backoff = backoff
        self.profile_id = profile_id
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        kind = request_kind(request)
        if self.backoff.refuses_requests(kind):
            self.backoff.record_refusal()
            raise ModelBackoffRefusal("model_backoff_active", request=request)
        try:
            response = await self.inner.handle_async_request(request)
        except ModelBackoffRefusal:
            raise
        except httpx.TransportError as error:
            self.backoff.record_failure(transport_failure_code(error), self.profile_id, kind)
            raise
        code = failure_code(response.status_code)
        if code is not None:
            self.backoff.record_failure(code, self.profile_id, kind)
        elif response.status_code < 400:
            self.backoff.record_success(self.profile_id, kind)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
