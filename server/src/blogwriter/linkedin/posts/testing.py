"""Test helpers for the posts package (no network, no Chromium, no real time). Never used in
production.

- :func:`seed_settings` saves the blocklist, the employer policy, pillars with hashtags and a
  brand kit; :func:`usable_story` / :func:`usable_pov` store bank entries the bank treats as
  usable (approved, classifier verdict on the current text).
- :class:`CompliantRenderService`: ``FakeRenderService`` whose PDF carries exactly Title and
  Author (the real renderer's invariant) and a configurable text layer.
- :class:`FakeTransport`: the staging transport, recording calls, with injectable failures.
- :func:`draft_reply`, :func:`claims_reply`: structured model output to script.
- :func:`run_job`: a posts job to the end on a real ``JobRunner`` with the services installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final, Literal, cast, override

from pydantic import JsonValue
from pypdf import PdfWriter

from blogwriter.db import NotFound
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ClassifierCheck
from blogwriter.domain.docstore import upsert_doc
from blogwriter.domain.ports import RenderedCarousel, Services, services_installed
from blogwriter.domain.posts import HookType
from blogwriter.domain.settings import (
    Blocklist,
    BlocklistTerm,
    BrandKit,
    EmployerPolicy,
    Pillar,
    PillarSettings,
)
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry, StoryMetric
from blogwriter.domain.testing import SAMPLE_NOW, FakeRenderService, FakeServices, png_bytes
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.linkedin.posts.checks import ClaimCheck, ClaimsOut
from blogwriter.linkedin.posts.derive import HookOut, PostDraftOut
from blogwriter.linkedin.posts.jobs import register_jobs
from blogwriter.linkedin.posts.staging import TokenCheck
from blogwriter.render.spec import (
    CoverSlide,
    RecapSlide,
    Slide,
    Source,
    SourcesSlide,
    StatementSlide,
)
from blogwriter.stories import entry_text

__all__ = [
    "CLEAN_BODY",
    "T0",
    "CompliantRenderService",
    "FakeTransport",
    "StepError",
    "carousel_slides",
    "claims_reply",
    "compliant_pdf",
    "draft_reply",
    "posts_registry",
    "run_job",
    "seed_settings",
    "usable_pov",
    "usable_story",
]

T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

CLEAN_BODY: Final = (
    "The first 1,400 apps moved in ten weeks. The last 600 took the rest of the 14 months, "
    "because each one had a hard-coded redirect, a service account or a vendor who had left.\n\n"
    "We stopped estimating by app count. We sorted the inventory by the oddest dependency "
    "first and scheduled from there. The easy apps never set the date.\n\n"
    "If you plan a migration this year, count the strange apps before you count anything else."
)
"""A post body with no AI tells, bait, links or invented numbers (numbers match the story)."""


class StepError(AssertionError):
    """A job step raised an unexpected exception (the runner would retry it)."""


# --------------------------------------------------------------------------------------------
# Settings and bank entries
# --------------------------------------------------------------------------------------------


async def seed_settings(
    services: FakeServices,
    *,
    terms: Sequence[str] = (),
    ai_use_confirmed: bool = True,
    blocklist_configured: bool = True,
    hashtags: Sequence[str] = ("AISecurity", "AgentIdentity"),
    brand: bool = True,
) -> None:
    """Save the blocklist (``terms`` as whole-word client names), the employer policy, the
    default pillars (``agents`` gets ``hashtags``) and a brand kit with an author name."""
    blocklist = Blocklist(
        id=Blocklist.SINGLETON_ID,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        configured=blocklist_configured,
        terms=[
            BlocklistTerm(id=f"t-{index + 1}", term=term, category="client")
            for index, term in enumerate(terms)
        ],
    )
    policy = EmployerPolicy(
        id=EmployerPolicy.SINGLETON_ID,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        ai_use_confirmed=ai_use_confirmed,
        ai_use_confirmed_at=SAMPLE_NOW if ai_use_confirmed else None,
        disclaimer_short="Views are my own.",
        configured=True,
    )
    defaults = PillarSettings.default(SAMPLE_NOW)
    pillars: list[Pillar] = []
    for pillar in defaults.pillars:
        if pillar.id == "agents":
            pillar = pillar.model_copy(update={"hashtags": list(hashtags)})
        pillars.append(pillar)
    await upsert_doc(services.store, blocklist)
    await upsert_doc(services.store, policy)
    await upsert_doc(services.store, defaults.model_copy(update={"pillars": pillars}))
    if brand:
        kit = BrandKit.default(SAMPLE_NOW).model_copy(
            update={"author_name": "A. Writer", "author_headline": "Identity and AI security"}
        )
        await upsert_doc(services.store, kit)


def _passed(text: str) -> ClassifierCheck:
    return ClassifierCheck(passed=True, checked_at=SAMPLE_NOW, content_sha256=sha256_hex(text))


async def usable_story(
    services: Services,
    story_id: str,
    *,
    pillar: str = "identity",
    title: str = "A 2,000-app identity migration",
    problem: str = "We had to move 2,000 apps off our old identity provider in 14 months.",
    decisions: str = "We sorted the inventory by the oddest dependency first.",
    lessons: str = "The last 600 apps set the date. The easy apps never set the date.",
    metrics: Sequence[tuple[str, str]] = (("apps moved", "2,000"), ("first wave", "1,400")),
) -> StoryEntry:
    """An approved story whose classifier verdict is about its current text (usable)."""
    story = StoryEntry(
        id=story_id,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        status=ReviewStatus.APPROVED,
        status_changed_at=SAMPLE_NOW,
        origin="owner",
        title=title,
        problem=problem,
        decisions=decisions,
        lessons=lessons,
        metrics=[StoryMetric(label=label, value=value) for label, value in metrics],
        pillars=[pillar],
        approved_at=SAMPLE_NOW,
    )
    story = story.model_copy(update={"classifier": _passed(entry_text(story))})
    await upsert_doc(services.store, story)
    return story


async def usable_pov(
    services: Services,
    pov_id: str,
    *,
    pillar: str = "identity",
    statement: str = "count the strange apps before you count anything else",
) -> PovEntry:
    pov = PovEntry(
        id=pov_id,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        status=ReviewStatus.APPROVED,
        status_changed_at=SAMPLE_NOW,
        origin="owner",
        pillar=pillar,
        statement=statement,
        approved_at=SAMPLE_NOW,
    )
    pov = pov.model_copy(update={"classifier": _passed(entry_text(pov))})
    await upsert_doc(services.store, pov)
    return pov


# --------------------------------------------------------------------------------------------
# Model replies
# --------------------------------------------------------------------------------------------


def draft_reply(
    *,
    body: str = CLEAN_BODY,
    hooks: Sequence[tuple[str, HookType]] = (
        ("We moved 2,000 apps in 14 months. The last 600 set the date.", HookType.RESULT_METRIC),
        ("count the strange apps before you count anything else", HookType.CHECKLIST_PROMISE),
        (
            "The easy apps never set the date. The first 1,400 took ten weeks.",
            HookType.STORY_IN_MOTION,
        ),
    ),
    fmt: Literal["text", "carousel", "image_card"] = "text",
    hashtags: Sequence[str] = ("AISecurity",),
    slides: Sequence[Slide] | None = None,
    document_title: str | None = None,
    citation_ids: Sequence[str] = (),
) -> PostDraftOut:
    """A whole-post draft (clean by default)."""
    return PostDraftOut(
        angle="Sequencing a migration by its oddest dependencies.",
        format=fmt,
        hooks=[
            HookOut(text=text, hook_type=kind, concrete_detail="2,000 apps in 14 months")
            for text, kind in hooks
        ],
        body=body,
        hashtags=list(hashtags),
        document_title=document_title,
        slides=list(slides) if slides is not None else None,
        citation_ids=list(citation_ids),
    )


def claims_reply(*claims: ClaimCheck) -> ClaimsOut:
    """The claim checker's answer (no claims: everything matched)."""
    return ClaimsOut(claims=list(claims))


