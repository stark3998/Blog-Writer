"""The end-to-end harness (contract I-6).

:class:`System` is the whole server on fakes: the real app with **every package router**
(:func:`blogwriter.main.package_routers`), **every job type**
(:func:`blogwriter.main.build_registry`) on a real :class:`~blogwriter.jobs.JobsService` (runner,
scheduler, broker) over the :func:`~blogwriter.domain.testing.make_services` fakes, and a fake
clock. External systems are mocked at the HTTP edge: the portfolio's GitHub Actions API behind
``httpx.MockTransport`` and the site behind the egress fake (:class:`E2EPortfolio`), LinkedIn
behind ``httpx.MockTransport`` (:class:`E2ELinkedIn`). The model is a
:class:`~blogwriter.domain.testing.ScriptedModelRunner` with scripted structured outputs (it
enforces the real sensitivity policy).

Requests go through the HTTP API as the owner (``core.testing`` headers). Time only moves when
a test moves it: :meth:`System.run_until` steps the clock through every delayed job and every
schedule item due on the way, so each one runs at the time it was meant for. Any step error or
quarantine fails the test at once (:meth:`System.idle`, :meth:`System.wait_job`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Collection, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, cast, override
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import RouterResult
from blogwriter.ai.types import Request, Sensitivity, Task
from blogwriter.core.crypto import TokenVault
from blogwriter.core.egress import EgressResponse
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import CONTAINERS, Filter, MemoryStore, NotFound, Store
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import find_docs
from blogwriter.domain.ports import (
    JobQueue,
    ScheduleEntry,
    ScheduleProvider,
    install_services,
    uninstall_services,
)
from blogwriter.domain.posts import HookType, Post
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import (
    FakeClassifier,
    FakeSlopChecker,
    HashEmbedder,
    MemoryBlobStore,
    RecordingNotifier,
    RecordingSeeder,
    ScriptedModelRunner,
    ScriptedReply,
    StaticPageFetcher,
    make_app,
    make_services,
)
from blogwriter.jobs import (
    JOBS_CONTAINER,
    DeltaMessage,
    Job,
    JobEventMessage,
    JobRegistry,
    JobsLifecycle,
    JobsService,
    QuarantineNotice,
    Subscription,
)
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings
from blogwriter.linkedin.posts.derive import HookOut, PostDraftOut
from blogwriter.linkedin.posts.testing import CompliantRenderService, claims_reply
from blogwriter.linkedin.transport.client import (
    INTROSPECT_URL,
    LinkedInClient,
    install_client,
    uninstall_client,
)
from blogwriter.linkedin.transport.testing import (
    IMAGE_URNS,
    POST_URN,
    POSTS_URL,
    FakeLinkedIn,
    make_linkedin_settings,
)
from blogwriter.main import SCHEDULE_PROVIDERS, build_registry, package_routers
from blogwriter.pipeline.testing import (
    ANSWER_TAKE,
    GUIDE_BODY,
    RouterLikeRunner,
    critique,
    draft_body,
    interview_reply,
    outline_reply,
    script_gates,
)
from blogwriter.publish.jobs import PublishRuntime
from blogwriter.publish.testing import SITE, FakePortfolio, make_publish_settings
from blogwriter.render.spec import ListSlide, Slide
from blogwriter.research.claims import citation_id_for
from blogwriter.research.testing import (
    ALLOW_URL,
    OPEN_URL,
    PASSAGE_TEXT,
    page_fetcher,
    script_happy_path,
)
from blogwriter.services.schedules import ScheduleSeeder
from blogwriter.voice.guide import ExemplarPick, GuideDraft, VocabularyItem

T0: Final = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
"""Monday 2026-11-30, 09:00 in New York: the owner starts the week."""
ANSWERED_AT: Final = T0 + timedelta(days=1, hours=1)
"""Tuesday 10:00 in New York: the owner answers the micro-interview."""
REVIEWED_AT: Final = T0 + timedelta(days=2, hours=3)
"""Wednesday 12:00 in New York: the owner reviews and approves the flagship."""
NEXT_MONDAY: Final = T0 + timedelta(days=7)
"""The next Monday 09:00 in New York: the default article slot after the review."""
SETTINGS: Final = make_settings()
READ: Final = principal_headers(OWNER_OID)
WRITE: Final = {**READ, **write_headers(SETTINGS)}
PASSAGE_MARKER: Final = "PASSAGE-MARKER-41c7e9"
"""In every fetched page: no fetched passage may reach the store, a job, the broker or a log."""
PUBLISH_CLICK: ContextVar[bool] = ContextVar("publish_click", default=False)
"""True only inside the owner's ``POST /api/posts/{id}/publish`` request (set by the test)."""

_JOB_SETTINGS: Final = fast_settings(
    # Production lease timing on the fake clock (a crashed runner's jobs are taken over 65 s
    # later); a retried step would wait for the fake clock: the harness fails fast instead.
    concurrency=4,
    broker_queue_size=100_000,
    tick_seconds=3600.0,  # the scheduler ticks when System.run_until moves the clock
)


# --------------------------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------------------------


@dataclass
class E2EServices:
    """:class:`~blogwriter.domain.ports.Services` over the ``make_services()`` fakes, with the
    real jobs service as ``jobs`` (set once it is running)."""

    store: MemoryStore
    clock: FakeClock
    notifier: RecordingNotifier
    egress: E2EPortfolio
    ai: RouterLikeRunner
    embedder: HashEmbedder
    blobs: MemoryBlobStore
    fetcher: StaticPageFetcher
    render: CompliantRenderService
    vault: TokenVault
    slop: FakeSlopChecker
    classifier: FakeClassifier
    schedules: RecordingSeeder
    jobs: JobQueue = field(init=False)


