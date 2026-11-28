"""Cross-package service interfaces, the services registry and schedule entries.

Packages never import each other's internals. What one package needs from another runtime
service is a :class:`typing.Protocol` here; the implementing package (or the integrator in
``blogwriter.main``) provides the object, and tests use the fakes in
:mod:`blogwriter.domain.testing`.

How code reaches the services:

- **Routes** use the FastAPI dependencies in :mod:`blogwriter.domain.deps`, which read
  ``app.state.services`` (a :class:`Services`).
- **Job steps** (``StepContext`` carries no app) call :func:`current_services`. The integrator
  calls :func:`install_services` at startup; tests use :func:`services_installed`.

The integrator builds ``app.state.services = StateServices(app)``, a live view over these
``app.state`` attributes (each set by the lifecycle named):

==============  =========================================  ========================================
attribute       type                                       set by
==============  =========================================  ========================================
``store``       ``blogwriter.db.Store``                    db (exists)
``jobs``        :class:`JobQueue`                          jobs (``JobsService``, exists)
``ai``          :class:`ModelRunner` | None                main ``AILifecycle`` (``AIRouter``)
``clock``       ``Clock``                                  integrator (``SystemClock``)
``notifier``    :class:`Notifier`                          integrator (web push or a null notifier)
``egress``      :class:`HttpGetter`                        integrator (``SafeHttpClient``)
``embedder``    :class:`Embedder` | None                   integrator (``RouterEmbedder``)
``blobs``       :class:`BlobStore` | None                  storage lifecycle
``fetcher``     :class:`PageFetcher` | None                research lifecycle
``render``      :class:`RenderService` | None              integrator (``render`` adapter)
``vault``       ``TokenVault`` | None                      integrator (``from_settings``)
``slop``        :class:`SlopChecker` | None                gates lifecycle
``classifier``  :class:`ConfidentialityClassifier` | None  stories lifecycle
``schedules``   :class:`ScheduleSeeder` | None             integrator (schedule seeding)
==============  =========================================  ========================================

Startup order matters (contract I-1): every lifecycle that sets one of these runs before the
jobs lifecycle starts the runner, so a job resumed after a deploy never sees a service that is
merely not set yet; ``None`` always means "not configured".
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Callable, Collection, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal, Protocol, cast, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import ImageResult, RouterEvent, RouterResult
from blogwriter.ai.types import ImageRequest, Request, Task
from blogwriter.core.crypto import TokenVault
from blogwriter.core.egress import EgressResponse
from blogwriter.db import Store
from blogwriter.domain.common import BlobContainer, ClassifierCheck, Finding
from blogwriter.domain.settings import BrandKit, CadenceSettings
from blogwriter.jobs.clock import Clock
from blogwriter.jobs.models import Job
from blogwriter.jobs.scheduler import DEFAULT_MAX_LATENESS, DailyRule, IntervalRule, WeeklyRule
from blogwriter.notify.payload import NotificationPayload

__all__ = [
    "SCHEDULE_ID_PATTERN",
    "BlobExists",
    "BlobInfo",
    "BlobNotFound",
    "BlobStore",
    "BlobStoreUnavailable",
    "ConfidentialityClassifier",
    "Embedder",
    "FetchRefused",
    "FetchedPage",
    "HttpGetter",
    "ImageGenerator",
    "JobQueue",
    "ModelRunner",
    "Notifier",
    "NotifyResult",
    "PageFetcher",
    "RenderFailed",
    "RenderService",
    "RenderedCarousel",
    "ScheduleEntry",
    "ScheduleProvider",
    "ScheduleSeeder",
    "ServiceUnavailable",
    "Services",
    "SlopChecker",
    "StateServices",
    "Urgency",
    "current_services",
    "image_generator",
    "install_services",
    "services_installed",
    "uninstall_services",
]


class ServiceUnavailable(RuntimeError):
    """A service is not configured or not running (routes answer 503 ``<name>_unavailable``)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not available")
        self.name = name


# --------------------------------------------------------------------------------------------
# AI
# --------------------------------------------------------------------------------------------


