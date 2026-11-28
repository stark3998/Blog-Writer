"""Test helpers for research runs (no network, no Azure): scripted replies for every research
model call, a fake credential, failing fetchers, a job runner harness and a store dump.

Packages that chain research (the pipeline, evals) can script a whole run with
:func:`script_happy_path` and drive it with :func:`run_jobs`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection, Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType

from azure.core.credentials import AccessToken
from pydantic import JsonValue

from blogwriter.ai.types import Citation as SearchCitation
from blogwriter.ai.types import Task
from blogwriter.db import CONTAINERS, MemoryStore, NotFound, Store
from blogwriter.domain.docstore import Stored, create_doc, read_doc, replace_doc
from blogwriter.domain.ports import FetchedPage, FetchRefused, PageFetcher, services_installed
from blogwriter.domain.research import ResearchRun
from blogwriter.domain.settings import Blocklist, BlocklistTerm
from blogwriter.domain.testing import (
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    StaticPageFetcher,
    make_services,
    sample_document,
)
from blogwriter.jobs import Job, JobRegistry, JobRunner, Step, StepContext
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings
from blogwriter.research.jobs import register_jobs
from blogwriter.research.models import (
    BriefOutput,
    FindingOut,
    PlanOutput,
    SourceExtraction,
    StatOut,
    VerifiedClaimOut,
    VerifyOutput,
)
from blogwriter.research.run import create_run, start_run

__all__ = [
    "ALLOW_URL",
    "BLOCKED_TERM",
    "OPEN_URL",
    "OWNER_URL",
    "PASSAGE_TEXT",
    "T0",
    "FailingFetcher",
    "FakeCredential",
    "blocklist_doc",
    "brief_reply",
    "extraction",
    "new_run",
    "page_fetcher",
    "plan_reply",
    "research_registry",
    "research_services",
    "run_jobs",
    "run_research",
    "save_blocklist",
    "script_happy_path",
    "search_reply",
    "store_dump",
    "verify_reply",
]

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
BLOCKED_TERM = "Fabrikamco"
"""A blocklisted client name (every helper blocklist has it)."""
ALLOW_URL = "https://learn.microsoft.com/en-us/entra/workload-id/agent-identities"
"""On the default ``agents`` pillar's allowlist."""
OPEN_URL = "https://www.example.org/research/agent-identity-survey-2026"
OWNER_URL = "https://www.example.net/notes/agent-authorization"

PASSAGE_TEXT = (
    "Agent identities are a distinct object type in the directory. Each agent identity is "
    "created from an agent identity blueprint, which holds the credentials and the permissions "
    "that the agents share. Administrators can apply Conditional Access policies to agent "
    "identities, and sign-in logs record every token an agent requests. The preview limits each "
    "tenant to 1,000 agent identities per blueprint. Tokens issued to agents last one hour and "
    "cannot be refreshed without the credential of the blueprint."
)

ALLOW_CLAIM = "Microsoft Entra stores each agent identity as its own directory object."
OPEN_CLAIM = "A 2026 survey found most agent deployments reuse one service principal."
ALLOW_EXTRACTED = "Each agent identity is created from a blueprint that holds shared credentials."
OPEN_EXTRACTED = "Most surveyed teams run their agents under one shared service principal."


def blocklist_doc(*terms: str, configured: bool = True) -> Blocklist:
    return sample_document(
        Blocklist,
        configured=configured,
        terms=[
            BlocklistTerm(id=f"t-{index}", term=term, category="client")
            for index, term in enumerate(terms, 1)
        ],
    )


async def save_blocklist(store: Store, *terms: str, configured: bool = True) -> None:
    """Create or replace the blocklist singleton (``configured`` unless told otherwise)."""
    doc = blocklist_doc(*terms, configured=configured)
    try:
        current = await read_doc(store, Blocklist, Blocklist.SINGLETON_ID)
    except NotFound:
        await create_doc(store, doc)
        return
    await replace_doc(store, doc, if_match=current.etag)


def research_services(
    *, fetcher: PageFetcher | None = None, ai: ScriptedModelRunner | None = None
) -> FakeServices:
    """Fake services at :data:`T0` with ``fetcher`` (any port implementation) and ``ai``."""
    return make_services(
        clock=FakeClock(T0),
        fetcher=fetcher,
        ai=ai if ai is not None else ScriptedModelRunner(),
    )


