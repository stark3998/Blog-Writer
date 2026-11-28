"""Sensitivity classes, enforced by the router's policy before any call.

Plan: "Security model" > Sensitivity classes (private: only a tool-free Azure OpenAI anonymizer,
or nothing in strict mode; anonymized: Azure OpenAI and Claude, tool-free only; public: the only
class tool-enabled and web_search contexts receive; the fallback chain can never relax them)
and "AI layer" (the routing table's max class per task).
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Literal, override

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai import policy as policy_module
from blogwriter.ai.config import AISettings, Route, RouteSettings, RouteTarget
from blogwriter.ai.policy import (
    PRIVATE_TASKS,
    TASK_CEILINGS,
    PolicyViolation,
    SensitivityPolicy,
    TaskPolicy,
    validate_route,
)
from blogwriter.ai.types import (
    FunctionTool,
    Message,
    ModelSpec,
    Request,
    Sensitivity,
    SensitivityError,
    Task,
    Tool,
    WebSearchTool,
)

PUBLIC = Sensitivity.PUBLIC
ANONYMIZED = Sensitivity.ANONYMIZED
PRIVATE = Sensitivity.PRIVATE

SOL = ModelSpec("openai", "gpt-6.2-sol", "gpt-6.2-sol", "high", 4_000, supports_tools=True)
ASTRA = ModelSpec("openai", "gpt-6.2-astra", "gpt-6.2-astra", None, 4_000, supports_tools=True)
OPUS = ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", "high", 4_000, True)
NO_TOOLS = ModelSpec("openai", "text-embedding-3-large", "text-embedding-3-large", None, 1, False)


class ArgsOnlyAISettings(AISettings):
    """``AISettings`` that ignore the process environment."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


class FetchArgs(BaseModel):
    url: str


FETCH = FunctionTool("fetch_page", "Fetch one page returned by search.", FetchArgs)
SEARCH = WebSearchTool(allowed_domains=("nist.gov",))

type ToolVariant = Literal["none", "function", "web_search"]
TOOLS: dict[ToolVariant, tuple[Tool, ...]] = {
    "none": (),
    "function": (FETCH,),
    "web_search": (SEARCH,),
}

CONFIDENTIAL = "Client Contoso migrated 2,000 apps"  # stands in for confidential text


def request(sensitivity: Sensitivity, *, tools: tuple[Tool, ...] = ()) -> Request:
    """A request as callers build it (the constructor itself enforces some rules)."""
    return Request(
        system="You are careful.",
        messages=(Message.user(CONFIDENTIAL),),
        sensitivity=sensitivity,
        tools=tools,
    )


def forged(sensitivity: Sensitivity, *, tools: tuple[Tool, ...] = (), **fields: bool) -> Request:
    """A request that skipped the constructor's checks (the policy must not rely on them)."""
    built = request(PUBLIC, tools=tools)
    object.__setattr__(built, "sensitivity", sensitivity)
    for name, value in fields.items():
        object.__setattr__(built, name, value)
    return built


DEFAULTS = ArgsOnlyAISettings()
ROUTES = DEFAULTS.routing()
ROUTED_TASKS: list[Task] = sorted(ROUTES, key=lambda task: task.value)


# --------------------------------------------------------------------------------------------
# Ceilings
# --------------------------------------------------------------------------------------------


def test_every_task_has_the_ceiling_from_the_plan() -> None:
    expected = {
        Task.DRAFT: (ANONYMIZED, False),
        Task.REVISE: (ANONYMIZED, False),
        Task.OUTLINE: (ANONYMIZED, False),
        Task.PLAN: (ANONYMIZED, False),
        Task.INTERVIEW: (ANONYMIZED, False),
        Task.SYNTHESIS: (ANONYMIZED, False),
        Task.CRITIQUE: (ANONYMIZED, False),
        Task.FACT_CHECK: (ANONYMIZED, False),
        Task.ANONYMIZE: (PRIVATE, False),
        Task.RESEARCH: (PUBLIC, True),
        Task.QUICK_SEARCH: (PUBLIC, True),
        Task.TRIAGE: (PUBLIC, False),
        Task.ENGAGEMENT: (ANONYMIZED, False),
        Task.EMBED: (ANONYMIZED, False),
        Task.IMAGE: (PUBLIC, False),
    }
    assert set(TASK_CEILINGS) == set(Task)
    assert {
        task: (ceiling.max_sensitivity, ceiling.tools) for task, ceiling in TASK_CEILINGS.items()
    } == expected


