"""Fakes for tests in any package: no network, no Azure, no Chromium, no real time.

Example (a route test)::

    services = make_services()
    services.ai.add(Task.SYNTHESIS, ScriptedReply(value=MyOutput(...)))
    app = make_app(services, routers=[kb.get_router()])
    async with asgi_client(app) as client:
        response = await client.put("/api/kb/pillars", json=..., headers={
            **write_headers(make_settings()), "If-None-Match": "*"})

Example (a job step)::

    with services_installed(make_services()) as services:
        await run_job_to_completion(...)   # steps call current_services()

:class:`ScriptedModelRunner` applies the real sensitivity policy
(``blogwriter.ai.policy.SensitivityPolicy``) to every call, so a test that sends anonymized
data to a public-only task, or tools with non-public data, fails the same way production would.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
from collections.abc import AsyncGenerator, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from fastapi import APIRouter, FastAPI
from PIL import Image
from pydantic import BaseModel, JsonValue
from pypdf import PdfWriter

from blogwriter.ai.ledger import Origin
from blogwriter.ai.policy import TASK_CEILINGS, SensitivityPolicy
from blogwriter.ai.router import (
    REVIEW_ONLY_TASKS,
    FallbackReason,
    Finished,
    RouterEvent,
    RouterResult,
    ServedBy,
)
from blogwriter.ai.types import (
    Citation,
    Completion,
    Message,
    Part,
    Refusal,
    Request,
    Task,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallPart,
    Usage,
)
from blogwriter.core.app import create_app
from blogwriter.core.crypto import TokenVault
from blogwriter.core.egress import EgressResponse
from blogwriter.core.settings import CoreSettings
from blogwriter.core.testing import make_settings
from blogwriter.db import MemoryStore, Store
from blogwriter.domain.analytics import (
    AnalyticsImport,
    EvalResult,
    EvalTopic,
    Experiment,
    InsightSuggestion,
)
from blogwriter.domain.articles import Article, ArticleMeta, ArticleVersion
from blogwriter.domain.base import Document, SingletonDocument, sha256_hex
from blogwriter.domain.common import BlobContainer, ClassifierCheck, Finding
from blogwriter.domain.engagement import EngagementItem, EngagementRecord, WatchEntry
from blogwriter.domain.ephemeral import OAuthState, SeedText
from blogwriter.domain.planner import Reminder, WeekPlan
from blogwriter.domain.ports import (
    BlobExists,
    BlobInfo,
    BlobNotFound,
    FetchedPage,
    FetchRefused,
    NotifyResult,
    RenderedCarousel,
    Services,
    Urgency,
    services_installed,
)
from blogwriter.domain.posts import Post
from blogwriter.domain.research import Brief, QuickSearch, ResearchRun
from blogwriter.domain.seeds import KbSeed, VoiceSeed
from blogwriter.domain.settings import BrandKit
from blogwriter.domain.signals import Idea, Signal, Source
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry
from blogwriter.domain.voice import StyleGuide, VoiceDigest, VoiceSample
from blogwriter.jobs import (
    Job,
    enqueue,
    get_job,
    provide_input,
    request_cancel,
    retry_job,
)
from blogwriter.jobs.testing import FakeClock
from blogwriter.notify.payload import NotificationPayload

__all__ = [
    "FakeClassifier",
    "FakeClock",
    "FakeRenderService",
    "FakeServices",
    "FakeSlopChecker",
    "HashEmbedder",
    "MemoryBlobStore",
    "RecordedCall",
    "RecordingNotifier",
    "RecordingSeeder",
    "ScriptedModelRunner",
    "ScriptedReply",
    "StaticHttp",
    "StaticPageFetcher",
    "StoreJobQueue",
    "fake_vault",
    "make_app",
    "make_services",
    "pdf_bytes",
    "png_bytes",
    "sample_document",
    "services_installed",
]

# --------------------------------------------------------------------------------------------
# AI
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScriptedReply:
    """What one scripted model call returns.

    ``value`` (a pydantic model) becomes the structured output and, unless ``text`` is given,
    its JSON is the text. With a request ``output_schema`` and only ``text``, the text is
    validated against the schema. ``error`` is raised instead of answering.
    """

    text: str = ""
    value: BaseModel | None = None
    refused: bool = False
    fallback_reason: FallbackReason | None = None
    review_only: bool = False
    citations: tuple[Citation, ...] = ()
    search_queries: tuple[str, ...] = ()
    search_sources: tuple[str, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    error: BaseException | None = None
    cost_usd: Decimal = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class RecordedCall:
    task: Task
    request: Request
    origin: Origin
    job_id: str | None


class ScriptedModelRunner:
    """A :class:`~blogwriter.domain.ports.ModelRunner` answering from per-task scripts."""

    def __init__(self, *, allow_private: bool = True) -> None:
        self._scripts: dict[Task, list[ScriptedReply]] = {}
        self._policy = SensitivityPolicy(allow_private=allow_private)
        self.calls: list[RecordedCall] = []

    def add(self, task: Task, *replies: ScriptedReply | str | BaseModel) -> None:
        """Queue replies for ``task`` (a string is text, a model is structured output)."""
        queue = self._scripts.setdefault(task, [])
        for reply in replies:
            if isinstance(reply, ScriptedReply):
                queue.append(reply)
            elif isinstance(reply, str):
                queue.append(ScriptedReply(text=reply))
            else:
                queue.append(ScriptedReply(value=reply))

    def calls_for(self, task: Task) -> list[RecordedCall]:
        return [call for call in self.calls if call.task is task]

    def pending(self, task: Task) -> int:
        return len(self._scripts.get(task, []))

    def _next(
        self, task: Task, request: Request, origin: Origin, job_id: str | None
    ) -> RouterResult:
        self._policy.check(TASK_CEILINGS[task], request, ())
        self.calls.append(RecordedCall(task, request, origin, job_id))
        queue = self._scripts.get(task)
        if not queue:
            raise AssertionError(f"unexpected {task.value} call (no scripted reply left)")
        reply = queue.pop(0)
        if reply.error is not None:
            raise reply.error
        return _result(task, request, reply)

    async def run(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> RouterResult:
        await asyncio.sleep(0)
        return self._next(task, request, origin, job_id)

    async def stream(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> AsyncGenerator[RouterEvent]:
        result = self._next(task, request, origin, job_id)
        if result.refused:
            refusal = result.completion.refusal
            if refusal is not None:
                yield refusal
        else:
            for piece in _split(result.text, 3):
                await asyncio.sleep(0)
                yield TextDelta(piece)
        yield Finished(result)


def _split(text: str, parts: int) -> list[str]:
    if not text:
        return []
    size = max(1, math.ceil(len(text) / parts))
    return [text[index : index + size] for index in range(0, len(text), size)]


def _result(task: Task, request: Request, reply: ScriptedReply) -> RouterResult:
    usage = Usage(input=100, output=50)
    review_only = reply.review_only or (
        reply.fallback_reason is not None and task in REVIEW_ONLY_TASKS
    )
    served_by = ServedBy(
        task=task,
        model="fake-model",
        deployment="fake-deployment",
        provider="openai",
        effort=None,
        fallback_reason=reply.fallback_reason,
        review_only=review_only,
    )
    if reply.refused:
        refusal = Refusal("cyber", None)
        completion = Completion(
            text="",
            finish_reason="refusal",
            usage=usage,
            model="fake-model",
            response_id="resp_fake",
            refusal=refusal,
        )
        return RouterResult(
            completion=completion,
            served_by=served_by,
            usage=usage,
            cost_usd=reply.cost_usd,
            refusals=(refusal,),
        )
    text = reply.text
    value = reply.value
    if value is not None and not text:
        text = value.model_dump_json()
    if value is None and request.output_schema is not None and text:
        value = request.output_schema.model_validate_json(text)
    parts: list[Part] = [ToolCallPart(c.call_id, c.name, c.arguments) for c in reply.tool_calls]
    if text.strip():
        parts.append(TextPart(text))
    completion = Completion(
        text=text,
        finish_reason="tool_calls" if reply.tool_calls else "stop",
        usage=usage,
        model="fake-model",
        response_id="resp_fake",
        tool_calls=reply.tool_calls,
        message=Message("assistant", tuple(parts)) if parts else None,
        citations=reply.citations,
        search_queries=reply.search_queries,
        search_sources=reply.search_sources,
    )
    # Like the router: a fallback after a refusal records the refusal it met on the way.
    met: tuple[Refusal, ...] = (
        (Refusal("cyber", None),) if reply.fallback_reason == "refusal" else ()
    )
    return RouterResult(
        completion=completion,
        served_by=served_by,
        usage=usage,
        cost_usd=reply.cost_usd,
        value=value,
        refusals=met,
    )


class HashEmbedder:
    """Deterministic bag-of-words embeddings: texts sharing words are similar."""

    def __init__(self, dimensions: int = 64) -> None:
        self._dimensions = dimensions
        self.calls: list[tuple[str, ...]] = []

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[list[float]]:
        await asyncio.sleep(0)
        self.calls.append(tuple(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in re.findall(r"\w+", text.casefold()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % self._dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class FakeSlopChecker:
    """Returns the findings it was given for any text (empty by default)."""

    def __init__(self, findings: Iterable[Finding] = ()) -> None:
        self.findings = list(findings)
        self.checked: list[str] = []

    def check(self, text: str) -> list[Finding]:
        self.checked.append(text)
        return list(self.findings)


class FakeClassifier:
    """A :class:`~blogwriter.domain.ports.ConfidentialityClassifier` with a fixed verdict.

    Every text passes unless ``flag`` (a substring) occurs in it; ``error`` is raised instead of
    answering (``AiUseNotConfirmed()``, ``ServiceUnavailable("ai")``...).
    """

    def __init__(
        self, *, flag: str | None = None, error: BaseException | None = None, now: str = ""
    ) -> None:
        self.flag = flag
        self.error = error
        self.now = now or SAMPLE_NOW
        self.calls: list[tuple[tuple[str, ...], Origin]] = []

    async def classify(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[ClassifierCheck]:
        await asyncio.sleep(0)
        self.calls.append((tuple(texts), origin))
        if self.error is not None:
            raise self.error
        checks: list[ClassifierCheck] = []
        for text in texts:
            flagged = self.flag is not None and self.flag in text
            findings = (
                [Finding(code="confidential-detail", severity="block", message="Too specific.")]
                if flagged
                else []
            )
            checks.append(
                ClassifierCheck(
                    passed=not flagged,
                    checked_at=self.now,
                    findings=findings,
                    content_sha256=sha256_hex(text),
                )
            )
        return checks


# --------------------------------------------------------------------------------------------
# Blobs, fetches, HTTP
# --------------------------------------------------------------------------------------------


class MemoryBlobStore:
    """An in-memory :class:`~blogwriter.domain.ports.BlobStore`."""

    def __init__(self) -> None:
        self.blobs: dict[tuple[BlobContainer, str], tuple[bytes, str]] = {}

    @staticmethod
    def _check_name(name: str) -> None:
        if not name or name.startswith("/") or ".." in name.split("/"):
            raise ValueError("invalid blob name")

    async def put(
        self,
        container: BlobContainer,
        name: str,
        data: bytes,
        *,
        content_type: str,
        overwrite: bool = False,
    ) -> BlobInfo:
        self._check_name(name)
        await asyncio.sleep(0)
        if not overwrite and (container, name) in self.blobs:
            raise BlobExists(name)
        self.blobs[(container, name)] = (bytes(data), content_type)
        return self._info(container, name)

    def _info(self, container: BlobContainer, name: str) -> BlobInfo:
        data, content_type = self.blobs[(container, name)]
        return BlobInfo(
            container=container,
            name=name,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            etag=f'"{hashlib.sha256(data).hexdigest()[:16]}"',
        )

    async def get(self, container: BlobContainer, name: str) -> bytes:
        await asyncio.sleep(0)
        try:
            return self.blobs[(container, name)][0]
        except KeyError:
            raise BlobNotFound(name) from None

    async def head(self, container: BlobContainer, name: str) -> BlobInfo | None:
        await asyncio.sleep(0)
        if (container, name) not in self.blobs:
            return None
        return self._info(container, name)

    async def delete(self, container: BlobContainer, name: str) -> None:
        await asyncio.sleep(0)
        self.blobs.pop((container, name), None)

    async def list_blobs(self, container: BlobContainer, prefix: str = "") -> list[BlobInfo]:
        await asyncio.sleep(0)
        names = sorted(n for c, n in self.blobs if c == container and n.startswith(prefix))
        return [self._info(container, name) for name in names]


class StaticPageFetcher:
    """Serves fixed passages by URL; enforces ``allowed_urls`` like the real fetcher."""

    def __init__(self, pages: Mapping[str, str] | None = None, *, title: str = "A page") -> None:
        self.pages: dict[str, str] = dict(pages or {})
        self.title = title
        self.fetched: list[str] = []

    async def fetch(self, url: str, *, allowed_urls: Collection[str]) -> FetchedPage:
        await asyncio.sleep(0)
        if url not in allowed_urls:
            raise FetchRefused("url_not_allowed")
        text = self.pages.get(url)
        if text is None:
            raise FetchRefused("http_error")
        self.fetched.append(url)
        return FetchedPage(
            url=url,
            title=self.title,
            text=text,
            via="webiq",
            fetched_at=datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
        )


class StaticHttp:
    """An :class:`~blogwriter.domain.ports.HttpGetter` with canned responses by URL."""

    def __init__(self, responses: Mapping[str, EgressResponse] | None = None) -> None:
        self.responses: dict[str, EgressResponse] = dict(responses or {})
        self.requested: list[str] = []

    def add(
        self, url: str, content: bytes, *, status: int = 200, content_type: str = "text/html"
    ) -> None:
        self.responses[url] = EgressResponse(
            url=url, status=status, content_type=content_type, content=content
        )

    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        await asyncio.sleep(0)
        self.requested.append(url)
        response = self.responses.get(url)
        if response is None:
            return EgressResponse(url=url, status=404, content_type="text/plain", content=b"")
        return response


# --------------------------------------------------------------------------------------------
# Rendering, notifications, jobs, schedules
# --------------------------------------------------------------------------------------------


def png_bytes(width: int = 8, height: int = 8, color: str = "#0E2A24") -> bytes:
    """A real PNG of the given size (Pillow)."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_bytes(pages: int = 1, *, width: float = 1080, height: float = 1350) -> bytes:
    """A real PDF with ``pages`` blank pages of ``width`` x ``height`` points (pypdf)."""
    writer = PdfWriter()
    for _ in range(max(1, pages)):
        writer.add_blank_page(width=width, height=height)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeRenderService:
    """A :class:`~blogwriter.domain.ports.RenderService` returning real tiny PNG/PDF/SVG bytes.

    ``fail_with`` makes every render raise that exception (a :class:`RenderFailed`).
    """

    SVG: bytes = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_with: Exception | None = None
        self.lint_findings: list[Finding] = []

    async def _enter(self, name: str) -> None:
        await asyncio.sleep(0)
        self.calls.append(name)
        if self.fail_with is not None:
            raise self.fail_with

    async def render_carousel(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> RenderedCarousel:
        await self._enter("render_carousel")
        slides = spec.get("slides")
        count = len(cast(list[object], slides)) if isinstance(slides, list) else 1
        pdf = pdf_bytes(count)
        return RenderedCarousel(
            pdf=pdf,
            pages=tuple(png_bytes(108, 135) for _ in range(count)),
            text=tuple("" for _ in range(count)),
            sha256=hashlib.sha256(pdf).hexdigest(),
        )

    async def carousel_preview_html(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> str:
        await self._enter("carousel_preview_html")
        return "<!doctype html><html><body><main>preview</main></body></html>"

    async def render_image_card(
        self, slide: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> bytes:
        await self._enter("render_image_card")
        return png_bytes(108, 135)

    async def render_og_image(
        self,
        *,
        title: str,
        subtitle: str | None,
        pillar_label: str | None,
        brand: BrandKit,
    ) -> bytes:
        await self._enter("render_og_image")
        return png_bytes(1200, 630)

    async def render_mermaid_svg(self, source: str, brand: BrandKit | None = None) -> bytes:
        await self._enter("render_mermaid_svg")
        return self.SVG

    async def render_mermaid_png(
        self, source: str, width: int, brand: BrandKit | None = None
    ) -> bytes:
        await self._enter("render_mermaid_png")
        return png_bytes(width, max(1, width // 2))

    def lint_carousel(self, spec: Mapping[str, object]) -> list[Finding]:
        self.calls.append("lint_carousel")
        return list(self.lint_findings)


class RecordingNotifier:
    """Records payloads. ``deliver=False`` simulates no delivery receipt (veto notice holds)."""

    def __init__(self, *, deliver: bool = True) -> None:
        self.deliver = deliver
        self.sent: list[tuple[NotificationPayload, Urgency]] = []

    async def notify(
        self, payload: NotificationPayload, *, urgency: Urgency = "normal"
    ) -> NotifyResult:
        await asyncio.sleep(0)
        self.sent.append((payload, urgency))
        return NotifyResult(delivered=1) if self.deliver else NotifyResult(failed=1)


class StoreJobQueue:
    """A :class:`~blogwriter.domain.ports.JobQueue` over the real ``blogwriter.jobs.queue``
    functions on a store (nothing runs the jobs; tests run steps themselves).

    With ``registered``, enqueuing any other type raises ``ValueError`` like
    ``JobsService.enqueue`` does for an unregistered type (test the ``job_hook_unregistered``
    path with it).
    """

    def __init__(
        self,
        store: Store,
        *,
        clock: FakeClock | None = None,
        registered: Collection[str] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock if clock is not None else FakeClock()
        self.registered = frozenset(registered) if registered is not None else None
        self.enqueued: list[Job] = []

    async def enqueue(
        self,
        job_type: str,
        input: JsonValue = None,
        *,
        job_id: str | None = None,
        run_after: datetime | None = None,
    ) -> Job:
        if self.registered is not None and job_type not in self.registered:
            raise ValueError(f"job type {job_type!r} is not registered")
        job = await enqueue(
            self._store, job_type, input, job_id=job_id, run_after=run_after, clock=self._clock
        )
        self.enqueued.append(job)
        return job

    def types(self) -> list[str]:
        return [job.type for job in self.enqueued]

    async def get(self, job_id: str) -> Job:
        return await get_job(self._store, job_id)

    async def cancel(self, job_id: str) -> Job:
        return await request_cancel(self._store, job_id, clock=self._clock)

    async def provide_input(self, job_id: str, value: JsonValue, *, key: str | None = None) -> Job:
        return await provide_input(self._store, job_id, value, key=key, clock=self._clock)

    async def retry(self, job_id: str) -> Job:
        return await retry_job(self._store, job_id, clock=self._clock)


class RecordingSeeder:
    def __init__(self) -> None:
        self.reseeds = 0

    async def reseed(self) -> None:
        await asyncio.sleep(0)
        self.reseeds += 1


def fake_vault() -> TokenVault:
    """A vault with a fixed test key (never use outside tests)."""
    return TokenVault({"test": hashlib.sha256(b"blogwriter-test-vault").digest()}, "test")


# --------------------------------------------------------------------------------------------
# The services bundle and an app for route tests
# --------------------------------------------------------------------------------------------


@dataclass
class FakeServices:
    """Every service, faked. Satisfies :class:`~blogwriter.domain.ports.Services`."""

    store: MemoryStore = field(default_factory=MemoryStore)
    clock: FakeClock = field(default_factory=FakeClock)
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)
    egress: StaticHttp = field(default_factory=StaticHttp)
    ai: ScriptedModelRunner | None = field(default_factory=ScriptedModelRunner)
    embedder: HashEmbedder | None = field(default_factory=HashEmbedder)
    blobs: MemoryBlobStore | None = field(default_factory=MemoryBlobStore)
    fetcher: StaticPageFetcher | None = field(default_factory=StaticPageFetcher)
    render: FakeRenderService | None = field(default_factory=FakeRenderService)
    vault: TokenVault | None = field(default_factory=fake_vault)
    slop: FakeSlopChecker | None = field(default_factory=FakeSlopChecker)
    classifier: FakeClassifier | None = field(default_factory=FakeClassifier)
    schedules: RecordingSeeder | None = field(default_factory=RecordingSeeder)
    jobs: StoreJobQueue = field(init=False)

    def __post_init__(self) -> None:
        self.jobs = StoreJobQueue(self.store, clock=self.clock)


def make_services(**overrides: object) -> FakeServices:
    """:class:`FakeServices` with keyword overrides (``ai=None`` simulates "not configured")."""
    services = FakeServices()
    for name, value in overrides.items():
        if not hasattr(services, name):
            raise TypeError(f"FakeServices has no service {name!r}")
        setattr(services, name, value)
    if "store" in overrides or "clock" in overrides:
        services.jobs = StoreJobQueue(services.store, clock=services.clock)
    return services


def make_app(
    services: Services,
    routers: Sequence[APIRouter] = (),
    *,
    settings: CoreSettings | None = None,
) -> FastAPI:
    """The real app (auth, CSRF, problems) with ``routers`` and ``app.state.services`` set.

    ``httpx.ASGITransport`` does not run the lifespan, so the state set here is what routes
    see. Send :func:`blogwriter.core.testing.write_headers` on non-GET requests.
    """
    app = create_app(settings or make_settings(), routers=routers)
    app.state.services = services
    app.state.store = services.store
    return app


# --------------------------------------------------------------------------------------------
# Sample documents
# --------------------------------------------------------------------------------------------

SAMPLE_NOW = "2026-11-26T12:00:00.000000Z"
"""The timestamp sample documents carry (the default :class:`FakeClock` time)."""
_T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
_LATER = "2026-11-28T12:00:00.000000Z"


def _minimal_fields(cls: type[Document]) -> dict[str, object]:
    """The smallest valid field set of each document class (python attribute names)."""
    status = {"status_changed_at": SAMPLE_NOW}
    by_class: dict[type[Document], dict[str, object]] = {
        Experiment: {
            **status,
            "question": "links_vs_none",
            "hypothesis": "Posts without links reach more people.",
            "metric": "impressions",
            "arms": [{"id": "none", "label": "No links"}, {"id": "link", "label": "One link"}],
        },
        InsightSuggestion: {
            **status,
            "week_start": "2026-11-23",
            "dimension": "format",
            "text": "Image cards beat carousels this month.",
        },
        EvalTopic: {"pillar": "agents", "format": "deep-explainer", "topic": "MCP tool scopes"},
        EvalResult: {"topic_id": "evt-0000000000000000000000000000", "run_id": "job-eval-1"},
        AnalyticsImport: {"sha256": sha256_hex("export"), "imported_at": SAMPLE_NOW},
        StoryEntry: {
            **status,
            "origin": "owner",
            "title": "A large identity migration",
            "pillars": ["identity"],
        },
        PovEntry: {
            **status,
            "origin": "quick_capture",
            "pillar": "agents",
            "statement": "Agents need their own identities, not borrowed ones.",
        },
        Interview: {
            **status,
            "mode": "micro",
            "questions": [{"id": "q1", "text": "What is your take?"}],
        },
        VoiceSample: {**status, "source_type": "linkedin_post", "text": "A post I wrote."},
        StyleGuide: {
            "id": StyleGuide.id_for(1),
            "version": 1,
            **status,
            "body": "# Voice\n\nPlain, specific, first-hand.",
        },
        VoiceDigest: {
            "id": VoiceDigest.id_for("2026-11-30"),
            "period_start": SAMPLE_NOW,
            "period_end": _LATER,
        },
        Idea: {**status, "origin": "owner", "title": "Agent identity in MCP servers"},
        WeekPlan: {
            "id": "plan-2026-11-30",
            "week_start": "2026-11-30",
            "timezone": "America/New_York",
            **status,
        },
        ResearchRun: {**status, "topic": "MCP authorization", "pillar": "agents"},
        Brief: {
            "topic": "MCP authorization",
            "pillar": "agents",
            "executive_summary": "What changed and why it matters.",
        },
        QuickSearch: {"query": "MCP authorization spec"},
        Article: {**status},
        ArticleVersion: {
            "id": ArticleVersion.id_for("art-0000000000000000000000000000", 1),
            "article_id": "art-0000000000000000000000000000",
            "version": 1,
            "meta": ArticleMeta(),
            "markdown": "Body.",
            "content_sha256": sha256_hex("body"),
            "source": "autosave",
            "author": "owner",
            "status_at_save": "in_review",
        },
        Post: {**status, "lane": "native", "pillar": "agents", "format": "text"},
        Source: {
            **status,
            "type": "feed",
            "title": "A feed",
            "url": "https://example.com/feed.xml",
        },
        Signal: {
            "id": Signal.id_for(
                "feed-0000000000000000000000000000", sha256_hex("https://example.com/a")
            ),
            "source_id": "feed-0000000000000000000000000000",
            "url_sha256": sha256_hex("https://example.com/a"),
            "seen_at": SAMPLE_NOW,
        },
        Reminder: {
            "id": Reminder.id_for("reply:post-1:15"),
            **status,
            "expires_at": _LATER,
            "reminder_kind": "reply",
            "due_at": SAMPLE_NOW,
            "target_kind": "post",
            "title": "Reply to comments",
            "url": "/today",
            "key": "reply:post-1:15",
        },
        EngagementItem: {
            **status,
            "expires_at": _LATER,
            "item_kind": "reply",
            "pasted_text": "Great post. How do you scope tokens?",
        },
        EngagementRecord: {
            "item_kind": "reply",
            "final_text": "Per tool call, never per agent.",
            "posted_at": SAMPLE_NOW,
        },
        WatchEntry: {"name": "A niche voice"},
        KbSeed: {
            "id": KbSeed.id_for("cloud security"),
            **status,
            "topic": "cloud security",
            "keywords": ["zero trust", "conditional access"],
            "suggested_pillar": "identity",
        },
        VoiceSeed: {
            "id": VoiceSeed.id_for("vp-1"),
            **status,
            "name": "Default voice",
            "text": "I write plainly.",
        },
    }
    return by_class[cls]


def sample_document[D: Document](cls: type[D], **overrides: object) -> D:
    """A minimal valid document of ``cls`` (fixed timestamps), with field overrides.

    Singletons start from their defaults. ``KeyError`` for a class without a sample.
    """
    # Bases are dumped by field name and overrides are keyed by field name too (either spelling
    # is accepted): mixing a camelCase key with its snake_case twin lets the alias win and keeps
    # the other one as a stray extra field.
    if issubclass(cls, SingletonDocument):
        base = cls.default(SAMPLE_NOW).model_dump(by_alias=False)
    elif cls is OAuthState:
        base = OAuthState.issue("sample-state", now=_T0).model_dump(by_alias=False)
    elif cls is SeedText:
        seed = SeedText.issue("Resume text: led a 2,000-app migration.", "cv", now=_T0)
        base = seed.model_dump(by_alias=False)
    else:
        fields = _minimal_fields(cls)
        base = {
            "id": fields.pop("id", None) or cls.new_id(now=_T0),
            "created_at": SAMPLE_NOW,
            "updated_at": SAMPLE_NOW,
            **fields,
        }
    return cls.model_validate({**base, **_by_field_name(cls, overrides)})


def _by_field_name(cls: type[Document], values: Mapping[str, object]) -> dict[str, object]:
    """``values`` with camelCase aliases of ``cls``'s fields renamed to the field names."""
    names = {info.alias: name for name, info in cls.model_fields.items() if info.alias}
    return {names.get(key, key): value for key, value in values.items()}