def page_fetcher(pages: dict[str, str] | None = None) -> StaticPageFetcher:
    """Serves :data:`PASSAGE_TEXT` for the allowlisted and the open URL by default."""
    return StaticPageFetcher(
        pages if pages is not None else {ALLOW_URL: PASSAGE_TEXT, OPEN_URL: PASSAGE_TEXT}
    )


def search_reply(
    lines: Sequence[tuple[str, str]],
    *,
    cited: Sequence[tuple[str, str]] = (),
    sources: Sequence[str] = (),
    queries: Sequence[str] = ("agent identity directory object",),
) -> ScriptedReply:
    """One ``web_search`` pass: finding lines, ``url_citation`` annotations, searched URLs."""
    text = "\n".join(f"- {sentence} [{url}]" for sentence, url in lines)
    return ScriptedReply(
        text=text,
        citations=tuple(SearchCitation(url=url, title=title) for url, title in cited),
        search_sources=tuple(sources),
        search_queries=tuple(queries),
    )


def plan_reply() -> PlanOutput:
    return PlanOutput(
        sub_questions=[
            "How does Microsoft Entra model agent identities in the directory?",
            "Which Conditional Access controls apply to agent identities?",
            "What limits does the agent identity preview set per tenant?",
        ],
        audience="identity engineers",
        recency_days=365,
    )


def extraction(
    claims: Sequence[str],
    quotes: Sequence[str] = (),
    *,
    publisher: str | None = "Microsoft",
    published: str | None = "2026-10-14",
    relevant: bool = True,
) -> SourceExtraction:
    return SourceExtraction(
        relevant=relevant,
        claims=list(claims),
        quotes=list(quotes),
        publisher=publisher,
        published=published,
    )


def verify_reply(
    *claims: tuple[str, Sequence[str], str], stats: Sequence[StatOut] = ()
) -> VerifyOutput:
    """``(text, input claim ids, status)`` per verified claim."""
    out: list[VerifiedClaimOut] = []
    for text, sources, status in claims:
        item = {"text": text, "sources": list(sources), "status": status}
        out.append(VerifiedClaimOut.model_validate(item))
    return VerifyOutput(claims=out, stats=list(stats))


def brief_reply(*findings: tuple[str, Sequence[str]]) -> BriefOutput:
    """A brief whose hook seeds include a formula opener and an AI tell (both dropped)."""
    return BriefOutput(
        executive_summary=(
            "Agent identities are now a separate directory object with their own sign-in logs. "
            "Conditional Access applies to them, and the preview caps each blueprint at 1,000 "
            "identities."
        ),
        findings=[FindingOut(text=text, citation_ids=list(ids)) for text, ids in findings],
        counterpoints=["The limits come from a preview and may change before general release."],
        open_questions=["How long did your first agent rollout take to reach production?"],
        angles=["Agent identities make per-agent Conditional Access possible for the first time."],
        hook_seeds=[
            "1,000 agent identities per blueprint is the preview limit.",
            "Unpopular opinion: agents do not need identities.",
            "Let's dive in to agent identity.",
        ],
    )


