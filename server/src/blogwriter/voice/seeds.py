"""Review of the v1 migration's voice seeds (``/api/voice/seeds``): list, approve, dismiss,
restore.

A :class:`~blogwriter.domain.seeds.VoiceSeed` is one v1 voice profile (name, description, tone,
style notes and the profile's sample text), written ``proposed`` by the v1 migration
(``docs/v2/build/migration.md``). Nothing uses it until the owner decides:

- **approve** (``proposed -> approved``, If-Match on the seed): the sample text joins the voice
  corpus through :func:`~blogwriter.voice.corpus.add_sample` (normalized, then scored for AI
  tells and by the structural detectors), as the source the owner names (default
  ``portfolio_post``: v1 wrote blog posts) and titled with the profile's name unless the owner
  gives a title. Like every intake save, the blocklist must be configured (409
  ``blocklist_not_configured``) and must clear the text and the title (422
  ``confidential_text``). The sample is written before the seed and carries the seed's id in
  ``sourceRef``, so a crash in between leaves a proposed seed whose second approval reuses that
  sample instead of adding another. A profile without sample text cannot be approved (409
  ``seed_empty``): dismiss it. The tone and style notes stay on the seed for reference; guide
  rules change only through the guide's own review.
- **dismiss** (``proposed -> rejected``) and **restore** (``rejected -> proposed``): the status
  only.

Every decision moves the seed's ``updatedAt`` forward (:func:`blogwriter.domain.seeds.reviewed`),
so a re-run of the migration skips the seed instead of undoing the review. Logs carry ids only,
never the profile's text.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Final

from fastapi import Query, Response

from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, NotFound, OrderBy, PreconditionFailed, Store
from blogwriter.domain.api import Page, Versioned, clamp_limit, decode_cursor, encode_cursor
from blogwriter.domain.base import to_iso
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import Stored, find_docs, read_doc, read_singleton, replace_doc
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.seeds import VoiceSeed, reviewed
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.states import REVIEW_MACHINE, ReviewStatus
from blogwriter.domain.voice import VoiceSample
from blogwriter.jobs.clock import Clock
from blogwriter.voice.corpus import MAX_TITLE_CHARS, add_sample
from blogwriter.voice.models import VoiceSeedApproveIn
from blogwriter.voice.text import MAX_SAMPLE_CHARS, clip, normalize_text

__all__ = [
    "SEED_LIST_DEFAULT_LIMIT",
    "approve_seed",
    "dismiss_seed",
    "get_seed",
    "list_seeds",
    "restore_seed",
]

SEED_LIST_DEFAULT_LIMIT: Final = 20
"""Voice seeds carry up to 20,000 characters of text (``Store.find`` reads whole documents)."""

_logger: Final = logging.getLogger("blogwriter.voice")


def _title(seed: VoiceSeed, request: VoiceSeedApproveIn) -> str | None:
    """The sample's title: the owner's (``null`` clears it), else the profile's name."""
    raw = request.title if "title" in request.model_fields_set else seed.name
    text = (raw or "").strip()
    return clip(text, MAX_TITLE_CHARS) if text else None


async def _sample_for(
    store: Store,
    seed: VoiceSeed,
    request: VoiceSeedApproveIn,
    *,
    text: str,
    title: str | None,
    slop: SlopChecker | None,
    now: datetime,
) -> Stored[VoiceSample]:
    """The sample an earlier approval of ``seed`` made before it could mark the seed (found by
    ``sourceRef``), else a new scored sample."""
    earlier = await find_docs(
        store, VoiceSample, [Filter("sourceRef", "==", seed.id)], limit=1, strict=True
    )
    if earlier:
        return earlier[0]
    return await add_sample(
        store,
        source_type=request.source_type,
        text=text,
        now=now,
        slop=slop,
        title=title,
        pillar=request.pillar,
        owner_unassisted=request.owner_unassisted,
        source_ref=seed.id,
    )


async def _current(store: Store, seed_id: str, if_match: str) -> Stored[VoiceSeed]:
    current = await read_doc(store, VoiceSeed, seed_id)
    if current.etag != if_match:
        raise etag_mismatch(current)
    return current


