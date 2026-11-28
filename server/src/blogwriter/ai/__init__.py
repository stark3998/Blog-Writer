"""blogwriter.ai: model providers, the router and its policy, limits, ledger and brake.

Entry point: :class:`AIRouter` (``run(task, request, origin=...)`` / ``stream(...)``, and
``embed(texts, origin=...)`` for ``Task.EMBED``), built with
:meth:`AIRouter.from_settings` from :class:`AISettings` (``BW_AI_*``), the providers from
:func:`create_providers` and a :class:`LedgerSink`. See ``docs/v2/phase1/ai-router.md``.

Modules: :mod:`.types` (requests, events, sensitivity classes), :mod:`.schema` (structured
output), :mod:`.providers` (Azure OpenAI Responses, Claude on Foundry), :mod:`.config`
(settings and routing table), :mod:`.policy` (sensitivity rules), :mod:`.limits` (semaphores,
rate limits, retries), :mod:`.ledger` (prices, usage records, brake) and :mod:`.router`.
"""

from blogwriter.ai.config import (
    DEFAULT_PRICES,
    DEFAULT_ROUTES,
    AISettings,
    ModelSettings,
    NoRouteError,
    Route,
    RouteSettings,
    RouteTarget,
    RoutingTable,
)
from blogwriter.ai.ledger import (
    BrakeEngaged,
    BrakeStatus,
    BudgetBrake,
    LedgerSink,
    MemoryLedger,
    ModelPrice,
    Origin,
    PriceTable,
    UsageRecord,
)
from blogwriter.ai.limits import Clock, ModelLimiter, ModelLimits, RetryPolicy, SystemClock
from blogwriter.ai.policy import PolicyViolation, SensitivityPolicy, TaskPolicy
from blogwriter.ai.router import (
    DISCARD_EVENTS,
    REVIEW_ONLY_TASKS,
    AIRouter,
    EmbeddingResult,
    Fallback,
    FallbackReason,
    Finished,
    Repair,
    Retry,
    RouterEvent,
    RouterResult,
    ServedBy,
    create_providers,
)

__all__ = [
    "DEFAULT_PRICES",
    "DEFAULT_ROUTES",
    "DISCARD_EVENTS",
    "REVIEW_ONLY_TASKS",
    "AIRouter",
    "AISettings",
    "BrakeEngaged",
    "BrakeStatus",
    "BudgetBrake",
    "Clock",
    "EmbeddingResult",
    "Fallback",
    "FallbackReason",
    "Finished",
    "LedgerSink",
    "MemoryLedger",
    "ModelLimiter",
    "ModelLimits",
    "ModelPrice",
    "ModelSettings",
    "NoRouteError",
    "Origin",
    "PolicyViolation",
    "PriceTable",
    "Repair",
    "Retry",
    "RetryPolicy",
    "Route",
    "RouteSettings",
    "RouteTarget",
    "RouterEvent",
    "RouterResult",
    "RoutingTable",
    "SensitivityPolicy",
    "ServedBy",
    "SystemClock",
    "TaskPolicy",
    "UsageRecord",
    "create_providers",
]
