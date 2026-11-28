"""The voice jobs.

``voice.build-guide`` (owner): ``score`` re-scores the active samples (AI tells + structural
detectors), ``classify`` runs the confidentiality classifier over the exemplar candidates
(flagged, failing or unclassifiable samples are never exemplars), ``synthesize`` writes a new
``proposed`` guide version (SYNTHESIS, anonymized; deterministic id, checkpointed version).

``voice.digest`` (schedule, owner): ``collect`` decides whether this run produces a digest
(pause, weekly -> monthly cadence, period, existing digest), finds the posts posted in the period
with both an AI draft and a final text, and adds their final texts as ``final_post`` samples;
``synthesize`` compares drafts with final texts (SYNTHESIS, anonymized) and saves the
``VoiceDigest`` (job outputs carry ids and counts only, so the step that produces content also
stores it); ``save`` sends the push "Voice digest ready" (once) and returns the result.

Steps are re-entrant: every document they create has a deterministic id, and a resumed step finds
what an earlier attempt stored. Job outputs and events hold ids, hashes and counts, never text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Final

from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Task
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.base import parse_iso, sha256_hex, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_findings,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.docstore import (
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
    update_singleton,
)
from blogwriter.domain.jobflow import defer_on_brake, require_service, step_services
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    VOICE_BUILD_GUIDE,
    VOICE_DIGEST,
    JobInput,
    entity_changed,
)
from blogwriter.domain.ports import Services, ServiceUnavailable
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    CadenceSettings,
    EmployerPolicy,
    VoicePreferences,
)
from blogwriter.domain.voice import StyleGuide, VoiceDigest, VoiceSample
from blogwriter.jobs import JobFailed, JobRegistry, Step, StepContext
from blogwriter.notify.payload import NotificationPayload
from blogwriter.voice._prompts import load_prompt
from blogwriter.voice.corpus import (
    active_samples,
    add_sample,
    count_linkedin_samples,
    required_linkedin_samples,
    rescore_samples,
)
from blogwriter.voice.digest import (
    MAX_COMPARISONS,
    DigestDraft,
    DigestPeriod,
    assemble_digest,
    digest_period,
    digest_request,
    effective_cadence,
    is_due,
    local_date_of,
)
from blogwriter.voice.guide import (
    GuideDraft,
    assemble_guide,
    built_by_synthesis,
    exemplar_candidates,
    guide_blocklist_findings,
    guide_request,
    load_active_guide,
    next_version,
)

__all__ = [
    "BUILD_STEPS",
    "CLASSIFY_BATCH",
    "DIGEST_STEPS",
    "build_job_id",
    "digest_job_id",
    "register_jobs",
]

_logger: Final = logging.getLogger("blogwriter.voice")
register_log_fields("sample_id", "digest_id", "guide_id", "post_id")

CLASSIFY_BATCH: Final = 4
"""Texts per classifier call (checkpointed after each batch)."""
MAX_ENTITY_EVENTS: Final = 25
VERSION_ATTEMPTS: Final = 5
DIGEST_PUSH_TITLE: Final = "Voice digest ready"
DIGEST_URL: Final = "/library/voice"

_AI_USE_DETAIL: Final = "Confirm the employer AI-use policy in Settings > Policy first."
_BLOCKLIST_DETAIL: Final = "Save the blocklist once in Settings > Policy first."


def build_job_id(now: datetime) -> str:
    """The owner's guide build of the day (``enqueue_once`` with ``rerun``: a double tap returns
    the running build, a later build gets ``-r<n>``)."""
    return f"voice-build-{now:%Y%m%d}"


def digest_job_id(now: datetime) -> str:
    """The owner's digest run of the day (``enqueue_once`` with ``rerun``)."""
    return f"voice-digest-run-{now:%Y%m%d}"


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _object(value: JsonValue) -> dict[str, JsonValue]:
    return dict(value) if isinstance(value, dict) else {}


def _string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _origin(ctx: StepContext) -> Origin:
    try:
        return JobInput.from_json(ctx.input).origin
    except ValueError:
        return "background"


class BuildGuideInput(JobInput):
    """``voice.build-guide`` input. ``provisional``: the owner asked for a guide from the samples
    there are, without the usual minimum of LinkedIn posts (the guide is marked provisional)."""

    provisional: bool = False


def _provisional(ctx: StepContext) -> bool:
    try:
        return BuildGuideInput.from_json(ctx.input).provisional
    except ValueError:
        return False


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _sha(sample: VoiceSample) -> str:
    return sample.content_sha256 or sha256_hex(sample.text)


