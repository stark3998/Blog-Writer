"""HTTP routes of the voice package (contract W-A4): samples, the guide, digests, and the review
of the v1 migration's voice seeds (:mod:`blogwriter.voice.seeds`).

Every route is owner-only and every non-GET passes CSRF (core). Mutations of existing documents
take ``If-Match``. Text the owner saves passes the blocklist (``check_texts``) like every intake
save; owner-typed sample URLs are fetched only through ``services.fetcher`` with
``allowed_urls=[url]`` (never the egress helper plus a second HTML parser).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, OrderBy, Store
from blogwriter.domain.api import (
    ApiModel,
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.confidentiality import BlocklistNotConfigured, check_texts, require_ai_use
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    JobsDep,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
)
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import VOICE_BUILD_GUIDE, VOICE_DIGEST, JobInput
from blogwriter.domain.ports import FetchRefused, Services, ServiceUnavailable
from blogwriter.domain.settings import Blocklist, EmployerPolicy, VoicePreferences
from blogwriter.domain.states import (
    GUIDE_MACHINE,
    SAMPLE_MACHINE,
    SUGGESTION_MACHINE,
    GuideStatus,
    SampleStatus,
    SuggestionStatus,
)
from blogwriter.domain.voice import StyleGuide, VoiceDigest, VoiceSample, VoiceSourceType
from blogwriter.voice.corpus import (
    MAX_TITLE_CHARS,
    add_sample,
    apply_score,
    linkedin_sample_count,
    required_linkedin_samples,
)
from blogwriter.voice.guide import (
    RulesFull,
    activate,
    derived_version,
    guide_texts,
    list_guides,
    load_active_guide,
    load_active_stored,
    reject,
    rules_with,
)
from blogwriter.voice.jobs import BuildGuideInput, build_job_id, digest_job_id
from blogwriter.voice.models import (
    GuideEditIn,
    GuideVersionSummary,
    GuideView,
    SampleIn,
    SampleStatusIn,
    SampleUpdateIn,
    SuggestionDecisionIn,
)
from blogwriter.voice.scoring import sample_weight, score_text
from blogwriter.voice.seeds import (
    approve_seed,
    dismiss_seed,
    get_seed,
    list_seeds,
    restore_seed,
)
from blogwriter.voice.text import MAX_SAMPLE_CHARS, clip, normalize_text

__all__ = ["LIST_DEFAULT_LIMIT", "get_router"]

LIST_DEFAULT_LIMIT: Final = 20
"""Samples and digests are large documents (``Store.find`` reads whole documents)."""

type LimitQuery = Annotated[int | None, Query()]
type CursorQuery = Annotated[str | None, Query()]


def _limit(limit: int | None) -> int:
    return clamp_limit(limit if limit is not None else LIST_DEFAULT_LIMIT)


async def _blocklist(store: Store, stamp: str) -> Blocklist:
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    return blocklist


# --------------------------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------------------------


async def list_samples(
    store: StoreDep,
    source_type: Annotated[VoiceSourceType | None, Query(alias="sourceType")] = None,
    status: Annotated[SampleStatus | None, Query()] = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[VoiceSample]:
    """Voice samples, most recently changed first."""
    size = _limit(limit)
    with problems():
        filters: list[Filter] = []
        if source_type is not None:
            filters.append(Filter("sourceType", "==", source_type))
        if status is not None:
            filters.append(Filter("status", "==", status.value))
        if cursor is not None:
            filters.append(Filter("updatedAt", "<", decode_cursor(cursor)))
        found = await find_docs(
            store,
            VoiceSample,
            filters,
            order_by=OrderBy("updatedAt", descending=True),
            limit=size + 1,
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.updated_at) if len(found) > size else None
    return Page[VoiceSample](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def get_sample(sample_id: str, store: StoreDep, response: Response) -> Versioned[VoiceSample]:
    with problems():
        stored = await read_doc(store, VoiceSample, sample_id)
    return versioned(stored, response)


async def create_sample(
    body: SampleIn,
    services: ServicesDep,
    store: StoreDep,
    clock: ClockDep,
    response: Response,
) -> Versioned[VoiceSample]:
    """Add a sample from pasted text, or from an owner-typed URL fetched by the research
    fetcher (``allowed_urls=[url]``). Scored on save; text and title clear the blocklist."""
    now = clock.now()
    with problems():
        blocklist = await _blocklist(store, to_iso(now))
        text = normalize_text(body.text or "")
        title = body.title.strip() if body.title is not None else None
        if not text and body.url is None:
            raise ProblemError(
                422,
                code="text_or_url_required",
                detail="Paste the text, or give the address of a page you wrote.",
            )
        check_texts({"text": text or None, "title": title, "url": body.url}, blocklist)
        if not text and body.url is not None:
            fetcher = services.fetcher
            if fetcher is None:
                raise ServiceUnavailable("fetcher")
            try:
                page = await fetcher.fetch(body.url, allowed_urls=[body.url])
            except FetchRefused:
                raise ProblemError(
                    502, code="fetch_failed", detail="The page could not be fetched."
                ) from None
            text = normalize_text(page.text)[:MAX_SAMPLE_CHARS].rstrip()
            if not text:
                raise ProblemError(502, code="fetch_failed", detail="The page had no text.")
            if not title and page.title:
                title = clip(page.title, MAX_TITLE_CHARS)
            check_texts({"text": text, "title": title}, blocklist)
        stored = await add_sample(
            store,
            source_type=body.source_type,
            text=text,
            now=now,
            slop=services.slop,
            title=title or None,
            url=body.url,
            posted_at=body.posted_at,
            pillar=body.pillar,
            owner_unassisted=body.owner_unassisted,
        )
    return versioned(stored, response)


async def update_sample(
    sample_id: str,
    body: SampleUpdateIn,
    services: ServicesDep,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[VoiceSample]:
    """Edit a sample (fields left out stay; ``null`` clears the title or pillar); re-scored."""
    now = clock.now()
    stamp = to_iso(now)
    sent = body.model_fields_set
    with problems():
        current = await read_doc(store, VoiceSample, sample_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        sample = current.doc
        text = sample.text
        if "text" in sent and body.text is not None:
            text = normalize_text(body.text)
            if not text:
                raise ProblemError(422, code="text_required", detail="The text cannot be empty.")
        title = sample.title
        if "title" in sent:
            title = (body.title or "").strip() or None
        pillar = body.pillar if "pillar" in sent else sample.pillar
        unassisted = sample.owner_unassisted
        if "owner_unassisted" in sent and body.owner_unassisted is not None:
            unassisted = body.owner_unassisted
        check_texts({"text": text, "title": title}, await _blocklist(store, stamp))
        score = await asyncio.to_thread(score_text, text, slop=services.slop)
        changed = sample.model_copy(
            update={
                "text": text,
                "title": title,
                "pillar": pillar,
                "owner_unassisted": unassisted,
                "weight": sample_weight(sample.source_type, unassisted),
                "content_sha256": sha256_hex(text),
                "updated_at": stamp,
            }
        )
        stored = await replace_doc(store, apply_score(changed, score), if_match=if_match)
    return versioned(stored, response)


async def set_sample_status(
    sample_id: str,
    body: SampleStatusIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[VoiceSample]:
    """Exclude a sample from the corpus, or include it again."""
    stamp = to_iso(clock.now())
    with problems():
        current = await read_doc(store, VoiceSample, sample_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        target = SAMPLE_MACHINE.transition(current.doc.status, body.status)
        changed = current.doc.model_copy(
            update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
        )
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


async def delete_sample(sample_id: str, store: StoreDep, if_match: IfMatch) -> Response:
    with problems():
        current = await read_doc(store, VoiceSample, sample_id)  # a voice sample, not a story
        if current.etag != if_match:
            raise etag_mismatch(current)
        await delete_doc(store, VoiceSample, sample_id, if_match=if_match)
    return Response(status_code=204)


# --------------------------------------------------------------------------------------------
# The guide
# --------------------------------------------------------------------------------------------


async def get_guide_view(store: StoreDep, clock: ClockDep) -> GuideView:
    with problems():
        active = await load_active_stored(store)
        guides = await list_guides(store)
        preferences, _ = await read_singleton(store, VoicePreferences, now=to_iso(clock.now()))
        have = await linkedin_sample_count(store)
    required = required_linkedin_samples(preferences)
    return GuideView(
        active=versioned(active) if active is not None else None,
        versions=[
            GuideVersionSummary(
                id=stored.doc.id,
                version=stored.doc.version,
                status=stored.doc.status,
                created_at=stored.doc.created_at,
                approved_at=stored.doc.approved_at,
            )
            for stored in guides
        ],
        linkedin_samples=have,
        required=required,
        can_build=have >= required,
    )


async def get_guide(guide_id: str, store: StoreDep, response: Response) -> Versioned[StyleGuide]:
    with problems():
        stored = await read_doc(store, StyleGuide, guide_id)
    return versioned(stored, response)


async def _require_model_inputs(services: Services, store: Store, stamp: str) -> None:
    """What every voice job needs before it can call a model (fail early with the owner's fix)."""
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    require_ai_use(policy)
    if not (await _blocklist(store, stamp)).configured:
        raise BlocklistNotConfigured()
    if services.ai is None:
        raise ServiceUnavailable("ai")


class BuildGuideIn(ApiModel):
    """``POST /api/voice/guide/build`` (optional body)."""

    provisional: bool = False
    """Build from the samples there are, without the minimum of LinkedIn posts. The guide is
    marked provisional until a full build replaces it."""


async def build_guide(
    services: ServicesDep,
    store: StoreDep,
    jobs: JobsDep,
    clock: ClockDep,
    body: BuildGuideIn | None = None,
) -> JobAccepted:
    """Start ``voice.build-guide`` (409 ``not_enough_samples`` below the required LinkedIn
    posts unless ``provisional``). A second click while a build runs returns the running build."""
    now = clock.now()
    stamp = to_iso(now)
    provisional = body is not None and body.provisional
    with problems():
        preferences, _ = await read_singleton(store, VoicePreferences, now=stamp)
        required = required_linkedin_samples(preferences)
        have = await linkedin_sample_count(store)
        if have < required and not provisional:
            raise ProblemError(
                409,
                code="not_enough_samples",
                detail=f"Add at least {required} LinkedIn posts you wrote yourself first.",
                extensions={"have": have, "need": required},
            )
        await _require_model_inputs(services, store, stamp)
        job = await enqueue_once(
            jobs,
            VOICE_BUILD_GUIDE,
            BuildGuideInput(origin="owner", provisional=provisional).to_json(),
            job_id=build_job_id(now),
            on_conflict="rerun",
        )
    return JobAccepted(job_id=job.id, status=job.status)


async def create_guide_version(
    guide_id: str, body: GuideEditIn, store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[StyleGuide]:
    """The owner's edit of version ``guide_id``: a new ``proposed`` version (``editedByOwner``)."""
    now = clock.now()
    with problems():
        base = (await read_doc(store, StyleGuide, guide_id)).doc
        text = normalize_text(body.body)
        if not text:
            raise ProblemError(422, code="body_required", detail="The guide cannot be empty.")
        rules = body.rules.to_rules()
        check_texts(
            guide_texts(base.model_copy(update={"body": text, "rules": rules})),
            await _blocklist(store, to_iso(now)),
        )
        stored = await derived_version(
            store, base, body=text, rules=rules, now=now, edited_by_owner=True
        )
    return versioned(stored, response)


async def activate_guide(
    guide_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
) -> Versioned[StyleGuide]:
    """Activate a version (the previous active one is superseded). The whole guide clears the
    blocklist first: it goes into every drafting prompt."""
    now = clock.now()
    with problems():
        current = await read_doc(store, StyleGuide, guide_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        GUIDE_MACHINE.transition(current.doc.status, GuideStatus.ACTIVE)
        check_texts(guide_texts(current.doc), await _blocklist(store, to_iso(now)))
        stored = await activate(store, current, if_match=if_match, now=now)
    return versioned(stored, response)


async def reject_guide(
    guide_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
) -> Versioned[StyleGuide]:
    now = clock.now()
    with problems():
        current = await read_doc(store, StyleGuide, guide_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        stored = await reject(store, current, if_match=if_match, now=now)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------------------------


async def list_digests(
    store: StoreDep, limit: LimitQuery = None, cursor: CursorQuery = None
) -> Page[VoiceDigest]:
    """Digests, newest period first."""
    size = _limit(limit)
    with problems():
        filters: list[Filter] = []
        if cursor is not None:
            filters.append(Filter("periodEnd", "<", decode_cursor(cursor)))
        found = await find_docs(
            store,
            VoiceDigest,
            filters,
            order_by=OrderBy("periodEnd", descending=True),
            limit=size + 1,
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.period_end) if len(found) > size else None
    return Page[VoiceDigest](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def get_digest(digest_id: str, store: StoreDep, response: Response) -> Versioned[VoiceDigest]:
    with problems():
        stored = await read_doc(store, VoiceDigest, digest_id)
    return versioned(stored, response)


async def decide_suggestion(
    digest_id: str,
    suggestion_id: str,
    body: SuggestionDecisionIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[VoiceDigest]:
    """Accept or dismiss one suggestion. Accepting adds its rule to a new ``proposed`` version
    of the active guide (deterministic, no AI); a rule the guide already has adds nothing."""
    now = clock.now()
    stamp = to_iso(now)
    with problems():
        current = await read_doc(store, VoiceDigest, digest_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        digest = current.doc
        index = next(
            (i for i, item in enumerate(digest.suggestions) if item.id == suggestion_id), None
        )
        if index is None:
            raise ProblemError(404, code="not_found")
        suggestion = digest.suggestions[index]
        accept = body.decision == "accept"
        target = SUGGESTION_MACHINE.transition(
            suggestion.status, SuggestionStatus.ACCEPTED if accept else SuggestionStatus.DISMISSED
        )
        if accept:
            check_texts({"suggestion": suggestion.text}, await _blocklist(store, stamp))
            base = await load_active_guide(store)
            if base is None:
                raise ProblemError(
                    409, code="guide_missing", detail="Activate a Voice & Style Guide first."
                )
            try:
                rules = rules_with(base.rules, suggestion.kind, suggestion.text)
            except RulesFull:
                raise ProblemError(
                    409,
                    code=RulesFull.code,
                    detail="The guide has no room for another rule of this kind. Edit it first.",
                ) from None
            if rules is not None:
                await derived_version(
                    store, base, body=base.body, rules=rules, now=now, edited_by_owner=False
                )
        decided = suggestion.model_copy(update={"status": target, "decided_at": stamp})
        suggestions = list(digest.suggestions)
        suggestions[index] = decided
        changed = digest.model_copy(update={"suggestions": suggestions, "updated_at": stamp})
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


async def run_digest(
    services: ServicesDep, store: StoreDep, jobs: JobsDep, clock: ClockDep
) -> JobAccepted:
    """Start ``voice.digest`` now (owner origin: runs whatever the cadence or the pause)."""
    now = clock.now()
    with problems():
        await _require_model_inputs(services, store, to_iso(now))
        job = await enqueue_once(
            jobs,
            VOICE_DIGEST,
            JobInput(origin="owner").to_json(),
            job_id=digest_job_id(now),
            on_conflict="rerun",
        )
    return JobAccepted(job_id=job.id, status=job.status)


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


def get_router() -> APIRouter:
    """Every ``/api/voice`` route."""
    router = APIRouter(prefix="/api/voice", tags=["voice"])
    add = router.add_api_route
    add("/samples", list_samples, methods=["GET"], summary="Voice samples")
    add("/samples", create_sample, methods=["POST"], status_code=201, summary="Add a sample")
    add("/samples/{sample_id}", get_sample, methods=["GET"], summary="One sample")
    add("/samples/{sample_id}", update_sample, methods=["PUT"], summary="Edit a sample")
    add(
        "/samples/{sample_id}",
        delete_sample,
        methods=["DELETE"],
        status_code=204,
        response_class=Response,
        summary="Delete a sample",
    )
    add(
        "/samples/{sample_id}/status",
        set_sample_status,
        methods=["POST"],
        summary="Exclude or include a sample",
    )
    add("/guide", get_guide_view, methods=["GET"], summary="The active guide and its versions")
    add("/guide/build", build_guide, methods=["POST"], status_code=202, summary="Build a guide")
    add("/guide/{guide_id}", get_guide, methods=["GET"], summary="One guide version")
    add(
        "/guide/{guide_id}/versions",
        create_guide_version,
        methods=["POST"],
        status_code=201,
        summary="Save an edited version",
    )
    add("/guide/{guide_id}/activate", activate_guide, methods=["POST"], summary="Activate")
    add("/guide/{guide_id}/reject", reject_guide, methods=["POST"], summary="Reject")
    add("/digests", list_digests, methods=["GET"], summary="Voice digests")
    add("/digests/run", run_digest, methods=["POST"], status_code=202, summary="Run a digest")
    add("/digests/{digest_id}", get_digest, methods=["GET"], summary="One digest")
    add(
        "/digests/{digest_id}/suggestions/{suggestion_id}",
        decide_suggestion,
        methods=["POST"],
        summary="Accept or dismiss a suggestion",
    )
    add("/seeds", list_seeds, methods=["GET"], summary="The v1 migration's voice seeds")
    add("/seeds/{seed_id}", get_seed, methods=["GET"], summary="One voice seed")
    add(
        "/seeds/{seed_id}/approve",
        approve_seed,
        methods=["POST"],
        summary="Add a seed's sample text to the corpus",
    )
    add("/seeds/{seed_id}/dismiss", dismiss_seed, methods=["POST"], summary="Dismiss a seed")
    add(
        "/seeds/{seed_id}/restore",
        restore_seed,
        methods=["POST"],
        summary="Put a dismissed seed back in review",
    )
    return router
