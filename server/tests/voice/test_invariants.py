"""Invariants: owner text never reaches job documents, events or logs; every voice model call is
anonymized and tool-free; nothing runs from a schedule that generates content."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.jobtypes import VOICE_BUILD_GUIDE, VOICE_DIGEST
from blogwriter.domain.posts import Post
from blogwriter.domain.testing import FakeServices, sample_document
from blogwriter.voice.corpus import add_sample
from blogwriter.voice.digest import DigestDraft, PostNotes, SuggestionDraft
from blogwriter.voice.guide import ExemplarPick, GuideDraft, VocabularyItem
from blogwriter.voice.testing import LINKEDIN_POSTS, run_job, run_until_settled, seed_policy

MARKERS = ("qzv-marker-sample", "qzv-marker-final", "qzv-marker-guide", "qzv-marker-note")


def log_dump(caplog: pytest.LogCaptureFixture) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.extend(str(value) for value in record.__dict__.values())
    return "\n".join(parts)


async def test_owner_text_never_reaches_jobs_events_or_logs(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    await seed_policy(services)
    for index, text in enumerate(LINKEDIN_POSTS[:5]):
        body = {
            "sourceType": "linkedin_post",
            "text": f"{text}\n\n{MARKERS[0]} {index}.",
            "ownerUnassisted": True,
        }
        created = await client.post("/api/voice/samples", json=body, headers=write_headers)
        assert created.status_code == 201
    assert services.ai is not None
    services.ai.add(
        Task.SYNTHESIS,
        GuideDraft(
            body=f"## Openings\n\nStarts with the incident. {MARKERS[2]}",
            prefer=[f"Lead with the number. {MARKERS[2]}"],
            avoid=[],
            banned_phrases=[],
            vocabulary=[VocabularyItem(term="token", note=MARKERS[2])],
            exemplars=[ExemplarPick(sample_id="sample-x", excerpt=LINKEDIN_POSTS[0][:120])],
        ),
    )
    accepted = await client.post("/api/voice/guide/build", headers=write_headers)
    job_id = accepted.json()["jobId"]
    build = await run_until_settled(services, job_id)
    assert build.status == "succeeded", build.error

    services.clock.set(datetime(2026, 11, 30, 13, 30, tzinfo=UTC))
    post = sample_document(
        Post,
        status="posted",
        posted_at="2026-11-25T15:00:00.000000Z",
        ai_draft_text=f"Let's dive in. {MARKERS[1]}",
        final_text=f"We moved 2,000 apps. {MARKERS[1]}",
    )
    stored_post = await create_doc(services.store, post)
    services.ai.add(
        Task.SYNTHESIS,
        DigestDraft(
            posts=[PostNotes(post_id=stored_post.doc.id, notable_edits=[f"Cut it. {MARKERS[3]}"])],
            suggestions=[
                SuggestionDraft(kind="prefer", text=f"Cut openers. {MARKERS[3]}", rationale="x")
            ],
        ),
    )
    digest = await run_job(
        services,
        VOICE_DIGEST,
        {"origin": "background"},
        job_id="digest-invariants",
        scheduled_for=datetime(2026, 11, 30, 13, 0, tzinfo=UTC),
    )
    assert digest.status == "succeeded", digest.error
    view = await client.get("/api/voice/guide", headers=read_headers)
    assert view.status_code == 200

    jobs = json.dumps([doc.body for doc in await services.store.find("jobs", [])])
    logs = log_dump(caplog)
    for marker in MARKERS:
        assert marker not in jobs, marker
        assert marker not in logs, marker
    stories = json.dumps([doc.body for doc in await services.store.find("stories", [])])
    assert MARKERS[0] in stories  # the samples themselves are stored, of course
    assert MARKERS[2] in stories


async def test_every_voice_model_call_is_anonymized_and_tool_free(
    services: FakeServices,
) -> None:
    await seed_policy(services)
    for text in LINKEDIN_POSTS[:5]:
        await add_sample(
            services.store,
            source_type="linkedin_post",
            text=text,
            now=services.clock.now(),
            slop=services.slop,
        )
    assert services.ai is not None
    services.ai.add(
        Task.SYNTHESIS,
        GuideDraft(
            body="## Openings\n\nX.",
            prefer=[],
            avoid=[],
            banned_phrases=[],
            vocabulary=[],
            exemplars=[],
        ),
    )
    await run_job(services, VOICE_BUILD_GUIDE, {"origin": "owner"}, job_id="build-inv")
    for call in services.ai.calls:
        assert call.task is Task.SYNTHESIS
        assert call.request.sensitivity is Sensitivity.ANONYMIZED
        assert call.request.tools == ()
        assert not call.request.background
        assert call.job_id == "build-inv"
