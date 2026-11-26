"""What every provider implements, and the errors they raise.

A provider turns a :class:`~blogwriter.ai.types.Request` into a normalized
:data:`~blogwriter.ai.types.StreamEvent` stream (``stream``) or a single
:class:`~blogwriter.ai.types.Completion` (``complete``). Providers persist nothing, log nothing
and never set ``temperature`` or a forced ``tool_choice``.

Errors are mapped to :class:`ProviderError` subclasses whose messages carry status codes, error
codes and request ids only, never prompt or completion text. Requests a provider cannot serve
(for example ``web_search`` on Anthropic, or ``private`` data on Claude) fail with
:class:`UnsupportedRequestError` before anything is sent.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from blogwriter.ai.types import (
    Completion,
    Done,
    ModelSpec,
    ProviderName,
    Request,
    StreamEvent,
    Usage,
)

__all__ = [
    "BackgroundPoll",
    "BackgroundStatus",
    "Provider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderStreamError",
    "ProviderUnavailableError",
    "RateLimitedError",
    "TokenProvider",
    "UnsupportedRequestError",
    "cap_sdk_logging",
    "check_spec",
    "collect",
    "count_attr",
    "error_for_status",
]

type TokenProvider = Callable[[], Awaitable[str]]
"""An async callable returning a fresh Entra bearer token (see :mod:`.auth`)."""

type BackgroundStatus = Literal[
    "queued", "in_progress", "completed", "incomplete", "failed", "cancelled"
]

_TERMINAL: Final = frozenset({"completed", "incomplete", "failed", "cancelled"})


class UnsupportedRequestError(ValueError):
    """The provider or model spec cannot serve this request; nothing was sent."""


class ProviderError(Exception):
    """A provider call failed.

    ``retryable`` says whether the same call may succeed later (rate limits, overload, network).
    ``usage`` is set when the provider reported consumption before failing, so the ledger can
    still record it. The message names codes and ids only.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: ProviderName,
        retryable: bool,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        usage: Usage | None = None,
    ) -> None:
        super().__init__(message)
        self.provider: ProviderName = provider
        self.retryable = retryable
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after
        self.usage = usage


class RateLimitedError(ProviderError):
    """HTTP 429 or a rate-limit failure. Usually retryable; ``retry_after`` is in seconds."""


class ProviderUnavailableError(ProviderError):
    """Overload, a 5xx, a timeout or a connection failure before the stream started."""


class ProviderAuthError(ProviderError):
    """HTTP 401 or 403: the identity, role assignment or token is wrong. Not retryable."""


class ProviderRequestError(ProviderError):
    """Any other 4xx: the request itself was rejected. Not retryable."""


class ProviderStreamError(ProviderError):
    """The stream failed after it started. Discard any partial output."""


@dataclass(frozen=True, slots=True)
class BackgroundPoll:
    """The state of a provider-side background run.

    ``completion`` is set once the run is terminal (``completed``, ``incomplete``, ``cancelled``,
    or ``failed`` with a policy refusal); ``error`` is set when the run itself failed. Delete the
    run afterwards in every case.
    """

    response_id: str
    status: BackgroundStatus
    completion: Completion | None = None
    error: ProviderError | None = None

    @property
    def done(self) -> bool:
        return self.status in _TERMINAL


class Provider(Protocol):
    """A model provider. Implementations: OpenAI Responses and Anthropic on Foundry."""

    @property
    def name(self) -> ProviderName: ...

    def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        """Stream one call. The last event is :class:`~blogwriter.ai.types.Done`."""
        ...

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        """Run one call to completion."""
        ...

    async def aclose(self) -> None:
        """Close the underlying client."""
        ...


async def collect(events: AsyncIterator[StreamEvent], *, provider: ProviderName) -> Completion:
    """Drain a stream and return its final completion."""
    async for event in events:
        if isinstance(event, Done):
            return event.completion
    raise ProviderStreamError(
        f"{provider} stream ended without a final event", provider=provider, retryable=True
    )


def check_spec(provider: ProviderName, spec: ModelSpec, request: Request) -> None:
    """Shared pre-flight checks; raises :class:`UnsupportedRequestError`."""
    if spec.provider != provider:
        raise UnsupportedRequestError(f"spec for {spec.provider} sent to the {provider} provider")
    if request.tools and not spec.supports_tools:
        raise UnsupportedRequestError(f"deployment {spec.deployment} is configured without tools")


def count_attr(value: object, *path: str) -> int | None:
    """Follow attributes leniently and return a non-negative int, or ``None`` if absent.

    The SDKs build response models without validation, so a field the service omits (Azure lags
    the public API on newer usage fields) is simply missing. Usage must never crash a call.
    """
    for name in path:
        value = getattr(value, name, None)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _parse_retry_after(headers: Mapping[str, str]) -> float | None:
    for header, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(header)
        if value is None:
            continue
        try:
            seconds = float(value) / divisor
        except ValueError:
            continue
        if seconds >= 0 and seconds != float("inf"):
            return seconds
    return None


_RATE_LIMIT_CODES: Final = frozenset({"rate_limit_exceeded", "rate_limit_error", "429"})
_OVERLOAD_CODES: Final = frozenset(
    {"overloaded_error", "api_error", "server_error", "internal_server_error", "timeout_error"}
)


def error_for_status(
    provider: ProviderName,
    status_code: int,
    *,
    code: str | None,
    request_id: str | None,
    headers: Mapping[str, str],
    during_stream: bool = False,
) -> ProviderError:
    """Map an HTTP status (or an error event inside a 200 stream) to a :class:`ProviderError`."""
    retry_after = _parse_retry_after(headers)
    detail = f"HTTP {status_code}" + (f", code={code}" if code else "")
    detail += f", request_id={request_id}" if request_id else ""

    def make(kind: type[ProviderError], summary: str, retryable: bool) -> ProviderError:
        return kind(
            f"{provider} {summary} ({detail})",
            provider=provider,
            retryable=retryable,
            status_code=status_code,
            code=code,
            request_id=request_id,
            retry_after=retry_after,
        )

    if during_stream:
        retryable = code is None or code in _RATE_LIMIT_CODES or code in _OVERLOAD_CODES
        error = make(ProviderStreamError, "stream failed", retryable)
    elif status_code == 429 or code in _RATE_LIMIT_CODES:
        error = make(RateLimitedError, "rate limited", True)
    elif status_code in (401, 403):
        error = make(ProviderAuthError, "rejected the credentials", False)
    elif status_code in (408, 409) or status_code >= 500:
        error = make(ProviderUnavailableError, "unavailable", True)
    else:
        error = make(ProviderRequestError, "rejected the request", False)
    should_retry = headers.get("x-should-retry")
    if should_retry in ("true", "false"):
        error.retryable = should_retry == "true"
    return error


_SDK_LOGGERS: Final = ("openai", "anthropic")


def cap_sdk_logging() -> None:
    """Keep the SDK loggers above DEBUG.

    At DEBUG the Anthropic SDK logs full request options (prompts included). The app's rule is
    that prompts and completions never reach logs, so building a client caps these loggers at
    INFO even if the root logger runs at DEBUG.
    """
    for name in _SDK_LOGGERS:
        logger = logging.getLogger(name)
        if logger.getEffectiveLevel() < logging.INFO:
            logger.setLevel(logging.INFO)
