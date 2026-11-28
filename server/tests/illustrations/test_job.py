"""``illustrations.generate`` on the real job runner over fake services: the plan, generation,
the "media matches text" check, storing (metadata stripped, content-hashed) and attaching;
refusals and failed checks (one retry, never kept silently), crash-only resumes (no image is
generated twice) and the public-data policy (no body or owner material in any prompt).

Plan: "AI layer" (``gpt-image-2`` illustrations are opt-in, public data, with a per-pillar art
brief and a "media matches text" check), "Confidentiality gate on the exact bytes" (EXIF
stripped by re-encoding) and "Reliability model: crash-only".
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image, ImageCms
from pydantic import JsonValue

from blogwriter.ai import PolicyViolation
from blogwriter.ai.providers import RateLimitedError
from blogwriter.ai.schema import StructuredOutputError
from blogwriter.ai.types import ImagePart, ImageRequest, Sensitivity, Task
from blogwriter.articles import link_illustration_job
from blogwriter.domain.articles import Article, Illustration
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.docstore import Stored, read_doc, update_doc, update_singleton
from blogwriter.domain.jobtypes import ILLUSTRATIONS_GENERATE, IllustrationsInput
from blogwriter.domain.settings import ModelPreferences
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedImage,
    ScriptedModelRunner,
    ScriptedReply,
)
from blogwriter.illustrations.briefs import IMAGE_RULES, load_subject, shape_plan
from blogwriter.illustrations.models import (
    IllustrationJobState,
    ImageState,
    PlanState,
    StoredBlob,
    VerdictOut,
)
from blogwriter.illustrations.steps import (
    CHECKED_LABEL,
    KEPT_LABEL,
    MADE_LABEL,
    MAX_CALLS_PER_IMAGE,
    reencode_illustration,
)
from blogwriter.illustrations.testing import (
    BODY_SENTINEL,
    HEADINGS,
    METADATA_MARKER,
    QUOTE_SENTINEL,
    enable_images,
    generated_png,
    illustrations_registry,
    plan_reply,
    save_blocklist,
    seed_article,
    start,
    verdict,
)
from blogwriter.jobs import Job
from blogwriter.pipeline.testing import rewind, running, start_at, wait_job
from blogwriter.storage import store_media

DONE = {"succeeded", "failed", "quarantined"}
COLORS = ("#335577", "#446688", "#557799", "#668800", "#779911")


@pytest.fixture
async def ready(services: FakeServices) -> Stored[Article]:
    """A saved blocklist, illustrations switched on and an article in review."""
    await save_blocklist(services, "Fabrikam")
    await enable_images(services)
    return await seed_article(services)


async def run_until_done(services: FakeServices, job_id: str) -> Job:
    async with running(services, illustrations_registry()):
        return await wait_job(services, job_id, DONE)


async def article_of(services: FakeServices, article_id: str) -> Article:
    return (await read_doc(services.store, Article, article_id)).doc


def media_blobs(services: FakeServices, article: Article) -> dict[str, bytes]:
    """The article's blobs in ``media`` besides the pipeline's draft card."""
    assert services.blobs is not None
    prefix = f"articles/{article.id}/"
    cards = {item.name for item in article.media if item.role == "og_image"}
    return {
        name: data
        for (container, name), (data, _) in services.blobs.blobs.items()
        if container == "media" and name.startswith(prefix) and name not in cards
    }


def script_images(ai: ScriptedModelRunner, *colors: str) -> list[bytes]:
    images = [generated_png(color) for color in colors]
    ai.add_images(*images)
    return images


def pixels(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGBA").tobytes()


def close_pixels(stored: bytes, original: bytes, *, tolerance: int = 4) -> bool:
    """The stored (lossy WebP) image has the original's pixels, up to rounding."""
    first, second = pixels(stored), pixels(original)
    return len(first) == len(second) and all(
        abs(a - b) <= tolerance for a, b in zip(first, second, strict=True)
    )


def webp_chunks(data: bytes) -> set[bytes]:
    """The chunk ids of a WebP file (``RIFF`` container), e.g. ``VP8 ``, ``EXIF``, ``ICCP``."""
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WEBP"
    assert int.from_bytes(data[4:8], "little") + 8 == len(data)
    chunks: set[bytes] = set()
    position = 12
    while position + 8 <= len(data):
        kind = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        chunks.add(kind)
        position += 8 + size + (size % 2)
    assert position == len(data)
    return chunks


