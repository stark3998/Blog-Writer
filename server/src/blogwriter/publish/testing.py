"""Test helpers for the publish job (no network, no GitHub, no portfolio, no real time).

- :class:`FakePortfolio`: the portfolio's GitHub Actions API (an ``httpx.MockTransport``
  handler behind the real :class:`~blogwriter.publish.portfolio.PortfolioClient`) and the
  portfolio site itself (an ``HttpGetter`` for ``services.egress``). A dispatched run concludes
  after ``polls_to_complete`` status reads; a successful run **builds the site** from the
  ``published-blogs`` documents stored at that moment, so verification only succeeds once a
  build has deployed the new version (the ``bw-updated-at`` marker, as portfolio PR B renders
  it).
- :func:`make_publish_settings`: :class:`PublishSettings` from arguments only (the process
  environment is ignored), configured for the fakes by default.
- :func:`seed_article`: an article ready to approve or publish (complete metadata, a body with a
  heading, a Mermaid diagram, an image from the media store and a citation, passing gates for its
  exact content, the pinned gated version, the brand kit, the brief and the image blob).
- :func:`make_harness` and :meth:`PublishHarness.run_until`: the real job runner on the fake
  services with the publish job types registered and a sleep that advances the fake clock.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, override
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.articles import get_router as articles_router
from blogwriter.core.egress import EgressResponse
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import PreconditionFailed, Store
from blogwriter.domain.articles import (
    GATE_ORDER,
    PUBLISHED_MARKER_NAME,
    Article,
    ArticleMeta,
    ArticleVersion,
    CalibrationFlags,
    GateReport,
    GateResult,
    GateVerdict,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.common import Citation, MediaRef
from blogwriter.domain.compose import word_stats
from blogwriter.domain.docstore import create_doc, save_singleton, upsert_doc
from blogwriter.domain.ports import services_installed
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import Blocklist, BlocklistTerm, BrandKit
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import (
    FakeServices,
    StaticHttp,
    make_app,
    make_services,
    png_bytes,
)
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.publish.jobs import PublishRuntime, register_jobs
from blogwriter.publish.portfolio import PortfolioClient
from blogwriter.publish.routes import get_router
from blogwriter.publish.settings import PublishSettings
from blogwriter.publish.site import LocalSiteClient

__all__ = [
    "IMAGE_NAME",
    "IMAGE_SRC",
    "SITE",
    "TEST_TOKEN",
    "FakePortfolio",
    "FakeRun",
    "PublishHarness",
    "article_body",
    "make_harness",
    "make_publish_settings",
    "owner_headers",
    "owner_hero",
    "passing_report",
    "publish_app",
    "save_blocklist",
    "seed_article",
]

SITE: Final = "https://site.test"
TEST_TOKEN: Final = "test-only-portfolio-token-0123456789"  # noqa: S105 - a fake for tests
IMAGE_PNG: Final = png_bytes(64, 32, "#335577")
IMAGE_NAME: Final = f"uploads/2026-11/{sha256_hex(IMAGE_PNG)}.png"
IMAGE_SRC: Final = f"/api/media/{IMAGE_NAME}"
_RUN_PATH: Final = re.compile(r"/repos/[^/]+/[^/]+/actions/runs/([0-9]+)$")
_DISPATCH_PATH: Final = re.compile(r"/repos/[^/]+/[^/]+/actions/workflows/([^/]+)/dispatches$")
_RUNS_PATH: Final = re.compile(r"/repos/[^/]+/[^/]+/actions/workflows/([^/]+)/runs$")


# --------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------


class _ArgumentsOnly(PublishSettings):
    """``PublishSettings`` that read only constructor arguments, never the environment."""

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


def make_publish_settings(**overrides: object) -> PublishSettings:
    """Settings for the fakes (``None`` unsets a value); the environment is ignored."""
    values: dict[str, object] = {
        "portfolio_repository": "owner/site",
        "portfolio_ref": "main",
        "portfolio_workflow": "deploy.yml",
        "portfolio_token": TEST_TOKEN,
        "site_origin": SITE,
        "preview_workflow": "preview.yml",
        "poll_seconds": 1.0,
        "build_timeout_seconds": 10.0,
        "verify_timeout_seconds": 10.0,
    }
    values.update(overrides)
    return _ArgumentsOnly.model_validate({k: v for k, v in values.items() if v is not None})


# --------------------------------------------------------------------------------------------
# GitHub and the site
# --------------------------------------------------------------------------------------------


@dataclass
class FakeRun:
    id: int
    workflow: str
    ref: str
    correlation: str
    status: str = "queued"
    conclusion: str | None = None
    status_reads: int = 0


class FakePortfolio(StaticHttp):
    """The GitHub Actions API of the portfolio repository and the portfolio site."""

    def __init__(
        self,
        store: Store,
        *,
        site_origin: str = SITE,
        polls_to_complete: int = 1,
        conclusion: str = "success",
    ) -> None:
        super().__init__()
        self.store = store
        self.site_origin = site_origin
        self.polls_to_complete = polls_to_complete
        self.conclusion = conclusion
        self.runs: dict[int, FakeRun] = {}
        self.calls: list[tuple[str, str]] = []
        """``(method, path)`` of every GitHub request."""
        self.authorizations: set[str] = set()
        self.fail_next: dict[str, list[int]] = {}
        """Per call (``dispatch``, ``runs``, ``run``): statuses answered before the real ones."""
        self.hidden_lookups = 0
        """``find_run`` lists nothing this many times (GitHub creating the run)."""
        self.status_gate: asyncio.Event | None = None
        """When set, every run-status read waits for this event (a build that never answers)."""
        self.deploy_builds = True
        """``False``: builds succeed but never change the site (the old page stays)."""
        self.pages: dict[str, tuple[int, str]] = {}
        self.builds = 0
        self.site_requests: list[str] = []
        self._next_id = 1000

    # -- GitHub --------------------------------------------------------------------------------

    @property
    def dispatches(self) -> list[FakeRun]:
        return sorted(self.runs.values(), key=lambda run: run.id)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client_factory(self) -> Callable[[PublishSettings], PortfolioClient]:
        def build(settings: PublishSettings) -> PortfolioClient:
            return PortfolioClient.from_settings(settings, transport=self.transport())

        return build

    def _injected(self, call: str) -> httpx.Response | None:
        queue = self.fail_next.get(call)
        if queue:
            return httpx.Response(queue.pop(0), json={"message": "injected failure"})
        return None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        path = request.url.path
        self.calls.append((request.method, path))
        self.authorizations.add(request.headers.get("Authorization", ""))
        if request.method == "POST" and (match := _DISPATCH_PATH.fullmatch(path)):
            return self._dispatch(match.group(1), request)
        if request.method == "GET" and (match := _RUNS_PATH.fullmatch(path)):
            return self._list(match.group(1))
        if request.method == "GET" and (match := _RUN_PATH.fullmatch(path)):
            return await self._status(int(match.group(1)))
        return httpx.Response(404, json={"message": "Not Found"})

    def _dispatch(self, workflow: str, request: httpx.Request) -> httpx.Response:
        injected = self._injected("dispatch")
        if injected is not None:
            return injected
        body: Any = json.loads(request.content)
        correlation = str(body["inputs"]["correlation_id"])
        self._next_id += 1
        run = FakeRun(self._next_id, workflow, str(body["ref"]), correlation)
        self.runs[run.id] = run
        return httpx.Response(204)

    def _list(self, workflow: str) -> httpx.Response:
        injected = self._injected("runs")
        if injected is not None:
            return injected
        if self.hidden_lookups > 0:
            self.hidden_lookups -= 1
            return httpx.Response(200, json={"total_count": 0, "workflow_runs": []})
        runs = [
            {
                "id": run.id,
                "name": "Deploy",
                "display_title": f"Publish {run.correlation}",
                "status": run.status,
                "conclusion": run.conclusion,
            }
            for run in sorted(self.runs.values(), key=lambda item: -item.id)
            if run.workflow == workflow
        ]
        return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})

    async def _status(self, run_id: int) -> httpx.Response:
        if self.status_gate is not None:
            await self.status_gate.wait()
        injected = self._injected("run")
        if injected is not None:
            return injected
        run = self.runs.get(run_id)
        if run is None:
            return httpx.Response(404, json={"message": "Not Found"})
        run.status_reads += 1
        if run.status != "completed":
            if run.status_reads >= self.polls_to_complete:
                run.status, run.conclusion = "completed", self.conclusion
                if self.conclusion == "success":
                    await self.build()
            else:
                run.status = "in_progress"
        return httpx.Response(
            200, json={"id": run.id, "status": run.status, "conclusion": run.conclusion}
        )

    # -- the site --------------------------------------------------------------------------------

    async def build(self) -> None:
        """Deploy the site from the current ``published-blogs`` documents."""
        self.builds += 1
        if not self.deploy_builds:
            return
        docs = {doc.id: doc.body for doc in await self.store.find("published-blogs")}
        pages: dict[str, tuple[int, str]] = {}
        for slug, body in docs.items():
            if body.get("status") == "published":
                pages[slug] = (200, _page(body))
            elif body.get("status") == "redirect":
                target = docs.get(str(body.get("redirectTo")))
                if target is not None and target.get("status") == "published":
                    pages[slug] = (200, _page(target))
        self.pages = pages

    def serve(self, slug: str, page: str, status: int = 200) -> None:
        """Put a page on the site directly (an old version, for example)."""
        self.pages[slug] = (status, page)

    @override
    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        await asyncio.sleep(0)
        self.site_requests.append(url)
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        status, body = 404, "Not Found"
        if origin == self.site_origin and parts.path.startswith("/blog/"):
            status, body = self.pages.get(parts.path[len("/blog/") :], (404, "Not Found"))
        return EgressResponse(
            url=url, status=status, content_type="text/html", content=body.encode("utf-8")
        )


def _page(doc: Mapping[str, Any]) -> str:
    marker = html.escape(str(doc.get("updatedAt", "")), quote=True)
    title = html.escape(str(doc.get("title", "")))
    return (
        f'<!doctype html><html><head><meta name="{PUBLISHED_MARKER_NAME}" content="{marker}">'
        f"<title>{title}</title></head><body>{doc.get('htmlContent', '')}</body></html>"
    )


# --------------------------------------------------------------------------------------------
# Articles
# --------------------------------------------------------------------------------------------

READY_META: Final[dict[str, object]] = {
    "title": "Scope every tool call, not every agent",
    "slug": "scope-every-tool-call",
    "excerpt": "Per-call scopes keep a confused agent from spending a whole token.",
    "pillar": "agents",
    "format": "deep-explainer",
    "tags": ["mcp", "identity"],
}


def article_body(*, image_src: str = IMAGE_SRC, extra: str = "") -> str:
    """A canonical-enough body with every published construct (h2/h3, Mermaid, an image from
    the media store, a callout, a citation)."""
    return (
        "Agents act with borrowed authority, and a broad token turns one bad call into many.\n"
        "\n"
        "## Why scope per call\n"
        "\n"
        "A per-call scope limits the blast radius of a confused agent.[^src-owasp]\n"
        "\n"
        "```mermaid\n"
        "flowchart LR\n"
        "  A[Agent] --> B[Gateway]\n"
        "  B --> C[Tool]\n"
        "```\n"
        "\n"
        "### The gateway pattern\n"
        "\n"
        f"![Gateway issuing a scoped token per call]({image_src})\n"
        "\n"
        ":::note\n"
        "Rotate the gateway's signing key on every deploy.\n"
        ":::\n"
        "\n"
        "## What to do next\n"
        "\n"
        f"Start with the tool that can delete data.{extra}\n"
        "\n"
        "[^src-owasp]: OWASP. *LLM Top 10*. [genai.owasp.org](https://genai.owasp.org/llm-top-10/)\n"
    )


def passing_report(article: Article, *, at: str) -> GateReport:
    """A complete gate report that passed on its own for ``article``'s exact content."""
    return GateReport(
        content_sha256=article.compute_sha256(),
        version=max(article.version, 1),
        ran_at=at,
        results=[GateResult(gate=gate, verdict=GateVerdict.PASS, ran_at=at) for gate in GATE_ORDER],
    )


