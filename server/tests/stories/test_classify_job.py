"""``stories.classify``: the route, which entries it picks, and how it writes verdicts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta

import httpx
import pytest
from pydantic import JsonValue

from blogwriter.ai.ledger import BrakeEngaged, Origin
from blogwriter.ai.types import Task
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ClassifierCheck, Finding
from blogwriter.domain.docstore import create_doc, read_doc, update_doc
from blogwriter.domain.jobflow import BRAKE_DEFER, JobDeferred
from blogwriter.domain.jobtypes import STORIES_CLASSIFY
from blogwriter.domain.ports import services_installed
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeClassifier,
    FakeServices,
    ScriptedReply,
    make_app,
    sample_document,
)
from blogwriter.jobs import Job, JobFailed
from blogwriter.stories import StoriesClassifier, entry_text, get_router, needs_classifier
from blogwriter.stories.jobs import classify_entries
from blogwriter.stories.testing import classifier_reply, save_policies

WRITE = {**principal_headers(OWNER_OID), **core_write_headers(make_settings())}

type RunJob = Callable[[str, JsonValue], Awaitable[Job]]


def use_real_classifier(services: FakeServices) -> StoriesClassifier:
    """Put the real classifier where the fake one was (``FakeServices`` types the field as
    the fake, so this goes around the dataclass attribute type on purpose)."""
    real = StoriesClassifier(lambda: services)
    object.__setattr__(services, "classifier", real)
    return real


def passed_for(entry: StoryEntry | PovEntry) -> ClassifierCheck:
    return ClassifierCheck(
        passed=True, checked_at=SAMPLE_NOW, content_sha256=sha256_hex(entry_text(entry))
    )


async def seed_bank(services: FakeServices) -> dict[str, str]:
    """One current story, one stale story, one unchecked POV, one "refused" POV."""
    current = sample_document(StoryEntry, status=ReviewStatus.APPROVED)
    current = current.model_copy(update={"classifier": passed_for(current)})
    stale = sample_document(StoryEntry, title="An older title", status=ReviewStatus.APPROVED)
    stale = stale.model_copy(
        update={
            "classifier": ClassifierCheck(
                passed=True, checked_at=SAMPLE_NOW, content_sha256=sha256_hex("before the edit")
            )
        }
    )
    unchecked = sample_document(PovEntry)
    refused = sample_document(PovEntry, statement="Identity is the new perimeter for agents.")
    refused = refused.model_copy(
        update={
            "classifier": ClassifierCheck(
                passed=False,
                checked_at=SAMPLE_NOW,
                content_sha256=sha256_hex(entry_text(refused)),
                findings=[
                    Finding(code="classifier-refused", severity="block", message="Declined.")
                ],
            )
        }
    )
    for doc in (current, stale, unchecked, refused):
        await create_doc(services.store, doc)
    return {
        "current": current.id,
        "stale": stale.id,
        "unchecked": unchecked.id,
        "refused": refused.id,
    }


async def test_the_job_classifies_what_is_missing_or_stale(
    services: FakeServices, run_job: RunJob
) -> None:
    ids = await seed_bank(services)
    services.classifier = FakeClassifier(flag="perimeter")
    job = await run_job(STORIES_CLASSIFY, {"origin": "owner"})
    assert job.status == "succeeded", job.error
    assert job.result == {"classified": 3, "passed": 2, "total": 3}
    assert services.classifier is not None
    ((texts, origin),) = services.classifier.calls
    assert origin == "owner"
    assert len(texts) == 3
    stale = (await read_doc(services.store, StoryEntry, ids["stale"])).doc
    assert stale.usable
    assert not needs_classifier(stale)
    refused = (await read_doc(services.store, PovEntry, ids["refused"])).doc
    assert refused.classifier.passed is False
    assert not needs_classifier(refused)
    changed = [str(event.data["id"]) for event in job.events if event.type == "entity.changed"]
    assert sorted(changed) == sorted([ids["stale"], ids["unchecked"], ids["refused"]])
    assert any(event.type == "progress" for event in job.events)
    assert ids["current"] not in changed


async def test_nothing_to_do_is_a_quiet_success(services: FakeServices, run_job: RunJob) -> None:
    job = await run_job(STORIES_CLASSIFY, {})
    assert job.result == {"classified": 0, "passed": 0, "total": 0}
    assert services.classifier is not None
    assert services.classifier.calls == []


async def test_an_entry_edited_meanwhile_keeps_its_new_state(services: FakeServices) -> None:
    story = sample_document(StoryEntry)
    await create_doc(services.store, story)

    class EditsWhileClassifying(FakeClassifier):
        async def classify(
            self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
        ) -> list[ClassifierCheck]:
            checks = await super().classify(texts, origin=origin, job_id=job_id)
            await update_doc(
                services.store,
                StoryEntry,
                story.id,
                lambda doc: doc.model_copy(update={"lessons": "Edited by the owner meanwhile."}),
            )
            return checks

    services.classifier = EditsWhileClassifying()
    with services_installed(services):
        result = await classify_entries(origin="owner", job_id=None)
    assert result["classified"] == 0
    after = (await read_doc(services.store, StoryEntry, story.id)).doc
    assert after.lessons == "Edited by the owner meanwhile."
    assert after.classifier.passed is None


@pytest.mark.parametrize(
    ("setup", "code"),
    [("ai_use", "ai_use_not_confirmed"), ("no_classifier", "classifier_unavailable")],
)
async def test_job_failures(services: FakeServices, run_job: RunJob, setup: str, code: str) -> None:
    await create_doc(services.store, sample_document(StoryEntry))
    if setup == "ai_use":
        use_real_classifier(services)
        await save_policies(services.store, ai_use=False)
    else:
        services.classifier = None
    job = await run_job(STORIES_CLASSIFY, {"origin": "background"})
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == code


async def test_background_work_defers_on_the_brake(services: FakeServices) -> None:
    await create_doc(services.store, sample_document(StoryEntry))
    use_real_classifier(services)
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, ScriptedReply(error=BrakeEngaged(None)))
    with services_installed(services), pytest.raises(JobDeferred) as deferred:
        await classify_entries(origin="background", job_id="job-x")
    assert deferred.value.reason == "brake_over_budget"
    assert deferred.value.run_after == services.clock.now() + BRAKE_DEFER


async def test_the_real_classifier_through_the_job(services: FakeServices, run_job: RunJob) -> None:
    story = sample_document(StoryEntry)
    await create_doc(services.store, story)
    use_real_classifier(services)
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    job = await run_job(STORIES_CLASSIFY, {"origin": "owner"})
    assert job.status == "succeeded"
    (call,) = services.ai.calls
    assert call.job_id == job.id
    stored = (await read_doc(services.store, StoryEntry, story.id)).doc
    assert stored.classifier.served_by is not None
    assert stored.classifier.content_sha256 == sha256_hex(entry_text(stored))


async def test_the_classify_route(client: httpx.AsyncClient, services: FakeServices) -> None:
    response = await client.post("/api/stories/classify", headers=WRITE)
    assert response.status_code == 202
    job = await services.jobs.get(response.json()["jobId"])
    assert job.type == STORIES_CLASSIFY
    assert job.input == {"origin": "owner"}
    await save_policies(services.store, ai_use=False)
    refused = await client.post("/api/stories/classify", headers=WRITE)
    assert refused.status_code == 409
    assert refused.json()["code"] == "ai_use_not_confirmed"


@pytest.mark.parametrize(("missing", "code"), [("classifier", "classifier"), ("ai", "ai")])
async def test_the_classify_route_needs_its_services(
    services: FakeServices, missing: str, code: str
) -> None:
    setattr(services, missing, None)
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        response = await http.post("/api/stories/classify", headers=WRITE)
    assert response.status_code == 503
    assert response.json()["code"] == f"{code}_unavailable"


def test_jobfailed_is_the_failure_type() -> None:
    assert issubclass(JobFailed, Exception)
    assert timedelta(hours=1) == BRAKE_DEFER