def test_only_the_anonymizer_may_receive_private_data() -> None:
    assert frozenset({Task.ANONYMIZE}) == PRIVATE_TASKS
    assert [task for task, route in ROUTES.items() if route.policy.max_sensitivity is PRIVATE] == [
        Task.ANONYMIZE
    ]


def test_default_routes_sit_exactly_at_their_ceilings() -> None:
    assert set(ROUTES) == set(Task) - {Task.IMAGE}  # images are opt-in
    for task, route in ROUTES.items():
        assert route.policy == TASK_CEILINGS[task]


def test_task_policies_can_tighten_but_never_loosen() -> None:
    assert TaskPolicy(Task.DRAFT, PUBLIC).max_sensitivity is PUBLIC
    assert TaskPolicy(Task.RESEARCH, PUBLIC, tools=False).tools is False
    with pytest.raises(ValueError, match="above the ceiling"):
        TaskPolicy(Task.DRAFT, PRIVATE)
    with pytest.raises(ValueError, match="above the ceiling"):
        TaskPolicy(Task.TRIAGE, ANONYMIZED)
    with pytest.raises(ValueError, match="tools are not allowed"):
        TaskPolicy(Task.ENGAGEMENT, PUBLIC, tools=True)
    with pytest.raises(TypeError, match="Sensitivity"):
        TaskPolicy(Task.DRAFT, "public")  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------------------------
# The full matrix: every routed task x every class x no tools / function tool / web_search
# --------------------------------------------------------------------------------------------


def expected_rule(task: Task, sensitivity: Sensitivity, variant: ToolVariant) -> str | None:
    ceiling = TASK_CEILINGS[task]
    if sensitivity > ceiling.max_sensitivity:
        return "above_task_max"
    if variant != "none" and not ceiling.tools:
        return "tools_not_allowed"
    if variant != "none" and sensitivity is not PUBLIC:
        return "tools_require_public"
    return None


@pytest.mark.parametrize("variant", list(TOOLS))
@pytest.mark.parametrize("sensitivity", list(Sensitivity))
@pytest.mark.parametrize("task", ROUTED_TASKS)
def test_the_sensitivity_matrix(task: Task, sensitivity: Sensitivity, variant: ToolVariant) -> None:
    route = ROUTES[task]
    tools = TOOLS[variant]
    rule = expected_rule(task, sensitivity, variant)
    check = SensitivityPolicy()
    if tools and sensitivity is not PUBLIC:
        # The request type already refuses this; the policy refuses it again on its own.
        with pytest.raises(SensitivityError):
            request(sensitivity, tools=tools)
        candidate = forged(sensitivity, tools=tools)
    else:
        candidate = request(sensitivity, tools=tools)
    if rule is None:
        check.check(route.policy, candidate, route.chain)
        return
    with pytest.raises(PolicyViolation) as violation:
        check.check(route.policy, candidate, route.chain)
    assert violation.value.rule == rule
    assert violation.value.task is task
    assert isinstance(violation.value, SensitivityError)
    assert CONFIDENTIAL not in str(violation.value)


# --------------------------------------------------------------------------------------------
# A fallback can never relax a rule
# --------------------------------------------------------------------------------------------


def test_private_data_never_reaches_claude_not_even_as_a_fallback() -> None:
    anonymizer = TaskPolicy.ceiling(Task.ANONYMIZE)
    check = SensitivityPolicy()
    check.check(anonymizer, request(PRIVATE), (SOL, ASTRA))
    with pytest.raises(PolicyViolation) as violation:
        check.check(anonymizer, request(PRIVATE), (SOL, OPUS))
    assert violation.value.rule == "private_requires_openai"
    # A route like that cannot even be built, from code or from settings.
    with pytest.raises(ValueError, match="private data may only go to Azure OpenAI"):
        validate_route(anonymizer, (SOL, OPUS))
    with pytest.raises(ValueError, match="private data may only go to Azure OpenAI"):
        Route(Task.ANONYMIZE, anonymizer, SOL, OPUS)
    override = RouteSettings(
        primary=RouteTarget(model="gpt-6.2-sol"),
        fallback=RouteTarget(model="claude-opus-5-5", effort="high"),
        max_sensitivity=PRIVATE,
    )
    with pytest.raises(ValidationError, match="private data may only go to Azure OpenAI"):
        ArgsOnlyAISettings(route_overrides={Task.ANONYMIZE: override})


