"""Seeding from the resume and portfolio: the route, the ephemeral text and the job."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.db import CONTAINERS, NotFound
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.confidentiality import AiUseNotConfirmed
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.ephemeral import SeedText
from blogwriter.domain.jobtypes import STORIES_SEED
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import FakeClassifier, FakeServices, ScriptedReply
from blogwriter.jobs import Job
from blogwriter.stories import seed_entry_id
from blogwriter.stories.jobs import SeedExtraction, SeedMetric, SeedPov, SeedStory
from blogwriter.stories.testing import save_policies

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **core_write_headers(make_settings())}
MARKER = "QZX-SEED-MARKER-4471"
RESUME = (
    "Avery Testperson | avery.t@fabrikam-bank.com | +1 (425) 555-0142\n"
    f"Identity lead. Led the Okta to Entra ID migration of 2,000+ apps. {MARKER}\n"
    "Built an agent identity pattern for MCP servers.\n"
)
SHA12 = sha256_hex(RESUME)[:12]

type DriveJob = Callable[[str], Awaitable[Job]]


def extraction() -> SeedExtraction:
    return SeedExtraction(
        stories=[
            SeedStory(
                title="Okta to Entra ID migration of 2,000+ apps",
                problem="Two identity providers and a hard deadline.",
                actions="I led the migration in waves.",
                metrics=[SeedMetric(label="Apps moved", value="2,000+")],
                pillars=["identity", "unknown"],
                tags=["Okta Migration", "entra"],
            ),
            SeedStory(title="Something off-pillar", pillars=["cooking"]),
            SeedStory(
                title="Agent identity for MCP servers",
                actions="I wrote the pattern; contact avery.t@fabrikam-bank.com.",
                pillars=["agents"],
            ),
        ],
        povs=[
            SeedPov(pillar="agents", statement="Agents need their own identities.", context="  ")
        ],
    )


async def post_seed(client: httpx.AsyncClient, text: str = RESUME) -> httpx.Response:
    return await client.post(
        "/api/stories/seed", json={"text": text, "source": "cv"}, headers=WRITE
    )


async def dump(services: FakeServices) -> str:
    bodies = [doc.body for name in CONTAINERS for doc in await services.store.find(name)]
    return json.dumps(bodies, ensure_ascii=False)


async def test_the_route_keeps_the_text_out_of_the_job(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    response = await post_seed(client)
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    assert response.json()["status"] == "queued"
    job = await services.jobs.get(job_id)
    assert job.type == STORIES_SEED
    seed_id = SeedText.id_for(RESUME)
    assert job.input == {"origin": "owner", "seedId": seed_id, "source": "cv"}
    seed = (await read_doc(services.store, SeedText, seed_id)).doc
    assert seed.text == RESUME
    assert seed.sha256 == sha256_hex(RESUME)
    assert MARKER not in json.dumps(job.to_body())


async def test_raw_text_bodies(client: httpx.AsyncClient, services: FakeServices) -> None:
    raw = await client.post(
        "/api/stories/seed?source=portfolio",
        content=RESUME.encode(),
        headers={**WRITE, "Content-Type": "text/markdown; charset=utf-8"},
    )
    assert raw.status_code == 202
    seed = (await read_doc(services.store, SeedText, SeedText.id_for(RESUME))).doc
    assert seed.source == "portfolio"
    tex = await client.post(
        "/api/stories/seed?source=cv",
        content=("\ufeff\\section{Experience} " + RESUME).encode(),
        headers={**WRITE, "Content-Type": "application/x-tex"},
    )
    assert tex.status_code == 202
    no_source = await client.post(
        "/api/stories/seed", content=b"text", headers={**WRITE, "Content-Type": "text/plain"}
    )
    assert no_source.status_code == 422
    assert no_source.json()["code"] == "source_required"
    pdf = await client.post(
        "/api/stories/seed?source=cv",
        content=b"%PDF-1.7",
        headers={**WRITE, "Content-Type": "application/pdf"},
    )
    assert pdf.status_code == 415
    latin1 = await client.post(
        "/api/stories/seed?source=cv",
        content="caf\xe9".encode("latin-1"),
        headers={**WRITE, "Content-Type": "text/plain"},
    )
    assert latin1.status_code == 415
    wrong_charset = await client.post(
        "/api/stories/seed?source=cv",
        content=b"text",
        headers={**WRITE, "Content-Type": "text/plain; charset=iso-8859-1"},
    )
    assert wrong_charset.status_code == 415
    empty = await client.post(
        "/api/stories/seed?source=cv",
        content=b"   ",
        headers={**WRITE, "Content-Type": "text/plain"},
    )
    assert empty.status_code == 422


async def test_size_limits(client: httpx.AsyncClient) -> None:
    too_many_chars = await post_seed(client, "a" * 60_001)
    assert too_many_chars.status_code == 422
    assert too_many_chars.json()["errors"][0]["type"] == "string_too_long"
    too_many_bytes = await client.post(
        "/api/stories/seed?source=cv",
        content=b"a" * (60_000 * 4 + 5),
        headers={**WRITE, "Content-Type": "text/plain"},
    )
    assert too_many_bytes.status_code == 413
    assert too_many_bytes.json()["code"] == "too_large"
    invalid = await client.post(
        "/api/stories/seed",
        content=b'{"text": 1}',
        headers={**WRITE, "Content-Type": "application/json"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_failed"
    assert "input" not in invalid.json()["errors"][0]


async def test_the_route_fails_closed(client: httpx.AsyncClient, services: FakeServices) -> None:
    blocked = await post_seed(client, RESUME + "Worked for Contoso.")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "confidential_text"
    assert "Contoso" not in blocked.text
    await save_policies(services.store, ai_use=False)
    no_ai_use = await post_seed(client)
    assert no_ai_use.status_code == 409
    assert no_ai_use.json()["code"] == "ai_use_not_confirmed"
    assert await services.store.find("ephemeral") == []


async def test_the_job_proposes_entries_with_deterministic_ids(
    client: httpx.AsyncClient, services: FakeServices, drive_job: DriveJob
) -> None:
    assert services.ai is not None
    services.ai.add(Task.SYNTHESIS, extraction())
    job = await drive_job((await post_seed(client)).json()["jobId"])
    assert job.status == "succeeded", job.error
    assert job.result == {"count": 2, "existing": 0, "skipped": 2}
    story = (await read_doc(services.store, StoryEntry, f"story-seed-{SHA12}-1")).doc
    assert story.id == seed_entry_id("story", sha256_hex(RESUME), 1)
    assert story.status.value == "proposed"
    assert story.origin == "seed"
    assert story.seed_source == "cv"
    assert story.confidentiality == "public"
    assert story.pillars == ["identity"]
    assert story.tags == ["okta-migration", "entra"]
    assert story.metrics[0].bucketed
    assert story.classifier.passed is True
    assert not story.usable  # proposed: the owner approves each entry
    assert story.served_by is not None
    assert story.served_by.step == "seed_extract"
    pov = (await read_doc(services.store, PovEntry, f"pov-seed-{SHA12}-1")).doc
    assert (pov.pillar, pov.context, pov.status.value) == ("agents", None, "proposed")
    assert pov.served_by is not None
    assert pov.served_by.step == "seed_extract"
    for missing in (f"story-seed-{SHA12}-2", f"story-seed-{SHA12}-3"):
        with pytest.raises(NotFound):
            await read_doc(services.store, StoryEntry, missing)
    with pytest.raises(NotFound):
        await read_doc(services.store, SeedText, SeedText.id_for(RESUME))
    events = {event.type: event.data for event in job.events}
    assert events["stories.seeded"] == {
        "count": 2,
        "stories": 1,
        "povs": 1,
        "existing": 0,
        "skipped": 2,
    }
    assert events["entity.changed"]["entity"] in {"story", "pov"}


async def test_the_seed_text_never_reaches_a_job_document_or_the_classifier(
    client: httpx.AsyncClient, services: FakeServices, drive_job: DriveJob
) -> None:
    assert services.ai is not None
    services.ai.add(Task.SYNTHESIS, extraction())
    job = await drive_job((await post_seed(client)).json()["jobId"])
    assert job.status == "succeeded"
    for stored in await services.store.find("jobs"):
        body = json.dumps(stored.body, ensure_ascii=False)
        assert MARKER not in body
        assert RESUME not in body
    assert MARKER not in await dump(services)
    assert services.classifier is not None
    assert all(MARKER not in text for texts, _ in services.classifier.calls for text in texts)
    (call,) = services.ai.calls_for(Task.SYNTHESIS)
    sent = "\n".join(message.text for message in call.request.messages)
    assert call.request.sensitivity is Sensitivity.PUBLIC
    assert call.request.tools == ()
    assert call.origin == "owner"
    assert "avery.t@fabrikam-bank.com" not in sent
    assert "555-0142" not in sent
    assert "[email address]" in sent
    assert "- identity: Identity modernization" in sent


async def test_seeding_twice_creates_nothing_new(
    client: httpx.AsyncClient, services: FakeServices, drive_job: DriveJob
) -> None:
    assert services.ai is not None
    services.ai.add(Task.SYNTHESIS, extraction(), extraction())
    await drive_job((await post_seed(client)).json()["jobId"])
    again = await drive_job((await post_seed(client)).json()["jobId"])
    assert again.result == {"count": 0, "existing": 2, "skipped": 2}
    assert len(await services.store.find("stories")) == 2


async def test_without_ai_use_the_entries_are_saved_unchecked(
    client: httpx.AsyncClient, services: FakeServices, drive_job: DriveJob
) -> None:
    assert services.ai is not None
    services.ai.add(Task.SYNTHESIS, extraction())
    job_id = (await post_seed(client)).json()["jobId"]
    services.classifier = FakeClassifier(error=AiUseNotConfirmed())
    job = await drive_job(job_id)
    assert job.status == "succeeded"
    story = (await read_doc(services.store, StoryEntry, f"story-seed-{SHA12}-1")).doc
    assert story.classifier.passed is None


async def test_job_failures(
    client: httpx.AsyncClient, services: FakeServices, drive_job: DriveJob
) -> None:
    assert services.ai is not None
    gone = (await post_seed(client)).json()["jobId"]
    await services.store.delete("ephemeral", SeedText.id_for(RESUME))
    failed = await drive_job(gone)
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "seed_missing"

    services.ai.add(Task.SYNTHESIS, ScriptedReply(refused=True))
    refused = await drive_job((await post_seed(client)).json()["jobId"])
    assert refused.error is not None
    assert refused.error.code == "refused"

    blocked_text = RESUME + " Northwind."
    await create_doc(services.store, SeedText.issue(blocked_text, "cv", now=services.clock.now()))
    hit = await services.jobs.enqueue(
        STORIES_SEED, {"seedId": SeedText.id_for(blocked_text), "source": "cv"}
    )
    blocked = await drive_job(hit.id)
    assert blocked.error is not None
    assert blocked.error.code == "blocklist_hit"


async def test_an_expired_seed_text_fails(services: FakeServices, drive_job: DriveJob) -> None:
    seed = SeedText.issue(RESUME, "cv", now=services.clock.now())
    await create_doc(services.store, seed)
    job = await services.jobs.enqueue(STORIES_SEED, {"seedId": seed.id, "source": "cv"})
    services.clock.advance(2 * 86_400)
    failed = await drive_job(job.id)
    assert failed.error is not None
    assert failed.error.code == "seed_expired"
