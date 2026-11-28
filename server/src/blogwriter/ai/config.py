"""AI settings (environment prefix ``BW_AI_``), the default routing table and prices.

Environment (every value is optional; defaults are safe for tests and connect nowhere)

``BW_AI_OPENAI_BASE_URL``
    Azure OpenAI v1 endpoint, ``https://<resource>.openai.azure.com/openai/v1/`` (the value
    infra/v2 exports as ``AZURE_OPENAI_BASE_URL``).
``BW_AI_ANTHROPIC_RESOURCE``
    Foundry resource name serving Claude (infra/v2's ``ANTHROPIC_FOUNDRY_RESOURCE``).
``BW_AI_DEPLOYMENT_SOL`` / ``_ASTRA`` / ``_LUNA`` / ``_OPUS`` / ``_EMBEDDING``
    Deployment names (infra/v2's ``MODEL_DEPLOYMENT_*``); each defaults to its model name.
``BW_AI_IMAGE_BASE_URL``
    The Azure OpenAI v1 endpoint of the **separate** Foundry resource that serves
    ``gpt-image-2`` (East US 2, where GPT-6.2 is not offered):
    ``https://<resource>.openai.azure.com/openai/v1/``. Unset: images are off (no ``image``
    route). It authenticates with the same Entra token provider as the Responses provider.
``BW_AI_DEPLOYMENT_IMAGE``
    The image deployment name (default ``gpt-image-2``).
``BW_AI_IMAGE_USD_1536X1024``, ``BW_AI_IMAGE_USD_1024X1024``
    The ledger's price of one ``high`` quality image of that size (defaults 0.25 and 0.17).
    **Assumption**: these are the published GPT Image high-quality prices per image (output
    image tokens at USD 40 per 1M: about 6,240 tokens for 1536x1024 and 4,160 for 1024x1024)
    and Azure has not published ``gpt-image-2`` prices for this resource; the prompt's few
    text tokens are not counted. Set the real prices here once the bill shows them.
``BW_AI_MODEL_OVERRIDES``, ``BW_AI_ROUTE_OVERRIDES``, ``BW_AI_PRICE_OVERRIDES``
    JSON objects merged over the defaults. Each entry replaces the whole default entry with the
    same key (a model name or a task), so an override can add a model (for example
    ``gpt-5.6-sol``) or reroute a task. Overrides can never loosen a task's data ceiling
    (:mod:`blogwriter.ai.policy`); a bad table fails at startup.
``BW_AI_WEB_SEARCH_USD_PER_1K``
    Price of hosted ``web_search`` per 1,000 requests (default 14).
``BW_AI_ALLOW_PRIVATE``
    ``false`` is strict mode: private data goes to no model at all.
``BW_AI_RETRY_MAX_ATTEMPTS``, ``_BASE_DELAY_S``, ``_MAX_DELAY_S``, ``BW_AI_RETRY_AFTER_MAX_S``
    Retry policy for model calls (see :class:`~blogwriter.ai.limits.RetryPolicy`).
``BW_AI_BRAKE_DAILY_CAP_USD``, ``_MULTIPLIER``, ``_WINDOW_DAYS``, ``_TIMEZONE``
    The runaway brake: background work pauses once today's spend exceeds
    ``max(cap, multiplier x median daily spend of the previous window)``.

Default routes (from the v2 plan; post and carousel copy use ``DRAFT``/``REVISE``):

============================  ======================  ======================  ==========  =====
Task                          Primary                 Fallback                Max class   Tools
============================  ======================  ======================  ==========  =====
draft, revise                 claude-opus-5-5 high    gpt-6.2-sol high        anonymized  no
outline, plan, interview,     gpt-6.2-sol high        gpt-6.2-astra           anonymized  no
synthesis
critique                      gpt-6.2-astra           gpt-6.2-sol xhigh       anonymized  no
fact_check                    gpt-6.2-sol             gpt-6.2-astra           anonymized  no
anonymize                     gpt-6.2-sol             gpt-6.2-astra           private     no
research                      gpt-6.2-sol             gpt-6.2-astra           public      yes
quick_search                  gpt-6.2-sol low         gpt-6.2-astra low       public      yes
triage                        gpt-6-luna              (none)                  public      no
engagement                    gpt-6.2-sol low         (none)                  anonymized  no
embed                         text-embedding-3-large  (none)                  anonymized  no
image (with the image URL)    gpt-image-2             (none)                  public      no
============================  ======================  ======================  ==========  =====

A fallback that serves ``draft``, ``revise`` or ``critique`` makes the output review-only.
``image`` is opt-in: it has a route only when ``BW_AI_IMAGE_BASE_URL`` is set (concurrency 1,
priced per image), so it is refused until then.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.ai.ledger import ModelPrice, PriceTable
from blogwriter.ai.limits import ModelLimits, RetryPolicy
from blogwriter.ai.policy import SensitivityPolicy, TaskPolicy, validate_route
from blogwriter.ai.providers.auth import validate_resource_name
from blogwriter.ai.types import Effort, ModelSpec, ProviderName, Sensitivity, Task

__all__ = [
    "ASTRA",
    "DEFAULT_IMAGE_USD",
    "DEFAULT_PRICES",
    "DEFAULT_ROUTES",
    "DEFAULT_WEB_SEARCH_USD_PER_1K",
    "EMBEDDING",
    "IMAGE",
    "IMAGE_ROUTE",
    "LUNA",
    "OPUS",
    "SOL",
    "AISettings",
    "ModelSettings",
    "NoRouteError",
    "Route",
    "RouteSettings",
    "RouteTarget",
    "RoutingTable",
]

SOL: Final = "gpt-6.2-sol"
ASTRA: Final = "gpt-6.2-astra"
LUNA: Final = "gpt-6-luna"
OPUS: Final = "claude-opus-5-5"
EMBEDDING: Final = "text-embedding-3-large"
IMAGE: Final = "gpt-image-2"

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _check_name(value: str, what: str) -> str:
    if not _NAME.fullmatch(value):
        raise ValueError(f"{what} {value!r} must be 1-64 characters of [A-Za-z0-9._-]")
    return value


# --------------------------------------------------------------------------------------------
# Configuration models
# --------------------------------------------------------------------------------------------


class ModelSettings(BaseModel):
    """A deployed model: where it runs and its client-side limits (``None`` = unlimited)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderName
    deployment: str
    supports_tools: bool = True
    concurrency: int | None = Field(default=None, ge=1)
    requests_per_minute: int | None = Field(default=None, ge=1)
    input_tokens_per_minute: int | None = Field(default=None, ge=1)
    output_tokens_per_minute: int | None = Field(default=None, ge=1)

    @field_validator("deployment")
    @classmethod
    def _check_deployment(cls, value: str) -> str:
        return _check_name(value, "deployment")

    def limits(self) -> ModelLimits:
        return ModelLimits(
            concurrency=self.concurrency,
            requests_per_minute=self.requests_per_minute,
            input_tokens_per_minute=self.input_tokens_per_minute,
            output_tokens_per_minute=self.output_tokens_per_minute,
        )