def test_a_private_route_never_streams_reasoning_summaries() -> None:
    anonymizer = TaskPolicy.ceiling(Task.ANONYMIZE)
    quiet = replace(SOL, thinking_summary=False)
    validate_route(anonymizer, (quiet, replace(ASTRA, thinking_summary=False)))
    with pytest.raises(ValueError, match="must not stream reasoning summaries"):
        validate_route(anonymizer, (quiet, ASTRA))
    # RouteTarget turns summaries on by default, so an override that leaves the field out
    # fails at startup instead of quietly echoing private text into the stream.
    careless = RouteSettings(
        primary=RouteTarget(model="gpt-6.2-sol", max_output_tokens=12_000),
        max_sensitivity=PRIVATE,
    )
    with pytest.raises(ValidationError, match="must not stream reasoning summaries"):
        ArgsOnlyAISettings(route_overrides={Task.ANONYMIZE: careless})
    explicit = RouteSettings(
        primary=RouteTarget(model="gpt-6.2-sol", max_output_tokens=12_000, thinking_summary=False),
        max_sensitivity=PRIVATE,
    )
    routes = ArgsOnlyAISettings(route_overrides={Task.ANONYMIZE: explicit}).routing()
    assert not routes[Task.ANONYMIZE].primary.thinking_summary
    # Routes for other classes keep their summaries.
    validate_route(TaskPolicy.ceiling(Task.DRAFT), (OPUS, SOL))


def test_anonymized_data_may_reach_claude_tool_free() -> None:
    check = SensitivityPolicy()
    check.check(TaskPolicy.ceiling(Task.DRAFT), request(ANONYMIZED), (OPUS, SOL))


def test_web_search_must_be_servable_by_every_model_of_the_chain() -> None:
    research = TaskPolicy.ceiling(Task.RESEARCH)
    check = SensitivityPolicy()
    check.check(research, request(PUBLIC, tools=(SEARCH,)), (SOL, ASTRA))
    # Function tools work on Claude, web_search does not: the fallback cannot take it.
    check.check(research, request(PUBLIC, tools=(FETCH,)), (SOL, OPUS))
    with pytest.raises(PolicyViolation) as violation:
        check.check(research, request(PUBLIC, tools=(SEARCH,)), (SOL, OPUS))
    assert violation.value.rule == "web_search_requires_openai"


def test_a_model_without_tools_cannot_take_a_tool_request() -> None:
    research = TaskPolicy.ceiling(Task.RESEARCH)
    with pytest.raises(PolicyViolation) as violation:
        SensitivityPolicy().check(research, request(PUBLIC, tools=(FETCH,)), (SOL, NO_TOOLS))
    assert violation.value.rule == "model_without_tools"
    with pytest.raises(ValueError, match="has none"):
        validate_route(research, (SOL, NO_TOOLS))
    with pytest.raises(ValueError, match="at least one model"):
        validate_route(research, ())


def test_settings_overrides_cannot_loosen_a_ceiling() -> None:
    draft = RouteSettings(
        primary=RouteTarget(model="claude-opus-5-5", effort="high"), max_sensitivity=PRIVATE
    )
    with pytest.raises(ValidationError, match="above the ceiling"):
        ArgsOnlyAISettings(route_overrides={Task.DRAFT: draft})
    triage = RouteSettings(
        primary=RouteTarget(model="gpt-6-luna"), max_sensitivity=PUBLIC, tools=True
    )
    with pytest.raises(ValidationError, match="tools are not allowed"):
        ArgsOnlyAISettings(route_overrides={Task.TRIAGE: triage})