def script_happy_path(
    ai: ScriptedModelRunner,
    *,
    allow_claims: Sequence[str] = (ALLOW_CLAIM,),
    open_claims: Sequence[str] = (OPEN_CLAIM,),
    extracted: Sequence[SourceExtraction] | None = None,
    verify: VerifyOutput | None = None,
    brief: BriefOutput | None = None,
    queries: tuple[Sequence[str], Sequence[str]] = (
        ("agent identity entra directory",),
        ("agent identity survey 2026",),
    ),
) -> None:
    """Replies for a run on the default ``agents`` pillar: an allowlist pass citing
    :data:`ALLOW_URL`, an open pass citing :data:`OPEN_URL`, one extraction per fetched source
    (allowlisted first), then verify and brief.

    Verify input ids: ``c1`` is the allowlisted page claim, ``c2`` its search claim, ``c3`` the
    open page claim, ``c4`` its search claim (when both pages are read).
    """
    ai.add(Task.PLAN, plan_reply())
    ai.add(
        Task.RESEARCH,
        search_reply(
            [(text, ALLOW_URL) for text in allow_claims],
            cited=[(ALLOW_URL, "Agent identities in Microsoft Entra")],
            sources=[ALLOW_URL],
            queries=queries[0],
        ),
        search_reply(
            [(text, OPEN_URL) for text in open_claims],
            cited=[(OPEN_URL, "Agent identity survey 2026")],
            sources=[OPEN_URL],
            queries=queries[1],
        ),
    )
    replies = (
        list(extracted)
        if extracted is not None
        else [extraction([ALLOW_EXTRACTED]), extraction([OPEN_EXTRACTED], publisher="Example")]
    )
    for reply in replies:
        ai.add(Task.RESEARCH, ScriptedReply(value=reply))
    ai.add(
        Task.SYNTHESIS,
        verify
        if verify is not None
        else verify_reply(
            (ALLOW_EXTRACTED, ["c1", "c2"], "verified"),
            (OPEN_EXTRACTED, ["c3", "c4"], "verified"),
        ),
    )
    ai.add(Task.SYNTHESIS, brief if brief is not None else brief_reply())


async def new_run(
    services: FakeServices,
    *,
    topic: str = "Agent identities in Microsoft Entra",
    pillar: str = "agents",
    owner_urls: Sequence[str] = (),
    run_id: str | None = None,
) -> Stored[ResearchRun]:
    return await create_run(
        services.store,
        topic=topic,
        pillar=pillar,
        now=services.clock.now(),
        owner_urls=owner_urls,
        run_id=run_id,
    )


type ExtraStep = tuple[str, Callable[[StepContext], Awaitable[JsonValue]]]


def research_registry(*extra: ExtraStep) -> JobRegistry:
    """A registry with ``research.run`` and one-step test job types."""
    registry = JobRegistry()
    register_jobs(registry)
    for name, step in extra:
        registry.register(name, [Step("wait", step)])
    return registry


async def run_jobs(
    services: FakeServices,
    registry: JobRegistry,
    *,
    done: Callable[[], Awaitable[bool]],
    within: float = 15.0,
) -> None:
    """Run a real ``JobRunner`` on the services' store and clock until ``done()``."""
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        await runner.start()
        try:
            await eventually(done, within=within)
        finally:
            await runner.stop()


async def run_research(services: FakeServices, run: ResearchRun) -> Job:
    """Enqueue ``research.run`` for ``run`` (owner origin) and run it to a terminal status."""
    job = await start_run(services, run, origin="owner")

    async def finished() -> bool:
        return (await services.jobs.get(job.id)).is_terminal

    await run_jobs(services, research_registry(), done=finished)
    return await services.jobs.get(job.id)


async def store_dump(store: MemoryStore, *, exclude_kinds: Collection[str] = ("blocklist",)) -> str:
    """Every stored document of every container (jobs included), as one JSON string.

    The blocklist itself is left out by default: it is the one place its terms live.
    """
    bodies: list[object] = []
    for container in CONTAINERS:
        bodies.extend(
            doc.body
            for doc in await store.find(container, [])
            if doc.body.get("kind") not in exclude_kinds
        )
    return json.dumps(bodies, ensure_ascii=False)


class FailingFetcher:
    """A ``PageFetcher`` that refuses every URL with one reason (and records what it saw)."""

    def __init__(self, reason: str = "unavailable") -> None:
        self.reason = reason
        self.seen: list[str] = []

    async def fetch(self, url: str, *, allowed_urls: Collection[str]) -> FetchedPage:
        self.seen.append(url)
        if url not in allowed_urls:
            raise FetchRefused("url_not_allowed")
        raise FetchRefused(self.reason)


class FakeCredential:
    """An async Azure credential that hands out a fixed token (and counts the calls)."""

    def __init__(self, token: str = "test-token") -> None:  # noqa: S107 - a fake for tests
        self.token = token
        self.scopes: list[tuple[str, ...]] = []
        self.closed = False

    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        self.scopes.append(scopes)
        expires = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
        return AccessToken(self.token, expires)

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeCredential:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()