class RouteTarget(BaseModel):
    """One model of a route and how to call it. ``model`` names a model settings entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    effort: Effort | None = None
    max_output_tokens: int = Field(default=4_096, ge=1)
    thinking_summary: bool = True


class RouteSettings(BaseModel):
    """A task's route: primary, optional fallback, highest data class, whether tools are allowed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: RouteTarget
    fallback: RouteTarget | None = None
    max_sensitivity: Sensitivity
    tools: bool = False


# --------------------------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------------------------

DEFAULT_PRICES: Final[Mapping[str, ModelPrice]] = MappingProxyType(
    {
        SOL: ModelPrice(input=Decimal("2"), output=Decimal("10"), cached_input=Decimal("0.10")),
        ASTRA: ModelPrice(input=Decimal("8"), output=Decimal("40"), cached_input=Decimal("0.40")),
        LUNA: ModelPrice(input=Decimal("0.10"), output=Decimal("0.50")),
        OPUS: ModelPrice(
            input=Decimal("4"),
            output=Decimal("20"),
            cached_input=Decimal("0.20"),
            cache_write=Decimal("8"),
        ),
        EMBEDDING: ModelPrice(input=Decimal("0.13")),
        IMAGE: ModelPrice(
            input=Decimal(0),
            image=Decimal("0.25"),
            image_sizes={"1536x1024": Decimal("0.25"), "1024x1024": Decimal("0.17")},
        ),
    }
)
"""USD per 1M tokens. Opus cache writes are the 1 hour TTL price. ``gpt-image-2`` is priced per
image by size (see ``BW_AI_IMAGE_USD_*`` in the module docstring for the assumption)."""

DEFAULT_IMAGE_USD: Final[Mapping[str, Decimal]] = MappingProxyType(
    {"1536x1024": Decimal("0.25"), "1024x1024": Decimal("0.17")}
)
"""USD per ``high`` quality image by size (the ``BW_AI_IMAGE_USD_*`` defaults)."""

