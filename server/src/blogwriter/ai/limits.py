"""Per-model admission control and retries for model calls.

Everything here waits on an injected :class:`Clock`, so tests move time by hand while production
uses :class:`SystemClock` (``time.monotonic`` and ``asyncio.sleep``).

:class:`TokenBucket`
    An async token bucket that refills continuously (``capacity`` tokens per ``period``
    seconds). Waiters are served first come, first served. A request larger than the capacity
    waits for a full bucket and then leaves it in debt, so the long-run rate still holds.
:class:`ModelLimiter`
    The limits of one model, applied by :meth:`ModelLimiter.admit` before every call:

    * a semaphore on calls in flight (``claude-opus-5-5`` defaults to 1);
    * a requests-per-minute bucket;
    * an input-tokens-per-minute bucket, reserved from an estimate before the call and settled
      with the real count afterwards;
    * an output-tokens-per-minute bucket. Output is unknown until the call ends, so it is charged
      afterwards and the next call waits until the bucket is out of debt;
    * a cooldown that a ``Retry-After`` on a failed call sets for every caller of the model. A
      caller can bound how long it waits for one (``max_cooldown``): past that bound,
      :class:`ModelCoolingDown` is raised at once rather than holding the call, and the model's
      slot, for as long as the server asked (a quota ``Retry-After`` can be hours).
:class:`RetryPolicy` and :func:`retry_reason`
    Whether and when a failed **model call** is retried: only rate limits (HTTP 429) and server
    errors (5xx), plus their transport-level equivalents (an error event inside a stream, or no
    HTTP response at all). Never 408, 409 or any other 4xx, and never an error the provider marked
    as not retryable (``x-should-retry: false``). Other kinds of calls (fetches, LinkedIn, the
    store) must not use these rules: a LinkedIn 5xx can still have created the post.

The SDK clients should be built with ``max_retries=0`` so the router owns retries (otherwise both
layers retry and the waits multiply).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from typing import Final, Literal, Protocol

from blogwriter.ai.providers.base import (
    ProviderError,
    ProviderStreamError,
    ProviderUnavailableError,
    RateLimitedError,
)
from blogwriter.ai.types import ImagePart, Request, TextPart, ToolCallPart, Usage

__all__ = [
    "Clock",
    "Lease",
    "ModelCoolingDown",
    "ModelLimiter",
    "ModelLimits",
    "RetryPolicy",
    "RetryReason",
    "SystemClock",
    "TokenBucket",
    "estimate_input_tokens",
    "retry_reason",
]

type RetryReason = Literal["rate_limited", "unavailable"]
"""Why a model call may be retried: HTTP 429 (or its equivalent) or a 5xx-class failure."""

_EPSILON: Final = 1e-9
_CHARS_PER_TOKEN: Final = 4
_IMAGE_TOKENS: Final = 1_600
"""Input reserved per image part (about a 1.15 MP image on Claude; settled with the real count)."""
_MAX_ATTEMPTS_CAP: Final = 10
# Error codes the providers use for rate limits inside a stream (see providers.base).
_RATE_LIMIT_CODES: Final = frozenset({"rate_limit_exceeded", "rate_limit_error", "429"})


class Clock(Protocol):
    """Monotonic time and sleeping, injectable for tests."""

    def monotonic(self) -> float:
        """Seconds on a monotonic clock."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` (cancellable)."""
        ...


class SystemClock:
    """The real clock: ``time.monotonic`` and ``asyncio.sleep``."""

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


# --------------------------------------------------------------------------------------------
# Token bucket
# --------------------------------------------------------------------------------------------


