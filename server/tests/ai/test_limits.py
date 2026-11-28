"""Per-model limits: token buckets, the model limiter and the retry rules.

Plan: "AI layer" > Calls (per-model semaphores with Opus = 1; async rate limiters) and "Risks"
(Claude throttling: semaphore and limiters). Retries apply to model calls, on 429/5xx only.
"""

from __future__ import annotations

import asyncio

import pytest

from blogwriter.ai.limits import (
    ModelCoolingDown,
    ModelLimiter,
    ModelLimits,
    RetryPolicy,
    RetryReason,
    SystemClock,
    TokenBucket,
    estimate_input_tokens,
    retry_reason,
)
from blogwriter.ai.providers.base import (
    ProviderAuthError,
    ProviderRequestError,
    ProviderStreamError,
    ProviderUnavailableError,
    UnsupportedRequestError,
    error_for_status,
)
from blogwriter.ai.types import (
    Message,
    Request,
    Sensitivity,
    ToolCallPart,
    ToolResultPart,
    Usage,
)


class FakeClock:
    """Manual time: ``sleep`` records the wait, moves the clock forward and yields."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --------------------------------------------------------------------------------------------
# Token bucket
# --------------------------------------------------------------------------------------------


async def test_a_bucket_starts_full_and_refills_continuously(clock: FakeClock) -> None:
    bucket = TokenBucket(60, clock=clock)  # one token per second
    assert bucket.capacity == 60
    assert bucket.rate == 1.0
    assert bucket.available() == 60
    assert await bucket.acquire(45) == 0.0
    assert bucket.available() == 15
    clock.advance(10)
    assert bucket.available() == 25
    clock.advance(1_000)
    assert bucket.available() == 60  # never above the capacity
    assert clock.sleeps == []


async def test_acquire_waits_exactly_for_the_missing_tokens(clock: FakeClock) -> None:
    bucket = TokenBucket(120, clock=clock)  # two tokens per second
    await bucket.acquire(100)
    assert bucket.wait_time(50) == pytest.approx(15.0)
    waited = await bucket.acquire(50)
    assert waited == pytest.approx(15.0)
    assert clock.sleeps == [pytest.approx(15.0)]
    assert bucket.available() == pytest.approx(0.0)


async def test_a_custom_period(clock: FakeClock) -> None:
    bucket = TokenBucket(10, clock=clock, period=1.0)  # 10 per second
    await bucket.acquire(10)
    assert await bucket.acquire(5) == pytest.approx(0.5)


async def test_amounts_above_the_capacity_wait_for_a_full_bucket_then_owe(
    clock: FakeClock,
) -> None:
    bucket = TokenBucket(60, clock=clock)
    await bucket.acquire(30)
    waited = await bucket.acquire(90)  # needs a full bucket: 30 s, then 30 tokens of debt
    assert waited == pytest.approx(30.0)
    assert bucket.available() == pytest.approx(-30.0)
    assert await bucket.acquire(1) == pytest.approx(31.0)


async def test_debit_and_credit_adjust_without_waiting(clock: FakeClock) -> None:
    bucket = TokenBucket(60, clock=clock)
    bucket.debit(80)
    assert bucket.available() == -20
    bucket.credit(50)
    assert bucket.available() == 30
    bucket.credit(1_000)
    assert bucket.available() == 60
    assert clock.sleeps == []
    with pytest.raises(ValueError, match="negative"):
        bucket.debit(-1)
    with pytest.raises(ValueError, match="negative"):
        bucket.credit(-1)
    with pytest.raises(ValueError, match="negative"):
        await bucket.acquire(-1)
    with pytest.raises(ValueError, match="negative"):
        bucket.wait_time(-1)


async def test_waiters_are_served_first_come_first_served(clock: FakeClock) -> None:
    bucket = TokenBucket(60, clock=clock)
    await bucket.acquire(60)
    served: list[tuple[str, float]] = []

    async def take(name: str) -> None:
        await bucket.acquire(10)
        served.append((name, clock.now))

    await asyncio.gather(take("a"), take("b"), take("c"))
    assert [name for name, _ in served] == ["a", "b", "c"]
    assert [at for _, at in served] == [
        pytest.approx(110.0),
        pytest.approx(120.0),
        pytest.approx(130.0),
    ]


def test_buckets_need_a_positive_capacity_and_period(clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="capacity"):
        TokenBucket(0, clock=clock)
    with pytest.raises(ValueError, match="period"):
        TokenBucket(10, clock=clock, period=0)


# --------------------------------------------------------------------------------------------
# Model limiter
# --------------------------------------------------------------------------------------------


def test_limits_must_be_positive_or_unset() -> None:
    assert ModelLimits() == ModelLimits(None, None, None, None)
    for name in (
        "concurrency",
        "requests_per_minute",
        "input_tokens_per_minute",
        "output_tokens_per_minute",
    ):
        with pytest.raises(ValueError, match=name):
            ModelLimits(**{name: 0})  # pyright: ignore[reportArgumentType]


async def _peak_concurrency(limiter: ModelLimiter, calls: int) -> int:
    active = 0
    peak = 0

    async def call() -> None:
        nonlocal active, peak
        async with limiter.admit(10):
            active += 1
            peak = max(peak, active)
            for _ in range(3):
                await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(*(call() for _ in range(calls)))
    assert limiter.in_flight == 0
    return peak


@pytest.mark.parametrize(("concurrency", "peak"), [(1, 1), (3, 3), (None, 5)])
async def test_the_semaphore_caps_calls_in_flight(
    clock: FakeClock, concurrency: int | None, peak: int
) -> None:
    limiter = ModelLimiter(ModelLimits(concurrency=concurrency), clock=clock)
    assert await _peak_concurrency(limiter, 5) == peak


async def test_requests_per_minute(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(requests_per_minute=3), clock=clock)
    for _ in range(3):
        async with limiter.admit(0):
            pass
    assert clock.sleeps == []
    async with limiter.admit(0):  # one request every 20 s once the burst is spent
        pass
    assert clock.sleeps == [pytest.approx(20.0)]


async def test_the_input_reservation_is_settled_with_the_real_count(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(input_tokens_per_minute=600), clock=clock)  # 10/s
    async with limiter.admit(400) as lease:
        assert lease.reserved_input == 400
        lease.settle(Usage(input=500, output=10))  # 100 more than estimated
    async with limiter.admit(200) as lease:  # 100 left: waits 10 s for 100 more
        lease.settle(Usage(input=50))  # 150 fewer than reserved: given back
    assert clock.sleeps == [pytest.approx(10.0)]
    async with limiter.admit(150):  # 150 available after the refund
        pass
    assert clock.sleeps == [pytest.approx(10.0)]


async def test_output_tokens_are_charged_after_the_call(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(output_tokens_per_minute=600), clock=clock)  # 10/s
    async with limiter.admit(0) as lease:
        lease.settle(Usage(input=10, output=900))  # 300 tokens of debt
    async with limiter.admit(0):  # waits until the bucket is out of debt
        pass
    assert clock.sleeps == [pytest.approx(30.0)]


async def test_settle_counts_once_and_no_usage_keeps_the_reservation(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(input_tokens_per_minute=600), clock=clock)
    async with limiter.admit(500) as lease:
        lease.settle(None)  # a failed call without usage: the reservation stays
        lease.settle(Usage(input=0))  # ignored
    async with limiter.admit(200):  # 100 left: 10 s
        pass
    assert clock.sleeps == [pytest.approx(10.0)]


async def test_unlimited_tokens_reserve_nothing(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(), clock=clock)
    async with limiter.admit(10_000_000) as lease:
        assert lease.reserved_input == 0
        lease.settle(Usage(input=10_000_000, output=10_000_000))
    async with limiter.admit(10_000_000):
        pass
    assert clock.sleeps == []


async def test_a_cooldown_holds_every_new_call(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(), clock=clock)
    limiter.cool_down(5.0)
    limiter.cool_down(2.0)  # never shortens a cooldown
    limiter.cool_down(-1.0)  # ignored
    async with limiter.admit(0):
        pass
    assert clock.sleeps == [pytest.approx(5.0)]
    async with limiter.admit(0):
        pass
    assert clock.sleeps == [pytest.approx(5.0)]


async def test_a_bounded_wait_refuses_a_long_cooldown_at_once(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(concurrency=1), clock=clock)
    limiter.cool_down(600.0)  # a quota Retry-After: ten minutes
    with pytest.raises(ModelCoolingDown) as cooling:
        async with limiter.admit(0, max_cooldown=60.0):
            pass  # pragma: no cover - never admitted
    assert cooling.value.remaining == pytest.approx(600.0)
    assert clock.sleeps == []  # nobody sits on the only slot for ten minutes
    assert limiter.in_flight == 0

    async def admitted() -> int:
        async with limiter.admit(0, max_cooldown=60.0):
            return limiter.in_flight

    clock.advance(570.0)  # 30 s left: within the bound, so the caller waits it out
    assert await asyncio.wait_for(admitted(), timeout=1) == 1  # the slot was given back
    assert clock.sleeps == [pytest.approx(30.0)]
    with pytest.raises(ValueError, match="max_cooldown"):
        async with limiter.admit(0, max_cooldown=-1.0):
            pass  # pragma: no cover


async def test_the_slot_is_released_on_errors_and_cancellation(clock: FakeClock) -> None:
    limiter = ModelLimiter(ModelLimits(concurrency=1), clock=clock)
    with pytest.raises(RuntimeError, match="boom"):
        async with limiter.admit(0):
            raise RuntimeError("boom")
    assert limiter.in_flight == 0

    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with limiter.admit(0):
            entered.set()
            await release.wait()

    async def waiter() -> None:
        async with limiter.admit(0):
            pass  # pragma: no cover - cancelled while waiting for the slot

    holding = asyncio.create_task(holder())
    await entered.wait()
    waiting = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    release.set()
    await holding
    async with limiter.admit(0):  # the slot is free again
        assert limiter.in_flight == 1
    with pytest.raises(ValueError, match="negative"):
        async with limiter.admit(-1):
            pass  # pragma: no cover


async def test_the_system_clock_sleeps_for_real() -> None:
    clock = SystemClock()
    before = clock.monotonic()
    await clock.sleep(0)
    assert clock.monotonic() >= before


def test_the_input_estimate_counts_text_tool_calls_and_results() -> None:
    request = Request(
        system="s" * 40,
        messages=(
            Message.user("u" * 400),
            Message("assistant", (ToolCallPart("call_1", "fetch_page", "a" * 80),)),
            Message.tool_results(ToolResultPart("call_1", "r" * 200)),
        ),
        sensitivity=Sensitivity.PUBLIC,
    )
    assert estimate_input_tokens(request) == (40 + 400 + 80 + 200) // 4 + 1


# --------------------------------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------------------------------


def _status(status: int, code: str | None = None, **headers: str) -> BaseException:
    return error_for_status("openai", status, code=code, request_id="req_1", headers=headers)


def _in_stream(code: str | None) -> BaseException:
    return error_for_status(
        "anthropic", 200, code=code, request_id=None, headers={}, during_stream=True
    )


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (_status(429, "rate_limit_exceeded"), "rate_limited"),
        (_status(429), "rate_limited"),
        (_status(500, "server_error"), "unavailable"),
        (_status(502), "unavailable"),
        (_status(503), "unavailable"),
        (_status(529, "overloaded_error"), "unavailable"),
        (_in_stream("overloaded_error"), "unavailable"),
        (_in_stream("api_error"), "unavailable"),
        (_in_stream("rate_limit_error"), "rate_limited"),
        (_in_stream(None), "unavailable"),
        (
            ProviderUnavailableError("connection failed", provider="openai", retryable=True),
            "unavailable",
        ),
        (
            ProviderStreamError("connection reset", provider="anthropic", retryable=True),
            "unavailable",
        ),
    ],
)
def test_rate_limits_and_server_errors_are_retried(
    error: BaseException, reason: RetryReason
) -> None:
    assert retry_reason(error) == reason


@pytest.mark.parametrize(
    "error",
    [
        _status(400, "invalid_request_error"),
        _status(401),
        _status(403),
        _status(404, "DeploymentNotFound"),
        _status(408),
        _status(409),
        _status(413),
        _status(422),
        _status(429, **{"x-should-retry": "false"}),
        _status(500, **{"x-should-retry": "false"}),
        _status(400, **{"x-should-retry": "true"}),
        _in_stream("invalid_request_error"),
        ProviderAuthError("denied", provider="openai", retryable=False, status_code=403),
        ProviderRequestError("bad", provider="openai", retryable=False, status_code=400),
        ProviderStreamError("failed", provider="openai", retryable=False),
        UnsupportedRequestError("web_search on Claude"),
        ValueError("not a provider error"),
        TimeoutError(),
    ],
)
def test_nothing_else_is_retried(error: BaseException) -> None:
    assert retry_reason(error) is None


def test_backoff_doubles_with_jitter_up_to_the_cap() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=8.0)
    assert policy.delay(1, jitter=0.0) == 0.5
    assert policy.delay(1, jitter=1.0) == 1.0
    assert policy.delay(2, jitter=0.5) == 1.5
    assert policy.delay(3, jitter=1.0) == 4.0
    assert policy.delay(4, jitter=1.0) == 8.0
    assert policy.delay(5, jitter=1.0) == 8.0  # capped
    assert policy.delay(6, jitter=1.0) is None  # six attempts made: give up


def test_retry_after_is_a_floor_and_a_long_one_gives_up() -> None:
    policy = RetryPolicy(max_retry_after=60.0)
    assert policy.delay(1, jitter=0.0, retry_after=7.0) == 7.0
    assert policy.delay(1, jitter=1.0, retry_after=0.25) == 1.0
    assert policy.delay(1, jitter=0.0, retry_after=60.0) == 60.0
    assert policy.delay(1, jitter=0.0, retry_after=61.0) is None


def test_the_default_policy_makes_four_attempts() -> None:
    policy = RetryPolicy()
    delays = [policy.delay(retry, jitter=0.5) for retry in (1, 2, 3, 4)]
    assert delays == [0.75, 1.5, 3.0, None]


def test_retry_policy_validation() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=11)
    with pytest.raises(ValueError, match="delays"):
        RetryPolicy(base_delay=5.0, max_delay=1.0)
    with pytest.raises(ValueError, match="delays"):
        RetryPolicy(base_delay=-1.0)
    with pytest.raises(ValueError, match="max_retry_after"):
        RetryPolicy(max_retry_after=-1.0)
    policy = RetryPolicy()
    with pytest.raises(ValueError, match="start at 1"):
        policy.delay(0, jitter=0.5)
    with pytest.raises(ValueError, match="jitter"):
        policy.delay(1, jitter=1.5)