DEFAULT_WEB_SEARCH_USD_PER_1K: Final = Decimal("14")


def _target(
    model: str, effort: Effort | None, max_output_tokens: int, *, thinking_summary: bool = True
) -> RouteTarget:
    return RouteTarget(
        model=model,
        effort=effort,
        max_output_tokens=max_output_tokens,
        thinking_summary=thinking_summary,
    )


def _default_routes() -> dict[Task, RouteSettings]:
    anonymized = Sensitivity.ANONYMIZED
    public = Sensitivity.PUBLIC
    drafting = RouteSettings(
        primary=_target(OPUS, "high", 32_000),
        fallback=_target(SOL, "high", 32_000),
        max_sensitivity=anonymized,
    )
    planning = RouteSettings(
        primary=_target(SOL, "high", 16_000),
        fallback=_target(ASTRA, None, 16_000),
        max_sensitivity=anonymized,
    )
    return {
        Task.DRAFT: drafting,
        Task.REVISE: drafting,
        Task.OUTLINE: planning,
        Task.PLAN: planning,
        Task.INTERVIEW: planning,
        Task.SYNTHESIS: planning,
        Task.CRITIQUE: RouteSettings(
            primary=_target(ASTRA, None, 16_000),
            fallback=_target(SOL, "xhigh", 32_000),
            max_sensitivity=anonymized,
        ),
        Task.FACT_CHECK: RouteSettings(
            primary=_target(SOL, None, 16_000),
            fallback=_target(ASTRA, None, 16_000),
            max_sensitivity=anonymized,
        ),
        # Reasoning summaries are off so private text is not echoed into the stream.
        Task.ANONYMIZE: RouteSettings(
            primary=_target(SOL, None, 8_000, thinking_summary=False),
            fallback=_target(ASTRA, None, 8_000, thinking_summary=False),
            max_sensitivity=Sensitivity.PRIVATE,
        ),
        Task.RESEARCH: RouteSettings(
            primary=_target(SOL, None, 32_000),
            fallback=_target(ASTRA, None, 32_000),
            max_sensitivity=public,
            tools=True,
        ),
        Task.QUICK_SEARCH: RouteSettings(
            primary=_target(SOL, "low", 8_000),
            fallback=_target(ASTRA, "low", 8_000),
            max_sensitivity=public,
            tools=True,
        ),
        Task.TRIAGE: RouteSettings(
            primary=_target(LUNA, None, 4_000, thinking_summary=False), max_sensitivity=public
        ),
        Task.ENGAGEMENT: RouteSettings(
            primary=_target(SOL, "low", 4_000, thinking_summary=False),
            max_sensitivity=anonymized,
        ),
        Task.EMBED: RouteSettings(
            primary=_target(EMBEDDING, None, 4_096, thinking_summary=False),
            max_sensitivity=anonymized,
        ),
    }


DEFAULT_ROUTES: Final[Mapping[Task, RouteSettings]] = MappingProxyType(_default_routes())
"""The default routing table (see the module docstring). ``image`` is not in it: it is added
when ``BW_AI_IMAGE_BASE_URL`` is set (:data:`IMAGE_ROUTE`)."""

IMAGE_ROUTE: Final = RouteSettings(
    primary=_target(IMAGE, None, 4_096, thinking_summary=False),
    max_sensitivity=Sensitivity.PUBLIC,
)
"""The ``image`` route once the image resource is configured: ``gpt-image-2``, public data
only, tool-free, no fallback."""


# --------------------------------------------------------------------------------------------
# Routing table
# --------------------------------------------------------------------------------------------


class NoRouteError(LookupError):
    """No route is configured for a task; the router refuses it (fail closed)."""


@dataclass(frozen=True, slots=True)
class Route:
    """How one task is served: its data rules and its models, primary first."""

    task: Task
    policy: TaskPolicy
    primary: ModelSpec
    fallback: ModelSpec | None = None

    def __post_init__(self) -> None:
        if self.policy.task is not self.task:
            raise ValueError("the route policy belongs to another task")
        validate_route(self.policy, self.chain)

    @property
    def chain(self) -> tuple[ModelSpec, ...]:
        """The models in the order they are tried."""
        return (self.primary,) if self.fallback is None else (self.primary, self.fallback)