async def seed_article(
    services: FakeServices,
    *,
    status: ArticleStatus = ArticleStatus.IN_REVIEW,
    meta: Mapping[str, object] | None = None,
    body: str | None = None,
    gated_body: str | None = None,
    brand: bool = True,
    blocklist: bool = True,
    **fields: object,
) -> Article:
    """Store an article ready for approval (or in ``status``) plus what publishing reads (the
    brief, the brand kit and a saved, empty blocklist; ``blocklist=False`` leaves it unsaved)."""
    now = services.clock.now()
    stamp = to_iso(now)
    article_meta = ArticleMeta.model_validate({**READY_META, **(meta or {})})
    markdown = body if body is not None else article_body()
    words, minutes = word_stats(markdown)
    article_id = Article.new_id(now=now)
    article = Article(
        id=article_id,
        created_at=stamp,
        updated_at=stamp,
        status=status,
        status_changed_at=stamp,
        meta=article_meta,
        markdown=markdown,
        version=1,
        word_count=words,
        reading_time_minutes=minutes,
        brief_id=f"brief-{article_id}",
    )
    sha = article.compute_sha256()
    gated = gated_body if gated_body is not None else markdown
    article = article.model_copy(
        update={
            "content_sha256": sha,
            "gates": passing_report(article, at=stamp),
            "calibration": CalibrationFlags(gated_sha256=sha, gated_version=1, gates_passed=True),
            **fields,
        }
    )
    article = Article.model_validate(article.model_dump())
    await create_doc(services.store, article)
    await create_doc(
        services.store,
        ArticleVersion(
            id=ArticleVersion.id_for(article.id, 1),
            article_id=article.id,
            version=1,
            meta=article.meta,
            markdown=gated,
            content_sha256=sha,
            source="pipeline",
            author="pipeline",
            status_at_save=ArticleStatus.GATING,
            created_at=stamp,
            updated_at=stamp,
        ),
    )
    brief = Brief(
        id=f"brief-{article.id}",
        created_at=stamp,
        updated_at=stamp,
        topic="Scoped tool calls for agents",
        pillar="agents",
        executive_summary="Per-call scopes.",
        citations=[
            Citation(
                id="src-owasp",
                url="https://genai.owasp.org/llm-top-10/",
                title="OWASP Top 10 for LLM Applications",
                publisher="OWASP",
                date="2025",
                domain="genai.owasp.org",
            )
        ],
    )
    await create_doc(services.store, brief)
    if brand:
        await _save_brand(services.store, stamp)
    if blocklist:
        await save_blocklist(services.store, (), now=stamp, keep_existing=True)
    blobs = services.blobs
    if blobs is not None and (await blobs.head("media", IMAGE_NAME)) is None:
        await blobs.put("media", IMAGE_NAME, IMAGE_PNG, content_type="image/png")
    return article