def stored_sha(original: bytes) -> str:
    """The content hash the job stores ``original`` under (after re-encoding)."""
    data, _, _ = reencode_illustration(original, max_pixels=12_000_000)
    return sha256_hex(data)


def output(job: Job, step: str) -> Any:
    state = job.step(step)
    assert state is not None
    return state.output


# --------------------------------------------------------------------------------------------
# The whole run
# --------------------------------------------------------------------------------------------


async def test_a_hero_and_two_inline_illustrations_are_attached_without_touching_the_article(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(3))
    originals = script_images(ai, *COLORS[:3])
    ai.add(Task.FACT_CHECK, verdict(), verdict(), verdict())
    linked, _, job_id = await start(services, ready, count=3)
    assert linked.illustration_job_id == job_id

    job = await run_until_done(services, job_id)

    assert job.status == "succeeded", job.error
    assert output(job, "attach") == {"attached": 3, "dropped": 0, "reasons": []}
    doc = await article_of(services, ready.doc.id)
    assert (doc.markdown, doc.meta, doc.version) == (
        ready.doc.markdown,
        ready.doc.meta,
        ready.doc.version,
    )
    assert doc.media == ready.doc.media  # the hero is the owner's choice, never the job's
    assert [item.role for item in doc.illustrations] == ["hero", "inline", "inline"]
    assert [item.section for item in doc.illustrations] == [None, HEADINGS[0], HEADINGS[1]]
    stored = media_blobs(services, doc)
    for item, original in zip(doc.illustrations, originals, strict=True):
        data = stored[item.media.name]
        assert item.media.name == f"articles/{doc.id}/{item.media.sha256}.webp"
        assert sha256_hex(data) == item.media.sha256 == stored_sha(original)
        assert item.id == Illustration.id_for(item.media.sha256)
        assert item.media.container == "media"
        assert item.media.content_type == "image/webp"
        assert (item.media.width, item.media.height) == (48, 32)
        assert item.media.size_bytes == len(data)
        assert item.alt_text == item.media.alt_text
        assert item.prompt_summary
        assert item.job_id == job_id
        assert [record.step for record in item.served_by] == [
            "illustrations_plan",
            "illustrations_generate",
            "illustrations_check",
        ]
        assert item.served_by[1].task == "image"
        assert item.served_by[2].prompt_version == "check.v1"
        assert item.cost_usd == pytest.approx(0.25 + 0.01)
    assert len(stored) == 3  # nothing else was stored for the article
    calls = ai.image_calls
    assert [call.request.size for call in calls] == ["1536x1024", "1024x1024", "1024x1024"]
    for call in calls:
        assert call.request.quality == "high"
        assert call.request.sensitivity is Sensitivity.PUBLIC
        assert (call.origin, call.job_id) == ("owner", job_id)
        assert IMAGE_RULES in call.request.prompt
    checks = ai.calls_for(Task.FACT_CHECK)
    assert len(checks) == 3
    for check in checks:
        assert check.request.image_count == 1
        assert check.request.sensitivity is Sensitivity.PUBLIC
        assert check.request.output_schema is VerdictOut
        assert check.request.tools == ()
    events = [event.type for event in job.events]
    assert "entity.changed" in events
    assert "progress" in events


def progress_of(job: Job, step: str) -> list[tuple[JsonValue, JsonValue, JsonValue]]:
    """``(done, total, label)`` of the step's ``progress`` events, in order."""
    return [
        (event.data.get("done"), event.data.get("total"), event.data.get("label"))
        for event in job.events
        if event.type == "progress" and event.step == step
    ]


