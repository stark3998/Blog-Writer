"""Interviews: the questions (INTERVIEW), the owner's confirmed answers, skips and expiry.

**Private interview text is never persisted.** The owner's raw dictation stays in the browser
and in the stories intake ``redact`` request; what reaches :func:`answer_interview` is the
wording the owner confirmed after redaction, and it is checked again here (blocklist plus the
blocking local findings: e-mail, phone, links, IP addresses, handles) before it is stored. The
pipeline job is woken with ``{"interviewId": ...}`` only: no answer text ever goes into a job
document, an event or a log.

A micro-interview has two questions ("What's your take?" and "What would you tell a client on
Monday?", tailored to the article); a full one (case studies and frameworks) has 5 to 8. The
questions come from the brief and the story bank's gaps, never from private text. When the model
refuses or answers out of shape, fixed questions take its place: the owner's input matters more
than the wording of the question.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Task
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Conflict, NotFound, PreconditionFailed
from blogwriter.domain.articles import Article
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.common import Finding, ServedByRecord
from blogwriter.domain.confidentiality import (
    ConfidentialText,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.docstore import Stored, create_doc, read_doc, read_singleton, replace_doc
from blogwriter.domain.jobflow import defer_on_brake
from blogwriter.domain.ports import JobQueue, Services, ServiceUnavailable
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import (
    Blocklist,
    CadenceSettings,
    EmployerPolicy,
    FormatPack,
    Pillar,
    PillarSettings,
)
from blogwriter.domain.slots import weekly_slot
from blogwriter.domain.states import INTERVIEW_MACHINE, InterviewStatus
from blogwriter.domain.stories import Interview, InterviewAnswer, InterviewQuestion
from blogwriter.domain.weeks import add_days, local_date_time, week_start
from blogwriter.jobs import Job, JobStateError
from blogwriter.pipeline.material import push, read_optional, request_texts, resolve_subject
from blogwriter.pipeline.models import AnswerIn, InterviewOut
from blogwriter.pipeline.prompting import INTERVIEW
from blogwriter.pipeline.requests import interview_request
from blogwriter.pipeline.text import clip
from blogwriter.stories import StoryBank, load_bank, local_findings, story_gaps
from blogwriter.voice import add_sample

__all__ = [
    "INTERVIEW_KEY",
    "MIN_INTERVIEW_LEAD",
    "InterviewMode",
    "InvalidAnswers",
    "StaleInterview",
    "SubjectIncomplete",
    "answer_interview",
    "expire_interview",
    "generate_interview",
    "interview_due_at",
    "interview_mode",
    "question_count",
    "skip_interview",
    "template_questions",
    "wake_pipeline",
]

type InterviewMode = Literal["micro", "full"]

INTERVIEW_KEY: Final = "interview"
"""The ``wait_for_input`` key of the pipeline's ``interview`` step."""
MIN_INTERVIEW_LEAD: Final = timedelta(hours=2)
"""An interview is due at the next ``cadence.interview_due`` at least this far ahead."""
_WAKE_ATTEMPTS: Final = 4
_WAKE_PAUSE_SECONDS: Final = 0.25
_MAX_GAPS: Final = 10

_logger: Final = logging.getLogger("blogwriter.pipeline.interviews")
register_log_fields("interview_id", "article_id", "questions", "answers", "samples")


class StaleInterview(PreconditionFailed):
    """412: the interview changed since the owner loaded it (``current`` for the dialog)."""

    def __init__(self, current: Stored[Interview]) -> None:
        super().__init__(f"stories/{current.doc.id}: changed since it was loaded")
        self.current = current


class SubjectIncomplete(ValueError):
    """The article's title, pillar or format is not set (an interview needs all three)."""


class InvalidAnswers(ValueError):
    """The answers name questions the interview does not have, or one question twice (422)."""

    code: Final = "invalid_answers"


# --------------------------------------------------------------------------------------------
# Shape and timing
# --------------------------------------------------------------------------------------------


def interview_mode(pack: FormatPack) -> InterviewMode:
    """``full`` for formats that need the full interview (case studies, frameworks), else
    ``micro``."""
    return "full" if pack.owner_input == "full_interview" else "micro"