async def save_blocklist(
    store: Store, terms: Sequence[str], *, now: str, keep_existing: bool = False
) -> None:
    """Save the blocklist (``terms`` as whole-word client names, ids ``t-1``...); with
    ``keep_existing`` a list saved before is left as it is."""
    blocklist = Blocklist(
        id=Blocklist.SINGLETON_ID,
        created_at=now,
        updated_at=now,
        configured=True,
        terms=[
            BlocklistTerm(id=f"t-{index + 1}", term=term, category="client")
            for index, term in enumerate(terms)
        ],
    )
    if keep_existing:
        try:
            await save_singleton(store, blocklist, if_match=None)
        except PreconditionFailed:
            return
        return
    await upsert_doc(store, blocklist)


async def _save_brand(store: Store, stamp: str) -> None:
    kit = BrandKit.default(stamp).model_copy(
        update={"author_name": "Test Author", "site_name": "site.test"}
    )
    try:
        await save_singleton(store, kit, if_match=None)
    except PreconditionFailed:
        return  # an earlier seed saved it


def owner_hero(*, alt: str = "A hand-drawn gateway") -> tuple[MediaRef, bytes]:
    """An owner-uploaded hero image (``media`` container) and its bytes."""
    data = png_bytes(1600, 900, "#884422")
    sha = sha256_hex(data)
    ref = MediaRef(
        role="hero_image",
        container="media",
        name=f"uploads/2026-11/{sha}.png",
        sha256=sha,
        content_type="image/png",
        size_bytes=len(data),
        width=1600,
        height=900,
        alt_text=alt,
    )
    return ref, data