async def _replace(store: Store, seed: VoiceSeed, if_match: str) -> Stored[VoiceSeed]:
    """Write the reviewed seed with the owner's If-Match; a lost race shows the current etag."""
    try:
        return await replace_doc(store, seed, if_match=if_match)
    except PreconditionFailed:
        try:
            latest = await read_doc(store, VoiceSeed, seed.id)
        except NotFound:
            raise ProblemError(404, code="not_found") from None
        raise etag_mismatch(latest) from None


# --------------------------------------------------------------------------------------------
# Routes (mounted by ``blogwriter.voice.routes.get_router``)
# --------------------------------------------------------------------------------------------


async def list_seeds(
    store: StoreDep,
    status: Annotated[ReviewStatus | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[VoiceSeed]:
    """The v1 migration's voice seeds by id (``?status=proposed``: the review queue)."""
    size = clamp_limit(limit if limit is not None else SEED_LIST_DEFAULT_LIMIT)
    filters: list[Filter] = []
    if status is not None:
        filters.append(Filter("status", "==", status.value))
    with problems():
        if cursor is not None:
            filters.append(Filter("id", ">", decode_cursor(cursor)))
        found = await find_docs(store, VoiceSeed, filters, order_by=OrderBy("id"), limit=size + 1)
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.id) if len(found) > size else None
    return Page[VoiceSeed](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def get_seed(seed_id: str, store: StoreDep, response: Response) -> Versioned[VoiceSeed]:
    with problems():
        stored = await read_doc(store, VoiceSeed, seed_id)
    return versioned(stored, response)


async def approve_seed(
    seed_id: str,
    services: ServicesDep,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
    body: VoiceSeedApproveIn | None = None,
) -> Versioned[VoiceSeed]:
    """Add the profile's sample text to the corpus, then mark the seed approved (see the module
    docs). 409 ``invalid_transition`` unless it is proposed, 409 ``seed_empty`` without text,
    412 on a stale etag."""
    request = body if body is not None else VoiceSeedApproveIn()
    now = clock.now()
    with problems():
        seed = (await _current(store, seed_id, if_match)).doc
        REVIEW_MACHINE.transition(seed.status, ReviewStatus.APPROVED)  # before anything changes
        text = normalize_text(seed.text)[:MAX_SAMPLE_CHARS].rstrip()
        if not text:
            raise ProblemError(
                409,
                code="seed_empty",
                detail="This voice profile has no sample text. Dismiss it instead.",
            )
        title = _title(seed, request)
        blocklist, _ = await read_singleton(store, Blocklist, now=to_iso(now))
        check_texts({"text": text, "title": title}, blocklist)
        sample = await _sample_for(
            store, seed, request, text=text, title=title, slop=services.slop, now=now
        )
        approved = reviewed(seed, ReviewStatus.APPROVED, now=now).model_copy(
            update={"sample_id": sample.doc.id}
        )
        stored = await _replace(store, approved, if_match)
    _logger.info("voice_seed_approved", extra={"doc_id": stored.doc.id})
    return versioned(stored, response)


async def _decide(
    store: Store,
    clock: Clock,
    seed_id: str,
    if_match: str,
    response: Response,
    target: ReviewStatus,
) -> Versioned[VoiceSeed]:
    with problems():
        current = await _current(store, seed_id, if_match)
        stored = await _replace(store, reviewed(current.doc, target, now=clock.now()), if_match)
    _logger.info("voice_seed_reviewed", extra={"doc_id": stored.doc.id, "status": target.value})
    return versioned(stored, response)


async def dismiss_seed(
    seed_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
) -> Versioned[VoiceSeed]:
    """Dismiss a proposed seed (``proposed -> rejected``); nothing joins the corpus."""
    return await _decide(store, clock, seed_id, if_match, response, ReviewStatus.REJECTED)


async def restore_seed(
    seed_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
) -> Versioned[VoiceSeed]:
    """Put a dismissed seed back in review (``rejected -> proposed``)."""
    return await _decide(store, clock, seed_id, if_match, response, ReviewStatus.PROPOSED)