def question_count(pack: FormatPack) -> int:
    """How many questions to ask: 2 for a micro-interview, the middle of the pack's range for a
    full one (5-8 by default: 6), never more than an interview holds (8)."""
    if interview_mode(pack) == "micro":
        return 2
    middle = (pack.min_questions + pack.max_questions) // 2
    return max(1, min(8, middle))


def interview_due_at(cadence: CadenceSettings, now: datetime) -> datetime:
    """The next ``cadence.interview_due`` (Wednesday 18:00 by default) that is at least
    :data:`MIN_INTERVIEW_LEAD` after ``now``."""
    earliest = now + MIN_INTERVIEW_LEAD
    monday = week_start(local_date_time(to_iso(now), cadence.timezone)[0])
    for offset in range(3):
        slot = weekly_slot(add_days(monday, 7 * offset), cadence.interview_due, cadence.timezone)
        moment = parse_iso(slot.utc)
        if moment >= earliest:
            return moment
    raise AssertionError("unreachable: a weekly time recurs within two weeks")  # pragma: no cover


_MICRO: Final = (
    ("What's your take on {topic}?", "Your position in your own words: the draft is built on it."),
    (
        "What would you tell a client to do first on Monday about {topic}?",
        "The concrete first step readers can act on.",
    ),
)
_FULL: Final = (
    (
        "What was the situation with {topic}, and which constraints shaped your approach?",
        "Gives the article its context and stakes.",
    ),
    (
        "Which decision mattered most, and what trade-off did you accept?",
        "The decision record readers cannot get from the sources.",
    ),
    (
        "What did you measure or observe along the way? Ranges or rounded numbers are fine.",
        "First-hand numbers make the claims checkable.",
    ),
    (
        "What went wrong or surprised you, and how did you notice?",
        "Failure modes are what readers came for.",
    ),
    (
        "What would you change if you did it again?",
        "Turns the story into advice.",
    ),
    (
        "Which step do teams most often get wrong, and how do they spot it early?",
        "Gives the pitfalls section its substance.",
    ),
    (
        "If you had to name your approach as a framework, what are its steps or levels?",
        "Frameworks must be yours, never invented by the draft.",
    ),
    (
        "What would you tell a client to do first on Monday about {topic}?",
        "The concrete first step readers can act on.",
    ),
)


def template_questions(topic: str, mode: InterviewMode, count: int) -> list[InterviewQuestion]:
    """Fixed questions (used when the model refuses or answers out of shape)."""
    subject = clip(topic, 120) or "this topic"
    source = _MICRO if mode == "micro" else _FULL
    picked = [source[index % len(source)] for index in range(count)]
    return [
        InterviewQuestion(
            id=f"q{index}", text=text.format(topic=subject)[:500], purpose=purpose[:300]
        )
        for index, (text, purpose) in enumerate(picked, 1)
    ]


def _questions_from(
    reply: InterviewOut | None, *, topic: str, mode: InterviewMode, count: int
) -> list[InterviewQuestion]:
    fallback = template_questions(topic, mode, count)
    if reply is None:
        return fallback
    asked: list[InterviewQuestion] = []
    for item in reply.questions:
        text = clip(item.text, 500)
        if len(text) < 8 or any(text == question.text for question in asked):
            continue
        asked.append(
            InterviewQuestion(
                id=f"q{len(asked) + 1}", text=text, purpose=clip(item.purpose, 300) or None
            )
        )
        if len(asked) == count:
            break
    if len(asked) < count:
        for question in fallback[len(asked) :]:
            asked.append(question.model_copy(update={"id": f"q{len(asked) + 1}"}))
    return asked


def _gap_lines(bank: StoryBank, pillars: PillarSettings, pillar: Pillar) -> list[str]:
    titles = {story.id: story.title for story in bank.stories}
    messages = {
        "metrics-missing": 'The story "{title}" has no metrics yet.',
        "decisions-missing": 'The story "{title}" does not say which decision mattered.',
        "lessons-missing": 'The story "{title}" has no lessons yet.',
    }
    lines: list[str] = []
    for code in story_gaps(bank, pillars):
        kind, _, ref = code.partition(":")
        if kind == "no-stories" and ref == pillar.id:
            lines.append(f"No approved story on {pillar.name} yet: ask for one that fits.")
        elif kind == "few-povs" and ref == pillar.id:
            lines.append(f"Fewer than three positions on {pillar.name}: ask for a clear stance.")
        elif kind in messages and ref in titles:
            lines.append(messages[kind].format(title=clip(titles[ref], 100)))
        if len(lines) >= _MAX_GAPS:
            break
    return lines