# --------------------------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------------------------


@dataclass
class PublishHarness:
    """Fake services, the fake portfolio and a registry with the publish job types."""

    services: FakeServices
    portfolio: FakePortfolio
    runtime: PublishRuntime
    registry: JobRegistry
    box: list[PublishSettings] = field(default_factory=list[PublishSettings])
    """The settings the steps read (``box[0]``; change them with :meth:`set_settings`)."""
    sleep_hooks: list[Callable[[float], None]] = field(
        default_factory=list[Callable[[float], None]]
    )
    """Called before each fake sleep of a step (raise from one to simulate a crash)."""

    @property
    def settings(self) -> PublishSettings:
        return self.box[0]

    def set_settings(self, settings: PublishSettings) -> None:
        self.box[0] = settings

    async def job(self, job_id: str) -> Job:
        return await self.services.jobs.get(job_id)

    async def run_until(
        self, condition: Callable[[], Awaitable[bool]], *, within: float = 15.0
    ) -> None:
        """Run the real job runner on the fake services until ``condition()`` holds."""
        runner = JobRunner(
            self.services.store,
            self.registry,
            # A long lease: the steps' sleeps advance the fake clock, never real time, and a
            # failed step is retried at once.
            settings=fast_settings(
                lease_seconds=3600.0, retry_base_seconds=0.0, retry_max_seconds=0.0
            ),
            clock=self.services.clock,
        )
        with services_installed(self.services):
            await runner.start()
            try:
                await eventually(condition, within=within, interval=0.005)
            finally:
                await runner.stop()

    async def run_job(self, job_id: str, *, within: float = 15.0) -> Job:
        """Run until ``job_id`` is finished or waiting for input, and return it."""

        async def settled() -> bool:
            job = await self.job(job_id)
            return job.is_terminal or job.status == "waiting_input"

        await self.run_until(settled, within=within)
        return await self.job(job_id)


