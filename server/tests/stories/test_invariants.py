"""Invariants of W-A3 and the plan: private text and blocklist terms never leak, intake fails
closed, and the package's integration surface (jobs, schedule, lifecycle, settings)."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.core.logging import JsonFormatter
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.db import CONTAINERS
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.jobtypes import SCHEDULED_JOB_TYPES, STORIES_CLASSIFY, STORIES_SEED
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import FakeServices, make_app, make_services, sample_document
from blogwriter.jobs import Job, JobRegistry
from blogwriter.stories import (
    StoriesClassifier,
    StoriesLifecycle,
    StoriesSettings,
    get_router,
    get_settings,
    register_jobs,
    schedule_entries,
)
from blogwriter.stories.testing import classifier_reply, save_policies

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **core_write_headers(make_settings())}
MARKER = "ZQX-PRIVATE-MARKER-7731"
RAW_ANSWER = (
    f"Honestly the hard part was {MARKER}: Contoso's Falcon cutover broke 2,137 apps and "
    "Avery Testperson (avery.t@fabrikam-bank.com, 425-555-0199) kept the Northwind tenant up."
)
CONFIRMED = "The hard part was a cutover that broke 2,000+ apps while a colleague kept things up."
TERMS = ("Contoso", "Falcon", "Northwind")

type DriveJob = Callable[[str], Awaitable[Job]]


def use_real_classifier(services: FakeServices) -> None:
    """``FakeServices`` types the field as the fake; install the real classifier around it."""
    object.__setattr__(services, "classifier", StoriesClassifier(lambda: services))


def request_texts(services: FakeServices) -> list[tuple[Task, Sensitivity, str]]:
    assert services.ai is not None
    return [
        (
            call.task,
            call.request.sensitivity,
            call.request.system + "\n" + "\n".join(m.text for m in call.request.messages),
        )
        for call in services.ai.calls
    ]


async def store_dump(services: FakeServices) -> str:
    bodies = [doc.body for name in CONTAINERS for doc in await services.store.find(name)]
    return json.dumps(bodies, ensure_ascii=False)


def log_dump(records: list[logging.LogRecord]) -> str:
    formatter = JsonFormatter()
    lines = [formatter.format(record) for record in records]
    raw = [f"{record.getMessage()} {record.args!r} {record.__dict__!r}" for record in records]
    return "\n".join([*lines, *raw])


async def test_private_text_never_persists_and_reaches_only_the_anonymizer(
    services: FakeServices, drive_job: DriveJob, caplog: pytest.LogCaptureFixture
) -> None:
    use_real_classifier(services)
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, CONFIRMED, CONFIRMED, CONFIRMED)
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"), classifier_reply("pass"))
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    app = make_app(services, routers=[get_router()], settings=make_settings())
    responses: list[httpx.Response] = []
    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as http:
            for purpose in ("interview_answer", "story", "pov"):
                redacted = await http.post(
                    "/api/stories/intake/redact",
                    json={"text": RAW_ANSWER, "purpose": purpose, "pillar": "identity"},
                    headers=WRITE,
                )
                assert redacted.status_code == 200
                assert redacted.json()["proposal"] == CONFIRMED
                responses.append(redacted)
            too_long = await http.post(
                "/api/stories/intake/redact",
                json={"text": RAW_ANSWER * 80, "purpose": "story"},
                headers=WRITE,
            )
            assert too_long.status_code == 422
            responses.append(too_long)
            checked = await http.post(
                "/api/stories/intake/check", json={"text": CONFIRMED}, headers=WRITE
            )
            assert checked.json()["ok"] is True
            responses.append(checked)
            story = await http.post(
                "/api/stories",
                json={"title": "A hard cutover", "problem": CONFIRMED, "pillars": ["identity"]},
                headers=WRITE,
            )
            assert story.status_code == 201
            assert story.json()["item"]["classifier"]["passed"] is True
            pov = await http.post(
                "/api/povs", json={"pillar": "identity", "statement": CONFIRMED}, headers=WRITE
            )
            assert pov.status_code == 201
            responses.extend([story, pov])
            stale = sample_document(StoryEntry, title="A story nobody classified yet")
            await create_doc(services.store, stale)
            classify = await http.post("/api/stories/classify", headers=WRITE)
            job = await drive_job(classify.json()["jobId"])
            assert job.status == "succeeded", job.error
            responses.append(classify)

    assert MARKER not in await store_dump(services)  # documents, job documents and events
    assert services.blobs is not None
    assert all(MARKER.encode() not in data for data, _ in services.blobs.blobs.values())
    assert caplog.records
    assert MARKER not in log_dump(caplog.records)
    for response in responses:
        assert MARKER not in response.text
    carrying = [
        (task, sensitivity) for task, sensitivity, text in request_texts(services) if MARKER in text
    ]
    assert carrying == [(Task.ANONYMIZE, Sensitivity.PRIVATE)] * 3
    assert {task for task, _, _ in request_texts(services)} == {Task.ANONYMIZE, Task.FACT_CHECK}


async def test_blocklist_terms_never_reach_a_model_a_log_or_a_response(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    use_real_classifier(services)
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, "Ask Contoso about Falcon.")  # the model must not add them
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    app = make_app(services, routers=[get_router()], settings=make_settings())
    with caplog.at_level(logging.DEBUG):
        async with asgi_client(app) as http:
            redacted = await http.post(
                "/api/stories/intake/redact",
                json={"text": RAW_ANSWER, "purpose": "story"},
                headers=WRITE,
            )
            refused = await http.post(
                "/api/stories",
                json={"title": "Northwind cutover", "pillars": ["identity"]},
                headers=WRITE,
            )
            seeded = await http.post(
                "/api/stories/seed",
                json={"text": "Led the Falcon program.", "source": "cv"},
                headers=WRITE,
            )
            assert services.classifier is not None
            checks = await services.classifier.classify(
                ["The Contoso tenant.", "A clean text."], origin="owner"
            )
    assert redacted.json()["proposal"] == "Ask [client] about [internal project]."
    assert refused.status_code == 422
    assert seeded.status_code == 422
    assert [check.passed for check in checks] == [False, True]
    assert [task for task, _, _ in request_texts(services)] == [Task.ANONYMIZE, Task.FACT_CHECK]
    for task, _, text in request_texts(services):
        for term in TERMS:
            assert term not in text, f"{term} reached {task.value}"
    for response in (redacted, refused, seeded):
        for term in TERMS:
            assert term not in response.text
    assert all(term not in log_dump(caplog.records) for term in TERMS)


async def test_an_unconfigured_blocklist_refuses_every_intake_route_and_save() -> None:
    services = make_services()
    await save_policies(services.store, blocklist=False)
    story = await create_doc(services.store, sample_document(StoryEntry))
    pov = await create_doc(services.store, sample_document(PovEntry))
    story_body = {"title": "A title", "pillars": ["identity"]}
    pov_body = {"pillar": "agents", "statement": "A position."}
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        answers = [
            await http.post(
                "/api/stories/intake/redact", json={"text": "hi", "purpose": "story"}, headers=WRITE
            ),
            await http.post("/api/stories/intake/check", json={"text": "hi"}, headers=WRITE),
            await http.post("/api/stories", json=story_body, headers=WRITE),
            await http.put(
                f"/api/stories/{story.doc.id}",
                json=story_body,
                headers={**WRITE, "If-Match": story.etag},
            ),
            await http.post("/api/povs", json=pov_body, headers=WRITE),
            await http.put(
                f"/api/povs/{pov.doc.id}", json=pov_body, headers={**WRITE, "If-Match": pov.etag}
            ),
            await http.post(
                "/api/stories/seed", json={"text": "Resume text.", "source": "cv"}, headers=WRITE
            ),
            await http.post(
                "/api/stories/seed?source=cv",
                content=b"Resume text.",
                headers={**WRITE, "Content-Type": "text/plain"},
            ),
        ]
    for answer in answers:
        assert answer.status_code == 409, answer.text
        assert answer.json()["code"] == "blocklist_not_configured"
    assert services.ai is not None
    assert services.ai.calls == []
    assert services.jobs.enqueued == []


# ---------------------------------------------------------------------------------------------
# Integration surface
# ---------------------------------------------------------------------------------------------


def test_register_jobs_and_schedule_entries() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    seed = registry.get(STORIES_SEED)
    classify = registry.get(STORIES_CLASSIFY)
    assert seed is not None
    assert seed.step_names == ("extract", "classify", "save")
    assert classify is not None
    assert classify.step_names == ("classify",)
    assert registry.names() == (STORIES_CLASSIFY, STORIES_SEED)
    cadence = CadenceSettings.default("2026-11-26T12:00:00.000000Z")
    assert schedule_entries(cadence) == []
    assert STORIES_SEED not in SCHEDULED_JOB_TYPES
    with pytest.raises(ValueError, match="already registered"):
        register_jobs(registry)


async def test_the_lifecycle_installs_the_classifier() -> None:
    app = FastAPI()
    lifecycle = StoriesLifecycle()
    await lifecycle.startup(app)
    assert isinstance(app.state.classifier, StoriesClassifier)
    await lifecycle.shutdown(app)
    assert app.state.classifier is None


def test_settings_defaults_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BW_STORIES_INTAKE_MAX_CHARS", "BW_STORIES_SEED_MAX_CHARS"):
        monkeypatch.delenv(name, raising=False)
    defaults = StoriesSettings()
    assert (defaults.intake_max_chars, defaults.seed_max_chars) == (8000, 60000)
    monkeypatch.setenv("BW_STORIES_INTAKE_MAX_CHARS", "4000")
    assert StoriesSettings().intake_max_chars == 4000
    monkeypatch.setenv("BW_STORIES_SEED_MAX_CHARS", "70000")
    with pytest.raises(ValueError, match="seed_max_chars"):
        StoriesSettings()
    get_settings.cache_clear()
    monkeypatch.delenv("BW_STORIES_SEED_MAX_CHARS")
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


async def test_routes_answer_problem_json_for_missing_services() -> None:
    services = make_services()
    app = make_app(services, routers=[get_router()], settings=make_settings())
    app.state.services = None
    async with asgi_client(app) as http:
        response = await http.get("/api/stories", headers=READ)
    assert response.status_code == 503
    assert response.json()["code"] == "services_unavailable"
