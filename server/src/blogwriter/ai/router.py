"""The AI router: one entry point for every model call.

:meth:`AIRouter.run` and :meth:`AIRouter.stream` serve one task with one request:

1. Look up the task's route; a task without a route is refused (:class:`NoRouteError`).
2. Check the sensitivity policy against **every** model of the chain, primary and fallback
   (:class:`~blogwriter.ai.policy.PolicyViolation`), compile the output schema and the function
   tools' parameters for each provider, and check the budget brake for background work
   (:class:`~blogwriter.ai.ledger.BrakeEngaged`).
   Nothing is sent until all of this passes.
3. Call the primary through its limiter (semaphore and rate limits), retrying HTTP 429 and 5xx
   with jittered backoff (:mod:`blogwriter.ai.limits`). Any other error is raised as it is. A
   model still cooling down from a ``Retry-After`` longer than the retry policy waits is not
   called at all: it counts as rate limited at once.
4. Re-validate structured output with Pydantic and allow one repair turn.
5. Fall back to the next model on a refusal, when retries are exhausted (quota or availability),
   or when the output is still invalid or truncated after the repair turn.
6. Record every call in the ledger, discarded ones included (a failed write is logged and still
   counted by the brake), and return a :class:`RouterResult`.
   Its :class:`ServedBy` names the deployment, provider, effort and fallback reason; a fallback
   that served ``draft``, ``revise`` or ``critique`` marks the result review-only.

Streaming contract
    ``stream`` yields the live :class:`~blogwriter.ai.types.TextDelta`,
    :class:`~blogwriter.ai.types.ThinkingSummary` and :class:`~blogwriter.ai.types.ToolCall`
    events of the current attempt, and a provider's :class:`~blogwriter.ai.types.Refusal`.
    :data:`DISCARD_EVENTS` (``Refusal``, :class:`Retry`, :class:`Repair`, :class:`Fallback`) void
    the current attempt: discard everything received since the start or since the previous one
    of them. A ``Refusal`` voids its attempt even when no fallback follows (the result is then
    refused, with empty text), so partial text is dropped the moment the model declines. The
    last event is always :class:`Finished`; its result is the only output to keep (run tool
    calls from ``result.completion.tool_calls`` only). Partial output of a refused or retried
    attempt is therefore never final. Iterate with ``contextlib.aclosing`` so an abandoned
    stream releases its model slot at once.

Embeddings
    :meth:`AIRouter.embed` serves ``Task.EMBED`` through the same checks (route, policy, brake),
    the model's limiter, the retry policy and the ledger, on the route's primary only:
    embeddings of different models live in different vector spaces, so a fallback would make
    stored vectors silently incomparable.

The router never sends a forced ``tool_choice`` or a ``temperature`` (the request types have
neither, and the providers never add them). Provider-side background runs are not served here.
Logs carry ids, counts, durations and costs only.
"""

from __future__ import annotations

import logging
import random
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel

from blogwriter.ai.config import AISettings, NoRouteError, Route, RoutingTable
from blogwriter.ai.ledger import (
    BudgetBrake,
    CallOutcome,
    LedgerSink,
    Origin,
    PriceTable,
    UsageRecord,
)
from blogwriter.ai.limits import (
    Clock,
    ModelCoolingDown,
    ModelLimiter,
    RetryPolicy,
    RetryReason,
    SystemClock,
    estimate_input_tokens,
    retry_reason,
)
from blogwriter.ai.policy import SensitivityPolicy
from blogwriter.ai.providers import (
    AnthropicFoundryProvider,
    EmbeddingProvider,
    OpenAIResponsesProvider,
    Provider,
    ProviderError,
    ProviderStreamError,
    RateLimitedError,
    TokenProvider,
    UnsupportedRequestError,
    create_anthropic_client,
    create_openai_client,
)
from blogwriter.ai.schema import (
    CompiledSchema,
    OutputValidationError,
    StructuredOutputError,
    compile_schema,
    repair_request,
)
from blogwriter.ai.types import (
    Completion,
    Done,
    Effort,
    Embeddings,
    FunctionTool,
    Message,
    ModelSpec,
    ProviderName,
    Refusal,
    Request,
    Sensitivity,
    Task,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    Usage,
    UsageEvent,
)
from blogwriter.core.logging import describe_exception, register_log_fields