class ModelRunner(Protocol):
    """What packages use of ``blogwriter.ai.AIRouter`` (it satisfies this protocol)."""

    async def run(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> RouterResult: ...

    def stream(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> AsyncGenerator[RouterEvent]: ...


@runtime_checkable
class ImageGenerator(Protocol):
    """Image generation (``Task.IMAGE``: ``gpt-image-2`` on its own Foundry resource).

    ``blogwriter.ai.AIRouter`` satisfies it, so packages reach it through ``services.ai``
    (:func:`image_generator`). ``serves_images`` is false when no image route or resource is
    configured (``BW_AI_IMAGE_BASE_URL`` unset): routes answer 503 ``images_unavailable``.
    Prompts are ``public`` data only (the policy refuses anything else before a call); a
    content filter refusal is a refused :class:`~blogwriter.ai.ImageResult`, never raised.
    """

    @property
    def serves_images(self) -> bool: ...

    async def generate_image(
        self, request: ImageRequest, *, origin: Origin, job_id: str | None = None
    ) -> ImageResult: ...


def image_generator(runner: ModelRunner | None) -> ImageGenerator | None:
    """``services.ai`` as an :class:`ImageGenerator` when it serves images, else ``None``."""
    if isinstance(runner, ImageGenerator) and runner.serves_images:
        return runner
    return None


class Embedder(Protocol):
    """Text embeddings (``Task.EMBED``, ``text-embedding-3-large`` at 1024 dimensions).

    Input is at most ``anonymized`` data. Served by ``blogwriter.services.RouterEmbedder`` over
    ``AIRouter.embed`` when the AI router has a ``Task.EMBED`` route; otherwise
    ``services.embedder`` is ``None`` and callers fail closed (gate 4 reports the novelty check
    as skipped, which blocks auto-publish but not review).
    """

    @property
    def dimensions(self) -> int: ...

    async def embed(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[list[float]]: ...


class SlopChecker(Protocol):
    """The structural slop detectors and AI-tell lint (implemented by gates).

    Synchronous and CPU-bound: run long texts through ``asyncio.to_thread``. Findings use the
    gates' codes (``isnt-x-its-y``, ``rule-of-three``, ``rhetorical-opener``, ``colon-reveal``,
    ``formulaic-closer``, ``one-sentence-run``, ``flat-rhythm``, ``low-specificity``,
    ``ai-tell``) with code-point spans into ``text``.
    """

    def check(self, text: str) -> list[Finding]: ...


class ConfidentialityClassifier(Protocol):
    """The confidentiality classifier and mosaic check (implemented by stories, used by stories,
    voice exemplars and gate 3), so every package decides confidentiality the same way.

    One :class:`~blogwriter.domain.common.ClassifierCheck` per text, in order, with
    ``content_sha256`` set and the local findings (blocklist, e-mail, phone...) merged in.
    ``Task.FACT_CHECK`` at ``Sensitivity.ANONYMIZED``. Raises
    :class:`~blogwriter.domain.confidentiality.AiUseNotConfirmed` before any model call when the
    owner has not confirmed the AI-use policy, :class:`ServiceUnavailable` (``ai``) without a
    model, and lets ``BrakeEngaged`` through for background calls. Callers treat any of these
    as "could not run": never a pass.
    """

    async def classify(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[ClassifierCheck]: ...


# --------------------------------------------------------------------------------------------
# Blob storage
# --------------------------------------------------------------------------------------------


class BlobNotFound(LookupError):
    """No blob with that name."""


class BlobStoreUnavailable(RuntimeError):
    """Blob storage is throttling, failing or unreachable: the operation may succeed later
    (``problems()`` answers 503 ``storage_unavailable``). Implementations raise a subclass."""


class BlobExists(Exception):
    """``put(..., overwrite=False)`` found an existing blob (content-addressed names make this
    harmless: the bytes are the same)."""


@dataclass(frozen=True, slots=True)
class BlobInfo:
    container: BlobContainer
    name: str
    size_bytes: int
    sha256: str
    """Hex digest of the bytes (computed on put, kept as blob metadata ``sha256``)."""
    content_type: str
    etag: str | None = None


class BlobStore(Protocol):
    """Private Blob storage (``azure.storage.blob.aio``, Entra ID). Names never start with
    ``/`` and never contain ``..``."""

    async def put(
        self,
        container: BlobContainer,
        name: str,
        data: bytes,
        *,
        content_type: str,
        overwrite: bool = False,
    ) -> BlobInfo: ...

    async def get(self, container: BlobContainer, name: str) -> bytes: ...

    async def head(self, container: BlobContainer, name: str) -> BlobInfo | None: ...

    async def delete(self, container: BlobContainer, name: str) -> None:
        """Idempotent: deleting a missing blob is not an error."""
        ...

    async def list_blobs(self, container: BlobContainer, prefix: str = "") -> list[BlobInfo]: ...


# --------------------------------------------------------------------------------------------
# Research fetches (Web IQ Browse, or the egress fallback)
# --------------------------------------------------------------------------------------------


class FetchRefused(Exception):
    """A fetch was refused or failed. ``reason``: ``url_not_allowed``, ``blocked``,
    ``too_large``, ``timeout``, ``http_error``, ``unsupported_type``, ``unavailable``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A fetched page's passage text. **In memory only**: never stored, logged or emitted."""

    url: str
    title: str | None
    text: str = field(repr=False)
    via: Literal["webiq", "egress"]
    fetched_at: datetime


class PageFetcher(Protocol):
    """``fetch_page``: only URLs in ``allowed_urls`` (this run's search sources, owner-typed
    URLs, or a brief's citations at gate time), https only, no added query strings."""

    async def fetch(self, url: str, *, allowed_urls: Collection[str]) -> FetchedPage: ...


class HttpGetter(Protocol):
    """The egress helper (``blogwriter.core.egress.SafeHttpClient`` satisfies it)."""

    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse: ...


# --------------------------------------------------------------------------------------------
# Rendering (adapter over blogwriter.render)
# --------------------------------------------------------------------------------------------


class RenderFailed(Exception):
    """A render did not produce output. ``code``: ``brand_incomplete``, ``layout_overflow``,
    ``diagram_labels``, ``mermaid_error``, ``unsafe_svg``, ``output_limit``,
    ``asset_integrity``, ``invalid_spec``, ``timeout``, ``unavailable``. ``detail`` is short
    owner-facing text (no document content)."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RenderedCarousel:
    """``blogwriter.render.RenderedDeck`` in domain terms."""

    pdf: bytes = field(repr=False)
    """The LinkedIn document (one 1080x1350 pt page per slide, metadata exactly Title/Author)."""
    pages: tuple[bytes, ...] = field(repr=False)
    """A PNG preview per page; ``pages[0]`` is the cover."""
    text: tuple[str, ...] = field(repr=False)
    """The PDF text layer per page (what the confidentiality checks scan)."""
    sha256: str
    warnings: tuple[Finding, ...] = ()

    @property
    def cover_png(self) -> bytes:
        return self.pages[0]

    @property
    def page_count(self) -> int:
        return len(self.pages)


class RenderService(Protocol):
    """Isolated Chromium rendering (adapter over ``blogwriter.render``). Concurrency 1; calls
    may take seconds.

    ``spec`` is the carousel slide spec (``blogwriter.render.CarouselSpec`` as JSON, stored
    opaque on the post); ``slide`` one slide of it. ``brand`` is the brand kit; the adapter maps
    it to ``RenderBrand`` (``author_name`` -> ``name_mark``, headshot bytes from Blob) and raises
    :class:`RenderFailed` ``brand_incomplete`` without an author name. ``disclaimer`` is the
    employer policy's short footer. Invalid specs raise :class:`RenderFailed` ``invalid_spec``.
    """

    async def render_carousel(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> RenderedCarousel: ...

    async def carousel_preview_html(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> str: ...

    async def render_image_card(
        self, slide: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> bytes: ...

    async def render_og_image(
        self,
        *,
        title: str,
        subtitle: str | None,
        pillar_label: str | None,
        brand: BrandKit,
    ) -> bytes: ...

    async def render_mermaid_svg(self, source: str, brand: BrandKit | None = None) -> bytes: ...

    async def render_mermaid_png(
        self, source: str, width: int, brand: BrandKit | None = None
    ) -> bytes: ...

    def lint_carousel(self, spec: Mapping[str, object]) -> list[Finding]: ...


# --------------------------------------------------------------------------------------------
# Notifications and jobs
# --------------------------------------------------------------------------------------------

type Urgency = Literal["very-low", "low", "normal", "high"]


@dataclass(frozen=True, slots=True)
class NotifyResult:
    delivered: int = 0
    failed: int = 0
    expired: int = 0

    @property
    def any_delivered(self) -> bool:
        """At least one push service answered 201/202 (the veto notice's receipt)."""
        return self.delivered > 0


class Notifier(Protocol):
    """Web push to every subscribed device: a title and a same-origin link, nothing else."""

    async def notify(
        self, payload: NotificationPayload, *, urgency: Urgency = "normal"
    ) -> NotifyResult: ...


class JobQueue(Protocol):
    """What packages use of ``blogwriter.jobs.JobsService`` (it satisfies this protocol)."""

    async def enqueue(
        self,
        job_type: str,
        input: JsonValue = None,
        *,
        job_id: str | None = None,
        run_after: datetime | None = None,
    ) -> Job: ...

    async def get(self, job_id: str) -> Job: ...

    async def cancel(self, job_id: str) -> Job: ...

    async def provide_input(
        self, job_id: str, value: JsonValue, *, key: str | None = None
    ) -> Job: ...

    async def retry(self, job_id: str) -> Job: ...


# --------------------------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------------------------

SCHEDULE_ID_PATTERN: Final = r"^sched-[a-z0-9]+(?:-[a-z0-9]+)*$"
_SCHEDULE_ID_RE: Final = re.compile(SCHEDULE_ID_PATTERN)


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """One scheduler item a package wants (seeded by the integrator with
    ``JobsService.scheduler.put``). Ids are ``sched-<package>-<name>`` and stable."""

    id: str
    name: str
    job_type: str
    rule: DailyRule | WeeklyRule | IntervalRule
    timezone: str
    job_input: JsonValue = None
    max_lateness: timedelta = DEFAULT_MAX_LATENESS

    def __post_init__(self) -> None:
        if not _SCHEDULE_ID_RE.fullmatch(self.id) or len(self.id) > 100:
            raise ValueError("schedule ids look like sched-<package>-<name>")
        if not self.name or len(self.name) > 200:
            raise ValueError("schedule names have 1-200 characters")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be an IANA zone name") from None
        if self.max_lateness < timedelta(0):
            raise ValueError("max_lateness must not be negative")


type ScheduleProvider = Callable[[CadenceSettings], Sequence[ScheduleEntry]]
"""A package's ``schedule_entries(cadence)``: pure, no I/O, deterministic."""


class ScheduleSeeder(Protocol):
    """Re-puts every package's schedule entries (after the owner changes cadence settings)."""

    async def reseed(self) -> None: ...


# --------------------------------------------------------------------------------------------
# The services registry
# --------------------------------------------------------------------------------------------


class Services(Protocol):
    """Every runtime service a package may use. Optional ones are ``None`` when not configured;
    ``store`` and ``jobs`` raise :class:`ServiceUnavailable` when missing."""

    @property
    def store(self) -> Store: ...
    @property
    def jobs(self) -> JobQueue: ...
    @property
    def clock(self) -> Clock: ...
    @property
    def notifier(self) -> Notifier: ...
    @property
    def egress(self) -> HttpGetter: ...
    @property
    def ai(self) -> ModelRunner | None: ...
    @property
    def embedder(self) -> Embedder | None: ...
    @property
    def blobs(self) -> BlobStore | None: ...
    @property
    def fetcher(self) -> PageFetcher | None: ...
    @property
    def render(self) -> RenderService | None: ...
    @property
    def vault(self) -> TokenVault | None: ...
    @property
    def slop(self) -> SlopChecker | None: ...
    @property
    def classifier(self) -> ConfidentialityClassifier | None: ...
    @property
    def schedules(self) -> ScheduleSeeder | None: ...


class StateServices:
    """:class:`Services` read live from ``app.state`` (attribute table in the module docs)."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def _get(self, name: str) -> object:
        return getattr(self._app.state, name, None)

    def _required(self, name: str) -> object:
        value = self._get(name)
        if value is None:
            raise ServiceUnavailable(name)
        return value

    @property
    def store(self) -> Store:
        return cast(Store, self._required("store"))

    @property
    def jobs(self) -> JobQueue:
        return cast(JobQueue, self._required("jobs"))

    @property
    def clock(self) -> Clock:
        return cast(Clock, self._required("clock"))

    @property
    def notifier(self) -> Notifier:
        return cast(Notifier, self._required("notifier"))

    @property
    def egress(self) -> HttpGetter:
        return cast(HttpGetter, self._required("egress"))

    @property
    def ai(self) -> ModelRunner | None:
        return cast(ModelRunner | None, self._get("ai"))

    @property
    def embedder(self) -> Embedder | None:
        return cast(Embedder | None, self._get("embedder"))

    @property
    def blobs(self) -> BlobStore | None:
        return cast(BlobStore | None, self._get("blobs"))

    @property
    def fetcher(self) -> PageFetcher | None:
        return cast(PageFetcher | None, self._get("fetcher"))

    @property
    def render(self) -> RenderService | None:
        return cast(RenderService | None, self._get("render"))

    @property
    def vault(self) -> TokenVault | None:
        return cast(TokenVault | None, self._get("vault"))

    @property
    def slop(self) -> SlopChecker | None:
        return cast(SlopChecker | None, self._get("slop"))

    @property
    def classifier(self) -> ConfidentialityClassifier | None:
        return cast(ConfidentialityClassifier | None, self._get("classifier"))

    @property
    def schedules(self) -> ScheduleSeeder | None:
        return cast(ScheduleSeeder | None, self._get("schedules"))


_installed: list[Services] = []


def install_services(services: Services) -> None:
    """Make ``services`` what :func:`current_services` returns (integrator, at startup)."""
    _installed[:] = [services]


def uninstall_services() -> None:
    _installed.clear()


def current_services() -> Services:
    """The installed services, for job steps. :class:`ServiceUnavailable` if none."""
    if not _installed:
        raise ServiceUnavailable("services")
    return _installed[0]


@contextmanager
def services_installed(services: Services) -> Generator[Services]:
    """Install ``services`` for the duration of a ``with`` block (tests)."""
    previous = list(_installed)
    install_services(services)
    try:
        yield services
    finally:
        _installed[:] = previous
