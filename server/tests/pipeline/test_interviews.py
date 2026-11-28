"""Interviews: questions (micro and full), the push, answers (checked again, stored as confirmed
wording, voice samples, the pipeline woken), skips, expiry, and the interview routes."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.db import Filter
from blogwriter.domain.articles import ArticleMeta
from blogwriter.domain.confidentiality import AiUseNotConfirmed, ConfidentialText
from blogwriter.domain.docstore import create_doc, find_docs, read_doc, read_singleton
from blogwriter.domain.settings import CadenceSettings, FormatPacks
from blogwriter.domain.states import ArticleStatus, InterviewStatus, TransitionError
from blogwriter.domain.stories import Interview, InterviewQuestion
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    sample_document,
)
from blogwriter.domain.voice import VoiceSample
from blogwriter.jobs import InputRequest, Job
from blogwriter.pipeline import (
    InvalidAnswers,
    StaleInterview,
    answer_interview,
    expire_interview,
    generate_interview,
    interview_due_at,
    interview_mode,
    question_count,
    skip_interview,
)
from blogwriter.pipeline.models import AnswerIn
from blogwriter.pipeline.testing import (
    ANSWER_TAKE,
    BLOCKED,
    T0,
    interview_reply,
    new_article,
    sample_brief,
    seed_world,
)

from .conftest import READ, WRITE

JOB = "pipeline-art-test-1"


async def waiting_job(services: FakeServices, job_id: str = JOB) -> Job:
    """A pipeline job suspended in its ``interview`` step (what answers wake up)."""
    await services.jobs.enqueue("pipeline.article", {"articleId": "x"}, job_id=job_id)
    raw = await services.store.read("jobs", job_id)
    job = Job.from_body(raw.body)
    job.status = "waiting_input"
    job.steps = [job.steps[0]] if job.steps else []
    job.awaiting = InputRequest(
        step="interview", key="interview", prompt="Answer the interview.", requested_at=SAMPLE_NOW
    )
    body = job.to_body()
    body["steps"] = [{"name": "interview", "status": "waiting_input"}]
    await services.store.replace("jobs", body, if_match=raw.etag)
    return await services.jobs.get(job_id)


async def pending_interview(services: FakeServices, *, job_id: str | None = JOB) -> Interview:
    article = await new_article(services, status=ArticleStatus.INTERVIEWING)
    interview = sample_document(
        Interview,
        status="pending",
        mode="micro",
        article_id=article.id,
        job_id=job_id,
        due_at="2026-12-02T23:00:00.000000Z",
        questions=[
            InterviewQuestion(id="q1", text="What's your take?"),
            InterviewQuestion(id="q2", text="What would you tell a client on Monday?"),
        ],
    )
    await create_doc(services.store, interview)
    return interview


async def etag_of(services: FakeServices, interview_id: str) -> str:
    return (await read_doc(services.store, Interview, interview_id)).etag


# --------------------------------------------------------------------------------------------
# Shape and timing
# --------------------------------------------------------------------------------------------


def test_micro_for_most_formats_full_for_case_studies_and_frameworks() -> None:
    packs = FormatPacks.default(SAMPLE_NOW)
    expected = {
        "case-study": ("full", 6),
        "framework": ("full", 6),
        "deep-explainer": ("micro", 2),
        "news-analysis": ("micro", 2),
    }
    for pack in packs.packs:
        assert (interview_mode(pack), question_count(pack)) == expected[pack.id]


def test_the_interview_is_due_at_the_cadence_time() -> None:
    cadence = CadenceSettings.default(SAMPLE_NOW)
    monday = T0  # Monday 09:00 New York
    assert interview_due_at(cadence, monday) == datetime.fromisoformat("2026-12-02T23:00:00+00:00")
    late_wednesday = datetime.fromisoformat("2026-12-02T22:30:00+00:00")  # 17:30 New York
    assert interview_due_at(cadence, late_wednesday) == datetime.fromisoformat(
        "2026-12-09T23:00:00+00:00"
    )


# --------------------------------------------------------------------------------------------
# Generating
# --------------------------------------------------------------------------------------------


async def test_a_micro_interview_is_asked_once_with_one_push(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(services, status=ArticleStatus.INTERVIEWING, brief_id=brief.id)
    ai.add(Task.INTERVIEW, interview_reply(2))

    first = await generate_interview(services, article, pipeline_run=1, origin="owner", job_id=JOB)
    again = await generate_interview(services, article, pipeline_run=1, origin="owner", job_id=JOB)

    interview = first.doc
    assert again.doc.id == interview.id == Interview.id_for_pipeline(article.id, 1)
    assert interview.mode == "micro"
    assert [q.id for q in interview.questions] == ["q1", "q2"]
    assert interview.questions[0].text.startswith("What's your take on scoping agent tokens")
    assert interview.due_at == "2026-12-02T23:00:00.000000Z"
    assert interview.job_id == JOB
    assert interview.status is InterviewStatus.PENDING
    assert [(r.step, r.prompt_version) for r in interview.served_by] == [
        ("interview_questions", "interview.v1")
    ]
    calls = ai.calls_for(Task.INTERVIEW)
    assert len(calls) == 1  # a resumed step never asks twice
    request = calls[0].request
    assert request.sensitivity is Sensitivity.ANONYMIZED
    assert request.tools == ()
    assert brief.executive_summary in request.messages[0].text
    assert world.pov is not None
    assert world.pov.statement in request.messages[0].text
    assert len(services.notifier.sent) == 1
    payload, _ = services.notifier.sent[0]
    assert payload.title == "2 quick questions"
    assert payload.url == f"/studio/{article.id}/interview"


async def test_fixed_questions_when_the_model_refuses_or_answers_short(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    case_study = ArticleMeta(
        title="Moving 2,000 apps to Entra", pillar="agents", format="case-study"
    )
    article = await new_article(services, status=ArticleStatus.INTERVIEWING, meta=case_study)
    ai.add(Task.INTERVIEW, interview_reply(3))
    full = await generate_interview(services, article, pipeline_run=1, origin="owner", job_id=None)
    assert full.doc.mode == "full"
    assert len(full.doc.questions) == 6  # three from the model, three fixed ones
    assert [q.id for q in full.doc.questions] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    assert services.notifier.sent[-1][0].title == "6 interview questions"

    micro = await new_article(services, status=ArticleStatus.INTERVIEWING)
    ai.add(Task.INTERVIEW, ScriptedReply(refused=True))
    refused = await generate_interview(services, micro, pipeline_run=1, origin="owner", job_id=None)
    assert [q.text.split(" ")[0] for q in refused.doc.questions] == ["What's", "What"]
    assert [r.refused for r in refused.doc.served_by] == [True]


async def test_no_model_call_without_ai_use_or_with_a_blocklisted_brief(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services, ai_use=False)
    article = await new_article(services, status=ArticleStatus.INTERVIEWING)
    with pytest.raises(AiUseNotConfirmed):
        await generate_interview(services, article, pipeline_run=1, origin="owner", job_id=None)

    await seed_world(services, guide=False, bank=False)
    brief = sample_brief(executive_summary=f"A rollout at {BLOCKED} went wrong.")
    await create_doc(services.store, brief)
    leaky = await new_article(services, status=ArticleStatus.INTERVIEWING, brief_id=brief.id)
    with pytest.raises(ConfidentialText):
        await generate_interview(services, leaky, pipeline_run=1, origin="owner", job_id=None)
    assert ai.calls == []
    assert services.notifier.sent == []


# --------------------------------------------------------------------------------------------
# Answers, skips, expiry
# --------------------------------------------------------------------------------------------


async def test_answers_are_stored_wake_the_pipeline_and_become_voice_samples(
    services: FakeServices,
) -> None:
    await seed_world(services)
    await waiting_job(services)
    interview = await pending_interview(services)
    answers = [
        AnswerIn(question_id="q2", text="  Inventory every tool call first.  "),
        AnswerIn(question_id="q1", text=ANSWER_TAKE, redaction_findings=2),
    ]

    stored = await answer_interview(
        services,
        interview.id,
        answers,
        if_match=await etag_of(services, interview.id),
        now=T0,
    )

    assert stored.doc.status is InterviewStatus.ANSWERED
    assert [(a.question_id, a.text) for a in stored.doc.answers] == [
        ("q1", ANSWER_TAKE),
        ("q2", "Inventory every tool call first."),
    ]
    assert stored.doc.answers[0].redaction_findings == 2
    job = await services.jobs.get(JOB)
    assert job.status == "queued"
    step = job.step("interview")
    assert step is not None
    assert step.inputs["interview"] == {"interviewId": interview.id}  # ids only, never text
    samples = await find_docs(
        services.store, VoiceSample, [Filter("sourceRef", "==", interview.id)]
    )
    assert sorted(s.doc.text for s in samples) == sorted(
        ["Inventory every tool call first.", ANSWER_TAKE]
    )
    assert {s.doc.source_type for s in samples} == {"interview_answer"}

    # Sending the same answers again (a lost response) is a replay: no 412, no duplicates.
    replay = await answer_interview(
        services, interview.id, answers, if_match='"stale"', now=T0 + timedelta(minutes=1)
    )
    assert replay.etag == stored.etag
    again = await find_docs(services.store, VoiceSample, [Filter("sourceRef", "==", interview.id)])
    assert len(again) == 2


async def test_answers_are_checked_again_before_anything_is_stored(services: FakeServices) -> None:
    await seed_world(services)
    interview = await pending_interview(services)
    etag = await etag_of(services, interview.id)
    for text in (
        f"We did this at {BLOCKED} last year.",
        "Mail me at jane.doe@contoso-mail.net please.",
    ):
        with pytest.raises(ConfidentialText) as caught:
            await answer_interview(
                services,
                interview.id,
                [AnswerIn(question_id="q1", text=text)],
                if_match=etag,
                now=T0,
            )
        assert set(caught.value.findings) == {"answers[0].text"}
        assert BLOCKED not in str(caught.value)
    with pytest.raises(InvalidAnswers):
        await answer_interview(
            services, interview.id, [AnswerIn(question_id="q7", text="x")], if_match=etag, now=T0
        )
    with pytest.raises(StaleInterview):
        await answer_interview(
            services,
            interview.id,
            [AnswerIn(question_id="q1", text="ok")],
            if_match='"old"',
            now=T0,
        )
    current = await read_doc(services.store, Interview, interview.id)
    assert current.etag == etag
    assert current.doc.answers == []
    assert await find_docs(services.store, VoiceSample) == []


async def test_skip_and_expiry_wake_the_pipeline(services: FakeServices) -> None:
    await seed_world(services)
    await waiting_job(services)
    interview = await pending_interview(services)
    skipped = await skip_interview(
        services, interview.id, if_match=await etag_of(services, interview.id), now=T0
    )
    assert skipped.doc.status is InterviewStatus.SKIPPED
    step = (await services.jobs.get(JOB)).step("interview")
    assert step is not None
    assert step.inputs["interview"] == {"interviewId": interview.id, "skipped": True}
    with pytest.raises(TransitionError):
        await answer_interview(
            services,
            interview.id,
            [AnswerIn(question_id="q1", text="Too late.")],
            if_match=skipped.etag,
            now=T0,
        )

    await waiting_job(services, "pipeline-art-test-2")
    other = await pending_interview(services, job_id="pipeline-art-test-2")
    expired = await expire_interview(services, other.id, now=T0 + timedelta(days=3))
    assert expired is not None
    assert expired.status is InterviewStatus.EXPIRED
    step = (await services.jobs.get("pipeline-art-test-2")).step("interview")
    assert step is not None
    assert step.inputs["interview"] == {"interviewId": other.id, "expired": True}
    # A late answer is still accepted (expired -> answered) and becomes a voice sample.
    late = await answer_interview(
        services,
        other.id,
        [AnswerIn(question_id="q1", text=ANSWER_TAKE)],
        if_match=await etag_of(services, other.id),
        now=T0 + timedelta(days=4),
    )
    assert late.doc.status is InterviewStatus.ANSWERED
    assert await expire_interview(services, "intv-0000000000000000000000000000", now=T0) is None


# --------------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------------


async def test_interview_routes(client: httpx.AsyncClient, services: FakeServices) -> None:
    await seed_world(services)
    interview = await pending_interview(services)

    listed = await client.get(
        "/api/interviews", params={"articleId": interview.article_id}, headers=READ
    )
    assert listed.status_code == 200
    assert [item["item"]["id"] for item in listed.json()["items"]] == [interview.id]
    assert listed.headers["cache-control"] == "no-store"
    one = await client.get(f"/api/interviews/{interview.id}", headers=READ)
    assert one.status_code == 200
    etag = one.headers["etag"]

    body = {
        "answers": [
            {"questionId": "q1", "text": f"At {BLOCKED} we did it.", "redactionFindings": 0}
        ]
    }
    blocked = await client.post(
        f"/api/interviews/{interview.id}/answers", json=body, headers={**WRITE, "If-Match": etag}
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "confidential_text"
    assert BLOCKED not in blocked.text
    assert blocked.json()["findings"][0]["ref"] == "t-1"

    missing = await client.post(f"/api/interviews/{interview.id}/answers", json=body, headers=WRITE)
    assert missing.status_code == 428
    unknown = {"answers": [{"questionId": "q9", "text": "Hello there."}]}
    invalid = await client.post(
        f"/api/interviews/{interview.id}/answers", json=unknown, headers={**WRITE, "If-Match": etag}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_answers"
    stale = await client.post(
        f"/api/interviews/{interview.id}/skip", headers={**WRITE, "If-Match": '"old"'}
    )
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == etag

    good = {"answers": [{"questionId": "q1", "text": ANSWER_TAKE}]}
    answered = await client.post(
        f"/api/interviews/{interview.id}/answers", json=good, headers={**WRITE, "If-Match": etag}
    )
    assert answered.status_code == 200
    assert answered.json()["item"]["status"] == "answered"
    again = await client.post(
        f"/api/interviews/{interview.id}/skip",
        headers={**WRITE, "If-Match": answered.headers["etag"]},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"
    assert (await read_singleton(services.store, CadenceSettings, now=SAMPLE_NOW))[1] is None