async def _require_ai_use(store: Store, stamp: str) -> None:
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    try:
        require_ai_use(policy)
    except AiUseNotConfirmed:
        raise JobFailed("ai_use_not_confirmed", _AI_USE_DETAIL) from None


async def _require_enough(
    store: Store, samples: Sequence[VoiceSample], stamp: str, *, provisional: bool = False
) -> tuple[int, int]:
    """``(have, required)`` LinkedIn posts in ``samples``; ``JobFailed`` below the required count
    (the owner may exclude samples while a build runs) unless the build is ``provisional``."""
    preferences, _ = await read_singleton(store, VoicePreferences, now=stamp)
    required = required_linkedin_samples(preferences)
    have = count_linkedin_samples(samples)
    if not samples:
        raise JobFailed("not_enough_samples", "Add voice samples first, then build the guide.")
    if have < required and not provisional:
        raise JobFailed(
            "not_enough_samples",
            f"Add at least {required} LinkedIn posts you wrote yourself ({have} so far), "
            "then build the guide again.",
        )
    return have, required


async def _configured_blocklist(store: Store, stamp: str) -> Blocklist:
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    if not blocklist.configured:
        raise JobFailed("blocklist_not_configured", _BLOCKLIST_DETAIL)
    return blocklist


# --------------------------------------------------------------------------------------------
# voice.build-guide
# --------------------------------------------------------------------------------------------


async def build_score(ctx: StepContext) -> JsonValue:
    """Re-score every active sample; fail when fewer LinkedIn posts than required remain."""
    services = step_services()
    store = services.store
    now = services.clock.now()
    changed = await rescore_samples(store, await active_samples(store), slop=services.slop, now=now)
    for sample_id in changed[:MAX_ENTITY_EVENTS]:
        await ctx.emit(ENTITY_CHANGED, entity_changed("voice_sample", sample_id))
    samples = [stored.doc for stored in await active_samples(store)]
    have, _ = await _require_enough(store, samples, to_iso(now), provisional=_provisional(ctx))
    return {
        "samples": len(samples),
        "rescored": len(changed),
        "eligible": sum(1 for sample in samples if sample.exemplar_eligible),
        "linkedinSamples": have,
        "slopChecked": services.slop is not None,
    }


def _verdicts(value: JsonValue) -> dict[str, dict[str, JsonValue]]:
    out: dict[str, dict[str, JsonValue]] = {}
    for sample_id, verdict in _object(value).items():
        if isinstance(verdict, dict):
            out[sample_id] = dict(verdict)
    return out


def _passed(verdict: Mapping[str, JsonValue] | None, sample: VoiceSample) -> bool:
    return (
        verdict is not None and verdict.get("passed") is True and verdict.get("sha") == _sha(sample)
    )


async def build_classify(ctx: StepContext) -> JsonValue:
    """The classifier over the exemplar candidates (verdicts keyed by sample id and text hash,
    checkpointed per batch). No classifier: no exemplars (never a pass)."""
    services = step_services()
    store = services.store
    origin = _origin(ctx)
    candidates = exemplar_candidates([stored.doc for stored in await active_samples(store)])
    classifier = services.classifier
    if classifier is None:
        return {"verdicts": {}, "approved": [], "classifier": "unavailable"}
    verdicts = _verdicts(_object(ctx.output).get("verdicts"))
    pending = [
        sample for sample in candidates if verdicts.get(sample.id, {}).get("sha") != _sha(sample)
    ]
    for batch in _chunks(pending, CLASSIFY_BATCH):
        try:
            with defer_on_brake(services.clock.now()):
                checks = await classifier.classify(
                    [sample.text for sample in batch], origin=origin, job_id=ctx.job_id
                )
        except AiUseNotConfirmed:
            raise JobFailed("ai_use_not_confirmed", _AI_USE_DETAIL) from None
        except ServiceUnavailable as exc:
            raise JobFailed(f"{exc.name}_unavailable", "This feature is not configured.") from None
        for index, sample in enumerate(batch):
            check = checks[index] if len(checks) == len(batch) else None
            passed = (
                check is not None and check.passed is True and check.content_sha256 == _sha(sample)
            )
            verdicts[sample.id] = {"sha": _sha(sample), "passed": passed}
        await ctx.checkpoint({"verdicts": dict(verdicts)})
    approved: list[JsonValue] = [
        sample.id for sample in candidates if _passed(verdicts.get(sample.id), sample)
    ]
    return {"verdicts": dict(verdicts), "approved": approved, "classifier": "ok"}