def carousel_slides(count: int = 8, *, sources: bool = False) -> list[Slide]:
    """A deck that passes the carousel rules: cover, statements, recap (and sources)."""
    slides: list[Slide] = [CoverSlide(hook="We moved 2,000 apps in 14 months")]
    middle = count - 2 - (1 if sources else 0)
    slides.extend(
        StatementSlide(statement=f"Lesson {chr(65 + index)}: sort by the oddest dependency.")
        for index in range(middle)
    )
    slides.append(
        RecapSlide(items=["Count the strange apps first.", "Schedule from the hard end."])
    )
    if sources:
        slides.append(
            SourcesSlide(sources=[Source(publisher="NIST", title="Digital Identity Guidelines")])
        )
    return slides


# --------------------------------------------------------------------------------------------
# Rendering and transport fakes
# --------------------------------------------------------------------------------------------


def compliant_pdf(
    pages: int, *, title: str, author: str = "A. Writer", extra: Mapping[str, str] | None = None
) -> bytes:
    """A PDF of ``pages`` 1080x1350 pt pages with exactly Title and Author (plus ``extra``)."""
    writer = PdfWriter()
    for _ in range(max(1, pages)):
        writer.add_blank_page(width=1080, height=1350)
    writer.metadata = {"/Title": title, "/Author": author, **dict(extra or {})}
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class CompliantRenderService(FakeRenderService):
    """A render fake whose PDFs carry exactly Title/Author; ``text_layer`` sets the PDF text
    per page and ``extra_metadata`` adds metadata the real renderer never writes."""

    def __init__(self) -> None:
        super().__init__()
        self.text_layer: list[str] = []
        self.extra_metadata: dict[str, str] = {}

    @override
    async def render_carousel(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> RenderedCarousel:
        await self._enter("render_carousel")
        slides = spec.get("slides")
        count = len(cast(list[object], slides)) if isinstance(slides, list) else 1
        title = spec.get("title")
        pdf = compliant_pdf(
            count, title=title if isinstance(title, str) else "", extra=self.extra_metadata
        )
        texts = tuple(self.text_layer[i] if i < len(self.text_layer) else "" for i in range(count))
        return RenderedCarousel(
            pdf=pdf,
            pages=tuple(png_bytes(108, 135) for _ in range(count)),
            text=texts,
            sha256=hashlib.sha256(pdf).hexdigest(),
        )


class FakeTransport:
    """A :class:`~blogwriter.linkedin.posts.staging.StagingTransport` for tests."""

    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.token_checks = 0
        self.upload_error: Exception | None = None
        self.token = TokenCheck(True)

    async def upload_media(self, services: Services, post_id: str) -> None:
        await asyncio.sleep(0)
        self.uploads.append(post_id)
        if self.upload_error is not None:
            raise self.upload_error

    async def check_token(self, services: Services, *, now: datetime) -> TokenCheck:
        await asyncio.sleep(0)
        self.token_checks += 1
        return self.token


# --------------------------------------------------------------------------------------------
# Running jobs
# --------------------------------------------------------------------------------------------


def posts_registry(transport: FakeTransport | None = None) -> JobRegistry:
    registry = JobRegistry()
    register_jobs(registry, transport=transport if transport is not None else FakeTransport())
    return registry


async def run_job(
    services: FakeServices,
    job_type: str,
    job_input: JsonValue = None,
    *,
    job_id: str,
    transport: FakeTransport | None = None,
    within: float = 10.0,
) -> Job:
    """Enqueue a posts job (due now) and run it to the end on a real ``JobRunner``.

    Raises :class:`StepError` when a step raised an unexpected exception (the runner would
    retry it later) instead of waiting for the retry.
    """
    registry = posts_registry(transport)
    definition = registry.get(job_type)
    await enqueue(
        services.store,
        job_type,
        job_input,
        job_id=job_id,
        steps=definition.step_names if definition is not None else (),
        clock=services.clock,
    )
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)

    async def settled() -> bool:
        try:
            job = await services.jobs.get(job_id)
        except NotFound:
            return False
        return job.is_terminal or (job.error is not None and job.error.code == "step_error")

    with services_installed(services):
        await runner.start()
        try:
            await eventually(settled, within=within)
        finally:
            await runner.stop()
    job = await services.jobs.get(job_id)
    if not job.is_terminal and job.error is not None:
        raise StepError(f"step {job.error.step} raised {job.error.error_type}")
    return job
