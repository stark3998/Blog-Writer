"""``posts.stage`` at T-60: render check, upload, token, ready (push); a no-op after a re-slot,
an edit, a switch to the kit and after downtime past its lateness; failures alert and push.
``posts.expire``: time-sensitive posts expire unless posted."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import read_doc, read_singleton, upsert_doc
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    POSTS_EXPIRE,
    POSTS_STAGE,
    PostJobInput,
    SlotJobInput,
    stage_job_id,
)
from blogwriter.domain.posts import Approval, Post, Slot
from blogwriter.domain.settings import Blocklist, BrandKit, CadenceSettings
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import (
    FakeServices,
    MemoryBlobStore,
    RecordingNotifier,
    sample_document,
)
from blogwriter.jobs import Job
from blogwriter.linkedin.posts.carousel import render_post_media, spec_json
from blogwriter.linkedin.posts.serialize import serialize
from blogwriter.linkedin.posts.staging import StagingFailed, TokenCheck
from blogwriter.linkedin.posts.testing import (
    T0,
    CompliantRenderService,
    FakeTransport,
    StepError,
    carousel_slides,
    run_job,
    seed_settings,
)
from blogwriter.render.spec import CarouselSpec


def _error_code(job: Job) -> str | None:
    return job.error.code if job.error is not None else None


# T0 is 07:00 in New York: an 08:00 slot is staged at T0 (60 minutes ahead).
SLOT = Slot.at("2026-11-26", "08:00", "America/New_York", "primary")
LATER = Slot.at("2026-11-27", "10:00", "America/New_York", "primary")


def notifier(services: FakeServices) -> RecordingNotifier:
    assert isinstance(services.notifier, RecordingNotifier)
    return services.notifier


def blobs(services: FakeServices) -> MemoryBlobStore:
    assert services.blobs is not None
    return services.blobs


async def approved_post(services: FakeServices, *, carousel: bool = True, **fields: object) -> Post:
    display, payload = serialize("Swipe for the order that worked.", [])
    values: dict[str, object] = {
        "status": PostStatus.APPROVED,
        "display_text": display,
        "payload": payload,
        "slot": SLOT,
        "go_live_mode": "one_click",
        **fields,
    }
    post = sample_document(Post, **values)
    if carousel:
        spec = spec_json(CarouselSpec(title="Deck", slides=carousel_slides(8)))
        post = post.model_copy(
            update={"format": "carousel", "carousel_spec": spec, "document_title": "Deck"}
        )
        await seed_settings(services)
        assert services.render is not None
        assert services.blobs is not None
        brand, _ = await read_singleton(services.store, BrandKit, now=to_iso(T0))
        blocklist, _ = await read_singleton(services.store, Blocklist, now=to_iso(T0))
        rendered = await render_post_media(
            post,
            render=services.render,
            blobs=services.blobs,
            brand=brand,
            blocklist=blocklist,
            disclaimer=None,
        )
        post = post.model_copy(update={"media": list(rendered.media)})
    post = post.model_copy(
        update={"approval": Approval(sha256=post.compute_approval_hash(), approved_at=to_iso(T0))}
    )
    await upsert_doc(services.store, post)
    return post


async def stage(
    services: FakeServices, post: Post, transport: FakeTransport, slot: Slot = SLOT
) -> tuple[Post, Job]:
    job = await run_job(
        services,
        POSTS_STAGE,
        SlotJobInput(post_id=post.id, slot_utc=slot.utc, origin="owner").to_json(),
        job_id=stage_job_id(post.id, slot.utc),
        transport=transport,
    )
    return (await read_doc(services.store, Post, post.id)).doc, job


async def test_staging_gets_the_post_ready_and_pushes(services: FakeServices) -> None:
    post = await approved_post(services)
    transport = FakeTransport()
    saved, job = await stage(services, post, transport)
    assert job.status == "succeeded"
    assert saved.status is PostStatus.STAGED
    assert saved.staging is not None
    assert saved.staging.media_ready
    assert saved.staging.token_ok
    assert post.approval is not None
    assert saved.staging.approval_sha256 == post.approval.sha256
    assert saved.staging.job_id == stage_job_id(post.id, SLOT.utc)
    assert saved.approval_current
    assert transport.uploads == [post.id]
    assert transport.token_checks == 1
    [(payload, urgency)] = notifier(services).sent
    assert payload.title == "Post at 08:00: tap to publish"
    assert payload.url == f"/studio/{post.id}/go-live?post={post.id}"
    assert urgency == "high"
    assert any(event.type == ENTITY_CHANGED for event in job.events)


async def test_a_text_post_uploads_nothing(services: FakeServices) -> None:
    await seed_settings(services)
    post = await approved_post(
        services, carousel=False, article_id="art-0193f00000000000000000000001"
    )
    transport = FakeTransport()
    saved, _ = await stage(services, post, transport)
    assert saved.status is PostStatus.STAGED
    assert transport.uploads == []
    assert (
        notifier(services).sent[0][0].url
        == f"/studio/art-0193f00000000000000000000001/go-live?post={post.id}"
    )


@pytest.mark.parametrize("change", ["reslot", "edited", "kit", "late"])
async def test_staging_is_a_no_op_when_things_changed(services: FakeServices, change: str) -> None:
    post = await approved_post(services)
    match change:
        case "reslot":
            await upsert_doc(services.store, post.model_copy(update={"slot": LATER}))
        case "edited":
            display, payload = serialize("Edited after approval.", [])
            await upsert_doc(
                services.store,
                post.model_copy(update={"display_text": display, "payload": payload}),
            )
        case "kit":
            await upsert_doc(services.store, post.model_copy(update={"go_live_mode": "kit"}))
        case _:
            services.clock.advance(timedelta(minutes=61))  # downtime past the slot itself
    transport = FakeTransport()
    saved, job = await stage(services, post, transport)
    assert job.status == "succeeded"
    assert saved.status is PostStatus.APPROVED
    assert saved.staging is None
    assert transport.uploads == []
    assert transport.token_checks == 0
    assert notifier(services).sent == []


async def test_a_failed_upload_alerts_and_pushes(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    post = await approved_post(services)
    transport = FakeTransport()
    transport.upload_error = StagingFailed("reconnect_required", "Reconnect LinkedIn.")
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        saved, job = await stage(services, post, transport)
    assert job.status == "failed"
    assert _error_code(job) == "reconnect_required"
    assert saved.status is PostStatus.APPROVED
    assert saved.staging is not None
    assert saved.staging.error_code == "reconnect_required"
    assert saved.last_error == "reconnect_required"
    alerts = [r for r in caplog.records if r.getMessage() == "alert_event"]
    assert [(vars(r).get("alert_kind"), vars(r).get("ref_id")) for r in alerts] == [
        ("needs_attention", post.id)
    ]
    [(payload, _)] = notifier(services).sent
    assert payload.title == "Post at 08:00 needs you"


async def test_a_term_blocklisted_after_approval_fails_staging(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    post = await approved_post(services)
    await seed_settings(services, terms=["order"])  # in the display text, added after approval
    transport = FakeTransport()
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        saved, job = await stage(services, post, transport)
    assert job.status == "failed"
    assert _error_code(job) == "blocklist_hit"
    assert saved.status is PostStatus.APPROVED
    assert saved.staging is not None
    assert saved.staging.error_code == "blocklist_hit"
    assert transport.uploads == []  # nothing went to LinkedIn
    assert transport.token_checks == 0
    alerts = [r for r in caplog.records if r.getMessage() == "alert_event"]
    assert [vars(r).get("ref_id") for r in alerts] == [post.id]
    [(payload, _)] = notifier(services).sent
    assert payload.title == "Post at 08:00 needs you"


async def test_an_unsaved_blocklist_fails_staging(services: FakeServices) -> None:
    post = await approved_post(services)
    await seed_settings(services, blocklist_configured=False)
    saved, job = await stage(services, post, FakeTransport())
    assert _error_code(job) == "blocklist_not_configured"
    assert saved.status is PostStatus.APPROVED


async def test_a_lost_token_fails_staging(services: FakeServices) -> None:
    post = await approved_post(services)
    transport = FakeTransport()
    transport.token = TokenCheck(False, "reconnect_required")
    saved, job = await stage(services, post, transport)
    assert _error_code(job) == "reconnect_required"
    assert saved.staging is not None
    assert saved.staging.error_code == "reconnect_required"


async def test_a_missing_blob_is_re_rendered_identically(services: FakeServices) -> None:
    post = await approved_post(services)
    pdf = next(item for item in post.media if item.role == "carousel_pdf")
    del blobs(services).blobs[("media", pdf.name)]
    saved, job = await stage(services, post, FakeTransport())
    assert job.status == "succeeded"
    assert saved.status is PostStatus.STAGED
    assert ("media", pdf.name) in blobs(services).blobs


async def test_media_that_render_differently_fail_staging(services: FakeServices) -> None:
    post = await approved_post(services)
    stale = MediaRef(
        role="carousel_pdf",
        name=f"posts/{post.id}/{'0' * 64}.pdf",
        sha256="0" * 64,
        content_type="application/pdf",
        size_bytes=10,
    )
    changed = post.model_copy(update={"media": [stale, *post.media[1:]]})
    changed = changed.model_copy(
        update={
            "approval": Approval(sha256=changed.compute_approval_hash(), approved_at=to_iso(T0))
        }
    )
    await upsert_doc(services.store, changed)
    saved, job = await stage(services, changed, FakeTransport())
    assert _error_code(job) == "media_changed"
    assert saved.status is PostStatus.APPROVED


async def test_staging_with_the_render_service_down(services: FakeServices) -> None:
    post = await approved_post(services)
    for item in post.media:
        del blobs(services).blobs[("media", item.name)]
    services.render = None
    _, job = await stage(services, post, FakeTransport())
    assert _error_code(job) == "render_unavailable"
    services.render = CompliantRenderService()


# --------------------------------------------------------------------------------------------
# posts.expire
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PostStatus.DRAFT, PostStatus.EXPIRED),
        (PostStatus.APPROVED, PostStatus.EXPIRED),
        (PostStatus.POSTED, PostStatus.POSTED),
    ],
)
async def test_time_sensitive_posts_expire_unless_posted(
    services: FakeServices, status: PostStatus, expected: PostStatus
) -> None:
    post = sample_document(
        Post, status=status, time_sensitive=True, expires_at=to_iso(T0 - timedelta(minutes=1))
    )
    await upsert_doc(services.store, post)
    job = await run_job(
        services,
        POSTS_EXPIRE,
        PostJobInput(post_id=post.id).to_json(),
        job_id=f"expire-{status.value}",
    )
    assert job.status == "succeeded"
    assert (await read_doc(services.store, Post, post.id)).doc.status is expected


async def test_expiry_waits_for_its_time(services: FakeServices) -> None:
    post = sample_document(Post, time_sensitive=True, expires_at=to_iso(T0 + timedelta(hours=1)))
    await upsert_doc(services.store, post)
    job = await run_job(
        services, POSTS_EXPIRE, PostJobInput(post_id=post.id).to_json(), job_id="expire-early"
    )
    assert job.result == {"postId": post.id, "outcome": "not_due"}
    assert (await read_doc(services.store, Post, post.id)).doc.status is PostStatus.DRAFT


async def test_a_transient_upload_error_is_retried_first(services: FakeServices) -> None:
    post = await approved_post(services)
    transport = FakeTransport()
    transport.upload_error = RuntimeError("LinkedIn 503")
    with pytest.raises(StepError, match="upload"):
        await stage(services, post, transport)
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.staging is None  # not recorded as a failure yet: the runner retries
    assert notifier(services).sent == []


@pytest.mark.parametrize(
    ("lead", "before_slot", "staged"),
    [
        (60, timedelta(minutes=5), True),  # approved 5 minutes before the slot
        (240, timedelta(hours=3), True),  # approved inside a long stage lead
        (60, timedelta(minutes=55), True),  # claimed late after a short downtime
        (15, -timedelta(minutes=30), False),  # the slot passed: the slot check takes over
        (60, timedelta(0), False),  # at the slot itself
    ],
)
async def test_lateness_is_judged_against_the_slot(
    services: FakeServices, lead: int, before_slot: timedelta, staged: bool
) -> None:
    post = await approved_post(services)
    stamp = to_iso(T0)
    cadence = CadenceSettings.default(stamp).model_copy(update={"stage_lead_minutes": lead})
    await upsert_doc(services.store, cadence)
    services.clock.set(parse_iso(SLOT.utc) - before_slot)
    saved, job = await stage(services, post, FakeTransport())
    assert job.status == "succeeded", job.error
    assert (saved.status is PostStatus.STAGED) is staged
