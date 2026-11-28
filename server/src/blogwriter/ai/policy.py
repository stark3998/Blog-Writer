"""Sensitivity-class policy for routed AI calls.

Data classes are ordered ``PUBLIC < ANONYMIZED < PRIVATE`` (see
:class:`~blogwriter.ai.types.Sensitivity`). The rules:

* A request's class must not exceed its task's maximum.
* Any tool-enabled request (function tools or ``web_search``) must be ``PUBLIC``, and only
  tasks whose ceiling allows tools may use them. Provider-side background runs must be
  ``PUBLIC`` too.
* ``PRIVATE`` data goes only to the anonymizer (:attr:`Task.ANONYMIZE`), only to Azure OpenAI
  (never Claude), only tool-free, and to models whose reasoning summaries are off (they would
  echo the private text into the stream). Strict mode (``allow_private=False``) sends it
  nowhere.
* Image input (:class:`~blogwriter.ai.types.ImagePart`) only for the tasks in
  :data:`IMAGE_TASKS` (the engagement helpers' screenshot extraction). Images cannot be checked
  against the blocklist, so no other task takes them.
* The fallback chain can never relax a rule: :meth:`SensitivityPolicy.check` applies every rule
  to every model of the chain before the first call, and the rules depend only on the task and
  the request, never on which model ends up serving.

Two layers enforce this:

:data:`TASK_CEILINGS`
    Hard per-task maxima from the v2 plan, in code. Configuration may tighten a route
    (:class:`TaskPolicy` with a lower class or without tools) but never loosen one:
    :class:`TaskPolicy` refuses a class or tool permission above the ceiling, and
    :func:`validate_route` refuses a route whose models could break a rule (for example a
    ``PRIVATE`` route with a Claude fallback). Both fail at startup.
:class:`SensitivityPolicy`
    The per-request check. It raises :class:`PolicyViolation` (a
    :class:`~blogwriter.ai.types.SensitivityError`) before anything is sent, and fails closed on
    a task it has no ceiling for. It re-checks the rules :class:`~blogwriter.ai.types.Request`
    already enforces on construction, so a request built some other way cannot slip through.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from blogwriter.ai.types import (
    ModelSpec,
    Request,
    Sensitivity,
    SensitivityError,
    Task,
    WebSearchTool,
)

__all__ = [
    "IMAGE_TASKS",
    "PRIVATE_TASKS",
    "TASK_CEILINGS",
    "PolicyRule",
    "PolicyViolation",
    "SensitivityPolicy",
    "TaskPolicy",
    "validate_route",
]

type PolicyRule = Literal[
    "unknown_task",
    "above_task_max",
    "private_disabled",
    "private_requires_openai",
    "background_requires_public",
    "tools_not_allowed",
    "tools_require_public",
    "model_without_tools",
    "web_search_requires_openai",
    "images_not_allowed",
]
"""Machine-readable names of the rules :class:`SensitivityPolicy` enforces."""

_PUBLIC: Final = Sensitivity.PUBLIC
_ANONYMIZED: Final = Sensitivity.ANONYMIZED
_PRIVATE: Final = Sensitivity.PRIVATE

# Task -> (highest data class, whether tool-enabled requests are allowed). From the plan's
# routing table and security model; change only with a code review.
_CEILINGS: Final[Mapping[Task, tuple[Sensitivity, bool]]] = MappingProxyType(
    {
        Task.DRAFT: (_ANONYMIZED, False),
        Task.REVISE: (_ANONYMIZED, False),
        Task.OUTLINE: (_ANONYMIZED, False),
        Task.PLAN: (_ANONYMIZED, False),
        Task.INTERVIEW: (_ANONYMIZED, False),
        Task.SYNTHESIS: (_ANONYMIZED, False),
        Task.CRITIQUE: (_ANONYMIZED, False),
        Task.FACT_CHECK: (_ANONYMIZED, False),
        Task.ANONYMIZE: (_PRIVATE, False),
        Task.RESEARCH: (_PUBLIC, True),
        Task.QUICK_SEARCH: (_PUBLIC, True),
        Task.TRIAGE: (_PUBLIC, False),
        Task.ENGAGEMENT: (_ANONYMIZED, False),
        Task.EMBED: (_ANONYMIZED, False),
        Task.IMAGE: (_PUBLIC, False),
    }
)

PRIVATE_TASKS: Final = frozenset(task for task, (cls, _) in _CEILINGS.items() if cls is _PRIVATE)
"""The tasks that may receive ``PRIVATE`` data: the anonymizer only."""

IMAGE_TASKS: Final = frozenset({Task.ENGAGEMENT})
"""The tasks that may receive image parts: the engagement helpers (the owner's own screenshots
of a comment thread, read as untrusted ``anonymized`` data). A hard rule like the ceilings: change
only with a code review."""


class PolicyViolation(SensitivityError):
    """A request breaks a sensitivity rule; nothing was sent.

    The message names the task, the classes and the rule, never request content.
    """

    def __init__(self, task: Task, rule: PolicyRule, detail: str) -> None:
        self.task = task
        self.rule: PolicyRule = rule
        super().__init__(f"{task.value}: {detail} ({rule})")


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    """The data rules of one task's route: the highest class and whether tools are allowed.

    Construction fails (``ValueError``) when either is above the task's ceiling.
    """

    task: Task
    max_sensitivity: Sensitivity
    tools: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.max_sensitivity, Sensitivity):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("max_sensitivity must be a Sensitivity")
        ceiling = _CEILINGS.get(self.task)
        if ceiling is None:
            raise ValueError(f"{self.task.value}: no sensitivity ceiling is defined")
        highest, tools = ceiling
        if self.max_sensitivity > highest:
            raise ValueError(
                f"{self.task.value}: max class {self.max_sensitivity.value} is above the "
                f"ceiling {highest.value}"
            )
        if self.tools and not tools:
            raise ValueError(f"{self.task.value}: tools are not allowed for this task")

    @classmethod
    def ceiling(cls, task: Task) -> TaskPolicy:
        """The loosest policy the task may have."""
        highest, tools = _CEILINGS[task]
        return cls(task, highest, tools)


TASK_CEILINGS: Final[Mapping[Task, TaskPolicy]] = MappingProxyType(
    {task: TaskPolicy.ceiling(task) for task in _CEILINGS}
)
"""The hard per-task maxima. Every :class:`~blogwriter.ai.types.Task` has one."""


def validate_route(policy: TaskPolicy, chain: Sequence[ModelSpec]) -> None:
    """Refuse (``ValueError``) a route whose models could break the task's rules.

    Called when the routing table is built, so a bad configuration fails at startup.
    """
    task = policy.task.value
    if not chain:
        raise ValueError(f"{task}: a route needs at least one model")
    for spec in chain:
        if policy.max_sensitivity is _PRIVATE and spec.provider != "openai":
            raise ValueError(
                f"{task}: private data may only go to Azure OpenAI, not {spec.deployment} "
                f"({spec.provider})"
            )
        if policy.tools and not spec.supports_tools:
            raise ValueError(f"{task}: the route allows tools but {spec.deployment} has none")
    for spec in chain:
        # Reasoning summaries would echo private text into the stream. RouteTarget turns them
        # on by default, so an override of a private route must turn them off explicitly.
        if policy.max_sensitivity is _PRIVATE and spec.thinking_summary:
            raise ValueError(
                f"{task}: a private route must not stream reasoning summaries; set "
                f"thinking_summary to false for {spec.deployment}"
            )


class SensitivityPolicy:
    """The per-request check (see the module docstring).

    ``allow_private=False`` is strict mode: ``PRIVATE`` data is refused for every task.
    """

    def __init__(self, *, allow_private: bool = True) -> None:
        self._allow_private = allow_private

    @property
    def allow_private(self) -> bool:
        return self._allow_private

    def check(self, policy: TaskPolicy, request: Request, chain: Sequence[ModelSpec]) -> None:
        """Raise :class:`PolicyViolation` unless every model of ``chain`` may serve ``request``."""
        task = policy.task
        ceiling = _CEILINGS.get(task)
        if ceiling is None:
            raise PolicyViolation(task, "unknown_task", "no sensitivity ceiling is defined")
        highest, tools_allowed = ceiling
        limit = min(policy.max_sensitivity, highest, key=lambda item: item.rank)
        tools_allowed = tools_allowed and policy.tools
        data = request.sensitivity
        if not isinstance(data, Sensitivity):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise PolicyViolation(task, "above_task_max", "the request has no data class")

        if data > limit:
            raise PolicyViolation(
                task, "above_task_max", f"{data.value} data is above the task maximum {limit.value}"
            )
        if data is _PRIVATE and not self._allow_private:
            raise PolicyViolation(task, "private_disabled", "strict mode refuses private data")
        if request.image_count and task not in IMAGE_TASKS:
            raise PolicyViolation(task, "images_not_allowed", "this task takes no image input")
        if request.background and data is not _PUBLIC:
            raise PolicyViolation(
                task, "background_requires_public", f"{data.value} data cannot run in background"
            )
        if request.tools:
            if not tools_allowed:
                raise PolicyViolation(task, "tools_not_allowed", "this task is tool-free")
            if data is not _PUBLIC:
                raise PolicyViolation(
                    task, "tools_require_public", f"{data.value} data cannot reach tools"
                )
        web_search = any(isinstance(tool, WebSearchTool) for tool in request.tools)
        for spec in chain:
            if data is _PRIVATE and spec.provider != "openai":
                raise PolicyViolation(
                    task,
                    "private_requires_openai",
                    f"private data cannot go to {spec.deployment} ({spec.provider})",
                )
            if request.tools and not spec.supports_tools:
                raise PolicyViolation(
                    task, "model_without_tools", f"{spec.deployment} is configured without tools"
                )
            if web_search and spec.provider != "openai":
                raise PolicyViolation(
                    task,
                    "web_search_requires_openai",
                    f"web_search is not offered on {spec.deployment} ({spec.provider})",
                )