async def test_progress_counts_images_made_then_images_checked(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(2))
    script_images(ai, COLORS[0], COLORS[1], COLORS[2])
    # the hero fails its first check and is made again (inside the check step)
    ai.add(Task.FACT_CHECK, verdict(legible_text=True), verdict(), verdict())
    _, _, job_id = await start(services, ready, count=2)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    assert progress_of(job, "generate") == [
        (0, 2, MADE_LABEL),
        (1, 2, MADE_LABEL),
        (2, 2, MADE_LABEL),
    ]
    # Nothing counts as checked before its check finished (the retry included).
    assert progress_of(job, "check") == [
        (0, 2, CHECKED_LABEL),
        (1, 2, CHECKED_LABEL),
        (2, 2, CHECKED_LABEL),
    ]
    assert progress_of(job, "attach") == [(2, 2, KEPT_LABEL)]
    (kept,) = [event for event in job.events if event.step == "attach" and event.type == "progress"]
    assert kept.data["reasons"] == ["legible_text"]  # the hero's first image, made again
    assert (MADE_LABEL, CHECKED_LABEL, KEPT_LABEL) == (
        "images made",
        "images checked",
        "images kept",
    )


async def test_the_stored_image_keeps_only_its_pixels(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(1))
    (original,) = script_images(ai, COLORS[0])
    ai.add(Task.FACT_CHECK, verdict())
    assert METADATA_MARKER.encode() in original
    _, _, job_id = await start(services, ready, count=1)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    (data,) = media_blobs(services, ready.doc).values()
    assert METADATA_MARKER.encode() not in data
    assert webp_chunks(data).isdisjoint({b"EXIF", b"XMP ", b"ICCP"})
    for chunk in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP"):
        assert chunk not in data
    assert close_pixels(data, original)
    # The check saw exactly the bytes that are stored (and published).
    (check,) = ai.calls_for(Task.FACT_CHECK)
    parts = [part for part in check.request.messages[0].parts if isinstance(part, ImagePart)]
    assert [(part.media_type, part.data) for part in parts] == [("image/webp", data)]


def test_a_colour_profile_is_applied_to_the_pixels_then_dropped() -> None:
    image = Image.new("RGBA", (40, 30), (51, 85, 119, 200))
    srgb = ImageCms.createProfile("sRGB")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    profile = ImageCms.ImageCmsProfile(srgb).tobytes()  # pyright: ignore[reportUnknownArgumentType]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", icc_profile=profile)
    original = buffer.getvalue()
    assert b"iCCP" in original
    data, width, height = reencode_illustration(original, max_pixels=12_000_000)
    assert (width, height) == (40, 30)
    assert webp_chunks(data).isdisjoint({b"EXIF", b"XMP ", b"ICCP"})
    with Image.open(io.BytesIO(data)) as stored:
        assert stored.format == "WEBP"
        assert stored.mode == "RGBA"  # transparency is kept
        assert "icc_profile" not in stored.info
    assert close_pixels(data, original)


def test_a_provider_image_too_large_is_refused_before_decoding() -> None:
    with pytest.raises(ValueError, match="too many pixels"):
        reencode_illustration(generated_png(size=(48, 32)), max_pixels=48 * 32 - 1)


# --------------------------------------------------------------------------------------------
# Failed checks and refusals: one retry, never kept silently
# --------------------------------------------------------------------------------------------


async def test_a_failed_check_discards_the_image_and_retries_once_with_a_simpler_prompt(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(1))
    first, second = script_images(ai, COLORS[0], COLORS[1])
    ai.add(Task.FACT_CHECK, verdict(legible_text=True), verdict())
    _, _, job_id = await start(services, ready, count=1)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    doc = await article_of(services, ready.doc.id)
    (candidate,) = doc.illustrations
    assert candidate.media.sha256 == stored_sha(second)
    assert list(media_blobs(services, doc)) == [candidate.media.name]  # the first is gone
    initial, retry = (call.request.prompt for call in ai.image_calls)
    assert retry.startswith("Second attempt")
    assert initial in retry  # the same picture, simpler: the planned prompt follows
    assert [record.step for record in candidate.served_by] == [
        "illustrations_plan",
        "illustrations_generate",
        "illustrations_check",
        "illustrations_generate",
        "illustrations_check",
    ]
    assert candidate.cost_usd == pytest.approx(2 * 0.25 + 2 * 0.01)
    assert stored_sha(first) not in {item.media.sha256 for item in doc.illustrations}