__all__ = [
    "DISCARD_EVENTS",
    "MAX_EMBED_CHARS",
    "MAX_EMBED_INPUTS",
    "REVIEW_ONLY_TASKS",
    "AIRouter",
    "EmbeddingResult",
    "Fallback",
    "FallbackReason",
    "Finished",
    "NoRouteError",
    "Repair",
    "Retry",
    "RouterEvent",
    "RouterResult",
    "ServedBy",
    "create_providers",
]

type FallbackReason = Literal[
    "refusal", "rate_limited", "unavailable", "invalid_output", "incomplete_output"
]
"""Why a model did not serve and the next one in the chain was tried."""

REVIEW_ONLY_TASKS: Final = frozenset({Task.DRAFT, Task.REVISE, Task.CRITIQUE})
"""Tasks whose output becomes review-only when a fallback model served it."""

# Tasks with a route (for policy and pricing) that run/stream do not serve.
_NOT_CHAT: Final = frozenset({Task.EMBED, Task.IMAGE})

MAX_EMBED_INPUTS: Final = 256
"""Most texts in one embeddings call (the service allows 2,048; batches stay small)."""
MAX_EMBED_CHARS: Final = 32_000
"""Longest text one embeddings input may have (about 8,000 tokens; the model takes 8,192)."""
# Finish reasons that hand control back to the caller without validating structured output.
_CALLER_TURN: Final = frozenset({"tool_calls", "pause", "cancelled"})

