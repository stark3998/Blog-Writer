"""A veto, end to end: the judge calibrated through the eval chain (analytics -> research child
-> pipeline child -> the rubric judge -> the owner's label), auto-publish switched on, a flagship
the pipeline approves on the auto path for the next slot, the Friday veto notice from the real
scheduler (push with a delivery receipt), the owner's veto (approved -> in_review, the publish
job cancelled, the calibration streak restarted), nothing shipped at the slot, and a
re-approval that publishes as a fresh run (``-r1``)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from pydantic import JsonValue

from blogwriter.ai.types import Task
from blogwriter.domain.articles import Article
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import read_doc, update_singleton
from blogwriter.domain.jobflow import rerun_job_id
from blogwriter.domain.jobtypes import PIPELINE_ARTICLE, publish_job_id
from blogwriter.domain.settings import AutonomySettings
from blogwriter.domain.states import ArticleStatus
from blogwriter.gates.testing import passing_scores
from blogwriter.pipeline.testing import (
    critique,
    draft_body,
    interview_reply,
    outline_reply,
    script_gates,
)
from blogwriter.research.testing import script_happy_path

from .conftest import (
    CITED,
    NEXT_MONDAY,
    T0,
    Owner,
    System,
    dig,
    prepare_owner,
    seed_schedules,
    start_system,
)

S = ArticleStatus
FRIDAY_NOTICE = T0 + timedelta(days=4, hours=3)
"""Friday 12:00 in New York: the default veto notice."""
VETO_NOTICE = "sched-planner-veto-notice"


def script_auto_flagship(system: System, owner: Owner, *, interview: bool) -> None:
    """A flagship whose gates all pass on their own: a POV and no anonymized story (no
    clearance needed), a passing critique the first time."""
    model = system.model
    script_happy_path(model)
    if interview:
        model.add(Task.INTERVIEW, interview_reply(2))
    model.add(Task.OUTLINE, outline_reply(story_ids=[], pov_ids=[owner.pov_id], citation_id=CITED))
    model.add(Task.DRAFT, draft_body(citation_id=CITED))
    model.add(Task.CRITIQUE, critique(passing=True))
    script_gates(model)


async def calibrate_the_judge(system: System, owner: Owner) -> None:
    """One eval topic run through the real generator and judged by gate 1; the owner labels
    the draft as-is, which recalibrates ``autonomy.judge``."""
    await system.ok(
        "POST",
        "/api/evals/topics",
        {"pillar": "agents", "format": "deep-explainer", "topic": "Agent identities in Entra"},
        status=201,
    )
    script_auto_flagship(system, owner, interview=False)
    system.model.add(Task.CRITIQUE, passing_scores())  # the judge
    run = await system.ok("POST", "/api/evals/runs", status=202)
    job = await system.wait_job(str(run["jobId"]))
    await system.idle()
    assert job.result == {
        "runId": job.id,
        "results": 1,
        "judged": 1,
        "passed": 1,
        "pushed": True,
    }
    results = await system.ok("GET", "/api/evals/results")
    items = results["items"]
    assert isinstance(items, list)
    (result,) = items
    assert isinstance(result, dict)
    eval_article = dig(result, "item", "articleId")
    assert isinstance(eval_article, str)
    stored = (await read_doc(system.store, Article, eval_article)).doc
    assert stored.purpose == "eval"
    assert stored.status is S.IN_REVIEW  # eval drafts are never approved
    await system.ok(
        "POST",
        f"/api/evals/results/{dig(result, 'item', 'id')}/label",
        {"label": "as_is"},
        etag=_etag(result),
    )


async def test_a_vetoed_auto_publish_never_ships() -> None:
    async with start_system() as system:
        owner = await prepare_owner(system)
        await calibrate_the_judge(system, owner)
        stamp = to_iso(system.clock.now())

        def six_clean_reviews(doc: AutonomySettings) -> AutonomySettings:
            # What six clean approvals would have recorded (publish's record_review).
            calibration = doc.calibration.model_copy(update={"clean_streak": 6})
            return doc.model_copy(update={"calibration": calibration, "updated_at": stamp})

        await update_singleton(system.store, AutonomySettings, six_clean_reviews, now=stamp)
        autonomy = await system.ok("GET", "/api/kb/autonomy")
        assert dig(autonomy, "decision", "reasons") == ["not_enabled"]
        etag = dig(autonomy, "settings", "etag")
        assert isinstance(etag, str)
        switched = await system.ok(
            "PUT", "/api/kb/autonomy", {"autoPublishEnabled": True}, etag=etag
        )
        assert dig(switched, "decision", "allowed") is True
        assert await seed_schedules(system, VETO_NOTICE) == [VETO_NOTICE]

        # The pipeline approves the flagship on the auto path (the owner skips the interview).
        script_auto_flagship(system, owner, interview=True)
        created = await system.ok(
            "POST",
            "/api/articles",
            {
                "meta": {
                    "title": "Agent tokens and the blast radius",
                    "pillar": "agents",
                    "format": "deep-explainer",
                }
            },
            status=201,
        )
        article_id = dig(created, "item", "id")
        assert isinstance(article_id, str)
        started = await system.ok(
            "POST",
            f"/api/articles/{article_id}/pipeline",
            {"startFrom": "research"},
            etag=_etag(created),
            status=202,
        )
        job_id = str(started["jobId"])
        await system.wait_job(job_id, {"waiting_input"}, key="interview")
        await system.run_until(T0 + timedelta(days=1))
        listed = await system.ok("GET", f"/api/interviews?articleId={article_id}")
        items = listed["items"]
        assert isinstance(items, list)
        (interview,) = items
        assert isinstance(interview, dict)
        await system.ok(
            "POST",
            f"/api/interviews/{dig(interview, 'item', 'id')}/skip",
            etag=_etag(interview),
        )
        done = await system.wait_job(job_id)
        await system.idle()
        assert isinstance(done.result, dict)
        assert done.result["decision"] == "auto"
        auto_job = publish_job_id(article_id, 1)
        assert done.result["publishJobId"] == auto_job
        approved = await article(system, article_id)
        assert approved.status is S.APPROVED
        assert approved.publish.auto is True
        assert approved.publish.publish_at == to_iso(NEXT_MONDAY)
        assert (await system.job(auto_job)).run_after == to_iso(NEXT_MONDAY)
        assert "Your article is ready for review" not in system.pushes()

        # Friday 12:00: the notice (title and link only), delivered.
        await system.run_until(FRIDAY_NOTICE + timedelta(minutes=30))
        noticed = await article(system, article_id)
        assert noticed.veto is not None
        assert noticed.veto.sent_at == to_iso(FRIDAY_NOTICE)
        assert noticed.veto.delivered is True
        payload, _ = system.services.notifier.sent[-1]
        assert payload.title == "Auto-publish scheduled. Tap to review or veto"
        assert article_id in payload.url
        view = await system.ok("GET", f"/api/articles/{article_id}/veto")
        assert view["auto"] is True
        assert dig(view, "notice", "delivered") is True

        # The veto: back to review, the job cancelled, the streak restarted.
        _, etag = await system.item(f"/api/articles/{article_id}")
        vetoed = await system.ok("POST", f"/api/articles/{article_id}/veto", etag=etag)
        assert dig(vetoed, "item", "status") == "in_review"
        assert dig(vetoed, "item", "calibration", "outcome") == "vetoed"
        assert (await system.job(auto_job)).status == "cancelled"
        autonomy = await system.ok("GET", "/api/kb/autonomy")
        assert dig(autonomy, "settings", "item", "calibration", "cleanStreak") == 0

        # Monday's slot passes: nothing is rendered, upserted, dispatched or verified.
        await system.run_until(NEXT_MONDAY + timedelta(hours=2))
        assert (await article(system, article_id)).status is S.IN_REVIEW
        assert system.portfolio.dispatches == []
        assert await system.store.find("published-blogs") == []

        # The owner approves it after all: a fresh run, published at the next slot.
        _, etag = await system.item(f"/api/articles/{article_id}")
        again = await system.ok("POST", f"/api/articles/{article_id}/approve", {}, etag=etag)
        assert again["jobId"] == rerun_job_id(auto_job, 1)
        assert dig(again, "article", "item", "publish", "auto") is False
        publish_at = dig(again, "article", "item", "publish", "publishAt")
        assert isinstance(publish_at, str)
        await system.run_until(parse_iso(publish_at))
        await system.wait_job(str(again["jobId"]))
        live = await article(system, article_id)
        assert live.status is S.PUBLISHED
        assert system.portfolio.deploy_dispatches() == 1
        jobs = await system.ok("GET", "/api/jobs")
        assert isinstance(jobs["items"], list)
        assert PIPELINE_ARTICLE in [dig(job, "item", "type") for job in jobs["items"]]


async def article(system: System, article_id: str) -> Article:
    return (await read_doc(system.store, Article, article_id)).doc


def _etag(body: Mapping[str, JsonValue]) -> str:
    etag = body["etag"]
    assert isinstance(etag, str)
    return etag