@pytest.mark.parametrize(
    "failed",
    [
        verdict(depicts_concept=False),
        verdict(logos_or_branding=True),
        verdict(recognisable_person=True),
        verdict(unsafe=True),
    ],
)
async def test_an_image_failing_twice_is_dropped_and_the_others_kept(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article], failed: VerdictOut
) -> None:
    ai.add(Task.PLAN, plan_reply(2))
    # generate makes the hero and the inline image; check retries the hero once
    hero_first, inline, hero_retry = script_images(ai, COLORS[0], COLORS[1], COLORS[2])
    ai.add(Task.FACT_CHECK, failed, failed, verdict())
    _, _, job_id = await start(services, ready, count=2)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    attach = output(job, "attach")
    assert attach["attached"] == 1
    assert attach["dropped"] == 1
    assert attach["reasons"] == failed.failures()
    doc = await article_of(services, ready.doc.id)
    (kept,) = doc.illustrations
    assert kept.role == "inline"
    assert kept.media.sha256 == stored_sha(inline)
    names = set(media_blobs(services, doc))
    assert names == {kept.media.name}
    for discarded in (hero_first, hero_retry):
        assert f"articles/{doc.id}/{stored_sha(discarded)}.webp" not in names


async def test_when_nothing_passes_the_job_fails_and_keeps_nothing(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(1))
    script_images(ai, COLORS[0], COLORS[1])
    ai.add(Task.FACT_CHECK, verdict(legible_text=True), verdict(legible_text=True))
    _, _, job_id = await start(services, ready, count=1)
    job = await run_until_done(services, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "no_illustrations"
    doc = await article_of(services, ready.doc.id)
    assert doc.illustrations == []
    assert media_blobs(services, doc) == {}
    assert len(ai.image_calls) == 2  # one retry, not more
    # The run still says what happened: none of one image kept, and why.
    assert progress_of(job, "attach") == [(0, 1, KEPT_LABEL)]
    (kept,) = [event for event in job.events if event.step == "attach" and event.type == "progress"]
    assert kept.data["reasons"] == ["legible_text"]


async def test_a_content_filter_refusal_is_typed_and_retried_once(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(1))
    ai.add_images(ScriptedImage(refused=True))
    (allowed,) = script_images(ai, COLORS[3])
    ai.add(Task.FACT_CHECK, verdict())
    _, _, job_id = await start(services, ready, count=1)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    doc = await article_of(services, ready.doc.id)
    (candidate,) = doc.illustrations
    assert candidate.media.sha256 == stored_sha(allowed)
    refused, generated = (r for r in candidate.served_by if r.step == "illustrations_generate")
    assert refused.refused
    assert not generated.refused
    assert ai.image_calls[1].request.prompt.startswith("Second attempt")


async def test_two_refusals_drop_the_image(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(1))
    ai.add_images(ScriptedImage(refused=True), ScriptedImage(refused=True))
    _, _, job_id = await start(services, ready, count=1)
    job = await run_until_done(services, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "no_illustrations"
    assert ai.calls_for(Task.FACT_CHECK) == []  # nothing was generated to check
    assert output(job, "store")["reasons"] == ["content_filter"]


@pytest.mark.parametrize(
    "reply",
    [
        ScriptedReply(refused=True),
        ScriptedReply(error=StructuredOutputError("invalid", ("depictsConcept: missing",))),
    ],
)
async def test_a_refused_or_unreadable_verdict_fails_closed(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article], reply: ScriptedReply
) -> None:
    ai.add(Task.PLAN, plan_reply(1))
    first, second = script_images(ai, COLORS[0], COLORS[1])
    ai.add(Task.FACT_CHECK, reply, verdict())
    _, _, job_id = await start(services, ready, count=1)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    doc = await article_of(services, ready.doc.id)
    (candidate,) = doc.illustrations
    assert candidate.media.sha256 == stored_sha(second)  # the unchecked image was not kept
    assert stored_sha(first) != candidate.media.sha256
    assert list(media_blobs(services, doc)) == [candidate.media.name]
    assert len(ai.image_calls) == 2


# --------------------------------------------------------------------------------------------
# Crash-only: a resumed step never generates a stored image again
# --------------------------------------------------------------------------------------------


async def plan_output(services: FakeServices, article: Article, count: int = 3) -> JsonValue:
    subject = await load_subject(services.store, article, direction=None, now=SAMPLE_NOW)
    record = ServedByRecord(
        step="illustrations_plan",
        at=SAMPLE_NOW,
        task="plan",
        model="fake-model",
        deployment="fake-deployment",
        provider="openai",
        cost_usd=0.01,
    )
    planned = shape_plan(plan_reply(count), subject, count)
    state = PlanState(images=planned, served_by=record, cost_usd=0.01)
    return state.model_dump(mode="json", by_alias=True)


async def stored_blob(services: FakeServices, article_id: str, original: bytes) -> StoredBlob:
    assert services.blobs is not None
    data, width, height = reencode_illustration(original, max_pixels=12_000_000)
    ref = await store_media(
        services.blobs,
        data,
        role="image",
        area="articles",
        owner_id=article_id,
        content_type="image/webp",
        now=services.clock.now(),
    )
    return StoredBlob(
        name=ref.name,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        width=width,
        height=height,
        content_type="image/webp",
    )


def payload(article_id: str, count: int = 3) -> JsonValue:
    return IllustrationsInput(article_id=article_id, count=count, origin="owner").to_json()


async def linked(services: FakeServices, article: Stored[Article], job_id: str) -> None:
    await link_illustration_job(
        services.store, article.doc.id, job_id, if_match=article.etag, now=services.clock.now()
    )


async def test_a_resumed_generation_never_regenerates_a_stored_image(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    job_id = f"illustrations-{ready.doc.id}-crash1"
    await linked(services, ready, job_id)
    plan = await plan_output(services, ready.doc)
    first = generated_png(COLORS[0])
    kept = await stored_blob(services, ready.doc.id, first)
    crashed = IllustrationJobState(
        images=[
            ImageState(index=0, calls=1, phase="stored", blob=kept),
            ImageState(index=1, calls=1, phase="pending"),  # the call was in flight
            ImageState(index=2),
        ]
    ).model_dump(mode="json", by_alias=True)
    registry = illustrations_registry()
    await start_at(
        services,
        ILLUSTRATIONS_GENERATE,
        payload(ready.doc.id),
        job_id=job_id,
        step="generate",
        registry=registry,
        outputs={"plan": plan},
    )
    await rewind(services, job_id, "generate", checkpoint=crashed)
    script_images(ai, COLORS[1], COLORS[2])
    ai.add(Task.FACT_CHECK, verdict(), verdict(), verdict())
    async with running(services, registry):
        job = await wait_job(services, job_id, DONE)
    assert job.status == "succeeded", job.error
    assert len(ai.image_calls) == 2  # images 1 and 2 only
    planned = PlanState.model_validate(plan).images
    assert [call.request.prompt for call in ai.image_calls] == [
        planned[1].prompt,
        planned[2].prompt,
    ]
    generated = IllustrationJobState.model_validate(output(job, "generate"))
    assert [image.calls for image in generated.images] == [1, 2, 1]
    doc = await article_of(services, ready.doc.id)
    assert doc.illustrations[0].media.name == kept.name
    assert ai.calls_for(Task.PLAN) == []


async def retry_now(services: FakeServices, job_id: str, state: IllustrationJobState) -> None:
    """The runner's retry of the failed ``generate`` step, now (the fake clock never gets to the
    backoff): the step runs again from its checkpoint."""
    await rewind(
        services, job_id, "generate", checkpoint=state.model_dump(mode="json", by_alias=True)
    )
    raw = await services.store.read("jobs", job_id)
    job = Job.from_body(raw.body)
    job.error = None
    await services.store.replace("jobs", job.to_body(), if_match=raw.etag)


async def test_rate_limits_do_not_use_up_an_images_calls(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    """A 429 generates nothing: after two of them the image still gets its one retry with the
    adjusted prompt (each counted call would have dropped it as ``interrupted``)."""
    job_id = f"illustrations-{ready.doc.id}-limited"
    await linked(services, ready, job_id)
    plan = await plan_output(services, ready.doc, count=1)
    registry = illustrations_registry()
    await start_at(
        services,
        ILLUSTRATIONS_GENERATE,
        payload(ready.doc.id, 1),
        job_id=job_id,
        step="generate",
        registry=registry,
        outputs={"plan": plan},
    )
    limited = RateLimitedError("openai HTTP 429", provider="openai", retryable=True, retry_after=2)
    for _ in range(2):
        ai.add_images(ScriptedImage(error=limited))
        async with running(services, registry):
            with pytest.raises(AssertionError, match="RateLimitedError"):
                await wait_job(services, job_id, DONE)
        state = IllustrationJobState.model_validate(
            output(await services.jobs.get(job_id), "generate")
        )
        assert [(image.calls, image.phase) for image in state.images] == [(0, "new")]
        await retry_now(services, job_id, state)
    first, second = script_images(ai, COLORS[0], COLORS[1])
    ai.add(Task.FACT_CHECK, verdict(legible_text=True), verdict())
    async with running(services, registry):
        job = await wait_job(services, job_id, DONE)
    assert job.status == "succeeded", job.error
    assert len(ai.image_calls) == 4  # two 429s, the image and its retry
    assert ai.image_calls[-1].request.prompt.startswith("Second attempt")
    checked = IllustrationJobState.model_validate(output(job, "check"))
    assert [image.calls for image in checked.images] == [2]
    doc = await article_of(services, ready.doc.id)
    (candidate,) = doc.illustrations
    assert candidate.media.sha256 == stored_sha(second)
    assert stored_sha(first) != candidate.media.sha256


async def test_a_crash_after_generation_checks_again_without_generating(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    job_id = f"illustrations-{ready.doc.id}-crash2"
    await linked(services, ready, job_id)
    plan = await plan_output(services, ready.doc, count=2)
    blobs = [await stored_blob(services, ready.doc.id, generated_png(c)) for c in COLORS[:2]]
    generated = IllustrationJobState(
        images=[
            ImageState(index=index, calls=1, phase="stored", blob=blob)
            for index, blob in enumerate(blobs)
        ]
    ).model_dump(mode="json", by_alias=True)
    registry = illustrations_registry()
    await start_at(
        services,
        ILLUSTRATIONS_GENERATE,
        payload(ready.doc.id, 2),
        job_id=job_id,
        step="check",
        registry=registry,
        outputs={"plan": plan, "generate": generated},
    )
    ai.add(Task.FACT_CHECK, verdict(), verdict())
    async with running(services, registry):
        job = await wait_job(services, job_id, DONE)
    assert job.status == "succeeded", job.error
    assert ai.image_calls == []
    doc = await article_of(services, ready.doc.id)
    assert [item.media.name for item in doc.illustrations] == [blob.name for blob in blobs]


async def test_an_image_lost_to_crashes_too_often_is_dropped_without_another_call(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    job_id = f"illustrations-{ready.doc.id}-crash3"
    await linked(services, ready, job_id)
    plan = await plan_output(services, ready.doc, count=2)
    crashed = IllustrationJobState(
        images=[
            ImageState(index=0, calls=MAX_CALLS_PER_IMAGE, attempt=2, phase="pending"),
            ImageState(index=1),
        ]
    ).model_dump(mode="json", by_alias=True)
    registry = illustrations_registry()
    await start_at(
        services,
        ILLUSTRATIONS_GENERATE,
        payload(ready.doc.id, 2),
        job_id=job_id,
        step="generate",
        registry=registry,
        outputs={"plan": plan},
    )
    await rewind(services, job_id, "generate", checkpoint=crashed)
    script_images(ai, COLORS[4])
    ai.add(Task.FACT_CHECK, verdict())
    async with running(services, registry):
        job = await wait_job(services, job_id, DONE)
    assert job.status == "succeeded", job.error
    assert len(ai.image_calls) == 1  # only the inline image
    assert output(job, "attach")["reasons"] == ["interrupted"]
    doc = await article_of(services, ready.doc.id)
    assert [item.role for item in doc.illustrations] == ["inline"]


async def test_a_checkpointed_plan_is_not_asked_for_again(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    job_id = f"illustrations-{ready.doc.id}-plan1"
    await linked(services, ready, job_id)
    registry = illustrations_registry()
    await start_at(
        services,
        ILLUSTRATIONS_GENERATE,
        payload(ready.doc.id, 1),
        job_id=job_id,
        step="plan",
        registry=registry,
    )
    doc = await article_of(services, ready.doc.id)
    await rewind(services, job_id, "plan", checkpoint=await plan_output(services, doc, count=1))
    script_images(ai, COLORS[0])
    ai.add(Task.FACT_CHECK, verdict())
    async with running(services, registry):
        job = await wait_job(services, job_id, DONE)
    assert job.status == "succeeded", job.error
    assert ai.calls_for(Task.PLAN) == []  # a plan call was never scripted: it was not made


# --------------------------------------------------------------------------------------------
# Public data only, and fail closed
# --------------------------------------------------------------------------------------------


async def test_no_body_or_owner_material_reaches_any_model(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    ai.add(Task.PLAN, plan_reply(3))
    script_images(ai, *COLORS[:3])
    ai.add(Task.FACT_CHECK, verdict(), verdict(), verdict())
    direction = "Warmer light, and a bridge rather than a gate."
    _, _, job_id = await start(services, ready, count=3, direction=direction)
    job = await run_until_done(services, job_id)
    assert job.status == "succeeded", job.error
    assert BODY_SENTINEL in ready.doc.markdown
    assert ready.doc.owner_signals[0].quote == QUOTE_SENTINEL
    texts: list[str] = []
    for call in ai.calls:
        assert call.request.sensitivity is Sensitivity.PUBLIC
        assert call.request.tools == ()
        texts.append(call.request.system)
        texts.extend(message.text for message in call.request.messages)
    for image_call in ai.image_calls:
        assert image_call.request.sensitivity is Sensitivity.PUBLIC
        texts.append(image_call.request.prompt)
    for text in texts:
        assert BODY_SENTINEL not in text
        assert QUOTE_SENTINEL not in text
    (plan,) = ai.calls_for(Task.PLAN)
    asked = plan.request.messages[0].text
    assert ready.doc.meta.title is not None
    assert ready.doc.meta.title in asked
    assert all(heading in asked for heading in HEADINGS)
    assert direction in asked
    assert "#0E2A24" in asked  # the brand palette and the default art brief


async def test_the_image_policy_refuses_anything_but_public_prompts(
    ai: ScriptedModelRunner,
) -> None:
    for sensitivity in (Sensitivity.ANONYMIZED, Sensitivity.PRIVATE):
        request = ImageRequest(prompt="A quiet harbour at dawn.", sensitivity=sensitivity)
        with pytest.raises(PolicyViolation):
            await ai.generate_image(request, origin="owner")
    assert ai.image_calls == []


async def test_a_blocklisted_heading_stops_the_job_before_any_model_call(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    _, _, job_id = await start(services, ready, count=1)

    def rename(article: Article) -> Article:
        body = article.markdown.replace(HEADINGS[1], "The Fabrikam gateway")
        return article.model_copy(update={"markdown": body})

    await update_doc(services.store, Article, ready.doc.id, rename)
    job = await run_until_done(services, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    assert ai.calls == []
    assert ai.image_calls == []


async def test_switching_images_off_mid_run_fails_closed(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    job_id = f"illustrations-{ready.doc.id}-off"
    await linked(services, ready, job_id)
    plan = await plan_output(services, ready.doc, count=1)
    registry = illustrations_registry()
    await start_at(
        services,
        ILLUSTRATIONS_GENERATE,
        payload(ready.doc.id, 1),
        job_id=job_id,
        step="generate",
        registry=registry,
        outputs={"plan": plan},
    )
    await update_singleton(
        services.store,
        ModelPreferences,
        lambda prefs: prefs.model_copy(update={"images_enabled": False}),
        now=SAMPLE_NOW,
    )
    async with running(services, registry):
        job = await wait_job(services, job_id, DONE)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "images_disabled"
    assert ai.image_calls == []


async def test_a_superseded_run_stops_before_planning(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    _, etag, job_id = await start(services, ready, count=1)
    await link_illustration_job(
        services.store, ready.doc.id, "illustrations-newer", if_match=etag, now=services.clock.now()
    )
    job = await run_until_done(services, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "superseded"
    assert ai.calls == []


async def test_without_an_image_model_the_job_fails_unavailable(
    services: FakeServices, ai: ScriptedModelRunner, ready: Stored[Article]
) -> None:
    _, _, job_id = await start(services, ready, count=1)
    ai.serves_images = False
    job = await run_until_done(services, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "images_unavailable"
    assert ai.calls == []