def _check_samples(samples: Sequence[VoiceSample], blocklist: Blocklist) -> None:
    """Every sample text and title bound for the model clears the blocklist, or the job fails
    naming the samples (by id) the owner has to edit or exclude."""
    fields: dict[str, str | None] = {}
    for sample in samples:
        fields[sample.id] = sample.text
        fields[f"{sample.id}.title"] = sample.title
    try:
        check_texts(fields, blocklist)
    except BlocklistNotConfigured:
        raise JobFailed("blocklist_not_configured", _BLOCKLIST_DETAIL) from None
    except ConfidentialText as exc:
        ids = sorted({name.split(".")[0] for name in exc.findings})
        shown = ", ".join(ids[:5]) + (f" and {len(ids) - 5} more" if len(ids) > 5 else "")
        raise JobFailed(
            "blocklist_hit",
            f"Voice samples match the blocklist: {shown}. Edit or exclude them, then build again.",
        ) from None


def _guide_result(guide: StyleGuide) -> JsonValue:
    return {"guideId": guide.id, "version": guide.version, "exemplars": len(guide.exemplars)}


async def build_synthesize(ctx: StepContext) -> JsonValue:
    """A new ``proposed`` guide version from the corpus (the version number is checkpointed
    before the model call, so a resumed step never creates a second version)."""
    services = step_services()
    store = services.store
    ai = require_service(services.ai, "ai")
    now = services.clock.now()
    stamp = to_iso(now)
    await _require_ai_use(store, stamp)
    blocklist = await _configured_blocklist(store, stamp)
    checkpointed = _object(ctx.output).get("version")
    if isinstance(checkpointed, int) and checkpointed >= 1:
        try:
            existing = await read_doc(store, StyleGuide, StyleGuide.id_for(checkpointed))
        except NotFound:
            pass
        else:
            if built_by_synthesis(existing.doc):
                return _guide_result(existing.doc)
            checkpointed = None
    samples = [stored.doc for stored in await active_samples(store)]
    have, required = await _require_enough(store, samples, stamp, provisional=_provisional(ctx))
    provisional = have < required
    _check_samples(samples, blocklist)
    by_id = {sample.id: sample for sample in samples}
    verdicts = _verdicts(_object(ctx.outputs.get("classify")).get("verdicts"))
    approved = [
        sample.id
        for sample in exemplar_candidates(samples)
        if _passed(verdicts.get(sample.id), sample)
    ]
    base = await load_active_guide(store)
    prompt = load_prompt("guide")
    request, included = guide_request(samples, candidates=approved, base=base, prompt=prompt)
    if isinstance(checkpointed, int) and checkpointed >= 1:
        version = checkpointed
    else:
        version = await next_version(store)
        await ctx.checkpoint({"version": version})
    with defer_on_brake(now):
        result = await ai.run(Task.SYNTHESIS, request, origin=_origin(ctx), job_id=ctx.job_id)
    if result.refused:
        raise JobFailed("synthesis_refused", "The model declined to write the guide. Try again.")
    draft = result.output(GuideDraft)
    record = ServedByRecord.from_served_by(
        result.served_by,
        step="synthesize",
        at=stamp,
        refused=bool(result.refusals),
        cost_usd=float(result.cost_usd),
        prompt_version=prompt.version,
    )
    for _ in range(VERSION_ATTEMPTS):
        try:
            guide = assemble_guide(
                draft,
                version=version,
                samples=by_id,
                approved=approved,
                included=included,
                base=base,
                served_by=record,
                now=stamp,
            )
        except ValueError:
            raise JobFailed("synthesis_empty", "The model returned an empty guide.") from None
        if provisional:
            guide = guide.model_copy(update={"provisional": True})
        if guide_blocklist_findings(guide, blocklist):
            raise JobFailed(
                "blocklist_hit", "The proposed guide matched the blocklist. Build it again."
            )
        try:
            stored = await create_doc(store, guide)
        except Conflict:
            version = await next_version(store)
            await ctx.checkpoint({"version": version})
            continue
        await ctx.emit(ENTITY_CHANGED, entity_changed("style_guide", stored.doc.id))
        return _guide_result(stored.doc)
    raise JobFailed("version_conflict", "Other guide versions kept appearing. Build again.")


BUILD_STEPS: Final = (
    Step("score", build_score),
    Step("classify", build_classify),
    Step("synthesize", build_synthesize),
)


