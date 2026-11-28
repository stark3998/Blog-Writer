"""The flagship chain end to end, as the owner through the HTTP API: idea -> research ->
micro-interview (``waiting_input``, then the owner's confirmed answers) -> outline -> draft ->
critique -> revise -> gates -> in_review -> clearance and a gates re-run -> staging preview ->
approve -> publish (published-blogs upsert, portfolio dispatch, build, verification by the
version marker)."""

from __future__ import annotations

import logging

import pytest

from blogwriter.domain.articles import PUBLISHED_MARKER_NAME, Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import find_docs, read_doc
from blogwriter.domain.jobtypes import PUBLISH_ARTICLE, RESEARCH_RUN, pipeline_job_id
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ArticleStatus, IdeaStatus, InterviewStatus
from blogwriter.domain.stories import Interview
from blogwriter.domain.voice import VoiceSample
from blogwriter.pipeline.testing import ANSWER_TAKE
from blogwriter.publish.contract import validate_published_doc
from blogwriter.publish.testing import SITE

from .conftest import (
    NEXT_MONDAY,
    REVIEWED_AT,
    System,
    assert_nothing_leaked,
    draft_flagship,
    prepare_owner,
    review_and_publish,
    start_system,
)

S = ArticleStatus
PIPELINE_STEPS = (
    "research",
    "interview_questions",
    "interview",
    "outline",
    "draft",
    "normalize",
    "critique_revise",
    "gates",
    "assets",
    "finalize",
)


async def article(system: System, article_id: str) -> Article:
    return (await read_doc(system.store, Article, article_id)).doc


async def test_idea_to_a_verified_published_flagship(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    async with start_system() as system:
        owner = await prepare_owner(system)
        flagship = await draft_flagship(system, owner)

        # In review: research chained, the interview answered, critique -> revise, the gates.
        drafted = await article(system, flagship.article_id)
        assert drafted.status is S.IN_REVIEW
        assert flagship.pipeline_job_id == pipeline_job_id(drafted.id, 1)
        idea = (await read_doc(system.store, Idea, flagship.idea_id)).doc
        assert idea.status is IdeaStatus.USED
        assert drafted.idea_id == idea.id
        run_id = ResearchRun.id_for_pipeline(drafted.id, 1)
        assert drafted.research_run_id == run_id
        assert drafted.brief_id == Brief.id_for(run_id)
        runs = await find_docs(system.store, ResearchRun)
        assert [(stored.doc.id, stored.doc.status.value) for stored in runs] == [
            (run_id, "succeeded")
        ]
        research = await system.job(f"research-{run_id}")
        assert (research.type, research.status) == (RESEARCH_RUN, "succeeded")
        pipeline = await system.job(flagship.pipeline_job_id)
        assert [(step.name, step.status) for step in pipeline.steps] == [
            (name, "succeeded") for name in PIPELINE_STEPS
        ]
        waits = [event.data for event in pipeline.events if event.type == "input.requested"]
        assert {"key": "research"} in waits
        assert {"key": "interview"} in waits
        loop = pipeline.step("critique_revise")
        assert loop is not None
        assert isinstance(loop.output, dict)
        assert loop.output["loops"] == 1  # a failed critique, one revision, then a pass
        rounds = loop.output["rounds"]
        assert isinstance(rounds, list)
        assert [r["passed"] for r in rounds if isinstance(r, dict)] == [False, True]
        assert drafted.version == 2  # the draft, then the revision
        assert ANSWER_TAKE in drafted.markdown  # the owner's confirmed words, quoted
        assert {ref.kind for ref in drafted.owner_signals} == {"story", "pov", "interview"}
        assert sorted(item.role for item in drafted.media) == [
            "diagram_png",
            "diagram_svg",
            "hero_image",
            "og_image",
        ]
        interview = (await read_doc(system.store, Interview, flagship.interview_id)).doc
        assert interview.status is InterviewStatus.ANSWERED
        assert interview.answers[0].text == ANSWER_TAKE
        samples = await find_docs(system.store, VoiceSample)
        kinds = sorted(stored.doc.source_type for stored in samples)
        assert kinds == ["interview_answer"] * 2 + ["linkedin_post"] * 5  # answers become samples
        assert system.pushes() == ["2 quick questions", "Your article is ready for review"]
        assert system.services.notifier.sent[0][0].url == f"/studio/{drafted.id}/interview"

        # The owner's review, a staging preview, the approval, the publish at its slot.
        published = await review_and_publish(system, flagship)
        assert published.publish_at == NEXT_MONDAY  # the next article slot after Wednesday's review
        live = await article(system, flagship.article_id)
        assert live.status is S.PUBLISHED
        assert live.publish.live is True
        assert live.publish.url == f"{SITE}/blog/{published.slug}"
        assert live.publish.last_published_version == 2
        assert live.publish.verified_at == to_iso(system.clock.now())
        assert live.calibration is not None
        assert live.calibration.outcome == "gates_failed"  # gate 7 waited for the clearance

        staged = (await system.store.read("published-blogs-staging", published.slug)).body
        assert validate_published_doc(staged, now=REVIEWED_AT) == []
        doc = (await system.store.read("published-blogs", published.slug)).body
        assert doc["status"] == "published"
        assert validate_published_doc(doc, now=system.clock.now()) == []
        portfolio = system.portfolio
        assert [run.workflow for run in portfolio.dispatches] == ["preview.yml", "deploy.yml"]
        status, page = portfolio.pages[published.slug]
        assert status == 200
        assert f'name="{PUBLISHED_MARKER_NAME}" content="{doc["updatedAt"]}"' in page
        probe = f"{SITE}/blog/{published.slug}?"
        assert any(url.startswith(probe) for url in portfolio.site_requests)
        publish = await system.job(published.publish_job_id)
        assert publish.type == PUBLISH_ARTICLE
        assert [step.status for step in publish.steps] == ["succeeded"] * 7
        dispatch = publish.step("dispatch")
        assert dispatch is not None
        assert set(dispatch.remote_ids) == {"correlation", "dispatched", "run"}
        assert system.pushes()[-1] == f"Published: {live.meta.title}"
        blobs = system.services.blobs.blobs
        published_blobs = [name for container, name in blobs if container == "published"]
        assert published_blobs
        assert all(name.startswith(f"{published.slug}/") for name in published_blobs)

        # Nothing private, fetched or blocklisted leaked anywhere.
        await assert_nothing_leaked(system, caplog)