_logger: Final = logging.getLogger("blogwriter.ai.router")
register_log_fields(
    "task",
    "model",
    "deployment",
    "provider",
    "origin",
    "outcome",
    "job_id",
    "response_id",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "web_searches",
    "cost_usd",
    "attempt",
    "delay_ms",
    "retry_reason",
    "fallback_reason",
    "from_deployment",
    "to_deployment",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------------
# Results and events
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServedBy:
    """Which model served a step, and why the primary did not (stored with the step)."""

    task: Task
    model: str
    deployment: str
    provider: ProviderName
    effort: Effort | None
    fallback_reason: FallbackReason | None = None
    review_only: bool = False
    repaired: bool = False

    @property
    def fallback(self) -> bool:
        """Whether a fallback model served."""
        return self.fallback_reason is not None

    def to_dict(self) -> dict[str, str | bool | None]:
        """The record as a JSON object (camelCase keys, like the store's documents)."""
        return {
            "task": self.task.value,
            "model": self.model,
            "deployment": self.deployment,
            "provider": self.provider,
            "effort": self.effort,
            "fallbackReason": self.fallback_reason,
            "reviewOnly": self.review_only,
            "repaired": self.repaired,
        }


@dataclass(frozen=True, slots=True)
class RouterResult:
    """The outcome of one routed request.

    ``completion`` is the serving model's (refused when every model refused; then ``text`` is
    empty). ``value`` is the validated structured output, if the request had a schema and the
    model finished. ``usage`` and ``cost_usd`` cover every call made for the request, discarded
    attempts included. ``refusals`` lists the refusals met along the way.
    """

    completion: Completion
    served_by: ServedBy
    usage: Usage
    cost_usd: Decimal
    value: BaseModel | None = None
    refusals: tuple[Refusal, ...] = ()

    @property
    def text(self) -> str:
        return self.completion.text

    @property
    def refused(self) -> bool:
        return self.completion.finish_reason == "refusal"

    @property
    def review_only(self) -> bool:
        return self.served_by.review_only

    def output[ModelT: BaseModel](self, model: type[ModelT]) -> ModelT:
        """The structured output as ``model``; ``TypeError`` if there is none of that type."""
        value = self.value
        if not isinstance(value, model):
            raise TypeError(f"the result holds {type(value).__name__}, not {model.__name__}")
        return value


@dataclass(frozen=True, slots=True)
class Retry:
    """The attempt failed with a retryable error; discard its output. ``attempt`` starts next."""

    deployment: str
    attempt: int
    delay_s: float
    reason: RetryReason
    type: Literal["retry"] = field(default="retry", init=False)


@dataclass(frozen=True, slots=True)
class Repair:
    """The reply failed validation; discard it. A repair turn follows on the same model."""

    deployment: str
    problems: int
    type: Literal["repair"] = field(default="repair", init=False)


@dataclass(frozen=True, slots=True)
class Fallback:
    """The model did not serve; discard its output. The next model of the chain starts."""

    from_deployment: str
    to_deployment: str
    reason: FallbackReason
    type: Literal["fallback"] = field(default="fallback", init=False)


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """The outcome of one routed embeddings request (:meth:`AIRouter.embed`).

    ``usage`` and ``cost_usd`` cover every call made for it, failed attempts that reported
    usage included.
    """

    embeddings: Embeddings
    served_by: ServedBy
    usage: Usage
    cost_usd: Decimal

    @property
    def vectors(self) -> tuple[tuple[float, ...], ...]:
        return self.embeddings.vectors


@dataclass(frozen=True, slots=True)
class Finished:
    """The last event of every routed stream."""

    result: RouterResult
    type: Literal["finished"] = field(default="finished", init=False)


type RouterEvent = (
    TextDelta | ThinkingSummary | ToolCall | Refusal | Retry | Repair | Fallback | Finished
)

DISCARD_EVENTS: Final = (Refusal, Retry, Repair, Fallback)
"""Events after which everything streamed since the previous one (or the start) is void.

A :class:`~blogwriter.ai.types.Refusal` is one: a refused attempt's partial output is discarded
and never checkpointed, whether a :class:`Fallback` follows or the refused result is final.
"""


# --------------------------------------------------------------------------------------------
# Internal markers (never leave the router)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Completed:
    completion: Completion


@dataclass(frozen=True, slots=True)
class _GaveUp:
    reason: RetryReason
    error: ProviderError


@dataclass(frozen=True, slots=True)
class _Outcome:
    completion: Completion | None
    value: BaseModel | None = None
    repaired: bool = False
    failure: FallbackReason | None = None
    error: ProviderError | None = None
    problems: tuple[str, ...] = ()


class _Run:
    """Mutable state of one routed request."""

    def __init__(self, task: Task, request: Request, origin: Origin, job_id: str | None) -> None:
        self.task = task
        self.request = request
        self.origin: Origin = origin
        self.job_id = job_id
        self.usage = Usage()
        self.cost = Decimal(0)
        self.refusals: list[Refusal] = []


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


class AIRouter:
    """Routes tasks to models with policy, limits, retries, fallback, ledger and brake.

    ``providers`` must cover every provider the routing table uses, and ``prices`` every model.
    ``brake=None`` disables the brake (tests and local runs only; :meth:`from_settings` always
    sets one). ``jitter`` returns a number in ``[0, 1]`` for the retry backoff.
    """

    def __init__(
        self,
        routing: RoutingTable,
        providers: Mapping[ProviderName, Provider],
        *,
        prices: PriceTable,
        ledger: LedgerSink,
        brake: BudgetBrake | None = None,
        policy: SensitivityPolicy | None = None,
        retry: RetryPolicy | None = None,
        clock: Clock | None = None,
        jitter: Callable[[], float] = random.random,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        for name, provider in providers.items():
            if provider.name != name:
                raise ValueError(f"provider {provider.name!r} is registered as {name!r}")
        for task, route in routing.items():
            for spec in route.chain:
                if spec.provider not in providers:
                    raise ValueError(f"{task.value}: no {spec.provider} provider is configured")
        unpriced = sorted(family for family in routing.limits if family not in prices)
        if unpriced:
            raise ValueError(f"no price configured for model(s) {', '.join(unpriced)}")
        self._routing = routing
        self._providers: Mapping[ProviderName, Provider] = dict(providers)
        self._prices = prices
        self._ledger = ledger
        self._brake = brake
        self._policy = policy if policy is not None else SensitivityPolicy()
        self._retry = retry if retry is not None else RetryPolicy()
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._jitter = jitter
        self._now = now
        self._limiters = {
            family: ModelLimiter(limits, clock=self._clock)
            for family, limits in routing.limits.items()
        }

    @classmethod
    def from_settings(
        cls,
        settings: AISettings,
        providers: Mapping[ProviderName, Provider],
        *,
        ledger: LedgerSink,
        clock: Clock | None = None,
        jitter: Callable[[], float] = random.random,
        now: Callable[[], datetime] = _utc_now,
    ) -> AIRouter:
        """A router with the settings' routes, prices, limits, retries, policy and brake."""
        brake = BudgetBrake(
            ledger,
            daily_cap_usd=settings.brake_daily_cap_usd,
            timezone=settings.brake_zone(),
            multiplier=settings.brake_multiplier,
            window_days=settings.brake_window_days,
            now=now,
        )
        return cls(
            settings.routing(),
            providers,
            prices=settings.price_table(),
            ledger=ledger,
            brake=brake,
            policy=settings.sensitivity_policy(),
            retry=settings.retry_policy(),
            clock=clock,
            jitter=jitter,
            now=now,
        )

    @property
    def routing(self) -> RoutingTable:
        return self._routing

    @property
    def prices(self) -> PriceTable:
        """The price table every call is costed with (read-only; for the settings pages)."""
        return self._prices

    @property
    def brake(self) -> BudgetBrake | None:
        """The spend brake (``None`` when disabled); its ``status()`` feeds the System page."""
        return self._brake

    def limiter(self, model: str) -> ModelLimiter:
        """The limiter of a model family (shared by every task that uses it)."""
        return self._limiters[model]

    async def aclose(self) -> None:
        """Close every provider client."""
        for provider in self._providers.values():
            await provider.aclose()

    # ----------------------------------------------------------------------------------------
    # Entry points
    # ----------------------------------------------------------------------------------------

    async def run(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> RouterResult:
        """Serve ``request`` for ``task`` and return the final result (see the module docstring).

        ``origin`` says who started the work: ``"owner"`` calls are never braked.
        """
        async with aclosing(self.stream(task, request, origin=origin, job_id=job_id)) as events:
            async for event in events:
                if isinstance(event, Finished):
                    return event.result
        raise RuntimeError("the router stream ended without a result")  # pragma: no cover

    async def stream(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> AsyncGenerator[RouterEvent]:
        """Serve ``request`` for ``task`` as :data:`RouterEvent` items ending in :class:`Finished`.

        Raises before any call on a policy violation, a missing route, an unsupported request,
        a schema that cannot compile or an engaged brake (see the module docstring).
        """
        route = self._admit(task, request)
        schemas = self._compile(route, request)
        if self._brake is not None:
            await self._brake.check(origin)
        run = _Run(task, request, origin, job_id)
        chain = route.chain
        reason: FallbackReason | None = None
        for index, spec in enumerate(chain):
            outcome: _Outcome | None = None
            # aclosing: an abandoned stream must release the model slot now, not at GC.
            async with aclosing(self._serve(run, spec, schemas.get(spec.provider))) as items:
                async for item in items:
                    if isinstance(item, _Outcome):
                        outcome = item
                    else:
                        yield item
            if outcome is None:  # pragma: no cover - _serve always ends with an outcome
                raise RuntimeError("a model call ended without an outcome")
            if outcome.failure is not None and index + 1 < len(chain):
                reason = outcome.failure
                following = chain[index + 1]
                _logger.warning(
                    "ai_fallback",
                    extra={
                        "task": task,
                        "from_deployment": spec.deployment,
                        "to_deployment": following.deployment,
                        "fallback_reason": reason,
                        "job_id": job_id,
                    },
                )
                yield Fallback(spec.deployment, following.deployment, reason)
                continue
            if outcome.error is not None:
                raise outcome.error
            if outcome.completion is None or outcome.failure in (
                "invalid_output",
                "incomplete_output",
            ):
                kind = "invalid" if outcome.failure == "invalid_output" else "incomplete"
                raise StructuredOutputError(kind, outcome.problems)
            served_by = ServedBy(
                task=task,
                model=spec.family,
                deployment=spec.deployment,
                provider=spec.provider,
                effort=spec.effort,
                fallback_reason=reason if index > 0 else None,
                review_only=index > 0 and task in REVIEW_ONLY_TASKS,
                repaired=outcome.repaired,
            )
            result = RouterResult(
                completion=outcome.completion,
                served_by=served_by,
                usage=run.usage,
                cost_usd=run.cost,
                value=outcome.value,
                refusals=tuple(run.refusals),
            )
            yield Finished(result)
            return

    async def embed(
        self,
        texts: Sequence[str],
        *,
        origin: Origin,
        job_id: str | None = None,
        dimensions: int | None = None,
        sensitivity: Sensitivity = Sensitivity.ANONYMIZED,
    ) -> EmbeddingResult:
        """Embed ``texts`` (``Task.EMBED``): one vector per text, in order.

        Raises before any call on a missing route, a policy violation (``sensitivity`` against
        the route), invalid input (``ValueError``: 1-:data:`MAX_EMBED_INPUTS` non-blank texts of
        at most :data:`MAX_EMBED_CHARS` characters), a provider without embeddings
        (``UnsupportedRequestError``) or an engaged brake for background work. Retries 429/5xx
        like chat calls; the primary only (see the module docstring).
        """
        task = Task.EMBED
        route = self._routing.route(task)
        inputs = _embedding_inputs(texts)
        if dimensions is not None and dimensions < 1:
            raise ValueError("dimensions must be at least 1")
        request = Request(system="", messages=(Message.user(*inputs),), sensitivity=sensitivity)
        self._policy.check(route.policy, request, route.chain)
        spec = route.primary
        provider = self._providers[spec.provider]
        if not isinstance(provider, EmbeddingProvider):
            raise UnsupportedRequestError(f"the {spec.provider} provider serves no embeddings")
        if self._brake is not None:
            await self._brake.check(origin)
        run = _Run(task, request, origin, job_id)
        embeddings = await self._embed_call(run, spec, provider, inputs, dimensions)
        served_by = ServedBy(
            task=task,
            model=spec.family,
            deployment=spec.deployment,
            provider=spec.provider,
            effort=spec.effort,
        )
        return EmbeddingResult(embeddings, served_by, run.usage, run.cost)

    # ----------------------------------------------------------------------------------------
    # Steps
    # ----------------------------------------------------------------------------------------

    def _admit(self, task: Task, request: Request) -> Route:
        """Every check that needs no I/O; raises before anything is sent."""
        route = self._routing.route(task)
        self._policy.check(route.policy, request, route.chain)
        if task in _NOT_CHAT:
            raise UnsupportedRequestError(f"{task.value} is not served by run() or stream()")
        if request.background:
            raise UnsupportedRequestError("background runs are not served by run() or stream()")
        return route

    def _compile(
        self, route: Route, request: Request
    ) -> dict[ProviderName, CompiledSchema[BaseModel]]:
        """Compile the output schema and the function tools' parameters for every provider of
        the chain, before any call (so a model one provider cannot express fails now, not when
        the fallback is reached after the primary has already been paid for)."""
        for spec in route.chain:
            for tool in request.tools:
                if isinstance(tool, FunctionTool):
                    compile_schema(spec.provider, tool.parameters)
        schema = request.output_schema
        if schema is None:
            return {}
        return {spec.provider: compile_schema(spec.provider, schema) for spec in route.chain}

    async def _serve(
        self, run: _Run, spec: ModelSpec, compiled: CompiledSchema[BaseModel] | None
    ) -> AsyncGenerator[RouterEvent | _Outcome]:
        """One model: the call, structured-output validation and at most one repair turn."""
        request = run.request
        repaired = False
        while True:
            completion: Completion | None = None
            gave_up: _GaveUp | None = None
            async with aclosing(self._call(run, spec, request)) as items:
                async for item in items:
                    if isinstance(item, _GaveUp):
                        gave_up = item
                    elif isinstance(item, _Completed):
                        completion = item.completion
                    else:
                        yield item
            if gave_up is not None:
                yield _Outcome(None, failure=gave_up.reason, error=gave_up.error)
                return
            if completion is None:  # pragma: no cover - _call always ends with a marker
                raise RuntimeError("a model call ended without a completion")
            if completion.refusal is not None:
                run.refusals.append(completion.refusal)
                yield _Outcome(completion, failure="refusal")
                return
            finish = completion.finish_reason
            if compiled is None or (finish in _CALLER_TURN and not repaired):
                yield _Outcome(completion)
                return
            if finish != "stop":
                problem = f"finish_reason={finish}"
                yield _Outcome(completion, failure="incomplete_output", problems=(problem,))
                return
            invalid: OutputValidationError | None = None
            try:
                value = compiled.validate_json(completion.text)
            except OutputValidationError as error:
                invalid = error
            else:
                yield _Outcome(completion, value=value, repaired=repaired)
                return
            if repaired:
                yield _Outcome(completion, failure="invalid_output", problems=invalid.problems)
                return
            yield Repair(spec.deployment, len(invalid.problems))
            request = repair_request(run.request, completion, invalid)
            repaired = True

    async def _call(
        self, run: _Run, spec: ModelSpec, request: Request
    ) -> AsyncGenerator[RouterEvent | _Completed | _GaveUp]:
        """One model call through the limiter, retried on 429/5xx; ends with a marker."""
        limiter = self._limiters[spec.family]
        provider = self._providers[spec.provider]
        estimate = estimate_input_tokens(request)
        attempt = 1
        while True:
            started = self._clock.monotonic()
            completion: Completion | None = None
            failure: ProviderError | None = None
            try:
                async with limiter.admit(
                    estimate, max_cooldown=self._retry.max_retry_after
                ) as lease:
                    events = provider.stream(spec, request)
                    try:
                        async for event in events:
                            if isinstance(event, Done):
                                completion = event.completion
                            elif not isinstance(event, UsageEvent):
                                yield event
                    except ProviderError as error:
                        failure = error
                    finally:
                        if isinstance(events, AsyncGenerator):
                            await events.aclose()
                    if completion is None and failure is None:
                        failure = ProviderStreamError(
                            f"{spec.provider} stream ended without a final event",
                            provider=spec.provider,
                            retryable=True,
                        )
                    if completion is not None:
                        lease.settle(completion.usage)
                    elif failure is not None:
                        lease.settle(failure.usage)
            except ModelCoolingDown as cooling:
                # An earlier call got a Retry-After longer than the retry policy waits. Waiting
                # it out here would hold this call (and, for Opus, the only slot) for as long as
                # the server asked, possibly hours: give up on this model now, exactly as that
                # call did, so the fallback serves (or the caller sees the rate limit).
                _logger.info(
                    "ai_model_cooling_down",
                    extra={
                        "task": run.task,
                        "deployment": spec.deployment,
                        "delay_ms": round(cooling.remaining * 1000),
                        "job_id": run.job_id,
                    },
                )
                skipped = RateLimitedError(
                    f"{spec.provider} {spec.deployment} is cooling down after a Retry-After",
                    provider=spec.provider,
                    retryable=True,
                    retry_after=cooling.remaining,
                )
                yield _GaveUp("rate_limited", skipped)
                return
            duration_ms = (self._clock.monotonic() - started) * 1000
            if completion is not None:
                outcome: CallOutcome = "refused" if completion.refusal is not None else "completed"
                await self._record(
                    run, spec, completion.usage, outcome, completion.response_id, duration_ms
                )
                yield _Completed(completion)
                return
            if failure is None:  # pragma: no cover - set above
                raise RuntimeError("a model call ended without a result")
            if failure.usage is not None:
                await self._record(run, spec, failure.usage, "failed", None, duration_ms)
            reason = retry_reason(failure)
            if reason is None:
                raise failure
            if failure.retry_after:
                limiter.cool_down(failure.retry_after)
            delay = self._retry.delay(
                attempt, jitter=self._jitter(), retry_after=failure.retry_after
            )
            if delay is None:
                yield _GaveUp(reason, failure)
                return
            attempt += 1
            _logger.info(
                "ai_retry",
                extra={
                    "task": run.task,
                    "deployment": spec.deployment,
                    "attempt": attempt,
                    "delay_ms": round(delay * 1000),
                    "retry_reason": reason,
                    "status": failure.status_code,
                    "job_id": run.job_id,
                },
            )
            yield Retry(spec.deployment, attempt, delay, reason)
            await self._clock.sleep(delay)

    async def _embed_call(
        self,
        run: _Run,
        spec: ModelSpec,
        provider: EmbeddingProvider,
        inputs: Sequence[str],
        dimensions: int | None,
    ) -> Embeddings:
        """One embeddings call through the limiter, retried on 429/5xx like :meth:`_call`."""
        limiter = self._limiters[spec.family]
        estimate = estimate_input_tokens(run.request)
        attempt = 1
        while True:
            started = self._clock.monotonic()
            result: Embeddings | None = None
            failure: ProviderError | None = None
            try:
                async with limiter.admit(
                    estimate, max_cooldown=self._retry.max_retry_after
                ) as lease:
                    try:
                        result = await provider.embed(spec, inputs, dimensions=dimensions)
                    except ProviderError as error:
                        failure = error
                    if result is not None:
                        lease.settle(result.usage)
                    elif failure is not None:
                        lease.settle(failure.usage)
            except ModelCoolingDown as cooling:
                _logger.info(
                    "ai_model_cooling_down",
                    extra={
                        "task": run.task,
                        "deployment": spec.deployment,
                        "delay_ms": round(cooling.remaining * 1000),
                        "job_id": run.job_id,
                    },
                )
                raise RateLimitedError(
                    f"{spec.provider} {spec.deployment} is cooling down after a Retry-After",
                    provider=spec.provider,
                    retryable=True,
                    retry_after=cooling.remaining,
                ) from None
            duration_ms = (self._clock.monotonic() - started) * 1000
            if result is not None:
                await self._record(run, spec, result.usage, "completed", None, duration_ms)
                return result
            if failure is None:  # pragma: no cover - set above
                raise RuntimeError("an embeddings call ended without a result")
            if failure.usage is not None:
                await self._record(run, spec, failure.usage, "failed", None, duration_ms)
            reason = retry_reason(failure)
            if reason is None:
                raise failure
            if failure.retry_after:
                limiter.cool_down(failure.retry_after)
            delay = self._retry.delay(
                attempt, jitter=self._jitter(), retry_after=failure.retry_after
            )
            if delay is None:
                raise failure
            attempt += 1
            _logger.info(
                "ai_retry",
                extra={
                    "task": run.task,
                    "deployment": spec.deployment,
                    "attempt": attempt,
                    "delay_ms": round(delay * 1000),
                    "retry_reason": reason,
                    "status": failure.status_code,
                    "job_id": run.job_id,
                },
            )
            await self._clock.sleep(delay)

    async def _record(
        self,
        run: _Run,
        spec: ModelSpec,
        usage: Usage,
        outcome: CallOutcome,
        response_id: str | None,
        duration_ms: float,
    ) -> None:
        """Price one call, add it to the request's totals and write it to the ledger."""
        cost = self._prices.cost(spec.family, usage)
        run.usage = run.usage + usage
        run.cost += cost
        record = UsageRecord(
            task=run.task,
            model=spec.family,
            deployment=spec.deployment,
            provider=spec.provider,
            usage=usage,
            cost_usd=cost,
            at=self._now(),
            origin=run.origin,
            outcome=outcome,
            job_id=run.job_id,
            response_id=response_id,
        )
        _logger.info(
            "ai_model_call",
            extra={
                "task": run.task,
                "model": spec.family,
                "deployment": spec.deployment,
                "provider": spec.provider,
                "origin": run.origin,
                "outcome": outcome,
                "job_id": run.job_id,
                "response_id": response_id,
                "input_tokens": usage.input,
                "cached_input_tokens": usage.cached_input,
                "output_tokens": usage.output,
                "web_searches": usage.web_search_requests,
                "cost_usd": float(cost),
                "duration_ms": round(duration_ms, 1),
            },
        )
        try:
            await self._ledger.record(record)
        except Exception as exc:
            # The call happened and was paid for: keep its result, but make the gap visible,
            # and keep the brake counting (a sink that fails every write must not blind it).
            _logger.error(
                "ai_ledger_write_failed",
                extra={"task": run.task, "job_id": run.job_id, **describe_exception(exc)},
            )
            if self._brake is not None:
                self._brake.note_unrecorded(record)


def _embedding_inputs(texts: Sequence[str]) -> tuple[str, ...]:
    """The texts of one embeddings call, checked (``ValueError`` names no content)."""
    if isinstance(texts, str):
        raise TypeError("embed takes a sequence of texts, not one string")
    inputs = tuple(texts)
    if not 1 <= len(inputs) <= MAX_EMBED_INPUTS:
        raise ValueError(f"an embeddings call takes 1 to {MAX_EMBED_INPUTS} texts")
    for index, text in enumerate(inputs):
        if not isinstance(text, str) or not text.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(f"text {index} is blank")
        if len(text) > MAX_EMBED_CHARS:
            raise ValueError(f"text {index} is longer than {MAX_EMBED_CHARS} characters")
    return inputs


def create_providers(
    settings: AISettings, token_provider: TokenProvider, *, timeout: float | None = None
) -> dict[ProviderName, Provider]:
    """Providers for the configured endpoints, with SDK retries off (the router retries).

    ``token_provider`` is an Entra token provider for ``https://ai.azure.com/.default`` (see
    :func:`blogwriter.ai.providers.entra_token_provider`). Unset endpoints are skipped.
    """
    providers: dict[ProviderName, Provider] = {}
    if settings.openai_base_url is not None:
        client = create_openai_client(
            base_url=settings.openai_base_url,
            token_provider=token_provider,
            max_retries=0,
            timeout=timeout,
        )
        providers["openai"] = OpenAIResponsesProvider(client)
    if settings.anthropic_resource is not None:
        claude = create_anthropic_client(
            resource=settings.anthropic_resource,
            token_provider=token_provider,
            max_retries=0,
            timeout=timeout,
        )
        providers["anthropic"] = AnthropicFoundryProvider(claude)
    return providers