# --------------------------------------------------------------------------------------------
# voice.digest
# --------------------------------------------------------------------------------------------


async def _latest_end(store: Store, before: str) -> str | None:
    found = await find_docs(
        store,
        VoiceDigest,
        [Filter("periodEnd", "<", before)],
        order_by=OrderBy("periodEnd", descending=True),
        limit=1,
    )
    return found[0].doc.period_end if found else None


async def _period_posts(store: Store, period: DigestPeriod) -> list[Post]:
    found = await find_docs(
        store,
        Post,
        [Filter("postedAt", ">=", period.start), Filter("postedAt", "<", period.end)],
        order_by=OrderBy("postedAt"),
        limit=MAX_COMPARISONS * 2,
    )
    posts = [
        stored.doc
        for stored in found
        if (stored.doc.ai_draft_text or "").strip() and (stored.doc.final_text or "").strip()
    ]
    return posts[:MAX_COMPARISONS]


async def digest_collect(ctx: StepContext) -> JsonValue:
    """Decide whether this run produces a digest; collect the period's posts; add their final
    texts to the corpus."""
    services = step_services()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    origin = _origin(ctx)
    run_at = parse_iso(ctx.scheduled_for) if ctx.scheduled_for else now
    if origin == "background":
        autonomy, _ = await read_singleton(store, AutonomySettings, now=stamp)
        if autonomy.paused:
            return {"skipped": "paused"}
    preferences, _ = await read_singleton(store, VoicePreferences, now=stamp)
    cadence = effective_cadence(preferences, run_at)
    if cadence == "monthly" and preferences.digest_cadence != "monthly":
        await update_singleton(store, VoicePreferences, _monthly_mutation(stamp), now=stamp)
    settings, _ = await read_singleton(store, CadenceSettings, now=stamp)
    zone = settings.timezone
    if origin == "background" and not is_due(cadence, local_date_of(run_at, zone)):
        return {"skipped": "not_due", "cadence": cadence}
    probe = digest_period(run_at=run_at, timezone=zone, cadence=cadence, last_end=None)
    last_end = await _latest_end(store, probe.end)
    period = digest_period(run_at=run_at, timezone=zone, cadence=cadence, last_end=last_end)
    try:
        await read_doc(store, VoiceDigest, period.digest_id)
    except NotFound:
        pass
    else:
        return {"skipped": "exists", "digestId": period.digest_id}
    blocklist = await _configured_blocklist(store, stamp)
    posts = await _period_posts(store, period)
    summary: dict[str, JsonValue] = {
        "digestId": period.digest_id,
        "periodStart": period.start,
        "periodEnd": period.end,
        "endDate": period.end_date,
        "cadence": cadence,
    }
    if not posts:
        return {**summary, "skipped": "no_posts"}
    added = 0
    for post in posts:
        final = post.final_text or ""
        if blocklist_findings(final, blocklist):
            _logger.info("voice_final_text_skipped", extra={"post_id": post.id})
            continue
        stored = await add_sample(
            store,
            source_type="final_post",
            text=final,
            now=now,
            slop=services.slop,
            source_ref=post.id,
            posted_at=post.posted_at,
            pillar=post.pillar,
        )
        if stored.doc.created_at == stamp:
            added += 1
            await ctx.emit(ENTITY_CHANGED, entity_changed("voice_sample", stored.doc.id))
    post_ids: list[JsonValue] = [post.id for post in posts]
    return {**summary, "postIds": post_ids, "samplesAdded": added}


def _monthly_mutation(stamp: str) -> Callable[[VoicePreferences], VoicePreferences | None]:
    def mutate(preferences: VoicePreferences) -> VoicePreferences | None:
        if preferences.digest_cadence == "monthly":
            return None
        return preferences.model_copy(update={"digest_cadence": "monthly", "updated_at": stamp})

    return mutate


def _period_of(collected: Mapping[str, JsonValue]) -> DigestPeriod:
    start, end, end_date = (
        _string(collected.get("periodStart")),
        _string(collected.get("periodEnd")),
        _string(collected.get("endDate")),
    )
    if start is None or end is None or end_date is None:
        raise JobFailed("invalid_step_output", "The collect step left no period.")
    return DigestPeriod(start=start, end=end, end_date=end_date)


def _digest_result(digest: VoiceDigest) -> dict[str, JsonValue]:
    return {
        "digestId": digest.id,
        "comparisons": len(digest.comparisons),
        "suggestions": len(digest.suggestions),
    }