# --------------------------------------------------------------------------------------------
# Generating
# --------------------------------------------------------------------------------------------


async def generate_interview(
    services: Services,
    article: Article,
    *,
    pipeline_run: int,
    origin: Origin,
    job_id: str | None,
) -> Stored[Interview]:
    """The interview of one pipeline run (id ``Interview.id_for_pipeline(article.id, run)``).

    An interview that exists already is returned as it is: no model call and no push (a resumed
    step never asks twice). Otherwise INTERVIEW (anonymized: the brief, the story bank's gaps
    and the owner's positions; tool-free) writes the questions, the interview is created
    ``pending`` with ``dueAt`` from the cadence, and the owner gets one push ("2 quick
    questions" -> ``/studio/<id>/interview``); a create that hits ``Conflict`` returns the
    existing interview and pushes nothing.

    Raises ``AiUseNotConfirmed``, ``BlocklistNotConfigured``, ``ConfidentialText``,
    ``ServiceUnavailable("ai")`` and :class:`SubjectIncomplete`.
    """
    store = services.store
    interview_id = Interview.id_for_pipeline(article.id, pipeline_run)
    try:
        return await read_doc(store, Interview, interview_id)
    except NotFound:
        pass
    now = services.clock.now()
    stamp = to_iso(now)
    subject = await resolve_subject(store, article, now=stamp)
    if subject.pillar is None or subject.pack is None or subject.topic is None:
        raise SubjectIncomplete("the article's pillar, format and title are needed")
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    require_ai_use(policy)
    runner = services.ai
    if runner is None:
        raise ServiceUnavailable("ai")
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    mode = interview_mode(subject.pack)
    count = question_count(subject.pack)
    bank = await load_bank(store, pillar=subject.pillar.id, usable_only=True)
    brief = await read_optional(store, Brief, article.brief_id)
    request = interview_request(
        topic=subject.topic,
        pack=subject.pack,
        pillar=subject.pillar,
        brief=brief,
        gaps=_gap_lines(bank, pillars, subject.pillar),
        positions=[pov.statement for pov in bank.povs],
        count=count,
        mode=mode,
    )
    check_texts(request_texts(request), blocklist)
    with defer_on_brake(now):
        result = await runner.run(Task.INTERVIEW, request, origin=origin, job_id=job_id)
    reply: InterviewOut | None = None
    if not result.refused:
        try:
            reply = result.output(InterviewOut)
        except TypeError:
            reply = None
    questions = _questions_from(reply, topic=subject.topic, mode=mode, count=count)
    due = interview_due_at(cadence, now)
    record = ServedByRecord.from_served_by(
        result.served_by,
        step="interview_questions",
        at=stamp,
        refused=result.refused or bool(result.refusals),
        prompt_version=INTERVIEW,
        cost_usd=float(result.cost_usd),
    )
    interview = Interview(
        id=interview_id,
        created_at=stamp,
        updated_at=stamp,
        status=InterviewStatus.PENDING,
        status_changed_at=stamp,
        mode=mode,
        article_id=article.id,
        questions=questions,
        due_at=to_iso(due),
        job_id=job_id,
        served_by=[record],
    )
    try:
        stored = await create_doc(store, interview)
    except Conflict:
        return await read_doc(store, Interview, interview_id)
    _logger.info(
        "pipeline_interview_created",
        extra={"interview_id": interview_id, "article_id": article.id, "questions": count},
    )
    title = f"{count} quick questions" if mode == "micro" else f"{count} interview questions"
    await push(
        services,
        title=title,
        url=f"/studio/{article.id}/interview",
        tag=f"interview:{interview_id}",
    )
    return stored


# --------------------------------------------------------------------------------------------
# Waking the pipeline
# --------------------------------------------------------------------------------------------


