"""Job type names, deterministic job ids and cross-package job inputs."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from blogwriter.db.base import validate_id
from blogwriter.domain import jobtypes
from blogwriter.domain.jobtypes import (
    ALL_JOB_TYPES,
    CONTENT_JOB_PREFIXES,
    ENTITY_CHANGED,
    EVENT_TYPES,
    PIPELINE_INTERVIEW_EXPIRE,
    SCHEDULED_JOB_TYPES,
    ChildResult,
    InterviewInput,
    ParentRef,
    PipelineInput,
    PostLiveInput,
    PublishInput,
    ResearchRunInput,
    SlotJobInput,
    derive_job_id,
    entity_changed,
    expire_job_id,
    gates_job_id,
    generates_content,
    interview_expire_job_id,
    pipeline_job_id,
    post_live_job_id,
    post_scheduled_job_id,
    publish_job_id,
    remind_job_id,
    research_job_id,
    slot_check_job_id,
    slot_stamp,
    stage_job_id,
)
from blogwriter.jobs.models import NAME_PATTERN, Job
from blogwriter.jobs.runner import JobRegistry, Step, StepContext

SLOT = "2026-12-01T15:00:00.000000Z"


def test_job_type_names_are_valid_and_unique() -> None:
    assert len(ALL_JOB_TYPES) == len(set(ALL_JOB_TYPES))
    assert all(re.fullmatch(NAME_PATTERN, name) for name in [*ALL_JOB_TYPES, *EVENT_TYPES])
    constants = {
        value
        for name, value in vars(jobtypes).items()
        if name.isupper() and isinstance(value, str) and value not in EVENT_TYPES
    }
    assert constants == set(ALL_JOB_TYPES)
    assert PIPELINE_INTERVIEW_EXPIRE in ALL_JOB_TYPES


def test_no_schedule_starts_content_work() -> None:
    """Invariant: feeds and schedules never generate content without an owner action."""
    assert set(ALL_JOB_TYPES) >= SCHEDULED_JOB_TYPES
    assert not any(generates_content(job_type) for job_type in SCHEDULED_JOB_TYPES)
    content = {t for t in ALL_JOB_TYPES if t.startswith(CONTENT_JOB_PREFIXES)}
    assert content == {t for t in ALL_JOB_TYPES if generates_content(t)}
    assert "pipeline.article" in content
    assert "publish.article" in content


def test_every_type_registers() -> None:
    async def step(_ctx: StepContext) -> None:
        return None

    registry = JobRegistry()
    for name in ALL_JOB_TYPES:
        registry.register(name, [Step("run", step)])
    assert set(registry.names()) == set(ALL_JOB_TYPES)


def test_deterministic_job_ids_are_valid_ids() -> None:
    ids = [
        research_job_id("run-018f00aa"),
        gates_job_id("art-1", "a" * 64),
        publish_job_id("art-1", 3),
        stage_job_id("post-1", SLOT),
        slot_check_job_id("post-1", SLOT),
        remind_job_id("rem-1"),
        post_live_job_id("post-1"),
        pipeline_job_id("art-1", 2),
        derive_job_id("art-1", 4),
        expire_job_id("post-1"),
        post_scheduled_job_id("post-1", SLOT),
        interview_expire_job_id("intv-1"),
    ]
    assert [validate_id(job_id) for job_id in ids] == ids
    assert stage_job_id("post-1", SLOT) == "stage-post-1-20261201T150000Z"
    assert publish_job_id("art-1", 3) == "publish-art-1-v00003"
    assert slot_stamp(SLOT) == "20261201T150000Z"
    assert pipeline_job_id("art-1", 2) == "pipeline-art-1-2"
    with pytest.raises(ValueError, match="start at 1"):
        pipeline_job_id("art-1", 0)


def test_inputs_round_trip_as_camel_case_json() -> None:
    research = ResearchRunInput.model_validate(
        {"runId": "run-1", "parent": {"jobId": "job-1", "key": "research"}}
    )
    assert research.to_json() == {
        "origin": "background",
        "runId": "run-1",
        "parent": {"jobId": "job-1", "key": "research"},
    }
    assert ResearchRunInput.from_json(research.to_json()) == research
    assert PipelineInput(article_id="art-1", origin="owner").to_json() == {
        "origin": "owner",
        "articleId": "art-1",
        "startFrom": "research",
        "interview": "ask",
        "parent": None,
    }
    child_pipeline = PipelineInput(
        article_id="art-1",
        start_from="draft",
        interview="skip",
        parent=ParentRef(job_id="job-eval", key="eval-0"),
    )
    assert PipelineInput.from_json(child_pipeline.to_json()) == child_pipeline
    publish = PublishInput(article_id="art-1", version=2, publish_at=SLOT, auto=True)
    assert publish.to_json()["auto"] is True
    assert publish.to_json()["publishAt"] == SLOT
    assert InterviewInput(interview_id="intv-1").to_json()["interviewId"] == "intv-1"
    assert (
        ChildResult(ok=False, error_code="blocklist_hit").to_json()["errorCode"] == "blocklist_hit"
    )
    assert PostLiveInput(post_id="post-1", go_live_at=SLOT).to_json()["goLiveAt"] == SLOT


def test_inputs_are_validated() -> None:
    assert "origin" not in ChildResult(ok=True, ref="brief-1").to_json()
    with pytest.raises(ValueError, match="JSON object"):
        PipelineInput.from_json(["art-1"])
    with pytest.raises(ValidationError):
        SlotJobInput.from_json({"postId": "post-1", "slotUtc": "tomorrow"})
    with pytest.raises(ValidationError):
        PublishInput.from_json({"articleId": "art-1", "version": 0, "publishAt": SLOT})
    with pytest.raises(ValidationError):
        PublishInput.from_json({"articleId": "art-1", "version": 1})  # publishAt is required


def test_entity_changed_events_fit_a_job_event() -> None:
    job = Job(id="job-1", type="pipeline.article", run_after=SLOT, created_at=SLOT, updated_at=SLOT)
    event = job.add_event(ENTITY_CHANGED, at=SLOT, data=entity_changed("article", "art-1"))
    assert event.data == {"entity": "article", "id": "art-1"}
