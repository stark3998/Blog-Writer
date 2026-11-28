"""Job type names, deterministic job ids and the inputs of jobs enqueued across packages.

A package enqueues another package's job by these constants (never by importing it), so parallel
builds stay independent. The registering package owns the steps; the input models here are the
contract. ``JobsService.enqueue`` raises ``ValueError`` for a type nobody registered (for example
a wave-B type before wave B ships): callers of cross-package hooks catch it, log
``job_hook_unregistered`` and carry on. Enqueue through
:func:`blogwriter.domain.jobflow.enqueue_once`, which decides what a ``Conflict`` on a
deterministic id means.

Job events: a step that changes a document emits :data:`ENTITY_CHANGED` with
:func:`entity_changed` (ids only), which the web maps to query invalidation. A child job records
its outcome as :data:`CHILD_RESULT` before it tells its parent (``jobflow.finish_child``). The
draft and critique steps emit :data:`DRAFT_RESET` when the router discards streamed text.

Delayed jobs carry the time they were meant to run; past their maximum lateness (after
downtime) they hold for the owner instead of firing (:data:`PUBLISH_MAX_LATENESS`,
:data:`STAGE_MAX_LATENESS`, ``jobflow.past_max_lateness``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final, Literal, Self, cast

from pydantic import Field, JsonValue

from blogwriter.domain.base import Code, DocId, DomainModel, Sha256Hex, Timestamp, parse_iso

__all__ = [
    "ALL_JOB_TYPES",
    "ANALYTICS_EVAL",
    "ANALYTICS_SUGGEST",
    "BACKUPS_DUMP",
    "CHILD_RESULT",
    "CONTENT_JOB_PREFIXES",
    "DRAFT_RESET",
    "ENGAGEMENT_PURGE",
    "ENTITY_CHANGED",
    "EVENT_TYPES",
    "GATES_RUN",
    "GATES_STANDARDS_REFRESH",
    "LINKEDIN_INTROSPECT",
    "PIPELINE_ARTICLE",
    "PIPELINE_INTERVIEW_EXPIRE",
    "PIPELINE_REVISE",
    "PLANNER_PLAN_WEEK",
    "PLANNER_POST_LIVE",
    "PLANNER_POST_SCHEDULED",
    "PLANNER_REMIND",
    "PLANNER_SCORECARD",
    "PLANNER_SLOT_CHECK",
    "PLANNER_VETO_NOTICE",
    "POSTS_CHECK",
    "POSTS_DERIVE",
    "POSTS_EXPIRE",
    "POSTS_GENERATE",
    "POSTS_RENDER",
    "POSTS_STAGE",
    "PUBLISH_ARTICLE",
    "PUBLISH_MAX_LATENESS",
    "PUBLISH_STAGING",
    "PUBLISH_UNPUBLISH",
    "RESEARCH_RUN",
    "SCHEDULED_JOB_TYPES",
    "SIGNALS_CRAWL",
    "SIGNALS_SCOUT",
    "STAGE_MAX_LATENESS",
    "STORIES_CLASSIFY",
    "STORIES_SEED",
    "VOICE_BUILD_GUIDE",
    "VOICE_DIGEST",
    "ArticleJobInput",
    "ChildResult",
    "EntityName",
    "GatesRunInput",
    "InterviewInput",
    "JobInput",
    "JsonPayload",
    "ParentRef",
    "PipelineInput",
    "PostJobInput",
    "PostLiveInput",
    "PostScheduledInput",
    "PublishInput",
    "RemindInput",
    "ResearchRunInput",
    "ReviseInput",
    "SlotJobInput",
    "derive_job_id",
    "entity_changed",
    "expire_job_id",
    "gates_job_id",
    "generates_content",
    "interview_expire_job_id",
    "pipeline_job_id",
    "post_live_job_id",
    "post_scheduled_job_id",
    "publish_job_id",
    "remind_job_id",
    "research_job_id",
    "slot_check_job_id",
    "slot_stamp",
    "stage_job_id",
]

# Wave A
RESEARCH_RUN: Final = "research.run"
SIGNALS_CRAWL: Final = "signals.crawl"
SIGNALS_SCOUT: Final = "signals.scout"
STORIES_SEED: Final = "stories.seed"
STORIES_CLASSIFY: Final = "stories.classify"
VOICE_BUILD_GUIDE: Final = "voice.build-guide"
VOICE_DIGEST: Final = "voice.digest"
GATES_RUN: Final = "gates.run"
GATES_STANDARDS_REFRESH: Final = "gates.standards-refresh"
LINKEDIN_INTROSPECT: Final = "linkedin.introspect"
BACKUPS_DUMP: Final = "backups.dump"
# Wave B
PIPELINE_ARTICLE: Final = "pipeline.article"
PIPELINE_REVISE: Final = "pipeline.revise"
PIPELINE_INTERVIEW_EXPIRE: Final = "pipeline.interview-expire"
PUBLISH_ARTICLE: Final = "publish.article"
PUBLISH_UNPUBLISH: Final = "publish.unpublish"
PUBLISH_STAGING: Final = "publish.staging"
POSTS_DERIVE: Final = "posts.derive"
POSTS_GENERATE: Final = "posts.generate"
POSTS_CHECK: Final = "posts.check"
POSTS_RENDER: Final = "posts.render"
POSTS_STAGE: Final = "posts.stage"
POSTS_EXPIRE: Final = "posts.expire"
PLANNER_PLAN_WEEK: Final = "planner.plan-week"
PLANNER_VETO_NOTICE: Final = "planner.veto-notice"
PLANNER_REMIND: Final = "planner.remind"
PLANNER_POST_LIVE: Final = "planner.post-live"
PLANNER_POST_SCHEDULED: Final = "planner.post-scheduled"
PLANNER_SLOT_CHECK: Final = "planner.slot-check"
PLANNER_SCORECARD: Final = "planner.scorecard"
ENGAGEMENT_PURGE: Final = "engagement.purge"
ANALYTICS_SUGGEST: Final = "analytics.suggest"
ANALYTICS_EVAL: Final = "analytics.eval"

ALL_JOB_TYPES: Final = (
    RESEARCH_RUN,
    SIGNALS_CRAWL,
    SIGNALS_SCOUT,
    STORIES_SEED,
    STORIES_CLASSIFY,
    VOICE_BUILD_GUIDE,
    VOICE_DIGEST,
    GATES_RUN,
    GATES_STANDARDS_REFRESH,
    LINKEDIN_INTROSPECT,
    BACKUPS_DUMP,
    PIPELINE_ARTICLE,
    PIPELINE_REVISE,
    PIPELINE_INTERVIEW_EXPIRE,
    PUBLISH_ARTICLE,
    PUBLISH_UNPUBLISH,
    PUBLISH_STAGING,
    POSTS_DERIVE,
    POSTS_GENERATE,
    POSTS_CHECK,
    POSTS_RENDER,
    POSTS_STAGE,
    POSTS_EXPIRE,
    PLANNER_PLAN_WEEK,
    PLANNER_VETO_NOTICE,
    PLANNER_REMIND,
    PLANNER_POST_LIVE,
    PLANNER_POST_SCHEDULED,
    PLANNER_SLOT_CHECK,
    PLANNER_SCORECARD,
    ENGAGEMENT_PURGE,
    ANALYTICS_SUGGEST,
    ANALYTICS_EVAL,
)

SCHEDULED_JOB_TYPES: Final = frozenset(
    {
        SIGNALS_CRAWL,
        SIGNALS_SCOUT,
        PLANNER_PLAN_WEEK,
        PLANNER_VETO_NOTICE,
        PLANNER_SCORECARD,
        VOICE_DIGEST,
        ANALYTICS_SUGGEST,
        LINKEDIN_INTROSPECT,
        GATES_STANDARDS_REFRESH,
        BACKUPS_DUMP,
        ENGAGEMENT_PURGE,
    }
)
"""The only job types a schedule item may start (contract section 8). None of them generates
content (an integration test checks every package's ``schedule_entries``)."""

CONTENT_JOB_PREFIXES: Final = ("pipeline.", "posts.", "publish.")
"""Job types that write drafts, posts or published pages: started by an owner action only."""


def generates_content(job_type: str) -> bool:
    """Whether ``job_type`` writes content (never allowed on a schedule)."""
    return job_type.startswith(CONTENT_JOB_PREFIXES)


ENTITY_CHANGED: Final = "entity.changed"
"""Job event type: ``data = {"entity": <EntityName>, "id": <document id>}`` (ids only)."""
CHILD_RESULT: Final = "child.result"
"""Job event a child job stores on itself before telling its parent: ``data`` is a
:class:`ChildResult` (ids and codes only). The parent reads it when it (re)enters its wait."""
DRAFT_RESET: Final = "draft.reset"
"""Job event of the ``draft`` and ``critique_revise`` steps when the router discards streamed
text (a refusal, retry, repair or fallback): the web clears the live text it has shown."""
EVENT_TYPES: Final = (ENTITY_CHANGED, CHILD_RESULT, DRAFT_RESET)

PUBLISH_MAX_LATENESS: Final = timedelta(hours=1)
"""``publish.article`` that starts more than this after ``publishAt`` (downtime) holds: the
article goes back to in_review (``lastError: publish_late``) and the owner re-confirms."""
STAGE_MAX_LATENESS: Final = timedelta(0)
"""How late after **the slot itself** a ``posts.stage`` may still start: never. Before the slot
it stages however late it was claimed (an approval inside the stage lead, a lead the owner set
anywhere from 15 to 240 minutes, a short downtime); at or after the slot it does nothing and the
slot check offers the next free slot. Judging against slot minus the lead skipped slots approved
inside the lead and staged after short-lead slots had passed."""

type EntityName = Literal[
    "article",
    "post",
    "idea",
    "research_run",
    "brief",
    "quick_search",
    "story",
    "pov",
    "interview",
    "voice_sample",
    "style_guide",
    "voice_digest",
    "week_plan",
    "reminder",
    "source",
    "engagement_item",
    "experiment",
    "insight_suggestion",
    "eval_result",
    "backup_status",
    "linkedin_connection",
    "settings",
]


def entity_changed(entity: EntityName, doc_id: str) -> dict[str, JsonValue]:
    """The ``data`` of an :data:`ENTITY_CHANGED` event: ``ctx.emit(ENTITY_CHANGED, ...)``."""
    return {"entity": entity, "id": doc_id}


# --------------------------------------------------------------------------------------------
# Deterministic job ids (a retried enqueue gets Conflict, never a second job)
# --------------------------------------------------------------------------------------------


def slot_stamp(utc: str) -> str:
    """``2026-12-01T15:00:00.000000Z`` -> ``20261201T150000Z`` (for ids)."""
    return f"{parse_iso(utc):%Y%m%dT%H%M%SZ}"


def research_job_id(run_id: str) -> str:
    return f"research-{run_id}"


def pipeline_job_id(article_id: str, run: int) -> str:
    """``run`` is ``article.pipelineRuns + 1``."""
    if run < 1:
        raise ValueError("pipeline runs start at 1")
    return f"pipeline-{article_id}-{run}"


def derive_job_id(article_id: str, version: int) -> str:
    return f"derive-{article_id}-v{version:05d}"


def expire_job_id(post_id: str) -> str:
    return f"expire-{post_id}"


def post_scheduled_job_id(post_id: str, scheduled_at: str) -> str:
    return f"postsched-{post_id}-{slot_stamp(scheduled_at)}"


def gates_job_id(article_id: str, content_sha256: str) -> str:
    return f"gates-{article_id}-{content_sha256[:16]}"


def publish_job_id(article_id: str, version: int) -> str:
    return f"publish-{article_id}-v{version:05d}"


def stage_job_id(post_id: str, slot_utc: str) -> str:
    return f"stage-{post_id}-{slot_stamp(slot_utc)}"


def slot_check_job_id(post_id: str, slot_utc: str) -> str:
    return f"slotcheck-{post_id}-{slot_stamp(slot_utc)}"


def remind_job_id(reminder_id: str) -> str:
    return f"remind-{reminder_id}"


def post_live_job_id(post_id: str) -> str:
    return f"postlive-{post_id}"


def interview_expire_job_id(interview_id: str) -> str:
    return f"intvexpire-{interview_id}"


# --------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------


class JsonPayload(DomainModel):
    """A JSON object passed through the jobs API (inputs, ``provide_input`` values)."""

    def to_json(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json", by_alias=True))

    @classmethod
    def from_json(cls, value: JsonValue) -> Self:
        if not isinstance(value, dict):
            raise ValueError(f"{cls.__name__} must be a JSON object")
        return cls.model_validate(value)


class JobInput(JsonPayload):
    """Base of job inputs: small references, never content.

    ``origin`` is ``owner`` when an owner click enqueued the job (its AI calls are never braked)
    and ``background`` for scheduled and chained work.
    """

    origin: Literal["owner", "background"] = "background"


class ParentRef(DomainModel):
    """A parent job waiting (``waiting_input``) for a child job's result under ``key``."""

    job_id: DocId
    key: Code


class ChildResult(JsonPayload):
    """What a child job passes to ``provide_input(parent.job_id, ..., key=parent.key)``."""

    ok: bool
    ref: str | None = Field(default=None, max_length=255)
    """The produced document id (for example the brief id)."""
    error_code: Code | None = None


class ResearchRunInput(JobInput):
    run_id: DocId
    parent: ParentRef | None = None


class ArticleJobInput(JobInput):
    article_id: DocId


class PipelineInput(ArticleJobInput):
    start_from: Literal["research", "interview", "draft"] = "research"
    interview: Literal["ask", "skip"] = "ask"
    """``skip``: no interview (eval articles, which draft from the eval topic's brief)."""
    parent: ParentRef | None = None
    """Set when another job waits for this pipeline (``analytics.eval``)."""


class ReviseInput(ArticleJobInput):
    instruction: str = Field(min_length=1, max_length=1000)
    """The owner's revision instruction (owner text, not interview text)."""


class GatesRunInput(ArticleJobInput):
    content_sha256: Sha256Hex
    parent: ParentRef | None = None


class PublishInput(ArticleJobInput):
    version: int = Field(ge=1)
    publish_at: Timestamp
    """When it was meant to go out (``run_after``; publish-now: the click). The ``check`` step
    holds when it starts later than :data:`PUBLISH_MAX_LATENESS` after this."""
    auto: bool = False
    """Auto-publish path: needs the veto notice's delivery receipt."""


class PostJobInput(JobInput):
    post_id: DocId


class SlotJobInput(PostJobInput):
    """``posts.stage`` and ``planner.slot-check``: no-op when the post's slot changed."""

    slot_utc: Timestamp


class PostLiveInput(PostJobInput):
    go_live_at: Timestamp


class PostScheduledInput(PostJobInput):
    scheduled_at: Timestamp


class RemindInput(JobInput):
    reminder_id: DocId


class InterviewInput(JobInput):
    """``pipeline.interview-expire``: at the interview's ``dueAt``, a pending interview expires
    and the waiting pipeline continues without answers."""

    interview_id: DocId