async def wake_pipeline(
    jobs: JobQueue,
    job_id: str | None,
    value: Mapping[str, JsonValue],
    *,
    attempts: int = _WAKE_ATTEMPTS,
    pause: float = _WAKE_PAUSE_SECONDS,
) -> bool:
    """Give the waiting pipeline its interview outcome (``provide_input`` with key
    ``interview``); ``True`` when it took it.

    A job that went on already (expiry), finished or waits for something else is left alone
    (``JobStateError`` and ``NotFound`` are not errors here). A job still running (it has not
    suspended yet) is asked again a few times: its ``interview`` step also reads the interview
    document before it waits, so an answer is never lost either way.
    """
    if job_id is None:
        return False
    payload: dict[str, JsonValue] = dict(value)
    for attempt in range(attempts):
        try:
            await jobs.provide_input(job_id, payload, key=INTERVIEW_KEY)
            return True
        except JobStateError as exc:
            if exc.code != "not_waiting":
                return False
        except NotFound:
            return False
        try:
            job: Job = await jobs.get(job_id)
        except NotFound:
            return False
        awaiting = job.awaiting
        if job.status == "waiting_input" and awaiting is not None and awaiting.key == INTERVIEW_KEY:
            continue
        if job.status != "running" or attempt + 1 >= attempts:
            return False
        await asyncio.sleep(pause)
    return False


def _outcome(interview: Interview) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {"interviewId": interview.id}
    if interview.status is InterviewStatus.SKIPPED:
        value["skipped"] = True
    elif interview.status is InterviewStatus.EXPIRED:
        value["expired"] = True
    return value


# --------------------------------------------------------------------------------------------
# Answers, skips, expiry
# --------------------------------------------------------------------------------------------


def _same_answers(stored: Sequence[InterviewAnswer], sent: Sequence[AnswerIn]) -> bool:
    return sorted((a.question_id, a.text) for a in stored) == sorted(
        (a.question_id, a.text.strip()) for a in sent
    )


def _check_answers(interview: Interview, answers: Sequence[AnswerIn]) -> None:
    known = {question.id for question in interview.questions}
    ids = [answer.question_id for answer in answers]
    if len(ids) != len(set(ids)) or not set(ids) <= known:
        raise InvalidAnswers("answers must name each of the interview's questions at most once")
    if any(not answer.text.strip() for answer in answers):
        raise InvalidAnswers("answers must not be blank")


def _confidential(answers: Sequence[AnswerIn], blocklist: Blocklist) -> None:
    """The blocklist (``check_texts``: fails closed) and the blocking local findings."""
    fields = {f"answers[{index}].text": answer.text for index, answer in enumerate(answers)}
    check_texts(fields, blocklist)
    hits: dict[str, list[Finding]] = {}
    for name, text in fields.items():
        blocking = [f for f in local_findings(text, blocklist) if f.severity == "block"]
        if blocking:
            hits[name] = blocking
    if hits:
        raise ConfidentialText(hits)


async def _voice_samples(services: Services, interview: Interview, *, now: datetime) -> int:
    """Each confirmed answer also becomes a voice sample (``interview_answer``, retry-safe ids:
    the source is the interview). Best effort: a failure is logged and the answer stays."""
    article = await read_optional(services.store, Article, interview.article_id)
    pillar = article.meta.pillar if article is not None else None
    questions = {question.id: question.text for question in interview.questions}
    added = 0
    for answer in interview.answers:
        try:
            await add_sample(
                services.store,
                source_type="interview_answer",
                text=answer.text,
                now=now,
                slop=services.slop,
                title=clip(questions.get(answer.question_id), 200) or None,
                pillar=pillar,
                source_ref=interview.id,
            )
            added += 1
        except Exception as exc:
            _logger.warning(
                "pipeline_voice_sample_failed",
                extra={"interview_id": interview.id, **describe_exception(exc)},
            )
    return added


async def _after_answers(services: Services, interview: Interview, *, now: datetime) -> None:
    await wake_pipeline(services.jobs, interview.job_id, _outcome(interview))
    added = await _voice_samples(services, interview, now=now)
    _logger.info(
        "pipeline_interview_answered",
        extra={"interview_id": interview.id, "answers": len(interview.answers), "samples": added},
    )


