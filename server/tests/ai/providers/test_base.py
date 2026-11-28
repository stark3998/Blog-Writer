"""Shared provider rules: error classification, draining streams, SDK log levels.

Plan: "AI layer" > Calls (no forced tool_choice, limiters see retryable errors) and "Security
model" > Logging (never log prompts or completions).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel

from blogwriter.ai.providers.base import (
    BackgroundPoll,
    ProviderAuthError,
    ProviderRequestError,
    ProviderStreamError,
    ProviderUnavailableError,
    RateLimitedError,
    UnsupportedRequestError,
    cap_sdk_logging,
    check_spec,
    collect,
    count_attr,
    error_for_status,
)
from blogwriter.ai.types import (
    Completion,
    Done,
    FunctionTool,
    Message,
    ModelSpec,
    Request,
    Sensitivity,
    StreamEvent,
    TextDelta,
    Usage,
    UsageEvent,
)


@pytest.mark.parametrize(
    ("status", "code", "kind", "retryable"),
    [
        (429, "rate_limit_exceeded", RateLimitedError, True),
        (429, None, RateLimitedError, True),
        (401, None, ProviderAuthError, False),
        (403, "PermissionDenied", ProviderAuthError, False),
        (408, None, ProviderUnavailableError, True),
        (409, None, ProviderUnavailableError, True),
        (500, "server_error", ProviderUnavailableError, True),
        (503, None, ProviderUnavailableError, True),
        (529, "overloaded_error", ProviderUnavailableError, True),
        (400, "invalid_request_error", ProviderRequestError, False),
        (404, "DeploymentNotFound", ProviderRequestError, False),
        (413, None, ProviderRequestError, False),
        (422, None, ProviderRequestError, False),
    ],
)
def test_status_codes_map_to_error_kinds(
    status: int, code: str | None, kind: type[Exception], retryable: bool
) -> None:
    error = error_for_status("openai", status, code=code, request_id="req_1", headers={})
    assert type(error) is kind
    assert error.retryable is retryable
    assert error.status_code == status
    assert error.code == code
    assert error.request_id == "req_1"
    assert error.provider == "openai"
    assert f"HTTP {status}" in str(error)


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("overloaded_error", True),
        ("api_error", True),
        ("rate_limit_error", True),
        (None, True),
        ("invalid_request_error", False),
    ],
)
def test_errors_inside_a_stream_are_stream_errors(code: str | None, retryable: bool) -> None:
    error = error_for_status(
        "anthropic", 200, code=code, request_id=None, headers={}, during_stream=True
    )
    assert type(error) is ProviderStreamError
    assert error.retryable is retryable


def test_retry_after_headers_are_read() -> None:
    def after(headers: dict[str, str]) -> float | None:
        return error_for_status(
            "openai", 429, code=None, request_id=None, headers=headers
        ).retry_after

    assert after({"retry-after-ms": "1500"}) == 1.5
    assert after({"retry-after": "7"}) == 7.0
    assert after({"retry-after-ms": "250", "retry-after": "9"}) == 0.25
    assert after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert after({"retry-after": "-1"}) is None
    assert after({"retry-after": "inf"}) is None
    assert after({}) is None


def test_x_should_retry_overrides_the_status() -> None:
    no = error_for_status(
        "anthropic", 429, code=None, request_id=None, headers={"x-should-retry": "false"}
    )
    assert isinstance(no, RateLimitedError)
    assert no.retryable is False
    yes = error_for_status(
        "anthropic", 400, code=None, request_id=None, headers={"x-should-retry": "true"}
    )
    assert yes.retryable is True


async def _events(*events: StreamEvent) -> AsyncIterator[StreamEvent]:
    for event in events:
        yield event


async def test_collect_returns_the_final_completion() -> None:
    final = Completion(text="hi", finish_reason="stop", usage=Usage(), model="m", response_id="r")
    events = _events(TextDelta("h"), TextDelta("i"), UsageEvent(Usage()), Done("stop", final))
    assert await collect(events, provider="openai") is final


async def test_collect_without_done_is_a_stream_error() -> None:
    with pytest.raises(ProviderStreamError, match="without a final event") as caught:
        await collect(_events(TextDelta("partial")), provider="anthropic")
    assert caught.value.retryable is True


def test_check_spec_rejects_mismatched_providers_and_tool_less_models() -> None:
    class Args(BaseModel):
        url: str

    spec = ModelSpec("openai", "gpt-6-luna", "gpt-6-luna", "low", 100, supports_tools=False)
    request = Request(system="", messages=(Message.user("x"),), sensitivity=Sensitivity.PUBLIC)
    check_spec("openai", spec, request)
    with pytest.raises(UnsupportedRequestError, match="sent to the anthropic provider"):
        check_spec("anthropic", spec, request)
    with_tools = Request(
        system="",
        messages=(Message.user("x"),),
        sensitivity=Sensitivity.PUBLIC,
        tools=(FunctionTool("fetch_page", "Fetch.", Args),),
    )
    with pytest.raises(UnsupportedRequestError, match="configured without tools"):
        check_spec("openai", spec, with_tools)


def test_background_poll_reports_terminal_states() -> None:
    assert not BackgroundPoll("resp_1", "queued").done
    assert not BackgroundPoll("resp_1", "in_progress").done
    for status in ("completed", "incomplete", "failed", "cancelled"):
        assert BackgroundPoll("resp_1", status).done  # pyright: ignore[reportArgumentType]


def test_count_attr_is_lenient_about_missing_fields() -> None:
    class Leaf:
        tokens = 5
        flag = True
        negative = -3

    class Root:
        leaf = Leaf()

    assert count_attr(Root(), "leaf", "tokens") == 5
    assert count_attr(Root(), "leaf", "missing") is None
    assert count_attr(Root(), "missing", "tokens") is None
    assert count_attr(Root(), "leaf", "flag") is None
    assert count_attr(Root(), "leaf", "negative") is None
    assert count_attr(None, "anything") is None


def test_sdk_loggers_never_run_at_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("openai", "anthropic"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.DEBUG)
    cap_sdk_logging()
    for name in ("openai", "anthropic"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.INFO
    # A stricter level chosen elsewhere is left alone.
    monkeypatch.setattr(logging.getLogger("openai"), "level", logging.ERROR)
    cap_sdk_logging()
    assert logging.getLogger("openai").level == logging.ERROR
