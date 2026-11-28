"""Rules of the provider-neutral AI types.

Plan: "AI layer" (tasks, streaming events, served_by inputs) and "Security model" (sensitivity
classes: only public data reaches tools; private data is never stored).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel

from blogwriter.ai.types import (
    ANTHROPIC_EFFORTS,
    DEFAULT_MAX_TOOL_CALLS,
    MAX_CACHE_BREAKPOINTS,
    OPENAI_EFFORTS,
    Completion,
    Done,
    FunctionTool,
    Message,
    ModelSpec,
    NativeItems,
    Refusal,
    Request,
    Sensitivity,
    SensitivityError,
    Task,
    TextDelta,
    TextPart,
    ThinkingSummary,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
    Usage,
    UsageEvent,
    WebSearchTool,
)


class Args(BaseModel):
    url: str


def user_request(**overrides: object) -> Request:
    fields: dict[str, object] = {
        "system": "You are terse.",
        "messages": (Message.user("hello"),),
        "sensitivity": Sensitivity.PUBLIC,
    }
    fields.update(overrides)
    return Request(**fields)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------------------------
# Task
# --------------------------------------------------------------------------------------------


def test_task_covers_every_unit_of_work_with_snake_case_values() -> None:
    assert [task.name for task in Task] == [
        "DRAFT",
        "REVISE",
        "OUTLINE",
        "PLAN",
        "INTERVIEW",
        "SYNTHESIS",
        "CRITIQUE",
        "FACT_CHECK",
        "ANONYMIZE",
        "RESEARCH",
        "QUICK_SEARCH",
        "TRIAGE",
        "ENGAGEMENT",
        "EMBED",
        "IMAGE",
    ]
    for task in Task:
        assert task.value == task.name.lower()
        assert Task(task.value) is task
        assert isinstance(task, str)


# --------------------------------------------------------------------------------------------
# Sensitivity
# --------------------------------------------------------------------------------------------


def test_sensitivity_orders_private_over_anonymized_over_public() -> None:
    assert Sensitivity.PRIVATE > Sensitivity.ANONYMIZED > Sensitivity.PUBLIC
    assert Sensitivity.PUBLIC < Sensitivity.ANONYMIZED < Sensitivity.PRIVATE
    assert Sensitivity.PUBLIC <= Sensitivity.PUBLIC <= Sensitivity.ANONYMIZED
    assert Sensitivity.PRIVATE >= Sensitivity.PRIVATE >= Sensitivity.ANONYMIZED
    assert sorted(Sensitivity) == [Sensitivity.PUBLIC, Sensitivity.ANONYMIZED, Sensitivity.PRIVATE]
    assert [level.rank for level in Sensitivity] == [0, 1, 2]


def test_sensitivity_values_are_the_plan_class_names() -> None:
    assert {level.value for level in Sensitivity} == {"private", "anonymized", "public"}
    assert Sensitivity("anonymized") is Sensitivity.ANONYMIZED


def test_sensitivity_does_not_fall_back_to_string_order() -> None:
    # As strings, "public" > "private"; the class order must never be confused with that.
    with pytest.raises(TypeError):
        _ = Sensitivity.PRIVATE > "public"
    with pytest.raises(TypeError):
        _ = Sensitivity.PUBLIC <= "anonymized"
    with pytest.raises(TypeError):
        _ = Sensitivity.PUBLIC < "zzz"
    with pytest.raises(TypeError):
        _ = Sensitivity.PUBLIC >= "a"


def test_permits_allows_only_data_at_or_below_the_context_class() -> None:
    assert Sensitivity.PUBLIC.permits(Sensitivity.PUBLIC)
    assert not Sensitivity.PUBLIC.permits(Sensitivity.ANONYMIZED)
    assert not Sensitivity.PUBLIC.permits(Sensitivity.PRIVATE)
    assert Sensitivity.ANONYMIZED.permits(Sensitivity.PUBLIC)
    assert Sensitivity.ANONYMIZED.permits(Sensitivity.ANONYMIZED)
    assert not Sensitivity.ANONYMIZED.permits(Sensitivity.PRIVATE)
    assert all(Sensitivity.PRIVATE.permits(level) for level in Sensitivity)


def test_combined_data_takes_the_most_restrictive_class() -> None:
    assert Sensitivity.most_restrictive(Sensitivity.PUBLIC) is Sensitivity.PUBLIC
    assert (
        Sensitivity.most_restrictive(Sensitivity.PUBLIC, Sensitivity.ANONYMIZED, Sensitivity.PUBLIC)
        is Sensitivity.ANONYMIZED
    )
    assert (
        Sensitivity.most_restrictive(Sensitivity.ANONYMIZED, Sensitivity.PRIVATE)
        is Sensitivity.PRIVATE
    )


# --------------------------------------------------------------------------------------------
# ModelSpec
# --------------------------------------------------------------------------------------------


def test_model_spec_accepts_provider_efforts() -> None:
    for effort in OPENAI_EFFORTS:
        ModelSpec("openai", "gpt-6.2-sol", "gpt-6.2-sol", effort, 4000, True)  # pyright: ignore[reportArgumentType]
    for effort in ANTHROPIC_EFFORTS:
        ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", effort, 4000, False)  # pyright: ignore[reportArgumentType]
    spec = ModelSpec("openai", "gpt-6-luna", "gpt-6-luna", None, 100, False)
    assert spec.thinking_summary is True


@pytest.mark.parametrize("effort", ["none", "minimal"])
def test_anthropic_rejects_efforts_it_does_not_support(effort: str) -> None:
    with pytest.raises(ValueError, match="not supported by anthropic"):
        ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", effort, 4000, False)  # pyright: ignore[reportArgumentType]


def test_model_spec_rejects_bad_values() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        ModelSpec("bedrock", "x", "x", None, 1, False)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="non-empty"):
        ModelSpec("openai", " ", "gpt", None, 1, False)
    with pytest.raises(ValueError, match="non-empty"):
        ModelSpec("openai", "gpt", "", None, 1, False)
    with pytest.raises(ValueError, match="at least 1"):
        ModelSpec("openai", "gpt", "gpt", None, 0, False)
    with pytest.raises(ValueError, match="not supported by openai"):
        ModelSpec("openai", "gpt", "gpt", "extreme", 1, False)  # pyright: ignore[reportArgumentType]


def test_model_spec_is_frozen() -> None:
    spec = ModelSpec("openai", "gpt-6.2-sol", "gpt-6.2-sol", "high", 4000, True)
    with pytest.raises(FrozenInstanceError):
        spec.effort = "low"  # pyright: ignore[reportAttributeAccessIssue]


# --------------------------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_text_parts_need_visible_text(text: str) -> None:
    with pytest.raises(ValueError, match="non-whitespace"):
        TextPart(text)


def test_message_helpers_build_valid_turns() -> None:
    user = Message.user("a", "b")
    assert user.role == "user"
    assert user.parts == (TextPart("a"), TextPart("b"))
    assert user.text == "ab"
    assert Message.assistant("done").parts == (TextPart("done"),)
    results = Message.tool_results(
        ToolResultPart("call_1", "ok"), ToolResultPart("call_2", "x", True)
    )
    assert results.role == "user"
    assert len(results.parts) == 2
    assert results.text == ""


def test_parts_must_sit_in_the_right_turn() -> None:
    with pytest.raises(ValueError, match="assistant turns"):
        Message("user", (ToolCallPart("call_1", "fetch_page", "{}"),))
    with pytest.raises(ValueError, match="user turns"):
        Message("assistant", (ToolResultPart("call_1", "ok"),))
    with pytest.raises(ValueError, match="at least one part"):
        Message("user", ())
    with pytest.raises(ValueError, match="unknown role"):
        Message("system", (TextPart("x"),))  # pyright: ignore[reportArgumentType]


def test_native_items_are_assistant_only_and_non_empty() -> None:
    native = NativeItems("openai", ({"type": "reasoning", "id": "rs_1"},))
    assert Message("assistant", (), native).native is native
    with pytest.raises(ValueError, match="assistant turns"):
        Message("user", (TextPart("x"),), native)
    with pytest.raises(ValueError, match="must not be empty"):
        NativeItems("anthropic", ())


def test_tool_parts_need_ids() -> None:
    with pytest.raises(ValueError, match="call_id and a name"):
        ToolCallPart("", "fetch_page", "{}")
    with pytest.raises(ValueError, match="call_id and a name"):
        ToolCallPart("call_1", "", "{}")
    with pytest.raises(ValueError, match="call_id they answer"):
        ToolResultPart("", "ok")


def test_message_parts_are_stored_as_a_tuple() -> None:
    message = Message("user", [TextPart("x")])  # pyright: ignore[reportArgumentType]
    assert message.parts == (TextPart("x"),)


# --------------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------------


def test_function_tool_validation() -> None:
    tool = FunctionTool("fetch_page", "Fetch one page.", Args)
    assert tool.parameters is Args
    with pytest.raises(ValueError, match="must match"):
        FunctionTool("fetch page", "Fetch one page.", Args)
    with pytest.raises(ValueError, match="must match"):
        FunctionTool("x" * 65, "Fetch one page.", Args)
    with pytest.raises(ValueError, match="description"):
        FunctionTool("fetch_page", " ", Args)
    with pytest.raises(TypeError, match="BaseModel"):
        FunctionTool("fetch_page", "Fetch one page.", dict)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    "domain", ["example.com", "pubmed.ncbi.nlm.nih.gov", "learn.microsoft.com", "xn--bcher-kva.ch"]
)
def test_web_search_accepts_bare_domains(domain: str) -> None:
    assert WebSearchTool((domain,)).allowed_domains == (domain,)


@pytest.mark.parametrize(
    "domain",
    [
        "https://example.com",
        "example.com/path",
        "Example.com",
        "localhost",
        "*.example.com",
        "-bad.example.com",
        "bad-.example.com",
        "a..example.com",
        "example.com.",
        "example.com:443",
        "exa mple.com",
        "",
    ],
)
def test_web_search_rejects_anything_but_bare_domains(domain: str) -> None:
    with pytest.raises(ValueError, match="bare lowercase host name"):
        WebSearchTool((domain,))


def test_web_search_defaults() -> None:
    tool = WebSearchTool(["example.com"])  # pyright: ignore[reportArgumentType]
    assert tool.allowed_domains == ("example.com",)
    assert tool.search_context_size is None


# --------------------------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------------------------


def test_request_needs_messages_that_start_and_end_with_the_user() -> None:
    with pytest.raises(ValueError, match="at least one message"):
        user_request(messages=())
    with pytest.raises(ValueError, match="start and end with a user turn"):
        user_request(messages=(Message.assistant("hi"), Message.user("x")))
    with pytest.raises(ValueError, match="start and end with a user turn"):
        user_request(messages=(Message.user("x"), Message.assistant("prefill")))
    request = user_request(messages=[Message.user("a"), Message.assistant("b"), Message.user("c")])
    assert isinstance(request.messages, tuple)


def test_request_sensitivity_is_required_and_typed() -> None:
    with pytest.raises(TypeError):
        Request(system="", messages=(Message.user("x"),))  # pyright: ignore[reportCallIssue]
    with pytest.raises(TypeError, match="must be a Sensitivity"):
        user_request(sensitivity="public")


@pytest.mark.parametrize("level", [Sensitivity.ANONYMIZED, Sensitivity.PRIVATE])
def test_only_public_data_reaches_tools(level: Sensitivity) -> None:
    with pytest.raises(SensitivityError, match="tool-enabled"):
        user_request(sensitivity=level, tools=(WebSearchTool(),))
    with pytest.raises(SensitivityError, match="tool-enabled"):
        user_request(sensitivity=level, tools=(FunctionTool("fetch_page", "Fetch.", Args),))


@pytest.mark.parametrize("level", [Sensitivity.ANONYMIZED, Sensitivity.PRIVATE])
def test_only_public_data_runs_as_a_stored_background_response(level: Sensitivity) -> None:
    with pytest.raises(SensitivityError, match="background"):
        user_request(sensitivity=level, background=True)


def test_public_requests_may_use_tools_and_background() -> None:
    request = user_request(tools=(WebSearchTool(("example.com",)),), background=True)
    assert request.tool_call_cap == DEFAULT_MAX_TOOL_CALLS
    assert user_request(tools=(WebSearchTool(),), max_tool_calls=3).tool_call_cap == 3


def test_tools_must_be_unambiguous() -> None:
    fetch = FunctionTool("fetch_page", "Fetch.", Args)
    with pytest.raises(ValueError, match="unique"):
        user_request(tools=(fetch, FunctionTool("fetch_page", "Again.", Args)))
    with pytest.raises(ValueError, match="at most one web_search"):
        user_request(tools=(WebSearchTool(), WebSearchTool(("example.com",))))
    with pytest.raises(ValueError, match="at least 1"):
        user_request(tools=(fetch,), max_tool_calls=0)


def test_output_schema_must_be_a_pydantic_model() -> None:
    assert user_request(output_schema=Args).output_schema is Args
    with pytest.raises(TypeError, match="BaseModel"):
        user_request(output_schema=dict)


def test_cache_breakpoints_are_capped() -> None:
    marked = Message("user", tuple(TextPart(f"p{i}", cache_breakpoint=True) for i in range(3)))
    assert user_request(messages=(marked,))
    too_many = Message(
        "user",
        tuple(TextPart(f"p{i}", cache_breakpoint=True) for i in range(MAX_CACHE_BREAKPOINTS + 1)),
    )
    with pytest.raises(ValueError, match="cache breakpoints"):
        user_request(messages=(too_many,))


# --------------------------------------------------------------------------------------------
# Usage, completion, events
# --------------------------------------------------------------------------------------------


def test_usage_sums_and_derives_uncached_input() -> None:
    first = Usage(input=1000, cached_input=600, cache_write=100, output=50, reasoning=20)
    second = Usage(input=10, output=5, web_search_requests=2, images=1)
    total = first + second
    assert total == Usage(
        input=1010,
        cached_input=600,
        cache_write=100,
        output=55,
        reasoning=20,
        web_search_requests=2,
        images=1,
    )
    assert first.uncached_input == 300
    assert Usage(input=5, cached_input=10).uncached_input == 0


@pytest.mark.parametrize(
    "field",
    [
        "input",
        "cached_input",
        "cache_write",
        "output",
        "reasoning",
        "web_search_requests",
        "images",
    ],
)
def test_usage_counts_are_never_negative(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        Usage(**{field: -1})  # pyright: ignore[reportArgumentType]


def test_refusal_completions_carry_a_refusal_and_no_output() -> None:
    refusal = Refusal("cyber", "Declined.")
    completion = Completion(
        text="", finish_reason="refusal", usage=Usage(), model="m", response_id="r", refusal=refusal
    )
    assert completion.refusal is refusal
    with pytest.raises(ValueError, match="exactly one Refusal"):
        Completion(text="", finish_reason="refusal", usage=Usage(), model="m", response_id=None)
    with pytest.raises(ValueError, match="exactly one Refusal"):
        Completion(
            text="x",
            finish_reason="stop",
            usage=Usage(),
            model="m",
            response_id=None,
            refusal=refusal,
        )
    with pytest.raises(ValueError, match="must not carry output"):
        Completion(
            text="partial",
            finish_reason="refusal",
            usage=Usage(),
            model="m",
            response_id=None,
            refusal=refusal,
        )
    with pytest.raises(ValueError, match="must not carry output"):
        Completion(
            text="",
            finish_reason="refusal",
            usage=Usage(),
            model="m",
            response_id=None,
            refusal=refusal,
            tool_calls=(ToolCall("c", "t", "{}"),),
        )


def test_stream_events_name_their_type() -> None:
    completion = Completion(
        text="x", finish_reason="stop", usage=Usage(), model="m", response_id=None
    )
    events = [
        TextDelta("a"),
        ThinkingSummary("b"),
        ToolCall("c", "t", "{}"),
        Refusal(None, None),
        UsageEvent(Usage()),
        Done("stop", completion),
    ]
    assert [event.type for event in events] == [
        "text_delta",
        "thinking_summary",
        "tool_call",
        "refusal",
        "usage",
        "done",
    ]


def test_reprs_never_show_prompt_or_model_text() -> None:
    marker = "SECRET-interview-text"
    user = Message("user", (TextPart(marker), ToolResultPart("call_1", marker)))
    reply = Message(
        "assistant",
        (ToolCallPart("call_1", "fetch_page", marker),),
        NativeItems("openai", ({"text": marker},)),
    )
    request = Request(
        system=marker,
        messages=(user, reply, Message.user(marker)),
        sensitivity=Sensitivity.PRIVATE,
    )
    completion = Completion(
        text=marker, finish_reason="stop", usage=Usage(), model="m", response_id=None, message=reply
    )
    values: list[object] = [
        user,
        reply,
        request,
        completion,
        Done("stop", completion),
        TextDelta(marker),
        ThinkingSummary(marker),
        ToolCall("call_1", "fetch_page", marker),
    ]
    for value in values:
        assert marker not in repr(value)
    assert "sensitivity=<Sensitivity.PRIVATE: 'private'>" in repr(request)