class TokenBucket:
    """``capacity`` tokens per ``period`` seconds, refilled continuously; starts full.

    :meth:`acquire` waits (first come, first served) until the amount is available, then takes
    it. An amount above the capacity waits for a full bucket and leaves the bucket in debt.
    :meth:`debit` and :meth:`credit` adjust the balance after the fact without waiting.
    """

    def __init__(self, capacity: int, *, clock: Clock, period: float = 60.0) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if period <= 0:
            raise ValueError("period must be positive")
        self._capacity = float(capacity)
        self._rate = capacity / period
        self._clock = clock
        self._tokens = float(capacity)
        self._updated = clock.monotonic()
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> int:
        return int(self._capacity)

    @property
    def rate(self) -> float:
        """Tokens added per second."""
        return self._rate

    def _refill(self) -> None:
        now = self._clock.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated = max(self._updated, now)

    def available(self) -> float:
        """The current balance (negative while in debt)."""
        self._refill()
        return self._tokens

    def wait_time(self, amount: float) -> float:
        """Seconds until ``amount`` could be taken (amounts above the capacity need it full)."""
        if amount < 0:
            raise ValueError("amount must not be negative")
        deficit = min(amount, self._capacity) - self.available()
        return deficit / self._rate if deficit > _EPSILON else 0.0

    async def acquire(self, amount: float) -> float:
        """Wait until ``amount`` is available, then take it. Returns the seconds waited."""
        if amount < 0:
            raise ValueError("amount must not be negative")
        waited = 0.0
        async with self._lock:
            while (delay := self.wait_time(amount)) > 0:
                await self._clock.sleep(delay)
                waited += delay
            self._tokens -= amount
        return waited

    def debit(self, amount: float) -> None:
        """Take ``amount`` now without waiting (the balance may go negative)."""
        if amount < 0:
            raise ValueError("amount must not be negative")
        self._refill()
        self._tokens -= amount

    def credit(self, amount: float) -> None:
        """Give back ``amount`` (for example an over-estimated reservation), up to the capacity."""
        if amount < 0:
            raise ValueError("amount must not be negative")
        self._refill()
        self._tokens = min(self._capacity, self._tokens + amount)


# --------------------------------------------------------------------------------------------
# Per-model limiter
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelLimits:
    """Client-side limits of one model. ``None`` means unlimited."""

    concurrency: int | None = None
    requests_per_minute: int | None = None
    input_tokens_per_minute: int | None = None
    output_tokens_per_minute: int | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            value: int | None = getattr(self, item.name)
            if value is not None and value < 1:
                raise ValueError(f"{item.name} must be at least 1 (or unset)")


class ModelCoolingDown(Exception):
    """A model's ``Retry-After`` cooldown has longer to run than the caller will wait.

    ``remaining`` is the rest of the cooldown, in seconds. Nothing was reserved or sent.
    """

    def __init__(self, remaining: float) -> None:
        self.remaining = remaining
        super().__init__(f"the model is cooling down for another {remaining:.1f} s")


class Lease:
    """One admitted call. Report its usage with :meth:`settle` once it ends."""

    def __init__(self, limiter: ModelLimiter, reserved_input: int) -> None:
        self._limiter = limiter
        self._reserved_input = reserved_input
        self._settled = False

    @property
    def reserved_input(self) -> int:
        """Input tokens reserved from the estimate."""
        return self._reserved_input

    def settle(self, usage: Usage | None) -> None:
        """Replace the input estimate with the real count and charge the output tokens.

        ``None`` (a failed call without reported usage) keeps the reservation. Only the first
        call counts.
        """
        if self._settled:
            return
        self._settled = True
        if usage is not None:
            self._limiter.settle(self._reserved_input, usage)


class ModelLimiter:
    """Concurrency, rate limits and cooldown for one model (see the module docstring)."""

    def __init__(self, limits: ModelLimits, *, clock: Clock) -> None:
        self._limits = limits
        self._clock = clock
        self._semaphore = (
            asyncio.Semaphore(limits.concurrency) if limits.concurrency is not None else None
        )
        self._requests = self._bucket(limits.requests_per_minute)
        self._input = self._bucket(limits.input_tokens_per_minute)
        self._output = self._bucket(limits.output_tokens_per_minute)
        self._cooldown_until = clock.monotonic()
        self._in_flight = 0

    def _bucket(self, per_minute: int | None) -> TokenBucket | None:
        return TokenBucket(per_minute, clock=self._clock) if per_minute is not None else None

    @property
    def limits(self) -> ModelLimits:
        return self._limits

    @property
    def in_flight(self) -> int:
        """Calls admitted and not yet finished."""
        return self._in_flight

    def cool_down(self, seconds: float) -> None:
        """Hold every new call to this model for ``seconds`` (from a ``Retry-After``)."""
        if seconds > 0:
            self._cooldown_until = max(self._cooldown_until, self._clock.monotonic() + seconds)

    async def _wait_for_cooldown(self, max_wait: float | None) -> None:
        while (remaining := self._cooldown_until - self._clock.monotonic()) > _EPSILON:
            if max_wait is not None and remaining > max_wait:
                raise ModelCoolingDown(remaining)
            await self._clock.sleep(remaining)

    @asynccontextmanager
    async def admit(
        self, input_estimate: int, *, max_cooldown: float | None = None
    ) -> AsyncGenerator[Lease]:
        """Wait for a slot and the rate limits, then hold the slot for the call.

        ``input_estimate`` is reserved from the input bucket; call :meth:`Lease.settle` with the
        call's usage so the real counts replace it. ``max_cooldown`` bounds the wait for a
        ``Retry-After`` cooldown: when more than that remains, :class:`ModelCoolingDown` is
        raised and the slot is given back (``None`` waits for the whole cooldown).
        """
        if input_estimate < 0:
            raise ValueError("input_estimate must not be negative")
        if max_cooldown is not None and max_cooldown < 0:
            raise ValueError("max_cooldown must not be negative")
        semaphore = self._semaphore
        if semaphore is not None:
            await semaphore.acquire()
        try:
            # Checked after the slot is held: a cooldown may start while a caller waits for it.
            await self._wait_for_cooldown(max_cooldown)
            if self._requests is not None:
                await self._requests.acquire(1)
            if self._input is not None:
                await self._input.acquire(input_estimate)
            if self._output is not None:
                await self._output.acquire(0)  # wait until the bucket is out of debt
            lease = Lease(self, input_estimate if self._input is not None else 0)
            self._in_flight += 1
            try:
                yield lease
            finally:
                self._in_flight -= 1
        finally:
            if semaphore is not None:
                semaphore.release()

    def settle(self, reserved_input: int, usage: Usage) -> None:
        """Apply a finished call's usage (normally through :meth:`Lease.settle`).

        Every input token counts, cached ones included: Foundry quotas may count them.
        """
        if self._input is not None:
            difference = usage.input - reserved_input
            if difference > 0:
                self._input.debit(difference)
            elif difference < 0:
                self._input.credit(-difference)
        if self._output is not None and usage.output:
            self._output.debit(usage.output)