async def answer_interview(
    services: Services,
    interview_id: str,
    answers: Sequence[AnswerIn],
    *,
    if_match: str,
    now: datetime,
) -> Stored[Interview]:
    """Store the owner's confirmed answers (pending or expired -> answered), wake the pipeline
    with ``{"interviewId": id}`` and add each answer as a voice sample.

    The text was confirmed through stories intake and is checked again: the blocklist
    (``ConfidentialText``, ``BlocklistNotConfigured``) and the blocking local findings.
    Sending the same answers again to an answered interview is a replay (the pipeline is woken
    and missing samples are added; no 412). Raises :class:`StaleInterview`,
    :class:`InvalidAnswers`, ``TransitionError`` (skipped or already answered differently).
    """
    store = services.store
    current = await read_doc(store, Interview, interview_id)
    doc = current.doc
    if doc.status is InterviewStatus.ANSWERED and _same_answers(doc.answers, answers):
        await _after_answers(services, doc, now=now)
        return current
    if current.etag != if_match:
        raise StaleInterview(current)
    target = INTERVIEW_MACHINE.transition(doc.status, InterviewStatus.ANSWERED)
    _check_answers(doc, answers)
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    _confidential(answers, blocklist)
    confirmed = [
        InterviewAnswer(
            question_id=answer.question_id,
            text=answer.text.strip(),
            confirmed_at=stamp,
            redaction_findings=answer.redaction_findings,
        )
        for answer in answers
    ]
    order = {question.id: index for index, question in enumerate(doc.questions)}
    confirmed.sort(key=lambda answer: order[answer.question_id])
    changed = doc.model_copy(
        update={
            "status": target,
            "status_changed_at": stamp,
            "updated_at": stamp,
            "answers": confirmed,
        }
    )
    try:
        stored = await replace_doc(store, changed, if_match=current.etag)
    except PreconditionFailed:
        raise StaleInterview(await read_doc(store, Interview, interview_id)) from None
    await _after_answers(services, stored.doc, now=now)
    return stored


async def skip_interview(
    services: Services, interview_id: str, *, if_match: str, now: datetime
) -> Stored[Interview]:
    """pending or expired -> skipped; the pipeline goes on with the story bank
    (``{"interviewId": id, "skipped": true}``). Skipping a skipped interview is a replay."""
    store = services.store
    current = await read_doc(store, Interview, interview_id)
    doc = current.doc
    if doc.status is InterviewStatus.SKIPPED:
        await wake_pipeline(services.jobs, doc.job_id, _outcome(doc))
        return current
    if current.etag != if_match:
        raise StaleInterview(current)
    target = INTERVIEW_MACHINE.transition(doc.status, InterviewStatus.SKIPPED)
    stamp = to_iso(now)
    changed = doc.model_copy(
        update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
    )
    try:
        stored = await replace_doc(store, changed, if_match=current.etag)
    except PreconditionFailed:
        raise StaleInterview(await read_doc(store, Interview, interview_id)) from None
    await wake_pipeline(services.jobs, stored.doc.job_id, _outcome(stored.doc))
    return stored


async def expire_interview(
    services: Services, interview_id: str, *, now: datetime
) -> Interview | None:
    """At ``dueAt``: a pending interview expires and the waiting pipeline goes on without
    answers (``{"interviewId": id, "expired": true}``). An answered, skipped or already expired
    interview is delivered again (harmless: a pipeline that went on ignores it), so an answer
    whose wake-up was lost still reaches the pipeline. ``None`` when the interview is gone."""
    store = services.store
    try:
        current = await read_doc(store, Interview, interview_id)
    except NotFound:
        return None
    doc = current.doc
    if doc.status is InterviewStatus.PENDING:
        stamp = to_iso(now)
        target = INTERVIEW_MACHINE.transition(doc.status, InterviewStatus.EXPIRED)
        changed = doc.model_copy(
            update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
        )
        try:
            doc = (await replace_doc(store, changed, if_match=current.etag)).doc
        except PreconditionFailed:
            doc = (await read_doc(store, Interview, interview_id)).doc  # answered meanwhile
    await wake_pipeline(services.jobs, doc.job_id, _outcome(doc))
    return doc