class E2EPortfolio(FakePortfolio):
    """The portfolio's GitHub Actions API and site (:class:`FakePortfolio`), plus canned egress
    answers for other URLs (``add``) and a way to hold a dispatch in flight (crash tests)."""

    def __init__(self, store: Store) -> None:
        super().__init__(store, site_origin=SITE)
        self.hold_dispatch: asyncio.Event | None = None
        self.dispatch_reached = asyncio.Event()

    @override
    async def handle(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle(request)
        if (
            request.method == "POST"
            and request.url.path.endswith("/dispatches")
            and self.hold_dispatch is not None
        ):
            self.dispatch_reached.set()
            await self.hold_dispatch.wait()  # GitHub took it; the answer is still on its way
        return response

    @override
    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        canned = self.responses.get(url)
        if canned is not None:
            await asyncio.sleep(0)
            self.requested.append(url)
            return canned
        return await super().get(url, allow_http=allow_http, headers=headers, max_bytes=max_bytes)

    def deploy_dispatches(self) -> int:
        """Dispatches of the site's deploy workflow (publishes and unpublishes)."""
        return sum(1 for run in self.dispatches if run.workflow == "deploy.yml")


@dataclass(frozen=True, slots=True)
class CreateSeen:
    """What LinkedIn's ``POST /rest/posts`` found when it arrived."""

    in_owner_click: bool
    post_id: str | None
    status: str | None
    intent_status: str | None


class E2ELinkedIn:
    """LinkedIn behind ``httpx.MockTransport``: :class:`FakeLinkedIn` answers; this wrapper
    records what the store held when a ``POST /rest/posts`` arrived (the intent must be written
    first, by the owner's click) and can hold an upload ``PUT`` in flight (crash tests)."""

    def __init__(self, store: Store) -> None:
        self.fake = FakeLinkedIn()
        self.store = store
        self.creates: list[CreateSeen] = []
        self.hold_put: asyncio.Event | None = None
        self.put_reached = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == POSTS_URL:
            self.creates.append(await self._seen())
        response = self.fake(request)
        if request.method == "PUT" and self.hold_put is not None:
            self.put_reached.set()
            await self.hold_put.wait()  # LinkedIn has the bytes; the answer is on its way
        return response

    async def _seen(self) -> CreateSeen:
        publishing = await find_docs(
            self.store, Post, [Filter("status", "==", PostStatus.PUBLISHING.value)]
        )
        post = publishing[0].doc if len(publishing) == 1 else None
        return CreateSeen(
            in_owner_click=PUBLISH_CLICK.get(),
            post_id=post.id if post is not None else None,
            status=post.status.value if post is not None else None,
            intent_status=post.intent.status if post is not None and post.intent else None,
        )

    def calls(self, method: str, url: str) -> list[httpx.Request]:
        return self.fake.calls(method, url)


class RefusingModel(ScriptedModelRunner):
    """A scripted model whose unscripted calls are refused (the sensitivity policy still
    applies to every call)."""

    @override
    async def run(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> RouterResult:
        if self.pending(task) == 0:
            self.add(task, ScriptedReply(refused=True))
        return await super().run(task, request, origin=origin, job_id=job_id)


class _StepErrors(logging.Handler):
    """Keeps the frames of every ``job_step_error`` (for fail-fast messages)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.frames: dict[str, object] = {}

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == "job_step_error":
            job_id = getattr(record, "job_id", None)
            if isinstance(job_id, str):
                self.frames[job_id] = getattr(record, "error_frames", None)


# --------------------------------------------------------------------------------------------
# The system
# --------------------------------------------------------------------------------------------


class System:
    """The server on fakes (see the module docs). Build it with :func:`start_system`."""

    def __init__(
        self,
        services: E2EServices,
        model: ScriptedModelRunner,
        linkedin: E2ELinkedIn,
        registry: JobRegistry,
    ) -> None:
        self.services = services
        self.model = model
        self.linkedin = linkedin
        self.registry = registry
        self.app: FastAPI = make_app(
            services, routers=package_routers(publish=make_publish_settings), settings=SETTINGS
        )
        self.app.state.linkedin_settings = make_linkedin_settings()
        self.http = asgi_client(self.app)
        self.quarantined: list[QuarantineNotice] = []
        self.messages: list[JobEventMessage | DeltaMessage] = []
        self._drain: asyncio.Task[None] | None = None
        self._lifecycle: JobsLifecycle | None = None
        self.step_errors = _StepErrors()
        self.boots = 0

    # -- shortcuts -------------------------------------------------------------------------------

    @property
    def store(self) -> MemoryStore:
        return self.services.store

    @property
    def clock(self) -> FakeClock:
        return self.services.clock

    @property
    def portfolio(self) -> E2EPortfolio:
        return self.services.egress

    @property
    def jobs(self) -> JobsService:
        service: object = getattr(self.app.state, "jobs", None)
        assert isinstance(service, JobsService)
        return service

    # -- lifecycle -------------------------------------------------------------------------------

    async def boot(self) -> None:
        """Start a jobs service (a new claim owner: a fresh process)."""

        async def on_quarantine(notice: QuarantineNotice) -> None:
            await asyncio.sleep(0)
            self.quarantined.append(notice)

        lifecycle = JobsLifecycle(
            self.registry,
            settings=_JOB_SETTINGS,
            clock=self.clock,
            on_quarantine=on_quarantine,
            stop_requested=None,
        )
        await lifecycle.startup(self.app)
        self._lifecycle = lifecycle
        self.services.jobs = self.jobs
        self.boots += 1
        subscription = self.jobs.broker.subscribe()
        self._drain = asyncio.create_task(self._collect(subscription), name="e2e-broker-drain")

    async def _collect(self, subscription: Subscription) -> None:
        async with subscription:
            while True:
                message = await subscription.get()
                if message is None:
                    return
                self.messages.append(message)

    async def stop(self) -> None:
        """Graceful shutdown (running jobs are released)."""
        if self._lifecycle is not None:
            await self._lifecycle.shutdown(self.app)
            self._lifecycle = None
        await self._stop_drain()

    async def crash(self) -> None:
        """Kill the process: the runner and the scheduler stop without writing anything, so a
        step in flight is cut off where it stands and its lease simply lapses."""
        service = self.jobs
        await service.scheduler.stop()
        await service.runner.stop(release=False)
        service.broker.close()
        self._lifecycle = None
        await self._stop_drain()

    async def _stop_drain(self) -> None:
        if self._drain is not None:
            self._drain.cancel()
            await asyncio.gather(self._drain, return_exceptions=True)
            self._drain = None

    # -- HTTP as the owner ---------------------------------------------------------------------

    async def get(self, path: str, **params: str) -> httpx.Response:
        return await self.http.get(path, params=params or None, headers=READ)

    async def send(
        self,
        method: str,
        path: str,
        body: JsonValue = None,
        *,
        etag: str | None = None,
        create: bool = False,
    ) -> httpx.Response:
        headers = dict(WRITE)
        if etag is not None:
            headers["If-Match"] = etag
        if create:
            headers["If-None-Match"] = "*"
        if body is not None:
            headers["Content-Type"] = "application/json"
        content = json.dumps(body).encode() if body is not None else None
        return await self.http.request(method, path, content=content, headers=headers)

    async def ok(
        self,
        method: str,
        path: str,
        body: JsonValue = None,
        *,
        etag: str | None = None,
        create: bool = False,
        status: int | Collection[int] = (200, 201, 202, 204),
    ) -> dict[str, JsonValue]:
        """A request that must succeed; its JSON body (``{}`` for none)."""
        if method == "GET":
            response = await self.get(path)
        else:
            response = await self.send(method, path, body, etag=etag, create=create)
        wanted = {status} if isinstance(status, int) else set(status)
        assert response.status_code in wanted, (
            f"{method} {path}: {response.status_code} {response.text[:2000]}"
        )
        if not response.content or response.headers.get("content-type", "").startswith("text/"):
            return {}
        value: JsonValue = response.json()
        assert isinstance(value, dict)
        return value

    async def item(self, path: str) -> tuple[dict[str, JsonValue], str]:
        """``GET`` a ``Versioned[...]`` resource: ``(item, etag)``."""
        body = await self.ok("GET", path)
        item, etag = body["item"], body["etag"]
        assert isinstance(item, dict)
        assert isinstance(etag, str)
        return item, etag

    # -- jobs --------------------------------------------------------------------------------------

    async def job(self, job_id: str) -> Job:
        return await self.jobs.get(job_id)

    async def all_jobs(self) -> list[Job]:
        docs = await self.store.find(JOBS_CONTAINER, [Filter("kind", "==", "job")])
        return [Job.from_body(doc.body) for doc in docs]

    def _check(self, jobs: list[Job]) -> None:
        """Fail fast on a step error (a retry the fake clock would never reach) or a
        quarantine: the happy paths here never need either."""
        for job in jobs:
            error = job.error
            if job.status == "queued" and error is not None and error.code == "step_error":
                frames = self.step_errors.frames.get(job.id)
                raise AssertionError(
                    f"{job.type} {job.id}: step {error.step} raised {error.error_type} {frames}"
                )
            if job.status == "quarantined":
                raise AssertionError(f"{job.type} {job.id} was quarantined: {job.error}")
        assert self.quarantined == [], self.quarantined

    async def idle(self, *, within: float = 30.0) -> None:
        """Wait until nothing runs and nothing is due (every job finished, waiting for the
        owner, or delayed to a later time)."""

        async def settled() -> bool:
            jobs = await self.all_jobs()
            self._check(jobs)
            now = to_iso(self.clock.now())
            busy = [
                job
                for job in jobs
                if job.status == "running" or (job.status == "queued" and job.run_after <= now)
            ]
            return not busy and not self.jobs.runner.active_job_ids

        await eventually(settled, within=within)
        await eventually(settled, within=within)  # twice: a step may have just enqueued work

    async def wait_job(
        self,
        job_id: str,
        statuses: Collection[str] = ("succeeded",),
        *,
        key: str | None = None,
        within: float = 30.0,
    ) -> Job:
        """The job once its status is in ``statuses`` (and, with ``key``, once it waits for
        that input). Fails fast on step errors."""
        found: list[Job] = []

        async def reached() -> bool:
            try:
                job = await self.job(job_id)
            except NotFound:
                return False
            self._check([job])
            if job.status not in statuses:
                if job.is_terminal:
                    raise AssertionError(f"{job.type} {job.id} ended {job.status}: {job.error}")
                return False
            if key is not None and (job.awaiting is None or job.awaiting.key != key):
                return False
            found.append(job)
            return True

        await eventually(reached, within=within)
        return found[-1]

    async def run_until(self, moment: datetime, *, within: float = 30.0) -> None:
        """Let time pass until ``moment``: the clock stops at every delayed job and every
        schedule item due on the way (the scheduler ticks there), so each one runs when it was
        meant to (never late), then at ``moment``."""
        await self.idle(within=within)
        end = to_iso(moment)
        while True:
            now = to_iso(self.clock.now())
            due = sorted(
                [
                    job.run_after
                    for job in await self.all_jobs()
                    if job.status == "queued" and now < job.run_after <= end
                ]
                + [
                    item.next_run_at
                    for item in await self.jobs.scheduler.list_items()
                    if item.status == "active" and now < item.next_run_at <= end
                ]
            )
            if not due:
                break
            self.clock.set(parse_iso(due[0]))
            await self.tick()
        if self.clock.now() < moment:
            self.clock.set(moment)
        await self.tick()

    async def tick(self, *, within: float = 30.0) -> None:
        """One scheduler tick at the current time, then wait until the work it started (and
        any delayed job now due) is done."""
        await self.jobs.scheduler.tick()
        self.jobs.runner.wake()
        await self.idle(within=within)

    async def run_for(self, delta: timedelta, *, within: float = 30.0) -> None:
        await self.run_until(self.clock.now() + delta, within=within)

    # -- evidence ----------------------------------------------------------------------------------

    async def store_dump(self, *, exclude_kinds: Collection[str] = ("blocklist",)) -> str:
        """Every stored document of every container (job documents and their events
        included), as one JSON string. The blocklist itself is left out: it is the one place
        its terms live."""
        bodies: list[object] = []
        for container in CONTAINERS:
            bodies.extend(
                doc.body
                for doc in await self.store.find(container, [])
                if doc.body.get("kind") not in exclude_kinds
            )
        return json.dumps(bodies, ensure_ascii=False)

    def blob_dump(self) -> bytes:
        return b"".join(data for data, _ in self.services.blobs.blobs.values())

    def broker_dump(self) -> str:
        """Every job event and token delta the broker carried (what SSE clients saw)."""
        return json.dumps(
            [message.payload() for message in self.messages], ensure_ascii=False, default=str
        )

    def model_requests(self) -> list[tuple[str, str]]:
        """``(task, every text of the request)`` per model call."""
        calls: list[tuple[str, str]] = []
        for call in self.model.calls:
            request = call.request
            texts = [request.system or ""]
            texts.extend(message.text for message in request.messages)
            calls.append((call.task.value, "\n".join(texts)))
        return calls

    def pushes(self) -> list[str]:
        return [payload.title for payload, _ in self.services.notifier.sent]


@contextlib.asynccontextmanager
async def start_system(
    *, fetched: Mapping[str, str] | None = None, refuse_unscripted: bool = False
) -> AsyncGenerator[System]:
    """The running system at :data:`T0` (see :class:`System`). ``refuse_unscripted``: a model
    call nobody scripted is refused (as a provider would) instead of failing the test."""
    fakes = make_services(clock=FakeClock(T0))
    model = RefusingModel() if refuse_unscripted else ScriptedModelRunner()
    portfolio = E2EPortfolio(fakes.store)
    linkedin = E2ELinkedIn(fakes.store)
    pages = {
        ALLOW_URL: f"{PASSAGE_TEXT} {PASSAGE_MARKER}",
        OPEN_URL: f"{PASSAGE_TEXT} {PASSAGE_MARKER}",
    }
    fetcher = page_fetcher(dict(fetched) if fetched is not None else pages)
    services = E2EServices(
        store=fakes.store,
        clock=fakes.clock,
        notifier=fakes.notifier,
        egress=portfolio,
        ai=RouterLikeRunner(model),
        embedder=HashEmbedder(),
        blobs=MemoryBlobStore(),
        fetcher=fetcher,
        render=CompliantRenderService(),
        vault=_vault(fakes.vault),
        slop=FakeSlopChecker(),
        classifier=FakeClassifier(now=to_iso(T0)),
        schedules=RecordingSeeder(),
    )

    async def sleep(seconds: float) -> None:
        services.clock.advance(seconds)
        await asyncio.sleep(0)

    runtime = PublishRuntime(
        settings=make_publish_settings, portfolio=portfolio.client_factory(), sleep=sleep
    )
    registry = build_registry(publish_runtime=runtime)
    system = System(services, model, linkedin, registry)
    # The cited page answers the link check (gate 5); its text must never be stored either.
    portfolio.add(ALLOW_URL, f"<html><body><p>{PASSAGE_MARKER}</p></body></html>".encode())
    client = LinkedInClient(make_linkedin_settings(), transport=httpx.MockTransport(linkedin))
    errors = system.step_errors
    logging.getLogger("blogwriter").addHandler(errors)
    install_client(client)
    install_services(services)
    try:
        await system.boot()
        async with system.http:
            yield system
    finally:
        await system.stop()
        uninstall_services()
        uninstall_client()
        await client.aclose()
        logging.getLogger("blogwriter").removeHandler(errors)


def _vault(vault: TokenVault | None) -> TokenVault:
    assert vault is not None
    return vault


@pytest.fixture(autouse=True)
def _clean_module_state() -> Iterator[None]:
    """No installed services, LinkedIn client or cached settings leak between tests."""
    from blogwriter.articles import get_settings as articles_settings
    from blogwriter.gates import gates_settings
    from blogwriter.pipeline import pipeline_settings

    for clear in (pipeline_settings.cache_clear, gates_settings.cache_clear):
        clear()
    articles_settings.cache_clear()
    uninstall_services()
    yield
    uninstall_services()
    uninstall_client()
    for clear in (pipeline_settings.cache_clear, gates_settings.cache_clear):
        clear()
    articles_settings.cache_clear()


def query_param(url: str, name: str) -> str:
    values = parse_qs(urlsplit(url).query).get(name, [])
    assert len(values) == 1, url
    return values[0]


# --------------------------------------------------------------------------------------------
# The owner's world and the flagship chain (shared by the test modules)
# --------------------------------------------------------------------------------------------

BLOCKED: Final = "Fabrikamco"
"""A blocklisted client name: it never reaches a model, a job, the broker or a log."""
PRIVATE_MARKER: Final = "zq7-pager-rotation-incident"
"""Only in the owner's raw interview dictation: it may reach the anonymizer and nothing else."""
TAKE_RAW: Final = (
    f"{ANSWER_TAKE} We learned it during the {PRIVATE_MARKER} at {BLOCKED}, when one borrowed "
    "token opened every mailbox."
)
MONDAY: Final = "List every tool call your agents make before you touch a single scope."
MONDAY_RAW: Final = f"{MONDAY} That list is what saved us after the {PRIVATE_MARKER}."
CITED: Final = citation_id_for(ALLOW_URL)
STORY: Final[dict[str, JsonValue]] = {
    "title": "Agent rollout on a shared service principal",
    "problem": "Twelve internal agents shared one service principal with broad Graph scopes.",
    "actions": "Split them into per-agent identities and scoped tokens per tool call.",
    "decisions": "Accepted a two-week delay to rebuild consent instead of patching scopes.",
    "metrics": [{"label": "Agents migrated", "value": "12 in 3 weeks"}],
    "lessons": "Inventory every tool call before you design the identity model.",
    "pillars": ["agents"],
    "confidentiality": "anonymized",
}
POV: Final[dict[str, JsonValue]] = {
    "pillar": "agents",
    "statement": "An agent that borrows a human's token is an incident waiting to happen.",
    "confidentiality": "anonymized",
}


@dataclass(frozen=True, slots=True)
class Owner:
    """What :func:`prepare_owner` saved."""

    story_id: str
    pov_id: str
    guide_id: str


@dataclass(frozen=True, slots=True)
class Flagship:
    """A flagship the pipeline brought to review."""

    article_id: str
    idea_id: str
    pipeline_job_id: str
    interview_id: str


def revised_body(*, citation_id: str = CITED) -> str:
    """The reviser's answer: the draft with the first-hand detail the critique asked for."""
    return f"""# Scope agent tokens per tool call

Twelve agents ran on one service principal until the day a prompt injection used it. We \
accepted a two-week delay to rebuild consent instead of patching scopes. Agent identities now \
exist as their own directory objects.[^{citation_id}] The author put it plainly: \
"{ANSWER_TAKE}"

## One principal, twelve agents

A shared principal means every agent carries every scope. Conditional Access applies to agent \
identities, so you can finally write policy per agent.[^{citation_id}]

```mermaid
flowchart LR
  Agent --> Blueprint --> Tool
```

## Scope the token to the call

Issue a token per tool call and let it expire with the call. {MONDAY}

[^{citation_id}]: Somebody. *Invented title*. [example](https://example.com/fake)
"""


async def prepare_owner(system: System) -> Owner:
    """The owner's settings and material, through the API: the blocklist, the employer policy
    (AI use confirmed), the brand kit, a Voice & Style Guide built from past posts and
    activated, a story and a POV (classified on save)."""
    await system.ok(
        "PUT",
        "/api/kb/blocklist",
        {"terms": [{"term": BLOCKED, "category": "client"}]},
        create=True,
    )
    await system.ok(
        "PUT",
        "/api/kb/policy",
        {
            "disclaimerShort": "Views are my own.",
            "disclaimerLong": None,
            "forbiddenTopics": [],
            "noEmployerBranding": True,
            "aiUseConfirmed": True,
            "clearanceStatement": "I checked this piece against my employer's policy.",
        },
        create=True,
    )
    brand = (await system.ok("GET", "/api/kb/brand"))["item"]
    assert isinstance(brand, dict)
    await system.ok(
        "PUT",
        "/api/kb/brand",
        {
            "authorName": "A. Writer",
            "authorHeadline": "Identity and AI security",
            "siteName": "site.test",
            "colors": brand["colors"],
            "font": "inter",
            "headshot": None,
            "nameMark": None,
            "artBriefs": {},
        },
        create=True,
    )
    guide = await _activate_guide(system)
    story = await system.ok("POST", "/api/stories", STORY, status=201)
    pov = await system.ok("POST", "/api/povs", POV, status=201)
    return Owner(story_id=_item_id(story), pov_id=_item_id(pov), guide_id=guide)


VOICE_SAMPLES: Final = (
    "We moved twelve agents off one shared principal. The hard part was not the code. It was "
    "the inventory of every tool call each agent made, which nobody had written down.",
    "A borrowed human token is the most common agent mistake I see. It works in the demo and "
    "fails the audit. Give the agent its own identity on day one.",
    "Conditional Access for agents is finally real. Start with the agent that can delete data, "
    "write its policy first, and measure what breaks before you widen it.",
    "I stopped estimating identity migrations by app count. I sort by the oddest dependency "
    "first. The easy apps never set the date; the strange ones always do.",
    "Least privilege for agents means least privilege per call, not per agent. Scope the token "
    "to the tool call and let it expire with the call. Then audit the gaps.",
)


async def _activate_guide(system: System) -> str:
    """The Voice & Style Guide, as the owner builds it: five past LinkedIn posts,
    ``voice.build-guide`` (scores, the classifier over exemplar candidates, SYNTHESIS), then the
    owner activates the proposed version."""
    samples: list[str] = []
    for text in VOICE_SAMPLES:
        made = await system.ok(
            "POST",
            "/api/voice/samples",
            {"sourceType": "linkedin_post", "text": text, "ownerUnassisted": True},
            status=201,
        )
        samples.append(_item_id(made))
    system.model.add(
        Task.SYNTHESIS,
        GuideDraft(
            body=GUIDE_BODY,
            prefer=["Lead with the decision."],
            avoid=["Hype words."],
            banned_phrases=["game-changer"],
            vocabulary=[VocabularyItem(term="per call", note="Scope tokens per tool call.")],
            exemplars=[ExemplarPick(sample_id=samples[-1], excerpt=VOICE_SAMPLES[-1])],
        ),
    )
    built = await system.ok("POST", "/api/voice/guide/build", status=202)
    await system.wait_job(str(built["jobId"]))
    await system.idle()
    view = await system.ok("GET", "/api/voice/guide")
    versions = view["versions"]
    assert isinstance(versions, list)
    (proposed,) = versions
    guide_id = dig(proposed, "id")
    assert isinstance(guide_id, str)
    assert dig(proposed, "status") == "proposed"
    _, etag = await system.item(f"/api/voice/guide/{guide_id}")
    active = await system.ok("POST", f"/api/voice/guide/{guide_id}/activate", etag=etag)
    assert dig(active, "item", "status") == "active"
    return guide_id


def _item_id(body: Mapping[str, JsonValue]) -> str:
    item = body["item"]
    assert isinstance(item, dict)
    value = item["id"]
    assert isinstance(value, str)
    return value


def script_flagship(system: System, owner: Owner) -> None:
    """Every model reply of one flagship run: research, a micro-interview (two anonymizer
    calls for the owner's dictation), outline, draft, a failed critique, a revision, a passing
    critique and the gates."""
    model = system.model
    script_happy_path(model)
    model.add(Task.INTERVIEW, interview_reply(2))
    model.add(Task.ANONYMIZE, ANSWER_TAKE, MONDAY)
    model.add(
        Task.OUTLINE,
        outline_reply(story_ids=[owner.story_id], pov_ids=[owner.pov_id], citation_id=CITED),
    )
    model.add(Task.DRAFT, draft_body(citation_id=CITED))
    model.add(Task.CRITIQUE, critique(passing=False))
    model.add(Task.REVISE, revised_body())
    model.add(Task.CRITIQUE, critique(passing=True))
    script_gates(model)


async def draft_flagship(system: System, owner: Owner) -> Flagship:
    """idea -> research -> micro-interview (waiting_input, then the owner's confirmed answers)
    -> outline -> draft -> critique -> revise -> gates -> in_review, as the owner."""
    script_flagship(system, owner)
    idea = await system.ok(
        "POST",
        "/api/ideas",
        {"title": "Agent identities in Entra", "pillar": "agents"},
        status=201,
    )
    idea_id = _item_id(idea)
    etag = idea["etag"]
    assert isinstance(etag, str)
    await system.ok("POST", f"/api/ideas/{idea_id}/transition", {"to": "accepted"}, etag=etag)
    created = await system.ok(
        "POST",
        "/api/articles",
        {
            "ideaId": idea_id,
            "meta": {
                "title": "Scope agent tokens per tool call",
                "pillar": "agents",
                "format": "deep-explainer",
            },
        },
        status=201,
    )
    article_id = _item_id(created)
    etag = created["etag"]
    assert isinstance(etag, str)
    started = await system.ok(
        "POST",
        f"/api/articles/{article_id}/pipeline",
        {"startFrom": "research"},
        etag=etag,
        status=202,
    )
    job_id = started["jobId"]
    assert isinstance(job_id, str)
    await system.wait_job(job_id, {"waiting_input"}, key="interview")
    await system.idle()
    await system.run_until(ANSWERED_AT)

    # The micro-interview: the owner dictates, redacts through stories intake, confirms.
    listed = await system.ok("GET", f"/api/interviews?articleId={article_id}")
    items = listed["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    interview_id = _item_id(cast(Mapping[str, JsonValue], items[0]))
    interview, interview_etag = await system.item(f"/api/interviews/{interview_id}")
    questions = interview["questions"]
    assert isinstance(questions, list)
    assert len(questions) == 2
    answers: list[JsonValue] = []
    for question, raw in zip(questions, (TAKE_RAW, MONDAY_RAW), strict=True):
        assert isinstance(question, dict)
        proposal = await system.ok(
            "POST",
            "/api/stories/intake/redact",
            {"text": raw, "purpose": "interview_answer", "pillar": "agents"},
        )
        confirmed = proposal["proposal"]
        assert isinstance(confirmed, str)
        findings = proposal["findings"]
        assert isinstance(findings, list)
        check = await system.ok("POST", "/api/stories/intake/check", {"text": confirmed})
        assert check["ok"] is True, check
        answers.append(
            {"questionId": question["id"], "text": confirmed, "redactionFindings": len(findings)}
        )
    await system.ok(
        "POST",
        f"/api/interviews/{interview_id}/answers",
        {"answers": answers},
        etag=interview_etag,
    )
    await system.wait_job(job_id)
    await system.idle()
    return Flagship(
        article_id=article_id, idea_id=idea_id, pipeline_job_id=job_id, interview_id=interview_id
    )


@dataclass(frozen=True, slots=True)
class Published:
    """A flagship the owner approved and the publish job brought live."""

    article_id: str
    slug: str
    publish_job_id: str
    staging_job_id: str
    publish_at: datetime


def script_gates_again(system: System) -> None:
    """The gates' model replies for one more run (fact check, rubric)."""
    script_gates(system.model)


async def review_and_publish(system: System, flagship: Flagship) -> Published:
    """The owner's review: attest the clearance (an anonymized story is in it), re-run the
    gates on the unchanged content, preview on staging, approve; then the publish job at its
    slot (upsert, dispatch, build, verification by the version marker)."""
    path = f"/api/articles/{flagship.article_id}"
    await system.run_until(max(REVIEWED_AT, system.clock.now()))
    _, etag = await system.item(path)
    attested = await system.ok("POST", f"{path}/clearance", {}, etag=etag)
    assert attested["item"] is not None
    script_gates_again(system)
    rerun = await system.ok("POST", f"{path}/gates/run", status=202)
    gates_job = rerun["jobId"]
    assert isinstance(gates_job, str)
    await system.wait_job(gates_job)
    await system.idle()
    gates = await system.ok("GET", f"{path}/gates")
    assert gates["blocking"] == [], gates

    staging = await system.ok("POST", f"{path}/staging-preview", status=202)
    staging_job = staging["jobId"]
    assert isinstance(staging_job, str)
    await system.wait_job(staging_job)
    await system.idle()

    article, etag = await system.item(path)
    approved = await system.ok("POST", f"{path}/approve", {}, etag=etag)
    publish_job = approved["jobId"]
    assert isinstance(publish_job, str)
    view = approved["article"]
    assert isinstance(view, dict)
    item = view["item"]
    assert isinstance(item, dict)
    publish = item["publish"]
    assert isinstance(publish, dict)
    publish_at = publish["publishAt"]
    assert isinstance(publish_at, str)
    meta = article["meta"]
    assert isinstance(meta, dict)
    slug = meta["slug"]
    assert isinstance(slug, str)
    await system.run_until(parse_iso(publish_at))
    await system.wait_job(publish_job)
    await system.idle()
    return Published(
        article_id=flagship.article_id,
        slug=slug,
        publish_job_id=publish_job,
        staging_job_id=staging_job,
        publish_at=parse_iso(publish_at),
    )


# --------------------------------------------------------------------------------------------
# LinkedIn: connection, derived posts, the one-click chain and the kit
# --------------------------------------------------------------------------------------------

CARD_BODY: Final = (
    "A shared service principal turns one prompt injection into a tenant-wide incident.\n\n"
    "We gave every agent its own identity and scoped each token to the tool call it was "
    "making. It cost us a delay we chose on purpose.\n\n"
    "Before you touch a scope, list every tool call your agents make."
)
KIT_BODY: Final = (
    "Agent identities are now their own directory objects, so Conditional Access can apply "
    "to one agent at a time.\n\n"
    "That changes the order of the work: inventory the tool calls, then design the "
    "identities, then write the policies.\n\n"
    "Retire the borrowed human token before anything else."
)
POST_HOOKS: Final = (
    ("One borrowed token opened every mailbox our agents could reach.", HookType.STORY_IN_MOTION),
    ("Inventory every tool call before you design the identity model.", HookType.CHECKLIST_PROMISE),
    (
        "We accepted a delay to rebuild consent instead of patching scopes.",
        HookType.DECISION_TRADEOFF,
    ),
)
SLOT_DATE: Final = "2026-12-08"
SLOT_TIME: Final = "10:00"
SLOT_UTC: Final = datetime(2026, 12, 8, 15, 0, tzinfo=UTC)
"""Tuesday 10:00 in New York: the one-click post's slot."""
STAGED_AT: Final = SLOT_UTC - timedelta(minutes=60)


def draft_out(*, body: str, fmt: Literal["text", "image_card"]) -> PostDraftOut:
    """A derivative as the drafter returns it (no numbers, no links, no tells)."""
    slides: list[Slide] | None = None
    if fmt == "image_card":
        slides = [
            ListSlide(
                title="Before you touch a scope",
                items=["List every tool call", "Give each agent an identity", "Scope per call"],
            )
        ]
    return PostDraftOut(
        angle="Per-call scopes after a shared-principal incident.",
        format=fmt,
        hooks=[
            HookOut(text=text, hook_type=kind, concrete_detail="one borrowed token")
            for text, kind in POST_HOOKS
        ],
        body=body,
        hashtags=[],
        slides=slides,
    )


async def connect_linkedin(system: System) -> None:
    """The owner connects LinkedIn: OAuth start, LinkedIn's redirect back (token exchange and
    member lookup behind the mock), then a connected, vault-encrypted connection."""
    started = await system.ok("POST", "/api/linkedin/oauth/start")
    url = started["authorizeUrl"]
    assert isinstance(url, str)
    fake = system.linkedin.fake
    fake.token_exchange()
    fake.userinfo()
    fake.always(
        "POST",
        INTROSPECT_URL,
        httpx.Response(
            200,
            json={
                "active": True,
                "status": "active",
                "expires_at": int((system.clock.now() + timedelta(days=60)).timestamp()),
            },
        ),
    )
    callback = await system.http.get(
        "/api/linkedin/oauth/callback",
        params={"code": "auth-code-e2e", "state": query_param(url, "state")},
        headers=READ,
    )
    assert callback.status_code == 303
    connection = await system.ok("GET", "/api/linkedin/connection")
    assert connection["status"] == "connected", connection


async def derive_posts(system: System, published: Published) -> tuple[str, str]:
    """``posts.derive`` on the published flagship: an image card (one-click) and a text post
    (the kit), each checked (lint, derived claims, near duplicates)."""
    model = system.model
    model.add(
        Task.DRAFT,
        draft_out(body=CARD_BODY, fmt="image_card"),
        draft_out(body=KIT_BODY, fmt="text"),
    )
    model.add(Task.FACT_CHECK, claims_reply(), claims_reply())
    accepted = await system.ok(
        "POST", f"/api/articles/{published.article_id}/posts/derive", status=202
    )
    job_id = accepted["jobId"]
    assert isinstance(job_id, str)
    job = await system.wait_job(job_id)
    await system.idle()
    result = job.result
    assert isinstance(result, dict)
    ids = result["postIds"]
    assert isinstance(ids, list)
    assert len(ids) == 2
    card, kit = ids
    assert isinstance(card, str)
    assert isinstance(kit, str)
    return card, kit


async def approve_for_slot(system: System, post_id: str) -> str:
    """Lint the text, render the card, approve it for :data:`SLOT_DATE` :data:`SLOT_TIME`;
    returns the approval's SHA-256."""
    post, _ = await system.item(f"/api/posts/{post_id}")
    text: JsonValue = {"displayText": post["displayText"]}
    lint = await system.ok("POST", f"/api/posts/{post_id}/lint", text)
    findings = lint["findings"]
    assert isinstance(findings, list)
    assert [f for f in findings if isinstance(f, dict) and f.get("severity") == "block"] == []
    rendered = await system.ok("POST", f"/api/posts/{post_id}/render", status=202)
    render_job = rendered["jobId"]
    assert isinstance(render_job, str)
    await system.wait_job(render_job)
    await system.idle()
    _, etag = await system.item(f"/api/posts/{post_id}")
    system.linkedin.fake.upload_flow("image", IMAGE_URNS[0])  # what staging will upload
    approved = await system.ok(
        "POST",
        f"/api/posts/{post_id}/approve",
        {"slot": {"localDate": SLOT_DATE, "localTime": SLOT_TIME}},
        etag=etag,
    )
    item = approved["item"]
    assert isinstance(item, dict)
    assert item["status"] == "approved"
    approval = item["approval"]
    assert isinstance(approval, dict)
    sha = approval["sha256"]
    assert isinstance(sha, str)
    return sha


async def publish_click(system: System, post_id: str) -> dict[str, JsonValue]:
    """The owner's tap on the go-live page: a fresh request with the page's confirm token."""
    view = await system.ok("GET", f"/api/posts/{post_id}/go-live")
    assert view["canPublish"] is True, view
    post = view["post"]
    assert isinstance(post, dict)
    etag = post["etag"]
    assert isinstance(etag, str)
    body: JsonValue = {
        "approvalSha256": view["approvalSha256"],
        "confirmToken": view["confirmToken"],
    }
    system.linkedin.fake.created(POST_URN)
    token = PUBLISH_CLICK.set(True)
    try:
        return await system.ok("POST", f"/api/posts/{post_id}/publish", body, etag=etag)
    finally:
        PUBLISH_CLICK.reset(token)


DRAFTING_TASKS: Final = (Task.INTERVIEW, Task.OUTLINE, Task.DRAFT, Task.CRITIQUE, Task.REVISE)


async def assert_nothing_leaked(system: System, caplog: pytest.LogCaptureFixture) -> None:
    """The owner's raw dictation reached only the anonymizer; fetched passages reached only
    research extraction; the blocklisted term reached no model; none of them is in a stored
    document (jobs and their events included), a blob, a broker message or a log line."""
    dump = await system.store_dump()
    broker = system.broker_dump()
    blobs = system.blob_dump()
    logged = caplog.text + "".join(str(record.__dict__) for record in caplog.records)
    for marker in (PRIVATE_MARKER, PASSAGE_MARKER, BLOCKED):
        assert marker not in dump, marker
        assert marker not in broker, marker
        assert marker.encode() not in blobs, marker
        assert marker not in logged, marker
    requests = system.model_requests()
    assert {task for task, text in requests if PRIVATE_MARKER in text} == {"anonymize"}
    assert {task for task, text in requests if PASSAGE_MARKER in text} <= {"research"}
    assert [task for task, text in requests if BLOCKED in text] == []
    for call in system.model.calls:
        if call.request.sensitivity is Sensitivity.PRIVATE:
            assert call.task is Task.ANONYMIZE
        if call.task in DRAFTING_TASKS:
            assert call.request.sensitivity is Sensitivity.ANONYMIZED
            assert call.request.tools == ()


def dig(value: JsonValue, *keys: str) -> JsonValue:
    """``value[k1][k2]...`` of a JSON answer (each level must be an object)."""
    for key in keys:
        assert isinstance(value, dict), (key, value)
        value = value[key]
    return value


async def seed_schedules(system: System, *ids: str) -> list[str]:
    """Seed schedule items through the real seeder (I-4) from every package's provider; with
    ``ids``, only those items (the others would fire jobs a test did not script)."""
    wanted = frozenset(ids)

    def only(provider: ScheduleProvider) -> ScheduleProvider:
        def entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
            return [entry for entry in provider(cadence) if not wanted or entry.id in wanted]

        return entries

    seeder = ScheduleSeeder(
        system.store,
        system.jobs.scheduler,
        [only(provider) for provider in SCHEDULE_PROVIDERS],
        clock=system.clock,
    )
    report = await seeder.run()
    assert report.refused == []
    return list(report.seeded)