def test_settings_overrides_can_tighten() -> None:
    settings = ArgsOnlyAISettings(
        route_overrides={
            Task.ENGAGEMENT: RouteSettings(
                primary=RouteTarget(model="gpt-6.2-sol", effort="low"), max_sensitivity=PUBLIC
            ),
            Task.RESEARCH: RouteSettings(
                primary=RouteTarget(model="gpt-6.2-sol"), max_sensitivity=PUBLIC, tools=False
            ),
        }
    )
    routes = settings.routing()
    check = settings.sensitivity_policy()
    with pytest.raises(PolicyViolation) as anonymized:
        check.check(
            routes[Task.ENGAGEMENT].policy, request(ANONYMIZED), routes[Task.ENGAGEMENT].chain
        )
    assert anonymized.value.rule == "above_task_max"
    research = routes[Task.RESEARCH]
    with pytest.raises(PolicyViolation) as tools:
        check.check(research.policy, request(PUBLIC, tools=(SEARCH,)), research.chain)
    assert tools.value.rule == "tools_not_allowed"


# --------------------------------------------------------------------------------------------
# Other rules
# --------------------------------------------------------------------------------------------


def test_strict_mode_sends_private_data_nowhere() -> None:
    anonymizer = TaskPolicy.ceiling(Task.ANONYMIZE)
    strict = ArgsOnlyAISettings(allow_private=False).sensitivity_policy()
    assert strict.allow_private is False
    with pytest.raises(PolicyViolation) as violation:
        strict.check(anonymizer, request(PRIVATE), (SOL,))
    assert violation.value.rule == "private_disabled"
    strict.check(anonymizer, request(ANONYMIZED), (SOL,))


def test_background_runs_must_be_public() -> None:
    research = TaskPolicy.ceiling(Task.RESEARCH)
    with pytest.raises(SensitivityError):
        replace(request(PUBLIC), sensitivity=ANONYMIZED, background=True)
    check = SensitivityPolicy()
    check.check(research, replace(request(PUBLIC), background=True), (SOL,))
    with pytest.raises(PolicyViolation) as violation:
        check.check(
            TaskPolicy.ceiling(Task.DRAFT), forged(ANONYMIZED, background=True), (OPUS, SOL)
        )
    assert violation.value.rule == "background_requires_public"


def test_a_request_without_a_class_fails_closed() -> None:
    unclassified = request(PUBLIC)
    object.__setattr__(unclassified, "sensitivity", "public")
    with pytest.raises(PolicyViolation, match="no data class"):
        SensitivityPolicy().check(TaskPolicy.ceiling(Task.DRAFT), unclassified, (OPUS,))


def test_a_task_without_a_ceiling_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = TaskPolicy.ceiling(Task.DRAFT)
    without_draft = {task: value for task, value in TASK_CEILINGS.items() if task is not Task.DRAFT}
    ceilings = MappingProxyType(
        {task: (value.max_sensitivity, value.tools) for task, value in without_draft.items()}
    )
    monkeypatch.setattr(policy_module, "_CEILINGS", ceilings)
    with pytest.raises(PolicyViolation) as violation:
        SensitivityPolicy().check(draft, request(PUBLIC), (OPUS,))
    assert violation.value.rule == "unknown_task"
    with pytest.raises(ValueError, match="no sensitivity ceiling"):
        TaskPolicy(Task.DRAFT, PUBLIC)


def test_tools_need_public_data_even_where_a_ceiling_allows_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No task allows tools above public today; the rule must still hold if one ever does.
    ceilings = {task: (value.max_sensitivity, value.tools) for task, value in TASK_CEILINGS.items()}
    ceilings[Task.FACT_CHECK] = (ANONYMIZED, True)
    monkeypatch.setattr(policy_module, "_CEILINGS", MappingProxyType(ceilings))
    fact_check = TaskPolicy(Task.FACT_CHECK, ANONYMIZED, tools=True)
    check = SensitivityPolicy()
    check.check(fact_check, request(ANONYMIZED), (SOL,))
    check.check(fact_check, request(PUBLIC, tools=(FETCH,)), (SOL,))
    with pytest.raises(PolicyViolation) as violation:
        check.check(fact_check, forged(ANONYMIZED, tools=(FETCH,)), (SOL,))
    assert violation.value.rule == "tools_require_public"


def test_violations_name_the_rule_but_never_the_content() -> None:
    with pytest.raises(PolicyViolation) as violation:
        SensitivityPolicy().check(TaskPolicy.ceiling(Task.TRIAGE), request(PRIVATE), (SOL,))
    message = str(violation.value)
    assert message == "triage: private data is above the task maximum public (above_task_max)"
    assert CONFIDENTIAL not in message
