"""The steps of ``illustrations.generate`` (``IllustrationsInput``; id
``illustrations-<articleId>-<token>``, written on the article by the route before the enqueue).

``plan`` (Sol, ``Task.PLAN``: one hero and up to two inline illustration briefs from the
article's public metadata, the pillar's art brief and the brand palette), ``generate``
(``gpt-image-2``, ``Task.IMAGE``), ``check`` (the "media matches text" vision check, Sol with the
image, ``Task.FACT_CHECK``), ``store`` (the checked images become candidates; the discarded
blobs are deleted) and ``attach`` (the candidates are saved on the article without touching its
body, metadata, version or hero).

**Public data only.** Every request is ``Sensitivity.PUBLIC`` and built from the article's
title, excerpt, pillar and headings, the art brief, the palette and the owner's short direction
(:mod:`blogwriter.illustrations.briefs`): never the body or owner material. Each request is
screened against the current blocklist right before it is sent.

**Crash-only.** The image API has no id to re-attach to, so each generation is recorded in the
step's checkpoint (``phase: pending``, ``calls + 1``) **before** it is awaited, and the image is
re-encoded with Pillow (:func:`reencode_illustration`: only the pixels survive, converted to
sRGB, every metadata chunk dropped, written as a WebP of a few hundred KB instead of the
provider's PNG of a few MB) and stored in Blob ``media`` under its content hash
(``storage.store_media``) the moment it arrives, then checkpointed (``phase: stored``). The
vision check reads those exact stored bytes. A resumed step never generates a stored image
again; a call that was in flight when the process died is made once more, at most
:data:`MAX_CALLS_PER_IMAGE` calls per image in all (then the image is dropped as
``interrupted``). A rate-limited call (429, or the model cooling down after one) generated
nothing and is not counted: the step is retried later with the image's budget intact. The plan
is checkpointed as soon as it arrives, and the check of a stored image is simply asked again.

**Progress.** ``generate`` and ``check`` emit ``progress`` events (``done`` of ``total``, with
the labels :data:`MADE_LABEL` and :data:`CHECKED_LABEL`): images made (stored or given up) while
generating, images whose check finished (passed or dropped) while checking, once when the step
starts and after every image. ``attach`` emits one with :data:`KEPT_LABEL`: the candidates kept
of the images planned and the codes of why the others were dropped (also when none was kept).

**Refusals and failed checks.** A content filter refusal is a typed result
(``ImageResult.refused``), not a crash. A refusal or a failed check (off concept, legible text,
branding, a recognisable person, unsafe; a refused or unreadable verdict fails closed) discards
the image (its blob is deleted unless the article uses it) and makes **one** automatic retry
with a simpler, more abstract prompt; a second failure drops the image. A failed image is never
kept silently: only images that passed their check become candidates. When none does, the job
fails ``no_illustrations``.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime
from typing import Final

from PIL import Image
from pydantic import JsonValue, ValidationError

from blogwriter.ai import ImageResult, NoRouteError, PolicyViolation, RouterResult
from blogwriter.ai.ledger import Origin
from blogwriter.ai.providers import ProviderError, RateLimitedError, UnsupportedRequestError
from blogwriter.ai.schema import StructuredOutputError
from blogwriter.ai.types import (
    MAX_IMAGE_BYTES,
    GeneratedImage,
    ImagePart,
    ImageRequest,
    Sensitivity,
    Task,
)
from blogwriter.articles import attach_illustrations
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import NotFound
from blogwriter.domain.articles import Article, Illustration
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import MediaRef, ServedByRecord
from blogwriter.domain.confidentiality import BlocklistNotConfigured, ConfidentialText, check_texts
from blogwriter.domain.docstore import read_doc, read_singleton
from blogwriter.domain.jobflow import defer_on_brake, require_service, step_services
from blogwriter.domain.jobtypes import ENTITY_CHANGED, IllustrationsInput, entity_changed
from blogwriter.domain.ports import (
    BlobNotFound,
    BlobStore,
    BlobStoreUnavailable,
    ImageGenerator,
    ModelRunner,
    Services,
    image_generator,
)
from blogwriter.domain.settings import Blocklist, ModelPreferences
from blogwriter.domain.states import ArticleStatus, WriterConflict
from blogwriter.illustrations.briefs import (
    PlanInvalid,
    check_request,
    load_subject,
    plan_request,
    shape_plan,
)
from blogwriter.illustrations.models import (
    IllustrationJobState,
    ImageState,
    PlannedImage,
    PlanOut,
    PlanState,
    StoredBlob,
    VerdictOut,
)
from blogwriter.illustrations.prompting import CHECK, PLAN
from blogwriter.jobs import JobFailed, Step, StepContext
from blogwriter.storage import storage_settings, store_media, to_srgb

__all__ = [
    "CHECKED_LABEL",
    "ILLUSTRATION_CONTENT_TYPE",
    "KEPT_LABEL",
    "MADE_LABEL",
    "MAX_CALLS_PER_IMAGE",
    "STEP_NAMES",
    "WEBP_QUALITY",
    "attach_step",
    "check_step",
    "generate_step",
    "plan_step",
    "reencode_illustration",
    "steps",
    "store_step",
]

MAX_CALLS_PER_IMAGE: Final = 3
"""Generation calls one planned image may cost in all: the first attempt, the one retry and one
call lost to a crash (or a 5xx). Past it the image is dropped (``interrupted``). Rate-limited
calls are not counted (nothing was generated)."""
ILLUSTRATION_CONTENT_TYPE: Final = "image/webp"
"""How illustrations are stored and published: the provider's PNG is a few MB (more than the
preview inlines and a heavy page for readers); a quality 90 WebP of it is a few hundred KB."""
WEBP_QUALITY: Final = 90
"""The WebP quality of a stored illustration (the same as re-encoded WebP uploads)."""
CHECK_MAX_SIDE: Final = 1536
"""A stored image above the vision input cap is sent to the check as a JPEG this size."""
MADE_LABEL: Final = "images made"
"""``progress`` label of ``generate`` (the step timeline shows "2 of 3 images made")."""
CHECKED_LABEL: Final = "images checked"
"""``progress`` label of ``check``."""
KEPT_LABEL: Final = "images kept"
"""``progress`` label of ``attach``: candidates kept of the images planned, with the codes of
why the others were dropped (``reasons``: ``legible_text``, ``content_filter``...)."""
PLAN_STEP: Final = "illustrations_plan"
GENERATE_STEP: Final = "illustrations_generate"
CHECK_STEP: Final = "illustrations_check"
STEP_NAMES: Final = ("plan", "generate", "check", "store", "attach")


_logger: Final = logging.getLogger("blogwriter.illustrations")
register_log_fields(
    "article_id", "job_id", "image_index", "attempt", "failures", "candidates", "dropped"
)


# --------------------------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------------------------


class _Run:
    """What every step needs: the input, the services, the clock."""

    def __init__(self, ctx: StepContext, payload: IllustrationsInput, services: Services) -> None:
        self.ctx = ctx
        self.payload = payload
        self.services = services
        self.store = services.store
        self.article_id = payload.article_id
        self.job_id = ctx.job_id
        self.origin: Origin = payload.origin

    def now(self) -> datetime:
        return self.services.clock.now()

    def stamp(self) -> str:
        return to_iso(self.now())


def _run(ctx: StepContext) -> _Run:
    try:
        payload = IllustrationsInput.from_json(ctx.input)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None
    return _Run(ctx, payload, step_services())


async def _article(run: _Run) -> Article:
    try:
        return (await read_doc(run.store, Article, run.article_id)).doc
    except NotFound:
        raise JobFailed("article_missing", "The article no longer exists.") from None


def _generator(run: _Run) -> ImageGenerator:
    generator = image_generator(run.services.ai)
    if generator is None:
        raise JobFailed("images_unavailable", "No image model is configured.")
    return generator


def _runner(run: _Run) -> ModelRunner:
    return require_service(run.services.ai, "ai")


def _blobs(run: _Run) -> BlobStore:
    return require_service(run.services.blobs, "storage")


async def _require_enabled(run: _Run) -> None:
    """Illustrations stay switched on (the owner may switch them off mid-run: fail closed)."""
    preferences, _ = await read_singleton(run.store, ModelPreferences, now=run.stamp())
    if not preferences.images_enabled:
        raise JobFailed("images_disabled", "Illustrations are switched off in Settings > Models.")


async def _screen(run: _Run, texts: dict[str, str]) -> None:
    """The current blocklist over text about to reach a model (fail closed)."""
    blocklist, _ = await read_singleton(run.store, Blocklist, now=run.stamp())
    try:
        check_texts(texts, blocklist)
    except BlocklistNotConfigured:
        raise JobFailed(
            "blocklist_not_configured", "Save the blocklist once in Settings > Policy."
        ) from None
    except ConfidentialText:
        raise JobFailed(
            "blocklist_hit", "Some text for the illustrations matches the blocklist."
        ) from None


def _record(
    result: RouterResult | ImageResult, *, step: str, at: str, prompt_version: str | None
) -> ServedByRecord:
    refused = result.refused or (isinstance(result, RouterResult) and bool(result.refusals))
    return ServedByRecord.from_served_by(
        result.served_by,
        step=step,
        at=at,
        refused=refused,
        prompt_version=prompt_version,
        cost_usd=float(result.cost_usd),
    )


def _plan(ctx: StepContext) -> PlanState:
    try:
        return PlanState.model_validate(ctx.outputs.get("plan"))
    except ValidationError:
        raise JobFailed("invalid_state", "The illustration plan is unreadable.") from None


def _state(value: JsonValue, plan: PlanState) -> IllustrationJobState | None:
    if value is None:
        return None
    try:
        state = IllustrationJobState.model_validate(value)
    except ValidationError:
        raise JobFailed("invalid_state", "The illustration state is unreadable.") from None
    if [image.index for image in state.images] != [image.index for image in plan.images]:
        raise JobFailed("invalid_state", "The illustration state does not match its plan.")
    return state


async def _save(run: _Run, state: IllustrationJobState) -> None:
    await run.ctx.checkpoint(state.model_dump(mode="json", by_alias=True))


def _dump(state: IllustrationJobState) -> JsonValue:
    return state.model_dump(mode="json", by_alias=True)


def _fail_attempt(image: ImageState, *reasons: str) -> None:
    """The current attempt failed: retry once with the adjusted prompt, then drop."""
    image.reasons.extend(reasons)
    if image.attempt == 1:
        image.attempt = 2
        image.phase = "new"
    else:
        image.phase = "dropped"


# --------------------------------------------------------------------------------------------
# Images: re-encoding, storing, deleting
# --------------------------------------------------------------------------------------------


class _Unusable(ValueError):
    """A generated image Pillow cannot read, or one too large to keep."""


def reencode_illustration(data: bytes, *, max_pixels: int) -> tuple[bytes, int, int]:
    """Decode the provider's PNG and write a fresh WebP from its pixels only (sync and
    CPU-bound: run it in a thread). Returns ``(webp, width, height)``.

    Nothing of the provider's file survives but the pixels: a colour profile is applied to them
    (sRGB, ``storage.to_srgb``), then text and XMP chunks, EXIF, ICC profiles and provenance
    manifests are all dropped (the new image is built from raw pixels and written without
    metadata). Quality :data:`WEBP_QUALITY` keeps a 1536x1024 illustration at a few hundred
    KB, so the preview inlines it and the published page stays light. Raises
    :class:`_Unusable`.
    """
    try:
        with Image.open(io.BytesIO(data), formats=["PNG"]) as image:
            if image.width * image.height > max_pixels:
                raise _Unusable("too many pixels")
            image.load()
            alpha = image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in image.info
            mode = "RGBA" if alpha else "RGB"
            pixels = to_srgb(image.convert(mode) if image.mode != mode else image)
            if pixels.mode != mode:
                pixels = pixels.convert(mode)
            clean = Image.frombytes(mode, pixels.size, pixels.tobytes())
    except _Unusable:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _Unusable("too many pixels") from None
    except Exception:
        raise _Unusable("unreadable") from None
    out = io.BytesIO()
    clean.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
    return out.getvalue(), clean.width, clean.height


async def _store_image(run: _Run, blobs: BlobStore, image: GeneratedImage) -> StoredBlob:
    """Re-encode (metadata stripped, WebP) and store under the content hash in Blob ``media``."""
    limits = storage_settings()
    data, width, height = await asyncio.to_thread(
        reencode_illustration, image.data, max_pixels=limits.max_image_pixels
    )
    if len(data) > limits.max_upload_bytes:  # the publish job copies images up to 10 MB
        raise _Unusable("too large")
    ref = await store_media(
        blobs,
        data,
        role="image",
        area="articles",
        owner_id=run.article_id,
        content_type=ILLUSTRATION_CONTENT_TYPE,
        now=run.now(),
        width=width,
        height=height,
    )
    return StoredBlob(
        name=ref.name,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        width=width,
        height=height,
        content_type=ILLUSTRATION_CONTENT_TYPE,
    )


def _held(state: IllustrationJobState) -> set[str]:
    """Blob names images of this run still hold (identical bytes share one name)."""
    return {image.blob.name for image in state.images if image.blob is not None}


async def _delete_unused(
    run: _Run, blobs: BlobStore, name: str, *, state: IllustrationJobState
) -> None:
    """Delete a discarded image unless this run, the article's body, its hero or one of its
    candidates still uses the same (content-addressed) blob."""
    if name in _held(state):
        return
    try:
        article = (await read_doc(run.store, Article, run.article_id)).doc
    except NotFound:
        article = None
    if article is not None and (
        article.references_media(name)
        or any(item.media.name == name for item in article.illustrations)
    ):
        return
    await blobs.delete("media", name)


def _jpeg_copy(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((CHECK_MAX_SIDE, CHECK_MAX_SIDE))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=90)
        return out.getvalue()


async def _check_part(data: bytes, content_type: str) -> ImagePart:
    """The stored image as a vision input: its exact bytes, or a JPEG copy when it is above
    the input cap (or of a type the check does not take)."""
    if len(data) <= MAX_IMAGE_BYTES:
        match content_type:
            case "image/webp" | "image/png" | "image/jpeg":
                return ImagePart(data, content_type)
            case _:
                pass
    return ImagePart(await asyncio.to_thread(_jpeg_copy, data), "image/jpeg")


# --------------------------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------------------------


async def plan_step(ctx: StepContext) -> JsonValue:
    """Sol plans one hero and up to two inline illustrations from public metadata only."""
    run = _run(ctx)
    article = await _article(run)
    if article.illustration_job_id != run.job_id:
        raise JobFailed("superseded", "A newer illustrations request replaced this one.")
    if article.status is ArticleStatus.ARCHIVED:
        raise JobFailed("read_only_state", "The article is archived.")
    await _require_enabled(run)
    _generator(run)
    runner = _runner(run)
    if ctx.output is not None:  # planned before a crash: the call is not made again
        try:
            return PlanState.model_validate(ctx.output).model_dump(mode="json", by_alias=True)
        except ValidationError:
            pass
    if article.meta.title is None:
        raise JobFailed("meta_incomplete", "Give the article a title first.")
    subject = await load_subject(
        run.store, article, direction=run.payload.direction, now=run.stamp()
    )
    await _screen(run, subject.texts())
    request = plan_request(subject, run.payload.count)
    stamp = run.stamp()
    try:
        with defer_on_brake(run.now()):
            result = await runner.run(Task.PLAN, request, origin=run.origin, job_id=run.job_id)
    except PolicyViolation:
        raise JobFailed("policy_violation") from None
    except NoRouteError:
        raise JobFailed("ai_unavailable", "No model is configured for this step.") from None
    except StructuredOutputError:
        raise JobFailed("plan_invalid", "The illustration plan could not be read.") from None
    record = _record(result, step=PLAN_STEP, at=stamp, prompt_version=PLAN)
    if result.refused:
        raise JobFailed("plan_refused", "The model declined to plan illustrations for this.")
    try:
        planned = shape_plan(result.output(PlanOut), subject, run.payload.count)
    except (TypeError, PlanInvalid):
        raise JobFailed("plan_invalid", "The illustration plan could not be used.") from None
    texts: dict[str, str] = {}
    for image in planned:
        texts[f"images[{image.index}].concept"] = image.concept
        texts[f"images[{image.index}].prompt"] = image.prompt
        texts[f"images[{image.index}].retryPrompt"] = image.retry_prompt
        texts[f"images[{image.index}].altText"] = image.alt_text
    await _screen(run, texts)
    state = PlanState(images=planned, served_by=record, cost_usd=float(result.cost_usd))
    output = state.model_dump(mode="json", by_alias=True)
    await ctx.checkpoint(output)
    _logger.info(
        "illustrations_planned",
        extra={"article_id": run.article_id, "job_id": run.job_id, "candidates": len(planned)},
    )
    return output


# --------------------------------------------------------------------------------------------
# generate (and the one retry the check step makes)
# --------------------------------------------------------------------------------------------


async def _produce(
    run: _Run,
    state: IllustrationJobState,
    image: ImageState,
    planned: PlannedImage,
    *,
    generator: ImageGenerator,
    blobs: BlobStore,
) -> None:
    """Generate until the current attempt is stored, or the image is dropped."""
    while image.phase in ("new", "pending"):
        if image.calls >= MAX_CALLS_PER_IMAGE:
            image.phase = "dropped"
            image.reasons.append("interrupted")
            await _save(run, state)
            return
        prompt = planned.prompt if image.attempt == 1 else planned.retry_prompt
        await _screen(run, {f"images[{planned.index}].prompt": prompt})
        image.calls += 1
        image.phase = "pending"
        await _save(run, state)  # recorded before the call is awaited
        request = ImageRequest(
            prompt=prompt, sensitivity=Sensitivity.PUBLIC, size=planned.size, quality="high"
        )
        stamp = run.stamp()
        try:
            with defer_on_brake(run.now()):
                result = await generator.generate_image(
                    request, origin=run.origin, job_id=run.job_id
                )
        except PolicyViolation:
            raise JobFailed("policy_violation") from None
        except (NoRouteError, UnsupportedRequestError):
            raise JobFailed("images_unavailable", "No image model is configured.") from None
        except ProviderError as exc:
            if exc.retryable:
                if isinstance(exc, RateLimitedError):
                    # A 429 (or the model cooling down after one): nothing was generated or
                    # billed, so the call does not use up this image's budget.
                    image.calls -= 1
                    image.phase = "new"
                    await _save(run, state)
                raise  # the runner retries the step later; any other failed call is counted
            _logger.warning(
                "illustration_generation_failed",
                extra={
                    "article_id": run.article_id,
                    "job_id": run.job_id,
                    "image_index": planned.index,
                    **describe_exception(exc),
                },
            )
            _fail_attempt(image, "image_error")
            await _save(run, state)
            continue
        image.cost_usd += float(result.cost_usd)
        image.served_by.append(_record(result, step=GENERATE_STEP, at=stamp, prompt_version=None))
        generated = result.image
        if generated is None:
            _logger.info(
                "illustration_refused",
                extra={
                    "article_id": run.article_id,
                    "job_id": run.job_id,
                    "image_index": planned.index,
                    "attempt": image.attempt,
                },
            )
            _fail_attempt(image, "content_filter")
            await _save(run, state)
            continue
        try:
            image.blob = await _store_image(run, blobs, generated)
        except _Unusable:
            _fail_attempt(image, "invalid_image")
            await _save(run, state)
            continue
        image.phase = "stored"
        await _save(run, state)
        _logger.info(
            "illustration_generated",
            extra={
                "article_id": run.article_id,
                "job_id": run.job_id,
                "image_index": planned.index,
                "attempt": image.attempt,
            },
        )


_MADE: Final = frozenset({"stored", "passed", "dropped"})
_CHECKED: Final = frozenset({"passed", "dropped"})


async def _progress(run: _Run, state: IllustrationJobState, *, checking: bool) -> None:
    """``progress``: images made (stored or given up) while generating; images whose check
    finished (passed or dropped) while checking."""
    finished = _CHECKED if checking else _MADE
    done = sum(image.phase in finished for image in state.images)
    label = CHECKED_LABEL if checking else MADE_LABEL
    await run.ctx.emit("progress", {"done": done, "total": len(state.images), "label": label})


async def generate_step(ctx: StepContext) -> JsonValue:
    """Generate every planned image (stored images are never generated again)."""
    run = _run(ctx)
    plan = _plan(ctx)
    state = _state(ctx.output, plan) or IllustrationJobState(
        images=[ImageState(index=image.index) for image in plan.images]
    )
    await _require_enabled(run)
    generator = _generator(run)
    blobs = _blobs(run)
    await _progress(run, state, checking=False)
    for image in state.images:
        if image.phase in ("new", "pending"):
            await _produce(
                run, state, image, plan.images[image.index], generator=generator, blobs=blobs
            )
            await _progress(run, state, checking=False)
    return _dump(state)


# --------------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------------


async def _verdict(
    run: _Run, image: ImageState, planned: PlannedImage, runner: ModelRunner, part: ImagePart
) -> list[str]:
    """The failed checks of one image (empty: it passed). Fails closed."""
    await _screen(run, {f"images[{planned.index}].concept": planned.concept})
    request = check_request(planned.concept, part)
    stamp = run.stamp()
    try:
        with defer_on_brake(run.now()):
            result = await runner.run(
                Task.FACT_CHECK, request, origin=run.origin, job_id=run.job_id
            )
    except PolicyViolation:
        raise JobFailed("policy_violation") from None
    except NoRouteError:
        raise JobFailed("ai_unavailable", "No model is configured for this step.") from None
    except StructuredOutputError:
        return ["check_invalid"]
    image.cost_usd += float(result.cost_usd)
    image.served_by.append(_record(result, step=CHECK_STEP, at=stamp, prompt_version=CHECK))
    if result.refused:
        return ["check_refused"]
    try:
        verdict = result.output(VerdictOut)
    except TypeError:
        return ["check_invalid"]
    return verdict.failures()


async def _check(
    run: _Run,
    state: IllustrationJobState,
    image: ImageState,
    planned: PlannedImage,
    *,
    runner: ModelRunner,
    blobs: BlobStore,
) -> None:
    """Check the stored image: passed, or discarded (one retry follows on attempt 1)."""
    blob = image.blob
    if blob is None:  # pragma: no cover - a stored image always has its blob
        _fail_attempt(image, "media_missing")
        await _save(run, state)
        return
    try:
        data = await blobs.get("media", blob.name)
    except BlobNotFound:
        failures = ["media_missing"]
    else:
        failures = await _verdict(
            run, image, planned, runner, await _check_part(data, blob.content_type)
        )
    if not failures:
        image.phase = "passed"
        await _save(run, state)
        return
    _logger.info(
        "illustration_check_failed",
        extra={
            "article_id": run.article_id,
            "job_id": run.job_id,
            "image_index": planned.index,
            "attempt": image.attempt,
            "failures": len(failures),
        },
    )
    image.discarded.append(blob.name)
    image.blob = None
    _fail_attempt(image, *failures)
    await _save(run, state)
    try:
        await _delete_unused(run, blobs, blob.name, state=state)
    except BlobStoreUnavailable as exc:  # the store step deletes it again
        _logger.warning(
            "illustration_discard_deferred",
            extra={"article_id": run.article_id, **describe_exception(exc)},
        )


async def check_step(ctx: StepContext) -> JsonValue:
    """Check every stored image; a failure discards it and retries once (adjusted prompt)."""
    run = _run(ctx)
    plan = _plan(ctx)
    state = _state(ctx.output, plan) or _state(ctx.outputs.get("generate"), plan)
    if state is None:
        raise JobFailed("invalid_state", "The generated images are unknown.")
    runner = _runner(run)
    blobs = _blobs(run)
    generator: ImageGenerator | None = None
    await _progress(run, state, checking=True)
    for image in state.images:
        planned = plan.images[image.index]
        if image.phase not in ("new", "pending", "stored"):
            continue
        while image.phase in ("new", "pending", "stored"):
            if image.phase != "stored":
                await _require_enabled(run)
                generator = generator or _generator(run)
                await _produce(run, state, image, planned, generator=generator, blobs=blobs)
                continue
            await _check(run, state, image, planned, runner=runner, blobs=blobs)
        await _progress(run, state, checking=True)
    return _dump(state)


# --------------------------------------------------------------------------------------------
# store and attach
# --------------------------------------------------------------------------------------------


async def store_step(ctx: StepContext) -> JsonValue:
    """The checked images as candidates (blob and hash verified); discarded blobs deleted."""
    run = _run(ctx)
    plan = _plan(ctx)
    state = _state(ctx.outputs.get("check"), plan)
    if state is None:
        raise JobFailed("invalid_state", "The checked images are unknown.")
    blobs = _blobs(run)
    stamp = run.stamp()
    candidates: list[JsonValue] = []
    dropped = 0
    reasons: set[str] = set()
    kept = IllustrationJobState(images=[image for image in state.images if image.phase == "passed"])
    for image in state.images:
        for name in image.discarded:
            await _delete_unused(run, blobs, name, state=kept)
        reasons.update(image.reasons)
        blob = image.blob
        if image.phase != "passed" or blob is None:
            dropped += 1
            continue
        info = await blobs.head("media", blob.name)
        if info is None or info.sha256 != blob.sha256:
            dropped += 1
            reasons.add("media_integrity")
            continue
        planned = plan.images[image.index]
        media = MediaRef(
            role="image",
            container="media",
            name=blob.name,
            sha256=blob.sha256,
            content_type=blob.content_type,
            size_bytes=blob.size_bytes,
            width=blob.width,
            height=blob.height,
            alt_text=planned.alt_text,
            uploaded_at=stamp,
        )
        candidate = Illustration(
            id=Illustration.id_for(blob.sha256),
            role=planned.role,
            media=media,
            alt_text=planned.alt_text,
            prompt_summary=planned.concept,
            section=planned.section,
            served_by=[plan.served_by, *image.served_by][-8:],
            cost_usd=round(image.cost_usd, 6),
            job_id=run.job_id,
            created_at=stamp,
        )
        candidates.append(candidate.model_dump(mode="json", by_alias=True))
    codes: list[JsonValue] = [*sorted(reasons)]
    output: dict[str, JsonValue] = {"candidates": candidates, "dropped": dropped, "reasons": codes}
    return output


async def attach_step(ctx: StepContext) -> JsonValue:
    """Save the candidates on the article (never its body, metadata, version or hero)."""
    run = _run(ctx)
    stored = ctx.outputs.get("store")
    if not isinstance(stored, dict):
        raise JobFailed("invalid_state", "The stored images are unknown.")
    raw = stored.get("candidates")
    if not isinstance(raw, list):
        raise JobFailed("invalid_state", "The stored images are unknown.")
    try:
        candidates = [Illustration.model_validate(item) for item in raw]
    except ValidationError:
        raise JobFailed("invalid_state", "The stored images are unreadable.") from None
    count = stored.get("dropped")
    dropped = count if isinstance(count, int) and count >= 0 else 0
    codes = stored.get("reasons")
    reasons: list[JsonValue] = (
        [code for code in codes if isinstance(code, str)] if isinstance(codes, list) else []
    )
    # How many images were kept, and why the others were not (codes only): the panel and the
    # step timeline ("2 of 3 images kept") say it, also when none was kept.
    kept: dict[str, JsonValue] = {
        "done": len(candidates),
        "total": len(candidates) + dropped,
        "label": KEPT_LABEL,
        "reasons": reasons,
    }
    if not candidates:
        await ctx.emit("progress", kept)
        raise JobFailed(
            "no_illustrations", "No illustration passed its checks. Try another direction."
        )
    try:
        await attach_illustrations(run.store, run.article_id, candidates, now=run.now())
    except WriterConflict:
        raise JobFailed("read_only_state", "The article was archived.") from None
    except NotFound:
        raise JobFailed("article_missing", "The article no longer exists.") from None
    await ctx.emit("progress", kept)
    await ctx.emit(ENTITY_CHANGED, entity_changed("article", run.article_id))
    _logger.info(
        "illustrations_attached",
        extra={
            "article_id": run.article_id,
            "job_id": run.job_id,
            "candidates": len(candidates),
            "dropped": dropped,
        },
    )
    return {"attached": len(candidates), "dropped": dropped, "reasons": reasons}


def steps() -> list[Step]:
    """The steps of ``illustrations.generate``, in order."""
    return [
        Step("plan", plan_step),
        Step("generate", generate_step),
        Step("check", check_step),
        Step("store", store_step),
        Step("attach", attach_step),
    ]