async def _usable_posts(
    store: Store, post_ids: Sequence[JsonValue], blocklist: Blocklist
) -> list[Post]:
    posts: list[Post] = []
    for post_id in post_ids:
        if not isinstance(post_id, str):
            continue
        try:
            post = (await read_doc(store, Post, post_id)).doc
        except NotFound:
            continue
        draft, final = post.ai_draft_text or "", post.final_text or ""
        if not draft.strip() or not final.strip():
            continue
        if blocklist_findings(draft, blocklist) or blocklist_findings(final, blocklist):
            # Text bound for a model must clear the blocklist: leave this post out.
            _logger.info("voice_digest_post_skipped", extra={"post_id": post.id})
            continue
        posts.append(post)
    return posts


async def digest_synthesize(ctx: StepContext) -> JsonValue:
    """Compare the drafts with the final texts and save the digest (deterministic id)."""
    collected = _object(ctx.outputs.get("collect"))
    if "skipped" in collected:
        return collected
    services = step_services()
    store = services.store
    ai = require_service(services.ai, "ai")
    now = services.clock.now()
    stamp = to_iso(now)
    period = _period_of(collected)
    try:
        existing = await read_doc(store, VoiceDigest, period.digest_id)
    except NotFound:
        pass
    else:
        return _digest_result(existing.doc)
    await _require_ai_use(store, stamp)
    blocklist = await _configured_blocklist(store, stamp)
    raw_ids = collected.get("postIds")
    posts = await _usable_posts(store, raw_ids if isinstance(raw_ids, list) else [], blocklist)
    if not posts:
        return {"skipped": "no_posts", "digestId": period.digest_id}
    guide = await load_active_guide(store)
    rules = guide.rules if guide is not None else None
    version = guide.version if guide is not None else None
    prompt = load_prompt("digest")
    request = digest_request(posts, rules=rules, guide_version=version, prompt=prompt)
    with defer_on_brake(now):
        result = await ai.run(Task.SYNTHESIS, request, origin=_origin(ctx), job_id=ctx.job_id)
    if result.refused:
        raise JobFailed("synthesis_refused", "The model declined to write the digest.")
    draft = result.output(DigestDraft)
    record = ServedByRecord.from_served_by(
        result.served_by,
        step="synthesize",
        at=stamp,
        refused=bool(result.refusals),
        cost_usd=float(result.cost_usd),
        prompt_version=prompt.version,
    )
    digest = assemble_digest(
        draft,
        period=period,
        posts=posts,
        rules=rules,
        guide_version=version,
        served_by=record,
        blocklist=blocklist,
        now=stamp,
    )
    try:
        stored = await create_doc(store, digest)
    except Conflict:
        stored = await read_doc(store, VoiceDigest, digest.id)
    await ctx.emit(ENTITY_CHANGED, entity_changed("voice_digest", stored.doc.id))
    return _digest_result(stored.doc)


async def _push(services: Services, digest_id: str) -> bool:
    payload = NotificationPayload(
        title=DIGEST_PUSH_TITLE, url=f"{DIGEST_URL}?digest={digest_id}", tag=digest_id
    )
    try:
        result = await services.notifier.notify(payload, urgency="low")
    except Exception as exc:  # push is best effort: the digest is saved and listed anyway
        _logger.warning(
            "voice_digest_push_failed", extra={"digest_id": digest_id, **describe_exception(exc)}
        )
        return False
    return result.any_delivered


async def digest_save(ctx: StepContext) -> JsonValue:
    """Tell the owner (push, once) and finish with the digest's id and counts."""
    synthesized = _object(ctx.outputs.get("synthesize"))
    if "skipped" in synthesized:
        return synthesized
    digest_id = _string(synthesized.get("digestId"))
    if digest_id is None:
        raise JobFailed("invalid_step_output", "The synthesize step left no digest.")
    pushed = _object(ctx.output).get("pushed")
    if not isinstance(pushed, bool):
        pushed = await _push(step_services(), digest_id)
        await ctx.checkpoint({"pushed": pushed})
    return {**synthesized, "pushed": pushed}


DIGEST_STEPS: Final = (
    Step("collect", digest_collect),
    Step("synthesize", digest_synthesize),
    Step("save", digest_save),
)


def register_jobs(registry: JobRegistry) -> None:
    """Register ``voice.build-guide`` and ``voice.digest`` (called once by ``blogwriter.main``)."""
    registry.register(VOICE_BUILD_GUIDE, BUILD_STEPS)
    registry.register(VOICE_DIGEST, DIGEST_STEPS)