def make_harness(
    services: FakeServices | None = None,
    *,
    settings: PublishSettings | None = None,
    polls_to_complete: int = 1,
    conclusion: str = "success",
    site: Callable[[PublishSettings], LocalSiteClient] | None = None,
) -> PublishHarness:
    """Fake services with :class:`FakePortfolio` as ``egress`` and the publish jobs registered
    on a fresh registry whose sleep advances the fake clock. ``site`` replaces the client of
    the portfolio in this replica (``BW_PUBLISH_SITE_UPSTREAM``)."""
    fakes = services if services is not None else make_services()
    portfolio = FakePortfolio(
        fakes.store, polls_to_complete=polls_to_complete, conclusion=conclusion
    )
    fakes.egress = portfolio
    box = [settings if settings is not None else make_publish_settings()]
    hooks: list[Callable[[float], None]] = []

    async def sleep(seconds: float) -> None:
        for hook in list(hooks):
            hook(seconds)
        fakes.clock.advance(seconds)
        await asyncio.sleep(0)

    runtime = PublishRuntime(
        settings=lambda: box[0],
        portfolio=portfolio.client_factory(),
        sleep=sleep,
        **({"site": site} if site is not None else {}),
    )
    registry = JobRegistry()
    register_jobs(registry, runtime=runtime)
    return PublishHarness(
        services=fakes,
        portfolio=portfolio,
        runtime=runtime,
        registry=registry,
        box=box,
        sleep_hooks=hooks,
    )


def publish_app(harness: PublishHarness) -> FastAPI:
    """The real app (auth, CSRF, problems) with the articles and publish routers."""
    return make_app(
        harness.services,
        [articles_router(), get_router(settings=lambda: harness.settings)],
        settings=make_settings(),
    )


def owner_headers(etag: str | None = None) -> dict[str, str]:
    """Headers of an owner write from the SPA (plus ``If-Match`` when given)."""
    headers = {**principal_headers(OWNER_OID), **write_headers(make_settings())}
    if etag is not None:
        headers["If-Match"] = etag
    return headers