class RoutingTable(Mapping[Task, Route]):
    """Routes by task, plus the client-side limits of every model they use (by family)."""

    def __init__(self, routes: Mapping[Task, Route], limits: Mapping[str, ModelLimits]) -> None:
        for task, route in routes.items():
            if route.task is not task:
                raise ValueError(f"route for {route.task.value} is filed under {task.value}")
            for spec in route.chain:
                if spec.family not in limits:
                    raise ValueError(f"{task.value}: no limits for model {spec.family!r}")
        self._routes: Mapping[Task, Route] = MappingProxyType(dict(routes))
        self._limits: Mapping[str, ModelLimits] = MappingProxyType(dict(limits))

    def __getitem__(self, task: Task) -> Route:
        return self._routes[task]

    def __iter__(self) -> Iterator[Task]:
        return iter(self._routes)

    def __len__(self) -> int:
        return len(self._routes)

    @property
    def limits(self) -> Mapping[str, ModelLimits]:
        return self._limits

    def route(self, task: Task) -> Route:
        """The task's route; :class:`NoRouteError` when there is none."""
        route = self._routes.get(task)
        if route is None:
            raise NoRouteError(f"no route is configured for task {task.value!r}")
        return route


def _spec(task: Task, target: RouteTarget, catalog: Mapping[str, ModelSettings]) -> ModelSpec:
    entry = catalog.get(target.model)
    if entry is None:
        raise ValueError(f"{task.value}: unknown model {target.model!r}")
    try:
        return ModelSpec(
            provider=entry.provider,
            deployment=entry.deployment,
            family=target.model,
            effort=target.effort,
            max_output_tokens=target.max_output_tokens,
            supports_tools=entry.supports_tools,
            thinking_summary=target.thinking_summary,
        )
    except ValueError as exc:
        raise ValueError(f"{task.value}: {exc}") from None


# --------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------


