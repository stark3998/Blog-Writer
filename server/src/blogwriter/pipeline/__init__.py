"""blogwriter.pipeline: the article pipeline (contract work order W-B1; plan "Article pipeline").

idea -> research (a chained ``research.run``) -> interview (micro: 2 questions; full: 5-8 for
case studies and frameworks; the job waits, ``waiting_input``, without holding a runner slot)
-> outline (OUTLINE) -> draft (DRAFT, streamed, cached prefix: style guide + brief + stories)
-> canonical normalization -> rubric critique (CRITIQUE) and at most two revise loops (REVISE)
-> the seven gates -> draft assets -> finalize (calibration flags; review, or the auto-publish
path once calibration, the judge and the owner allow it). Owner revisions (``pipeline.revise``)
and the editor's selection AI use REVISE too.

Guarantees: **the drafter only ever receives the brief** (never a research passage); every
model request is anonymized and tool-free, screened by the blocklist and gated by the AI-use
confirmation; private interview text is never stored (answers arrive confirmed through stories
intake); drafts served by a fallback model or with a refusal anywhere in the chain are
review-only; auto-publish stays off until the calibration streak, the judge and the owner's
switch all allow it.

Public API::

    await generate_interview(services, article, pipeline_run=n, origin=..., job_id=...)
    interview_mode(pack)                   # "micro" | "full"
    drafting_request(guide=..., brief=..., bank=..., interview=..., outline=...,
                     pack=..., pillar=...)

Wiring (``blogwriter.main``): ``get_router()``, ``register_jobs(registry)`` (``pipeline.article``,
``pipeline.revise``, ``pipeline.interview-expire``), ``schedule_entries(cadence)`` (none). No
lifecycle. Settings: :class:`PipelineSettings` (``BW_PIPELINE_MAX_REVISE_LOOPS``).
"""

from blogwriter.pipeline.interviews import (
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
from blogwriter.pipeline.jobs import register_jobs, schedule_entries
from blogwriter.pipeline.models import CritiqueOut, OutlineOut
from blogwriter.pipeline.requests import Outline, drafting_request
from blogwriter.pipeline.routes import get_router
from blogwriter.pipeline.settings import PipelineSettings, pipeline_settings
from blogwriter.pipeline.steps import Stopped, rubric_passes, run_number

__all__ = [
    "CritiqueOut",
    "InvalidAnswers",
    "Outline",
    "OutlineOut",
    "PipelineSettings",
    "StaleInterview",
    "Stopped",
    "answer_interview",
    "drafting_request",
    "expire_interview",
    "generate_interview",
    "get_router",
    "interview_due_at",
    "interview_mode",
    "pipeline_settings",
    "question_count",
    "register_jobs",
    "rubric_passes",
    "run_number",
    "schedule_entries",
    "skip_interview",
]