def estimate_input_tokens(request: Request) -> int:
    """A rough prompt size (about 4 characters per token, a fixed allowance per image) for the
    input reservation.

    Opaque native items are not counted; :meth:`Lease.settle` corrects the estimate.
    """
    characters = len(request.system)
    images = 0
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, TextPart):
                characters += len(part.text)
            elif isinstance(part, ImagePart):
                images += 1
            elif isinstance(part, ToolCallPart):
                characters += len(part.arguments)
            else:
                characters += len(part.output)
    return characters // _CHARS_PER_TOKEN + 1 + images * _IMAGE_TOKENS


# --------------------------------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------------------------------


def retry_reason(error: BaseException) -> RetryReason | None:
    """Why a failed model call may be retried, or ``None`` if it must not be.

    Retried: HTTP 429 and rate-limit errors; HTTP 5xx; a retryable error inside a stream (which
    arrives with the stream's 200 status, so its code decides); a failure with no HTTP response
    at all (connection failure or timeout), treated like a 503. Everything else is final,
    including 408, 409, auth and request errors, and any error marked not retryable.
    """
    if not isinstance(error, ProviderError) or not error.retryable:
        return None
    status = error.status_code
    if isinstance(error, RateLimitedError) or status == 429:
        return "rate_limited"
    if status is not None and status >= 500:
        return "unavailable"
    if isinstance(error, ProviderStreamError):
        return "rate_limited" if error.code in _RATE_LIMIT_CODES else "unavailable"
    if isinstance(error, ProviderUnavailableError) and status is None:
        return "unavailable"
    return None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with jitter for model calls.

    ``max_attempts`` counts the first call. The wait before retry ``n`` (1-based) is drawn from
    ``[step / 2, step]`` with ``step = min(max_delay, base_delay * 2 ** (n - 1))``. A
    ``Retry-After`` from the server is a floor; one longer than ``max_retry_after`` means the
    quota is exhausted for now, so the call stops retrying (and the router falls back). The
    router also caps every later caller's wait for that model's cooldown at
    ``max_retry_after`` (:meth:`ModelLimiter.admit`), so they fall back too instead of waiting.
    """

    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_retry_after: float = 60.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= _MAX_ATTEMPTS_CAP:
            raise ValueError(f"max_attempts must be between 1 and {_MAX_ATTEMPTS_CAP}")
        if self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("delays must satisfy 0 <= base_delay <= max_delay")
        if self.max_retry_after < 0:
            raise ValueError("max_retry_after must not be negative")

    def delay(self, retry: int, *, jitter: float, retry_after: float | None = None) -> float | None:
        """Seconds to wait before retry number ``retry``, or ``None`` to stop retrying.

        ``jitter`` is a random number in ``[0, 1]``.
        """
        if retry < 1:
            raise ValueError("retry numbers start at 1")
        if not 0.0 <= jitter <= 1.0:
            raise ValueError("jitter must be between 0 and 1")
        if retry >= self.max_attempts:
            return None
        if retry_after is not None and retry_after > self.max_retry_after:
            return None
        step = min(self.max_delay, self.base_delay * 2 ** (retry - 1))
        delay = step / 2 + jitter * step / 2
        return max(delay, retry_after or 0.0)
