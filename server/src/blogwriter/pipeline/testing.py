"""Test helpers for the pipeline (pipeline, analytics and end-to-end tests). No network, no Azure.

:func:`seed_world` saves what drafting needs (blocklist, employer policy with AI use confirmed, an
active voice guide, a usable story and POV on the ``agents`` pillar); :func:`new_article` and
:func:`sample_brief` give an article and a brief. Reply builders script every pipeline model call;
:class:`RouterLikeRunner` streams like the router (a fallback first streams the primary's text,
then a ``Fallback`` event). :func:`running` drives a real ``JobRunner`` on the fake services,
:func:`wait_job` waits for a job and fails fast on a step error, :func:`start_at` enqueues a job
whose earlier steps already succeeded, :func:`rewind` makes a finished step run again (a resume).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from blogwriter.ai import Fallback, Finished, RouterEvent, RouterResult
from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Refusal, Request, Task, TextDelta
from blogwriter.articles import create_article
from blogwriter.db import NotFound, Store
from blogwriter.domain.articles import Article, ArticleMeta
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.common import Citation, ClassifierCheck, Quote
from blogwriter.domain.docstore import create_doc, read_doc, replace_doc, save_singleton, update_doc
from blogwriter.domain.ports import services_installed
from blogwriter.domain.research import Brief, BriefClaim, BriefFinding, BriefStat
from blogwriter.domain.settings import (
    Blocklist,
    BlocklistTerm,
    EmployerPolicy,
    VoicePreferences,
)
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.stories import PovEntry, StoryEntry, StoryMetric
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner, sample_document
from blogwriter.domain.voice import GuideRules, StyleGuide
from blogwriter.gates.factcheck import ClaimMap
from blogwriter.gates.testing import passing_scores
from blogwriter.jobs import Job, JobRegistry, JobRunner, Step
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.pipeline.jobs import register_jobs
from blogwriter.pipeline.models import (
    CritiqueNote,
    CritiqueOut,
    InterviewOut,
    OutlineOut,
    QuestionOut,
    SectionOut,
)
from blogwriter.research import register_jobs as register_research
from blogwriter.stories import pov_text, story_text

__all__ = [
    "ANSWER_TAKE",
    "BLOCKED",
    "CITATION_ID",
    "SOURCE_URL",
    "T0",
    "RouterLikeRunner",
    "World",
    "critique",
    "draft_body",
    "interview_reply",
    "new_article",
    "outline_reply",
    "pipeline_registry",
    "rewind",
    "running",
    "sample_brief",
    "script_gates",
    "seed_world",
    "start_at",
    "wait_job",
]

T0: Final = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
"""Monday 2026-11-30, 09:00 in New York (the default cadence's research start)."""
BLOCKED: Final = "Fabrikamco"
"""A blocklisted client name (the same as ``research.testing.BLOCKED_TERM``)."""
SOURCE_URL: Final = "https://learn.microsoft.com/en-us/entra/workload-id/agent-identities"
CITATION_ID: Final = "src-entra-agent-identities"
ANSWER_TAKE: Final = (
    "Give every agent its own identity and scope its tokens to the single tool call it is "
    "making, because a shared service principal turns one prompt injection into a tenant-wide "
    "incident."
)
_STAMP: Final = to_iso(T0)

GUIDE_BODY: Final = """Write like a practitioner briefing a peer: short declarative sentences \
mixed with longer explanations. Lead with the decision, then the reason. State numbers with \
their scope ("about 2,000 apps", "in week 6"). Prefer concrete nouns over abstractions."""


# --------------------------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------------------------


class World:
    """What :func:`seed_world` saved."""

    def __init__(self, story: StoryEntry | None, pov: PovEntry | None, guide: StyleGuide | None):
        self.story = story
        self.pov = pov
        self.guide = guide


async def _save_singleton(store: Store, doc: Blocklist | EmployerPolicy | VoicePreferences) -> None:
    try:
        current = await read_doc(store, type(doc), doc.id)
    except NotFound:
        await save_singleton(store, doc, if_match=None)
        return
    await replace_doc(store, doc, if_match=current.etag)


async def seed_world(
    services: FakeServices,
    *,
    blocklist: bool = True,
    ai_use: bool = True,
    guide: bool = True,
    bank: bool = True,
) -> World:
    """The settings and owner material a pipeline run needs."""
    store = services.store
    if blocklist:
        await _save_singleton(
            store,
            Blocklist(
                id=Blocklist.SINGLETON_ID,
                created_at=_STAMP,
                updated_at=_STAMP,
                configured=True,
                terms=[BlocklistTerm(id="t-1", term=BLOCKED, category="client")],
            ),
        )
    await _save_singleton(
        store,
        EmployerPolicy(
            id=EmployerPolicy.SINGLETON_ID,
            created_at=_STAMP,
            updated_at=_STAMP,
            configured=True,
            ai_use_confirmed=ai_use,
            ai_use_confirmed_at=_STAMP if ai_use else None,
        ),
    )
    style: StyleGuide | None = None
    if guide:
        style = sample_document(
            StyleGuide,
            id=StyleGuide.id_for(1),
            version=1,
            status="active",
            body=GUIDE_BODY,
            rules=GuideRules(prefer=["Lead with the decision."], banned_phrases=["game-changer"]),
            approved_at=_STAMP,
        )
        await create_doc(store, style)
        await _save_singleton(
            store,
            VoicePreferences(
                id=VoicePreferences.SINGLETON_ID,
                created_at=_STAMP,
                updated_at=_STAMP,
                active_guide_id=style.id,
            ),
        )
    story: StoryEntry | None = None
    pov: PovEntry | None = None
    if bank:
        draft_story = sample_document(
            StoryEntry,
            status="approved",
            origin="owner",
            title="Agent rollout on a shared service principal",
            problem="Twelve internal agents shared one service principal with broad Graph scopes.",
            actions="Split them into per-agent identities and scoped tokens per tool call.",
            decisions="Accepted a two-week delay to rebuild consent instead of patching scopes.",
            metrics=[StoryMetric(label="Agents migrated", value="12 in 3 weeks")],
            lessons="Inventory every tool call before you design the identity model.",
            pillars=["agents"],
            confidentiality="anonymized",
            approved_at=_STAMP,
        )
        story = draft_story.model_copy(
            update={
                "classifier": ClassifierCheck(
                    passed=True,
                    checked_at=_STAMP,
                    content_sha256=sha256_hex(story_text(draft_story)),
                )
            }
        )
        await create_doc(store, story)
        draft_pov = sample_document(
            PovEntry,
            status="approved",
            origin="owner",
            pillar="agents",
            statement="An agent that borrows a human's token is an incident waiting to happen.",
            approved_at=_STAMP,
        )
        pov = draft_pov.model_copy(
            update={
                "classifier": ClassifierCheck(
                    passed=True, checked_at=_STAMP, content_sha256=sha256_hex(pov_text(draft_pov))
                )
            }
        )
        await create_doc(store, pov)
    return World(story=story, pov=pov, guide=style)


async def new_article(
    services: FakeServices,
    *,
    status: ArticleStatus = ArticleStatus.PLANNED,
    meta: ArticleMeta | None = None,
    purpose: str = "flagship",
    **fields: object,
) -> Article:
    """A stored article (created through the articles API), then given ``status`` and
    ``fields`` (links, markdown...)."""
    stored = await create_article(
        services.store,
        now=services.clock.now(),
        meta=meta
        or ArticleMeta(
            title="Scope agent tokens per tool call", pillar="agents", format="deep-explainer"
        ),
        purpose="eval" if purpose == "eval" else "flagship",
    )
    changes: dict[str, object] = {"status": status, **fields}
    if fields.get("markdown") and "version" not in fields:
        changes["version"] = 1  # a body that exists was saved once

    def mutate(article: Article) -> Article:
        changed = article.model_copy(update=changes)
        return changed.model_copy(update={"content_sha256": changed.compute_sha256()})

    return (await update_doc(services.store, Article, stored.doc.id, mutate)).doc


def sample_brief(**overrides: object) -> Brief:
    """A brief with one allowlisted citation (:data:`CITATION_ID`), a claim, a stat and a quote."""
    fields: dict[str, object] = {
        "topic": "Agent identities in Microsoft Entra",
        "pillar": "agents",
        "executive_summary": (
            "Microsoft Entra now models agent identities as their own directory objects with "
            "sign-in logs, and Conditional Access applies to them."
        ),
        "findings": [
            BriefFinding(
                id="f1",
                text="Agent identities are created from a blueprint that holds shared credentials.",
                citation_ids=[CITATION_ID],
            )
        ],
        "stats": [
            BriefStat(
                id="s1",
                value="1,000",
                label="agent identities per blueprint in the preview",
                citation_ids=[CITATION_ID],
                as_of="2026-10",
            )
        ],
        "claims": [
            BriefClaim(
                id="c1",
                text="Conditional Access policies apply to agent identities.",
                citation_id=CITATION_ID,
                status="verified",
            )
        ],
        "quotes": [
            Quote(
                id="q1",
                citation_id=CITATION_ID,
                text="Sign-in logs record every token an agent requests.",
            )
        ],
        "citations": [
            Citation(
                id=CITATION_ID,
                url=SOURCE_URL,
                title="Agent identities in Microsoft Entra",
                publisher="Microsoft Learn",
                domain="learn.microsoft.com",
                allowlisted=True,
            )
        ],
        "counterpoints": ["The preview limits may change before general availability."],
        "angles": ["Per-tool-call token scope is now possible for the first time."],
        "open_questions": ["How long did your first agent identity rollout take?"],
    }
    fields.update(overrides)
    return sample_document(Brief, **fields)


# --------------------------------------------------------------------------------------------
# Replies
# --------------------------------------------------------------------------------------------


def interview_reply(count: int = 2) -> InterviewOut:
    questions = [
        QuestionOut(
            text="What's your take on scoping agent tokens per tool call instead of per agent?",
            purpose="Your position becomes the thesis.",
        ),
        QuestionOut(
            text=(
                "What would you tell a client to change first on Monday if agents share one "
                "principal?"
            ),
            purpose="The first step readers take.",
        ),
    ]
    extra = [
        QuestionOut(text=f"Which constraint shaped decision {index}?", purpose="Context.")
        for index in range(3, count + 1)
    ]
    return InterviewOut(questions=[*questions, *extra][:count])


def outline_reply(
    *, story_ids: Sequence[str] = (), pov_ids: Sequence[str] = (), citation_id: str = CITATION_ID
) -> OutlineOut:
    return OutlineOut(
        title="Scope agent tokens per tool call, not per agent",
        excerpt="Why a shared service principal turns one prompt injection into a tenant incident.",
        slug="scope-agent-tokens-per-tool-call",
        tags=["agents", "Entra ID", "least privilege"],
        thesis="Agent tokens belong to the tool call, not to the agent.",
        opening="Twelve agents on one service principal, and the day one of them was tricked.",
        sections=[
            SectionOut(
                heading="One principal, twelve agents",
                purpose="The failure mode.",
                points=["Shared scopes", "Blast radius"],
                citation_ids=[citation_id, "src-invented"],
                story_ids=list(story_ids),
                pov_ids=list(pov_ids),
                interview_question_ids=["q1", "q9"],
            ),
            SectionOut(
                heading="Scope the token to the call",
                purpose="The fix.",
                points=["Blueprint", "Conditional Access"],
                citation_ids=[citation_id],
                story_ids=[],
                pov_ids=[],
                interview_question_ids=["q2"],
            ),
        ],
        closing="List every tool call your agents make before Friday.",
        story_ids=list(story_ids),
        pov_ids=[*pov_ids, "pov-unknown"],
        diagram="The token flow from agent to tool through the blueprint.",
    )


def draft_body(*, citation_id: str = CITATION_ID, quote: str = ANSWER_TAKE) -> str:
    """A drafter's reply with the habits the pipeline cleans up: a code fence around the whole
    answer, a title heading, an invented citation, its own definitions and a references list."""
    return f"""```markdown
# Scope agent tokens per tool call

Twelve agents ran on one service principal until the day a prompt injection used it. Agent \
identities now exist as their own directory objects.[^{citation_id}] The author put it plainly: \
"{quote}"

## One principal, twelve agents

A shared principal means every agent carries every scope. Conditional Access applies to agent \
identities, so you can finally write policy per agent.[^{citation_id}] A survey claimed most teams \
do this already.[^src-made-up]

```mermaid
flowchart LR
  Agent --> Blueprint --> Tool
```

## Scope the token to the call

Issue a token per tool call and let it expire with the call.

[^{citation_id}]: Somebody. *Invented title*. [example](https://example.com/fake)

## References

- https://example.com/fake
```"""


def critique(*, passing: bool = True) -> CritiqueOut:
    """A critique that passes gate 1's thresholds, or fails first-hand specificity."""
    good = 9 if passing else 5
    return CritiqueOut(
        clarity=9,
        structure=8,
        accuracy=9,
        depth=8,
        first_hand_specificity=good,
        voice=8,
        originality=8,
        actionability=8,
        overall=9 if passing else 6,
        notes=[]
        if passing
        else [
            CritiqueNote(
                dimension="first_hand_specificity",
                where="One principal, twelve agents",
                problem="The section never says what broke in the rollout.",
                fix="Use the story's decision to delay two weeks and why.",
            )
        ],
        keep=["The opening scene."],
    )


def script_gates(runner: ScriptedModelRunner) -> None:
    """The gates' model replies: a fact check that maps no claims and a passing rubric."""
    runner.add(Task.FACT_CHECK, ClaimMap(claims=[]))
    runner.add(Task.CRITIQUE, passing_scores())


# --------------------------------------------------------------------------------------------
# A runner that streams like the router
# --------------------------------------------------------------------------------------------


class RouterLikeRunner:
    """A ``ModelRunner`` over a :class:`ScriptedModelRunner` whose streams look like the
    router's: a reply served by a fallback first streams text from the primary, then (for a
    refusal) the ``Refusal``, then a ``Fallback`` event, then the served text."""

    def __init__(self, inner: ScriptedModelRunner) -> None:
        self.inner = inner

    async def run(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> RouterResult:
        return await self.inner.run(task, request, origin=origin, job_id=job_id)

    async def stream(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> AsyncGenerator[RouterEvent]:
        result = await self.inner.run(task, request, origin=origin, job_id=job_id)
        reason = result.served_by.fallback_reason
        if reason is not None:
            yield TextDelta("Primary model text the router discards.")
            if reason == "refusal":
                yield Refusal("cyber", None)
            yield Fallback(
                from_deployment="primary-deployment",
                to_deployment=result.served_by.deployment,
                reason=reason,
            )
        refusal = result.completion.refusal
        if result.refused and refusal is not None:
            yield refusal
        else:
            text = result.text
            for start in range(0, len(text), 200):
                await asyncio.sleep(0)
                yield TextDelta(text[start : start + 200])
        yield Finished(result)


# --------------------------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------------------------


def pipeline_registry(*extra: tuple[str, Sequence[Step]], research: bool = False) -> JobRegistry:
    """The pipeline's job types (plus research's, and test types)."""
    registry = JobRegistry()
    register_jobs(registry)
    if research:
        register_research(registry)
    for name, steps in extra:
        registry.register(name, steps)
    return registry


@asynccontextmanager
async def running(services: FakeServices, registry: JobRegistry) -> AsyncGenerator[JobRunner]:
    """A real ``JobRunner`` on the services' store and clock, with the services installed."""
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        await runner.start()
        try:
            yield runner
        finally:
            await runner.stop()


async def wait_job(
    services: FakeServices,
    job_id: str,
    statuses: Collection[str],
    *,
    key: str | None = None,
    within: float = 15.0,
) -> Job:
    """The job once its status is in ``statuses`` (and, with ``key``, once it waits for that
    input). A step error (the job queued for a retry the fake clock never reaches) fails at
    once with the error's type."""
    found: list[Job] = []

    async def reached() -> bool:
        try:
            job = await services.jobs.get(job_id)
        except NotFound:
            return False
        error = job.error
        if job.status == "queued" and error is not None and error.code == "step_error":
            raise AssertionError(f"step {error.step} failed with {error.error_type}")
        if job.status not in statuses:
            return False
        if key is not None and (job.awaiting is None or job.awaiting.key != key):
            return False
        found.append(job)
        return True

    await eventually(reached, within=within)
    return found[-1]


async def start_at(
    services: FakeServices,
    job_type: str,
    payload: JsonValue,
    *,
    job_id: str,
    step: str,
    registry: JobRegistry,
    outputs: Mapping[str, JsonValue] | None = None,
) -> Job:
    """Enqueue ``job_type`` with every step before ``step`` already succeeded (with
    ``outputs`` by step name), so a test runs one part of a job."""
    job_type_def = registry.get(job_type)
    if job_type_def is None:
        raise ValueError(f"{job_type} is not registered")
    names = job_type_def.step_names
    job = await enqueue(
        services.store, job_type, payload, job_id=job_id, steps=names, clock=services.clock
    )
    done = names[: names.index(step)]
    raw = await services.store.read("jobs", job.id)
    current = Job.from_body(raw.body)
    for state in current.steps:
        if state.name in done:
            state.status = "succeeded"
            state.output = (outputs or {}).get(state.name, {})
            state.runs = 1
    await services.store.replace("jobs", current.to_body(), if_match=raw.etag)
    return await services.jobs.get(job.id)


async def rewind(
    services: FakeServices, job_id: str, step: str, *, checkpoint: JsonValue = None
) -> Job:
    """Make ``step`` and every later step run again (a crash before ``step`` committed); the
    job is queued now. ``checkpoint`` is what ``step`` had checkpointed before the crash;
    inputs and remote ids stay, as after a real crash."""
    raw = await services.store.read("jobs", job_id)
    job = Job.from_body(raw.body)
    names = [state.name for state in job.steps]
    for state in job.steps[names.index(step) :]:
        state.status = "pending"
        if state.name == step:
            state.output = checkpoint
    job.status = "queued"
    job.awaiting = None
    job.finished_at = None
    job.result = None
    job.run_after = to_iso(services.clock.now())
    await services.store.replace("jobs", job.to_body(), if_match=raw.etag)
    return await services.jobs.get(job_id)