class AISettings(BaseSettings):
    """Settings for ``blogwriter.ai`` (environment prefix ``BW_AI_``; see the module docstring).

    Construction validates the whole routing table, the prices and the brake settings, so a bad
    configuration fails at startup rather than on the first call.
    """

    model_config = SettingsConfigDict(
        env_prefix="BW_AI_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
    )

    openai_base_url: str | None = None
    anthropic_resource: str | None = None

    deployment_sol: str = SOL
    deployment_astra: str = ASTRA
    deployment_luna: str = LUNA
    deployment_opus: str = OPUS
    deployment_embedding: str = EMBEDDING

    image_base_url: str | None = None
    """The separate Foundry resource serving ``gpt-image-2`` (unset: images are off)."""
    deployment_image: str = IMAGE
    image_usd_1536x1024: Decimal = Field(default=DEFAULT_IMAGE_USD["1536x1024"], ge=0)
    image_usd_1024x1024: Decimal = Field(default=DEFAULT_IMAGE_USD["1024x1024"], ge=0)

    model_overrides: dict[str, ModelSettings] = Field(default_factory=dict[str, ModelSettings])
    route_overrides: dict[Task, RouteSettings] = Field(default_factory=dict[Task, RouteSettings])
    price_overrides: dict[str, ModelPrice] = Field(default_factory=dict[str, ModelPrice])
    web_search_usd_per_1k: Decimal = Field(default=DEFAULT_WEB_SEARCH_USD_PER_1K, ge=0)

    allow_private: bool = True
    """``False`` is strict mode: private data is refused for every task."""

    retry_max_attempts: int = Field(default=4, ge=1, le=10)
    retry_base_delay_s: float = Field(default=1.0, ge=0)
    retry_max_delay_s: float = Field(default=30.0, ge=0)
    retry_after_max_s: float = Field(default=60.0, ge=0)

    brake_daily_cap_usd: Decimal = Field(default=Decimal(25), ge=0)
    brake_multiplier: Decimal = Field(default=Decimal(3), ge=0)
    brake_window_days: int = Field(default=7, ge=1, le=90)
    brake_timezone: str = "America/New_York"

    @field_validator("openai_base_url", "image_base_url")
    @classmethod
    def _check_openai_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value.strip())
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.path.rstrip("/") != "/openai/v1"
            or parts.query
            or parts.fragment
            or parts.username is not None
        ):
            raise ValueError(
                "must be an Azure OpenAI v1 endpoint (https://<resource>.openai.azure.com/openai/v1/)"
            )
        return f"https://{parts.netloc}/openai/v1/"

    @field_validator("anthropic_resource")
    @classmethod
    def _check_anthropic_resource(cls, value: str | None) -> str | None:
        return None if value is None else validate_resource_name(value.strip())

    @field_validator(
        "deployment_sol",
        "deployment_astra",
        "deployment_luna",
        "deployment_opus",
        "deployment_embedding",
        "deployment_image",
    )
    @classmethod
    def _check_deployment(cls, value: str) -> str:
        return _check_name(value, "deployment")

    @field_validator("model_overrides", "price_overrides")
    @classmethod
    def _check_model_keys[ValueT](cls, value: dict[str, ValueT]) -> dict[str, ValueT]:
        for name in value:
            _check_name(name, "model name")
        return value

    @field_validator("brake_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {value!r}") from None
        return value

    @model_validator(mode="after")
    def _check_tables(self) -> Self:
        self.retry_policy()
        table = self.routing()
        prices = self.price_table()
        missing = sorted(family for family in table.limits if family not in prices)
        if missing:
            raise ValueError(f"no price configured for model(s) {', '.join(missing)}")
        return self

    @property
    def configured(self) -> bool:
        """Whether both endpoints are set (otherwise no router can be built)."""
        return self.openai_base_url is not None and self.anthropic_resource is not None

    @property
    def images_configured(self) -> bool:
        """Whether the image resource is set (``BW_AI_IMAGE_BASE_URL``): the ``image`` route."""
        return self.image_base_url is not None

    def catalog(self) -> dict[str, ModelSettings]:
        """Every deployed model by name: the defaults with ``model_overrides`` applied."""
        defaults = {
            SOL: ModelSettings(provider="openai", deployment=self.deployment_sol),
            ASTRA: ModelSettings(provider="openai", deployment=self.deployment_astra),
            LUNA: ModelSettings(provider="openai", deployment=self.deployment_luna),
            # Opus 5.5 pay-as-you-go default quota (40 RPM, 40K input and 8K output TPM),
            # one call at a time.
            OPUS: ModelSettings(
                provider="anthropic",
                deployment=self.deployment_opus,
                concurrency=1,
                requests_per_minute=40,
                input_tokens_per_minute=40_000,
                output_tokens_per_minute=8_000,
            ),
            EMBEDDING: ModelSettings(
                provider="openai", deployment=self.deployment_embedding, supports_tools=False
            ),
            # gpt-image-2 on its own resource: one generation at a time.
            IMAGE: ModelSettings(
                provider="openai",
                deployment=self.deployment_image,
                supports_tools=False,
                concurrency=1,
            ),
        }
        return defaults | self.model_overrides

    def routes(self) -> dict[Task, RouteSettings]:
        """Every route by task: the defaults (plus ``image`` when the image resource is set)
        with ``route_overrides`` applied."""
        routes = dict(DEFAULT_ROUTES)
        if self.images_configured:
            routes[Task.IMAGE] = IMAGE_ROUTE
        return routes | self.route_overrides

    def routing(self) -> RoutingTable:
        """The validated routing table (raises ``ValueError`` on a bad table)."""
        catalog = self.catalog()
        routes: dict[Task, Route] = {}
        for task, settings in self.routes().items():
            policy = TaskPolicy(task, settings.max_sensitivity, settings.tools)
            primary = _spec(task, settings.primary, catalog)
            fallback = (
                _spec(task, settings.fallback, catalog) if settings.fallback is not None else None
            )
            routes[task] = Route(task, policy, primary, fallback)
        families = {spec.family for route in routes.values() for spec in route.chain}
        return RoutingTable(routes, {family: catalog[family].limits() for family in families})

    def price_table(self) -> PriceTable:
        """Prices: the defaults, the image prices of ``BW_AI_IMAGE_USD_*``, then
        ``price_overrides``."""
        image = ModelPrice(
            input=Decimal(0),
            image=self.image_usd_1536x1024,
            image_sizes={
                "1536x1024": self.image_usd_1536x1024,
                "1024x1024": self.image_usd_1024x1024,
            },
        )
        return PriceTable(
            dict(DEFAULT_PRICES) | {IMAGE: image} | self.price_overrides,
            web_search_per_1k=self.web_search_usd_per_1k,
        )

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.retry_max_attempts,
            base_delay=self.retry_base_delay_s,
            max_delay=self.retry_max_delay_s,
            max_retry_after=self.retry_after_max_s,
        )

    def sensitivity_policy(self) -> SensitivityPolicy:
        return SensitivityPolicy(allow_private=self.allow_private)

    def brake_zone(self) -> ZoneInfo:
        """The time zone whose days the brake counts."""
        return ZoneInfo(self.brake_timezone)
