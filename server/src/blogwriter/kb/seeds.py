"""Review of the v1 migration's keyword seeds (``/api/kb/seeds``): list, approve, dismiss, restore.

A :class:`~blogwriter.domain.seeds.KbSeed` is one v1 topic's keyword list with a guessed pillar,
written ``proposed`` by the v1 migration (``docs/v2/build/migration.md``). Nothing uses it until
the owner decides:

- **approve** (``proposed -> approved``, If-Match on the seed): the keywords the owner keeps
  (default: all of them) join the vocabulary of the pillar the owner names (default: the
  suggested one). The keywords did not start in v2, so this is an intake save: the blocklist must
  be configured (409 ``blocklist_not_configured``) and must clear every keyword (422
  ``confidential_text``, findings by position and term id). The pillar settings then pass kb's
  own checks (duplicates, limits, the blocklist over every pillar text) and are saved with
  If-Match on the etag they were read with; a concurrent save makes it read them again (the
  change only adds terms). A term the vocabulary already has (Unicode case-insensitive) is not
  added twice. The settings are written before the seed, so a crash in between leaves a proposed
  seed whose second approval adds nothing new.
- **dismiss** (``proposed -> rejected``) and **restore** (``rejected -> proposed``): the status
  only.

Every decision moves the seed's ``updatedAt`` forward (:func:`blogwriter.domain.seeds.reviewed`),
so a re-run of the migration skips the seed instead of undoing the review. Logs carry ids and
counts, never topics or keywords.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Sequence
from typing import Annotated, Final

from fastapi import Query, Response
from pydantic import ValidationError

from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, NotFound, OrderBy, PreconditionFailed, Store
from blogwriter.domain.api import Page, Versioned, clamp_limit, decode_cursor, encode_cursor
from blogwriter.domain.base import to_iso
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    Stored,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    update_singleton,
)
from blogwriter.domain.seeds import KbSeed, reviewed
from blogwriter.domain.settings import Blocklist, PillarSettings
from blogwriter.domain.states import REVIEW_MACHINE, ReviewStatus
from blogwriter.jobs.clock import Clock
from blogwriter.kb.checks import Invalid, invalid, pillar_problems, pillar_texts, validation_error
from blogwriter.kb.defaults import read_settings
from blogwriter.kb.models import KbSeedApproveIn

__all__ = [
    "MAX_PILLAR_VOCABULARY",
    "add_pillar_vocabulary",
    "approve_seed",
    "dismiss_seed",
    "get_seed",
    "list_seeds",
    "restore_seed",
]

MAX_PILLAR_VOCABULARY: Final = 200
"""``Pillar.vocabulary`` holds at most this many terms (the domain model's limit)."""

_logger: Final = logging.getLogger("blogwriter.kb")


def _fold(text: str) -> str:
    """The comparison key kb's duplicate check uses (``kb.checks``)."""
    return unicodedata.normalize("NFC", text).casefold()


def _unique(terms: Sequence[str]) -> list[str]:
    """``terms`` without case-insensitive repeats (the first spelling wins), order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = _fold(term)
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


def _validated(candidate: PillarSettings) -> PillarSettings:
    """``candidate`` validated (``model_copy`` does not); 422 on the request otherwise."""
    try:
        return PillarSettings.model_validate(candidate.model_dump(warnings=False))
    except ValidationError as exc:
        raise validation_error(exc) from None


async def add_pillar_vocabulary(
    store: Store, pillar_id: str, terms: Sequence[str], *, blocklist: Blocklist, now: str
) -> int:
    """Add ``terms`` the vocabulary of pillar ``pillar_id`` does not have yet (Unicode
    case-insensitive; the first spelling wins) and save the pillar settings. Returns how many
    terms were added; with none, nothing is written.

    The settings are saved with If-Match on the etag they were read with (created when the owner
    never saved them), and read again when a concurrent save wins. The saved document passes
    kb's pillar checks and ``blocklist`` over every pillar text. Raises the 422 problem when
    ``pillar_id`` is not a pillar or the vocabulary has no room, ``ConfidentialText`` or
    ``BlocklistNotConfigured`` from the blocklist check; nothing is written then.
    """
    added = 0

    def mutate(settings: PillarSettings) -> PillarSettings | None:
        nonlocal added
        index = next(
            (i for i, pillar in enumerate(settings.pillars) if pillar.id == pillar_id), None
        )
        if index is None:
            raise invalid([Invalid(("pillar",), "Not one of your pillars.")])
        pillar = settings.pillars[index]
        known = {_fold(term) for term in pillar.vocabulary}
        new_terms = [term for term in _unique(terms) if _fold(term) not in known]
        added = len(new_terms)
        if not new_terms:
            return None
        room = max(MAX_PILLAR_VOCABULARY - len(pillar.vocabulary), 0)
        if len(new_terms) > room:
            raise invalid(
                [
                    Invalid(
                        ("keywords",),
                        f"The pillar's vocabulary has room for {room} more terms; "
                        f"{len(new_terms)} would be added.",
                    )
                ]
            )
        pillars = list(settings.pillars)
        pillars[index] = pillar.model_copy(update={"vocabulary": [*pillar.vocabulary, *new_terms]})
        candidate = _validated(settings.model_copy(update={"pillars": pillars, "updated_at": now}))
        found = pillar_problems(candidate)
        if found:
            raise invalid(found)
        check_texts(pillar_texts(candidate), blocklist)
        return candidate

    await update_singleton(store, PillarSettings, mutate, now=now)
    if added:
        _logger.info(
            "kb_settings_saved",
            extra={"kind": PillarSettings.KIND, "doc_id": PillarSettings.SINGLETON_ID},
        )
    return added


def _chosen_keywords(seed: KbSeed, requested: Sequence[str] | None) -> list[str]:
    """The keywords to add: ``requested`` (each spelled as on the seed), else all of them."""
    if requested is None:
        chosen = _unique(seed.keywords)
    else:
        offered = set(seed.keywords)
        unknown = [
            Invalid(("keywords", index), "Not one of this seed's keywords.")
            for index, term in enumerate(requested)
            if term not in offered
        ]
        if unknown:
            raise invalid(unknown)
        chosen = _unique(requested)
    if not chosen:
        raise ProblemError(
            409, code="seed_empty", detail="This seed has no keywords to add. Dismiss it instead."
        )
    return chosen


async def _current(store: Store, seed_id: str, if_match: str) -> Stored[KbSeed]:
    current = await read_doc(store, KbSeed, seed_id)
    if current.etag != if_match:
        raise etag_mismatch(current)
    return current


async def _replace(store: Store, seed: KbSeed, if_match: str) -> Stored[KbSeed]:
    """Write the reviewed seed with the owner's If-Match; a lost race shows the current etag."""
    try:
        return await replace_doc(store, seed, if_match=if_match)
    except PreconditionFailed:
        try:
            latest = await read_doc(store, KbSeed, seed.id)
        except NotFound:
            raise ProblemError(404, code="not_found") from None
        raise etag_mismatch(latest) from None


# --------------------------------------------------------------------------------------------
# Routes (mounted by ``blogwriter.kb.routes.get_router``)
# --------------------------------------------------------------------------------------------


async def list_seeds(
    store: StoreDep,
    status: Annotated[ReviewStatus | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[KbSeed]:
    """The v1 migration's keyword seeds by id (``?status=proposed``: the review queue)."""
    size = clamp_limit(limit)
    filters: list[Filter] = []
    if status is not None:
        filters.append(Filter("status", "==", status.value))
    with problems():
        if cursor is not None:
            filters.append(Filter("id", ">", decode_cursor(cursor)))
        found = await find_docs(store, KbSeed, filters, order_by=OrderBy("id"), limit=size + 1)
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.id) if len(found) > size else None
    return Page[KbSeed](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def get_seed(seed_id: str, store: StoreDep, response: Response) -> Versioned[KbSeed]:
    with problems():
        stored = await read_doc(store, KbSeed, seed_id)
    return versioned(stored, response)


async def approve_seed(
    seed_id: str,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
    body: KbSeedApproveIn | None = None,
) -> Versioned[KbSeed]:
    """Add the seed's keywords to a pillar's vocabulary, then mark it approved (see the module
    docs). 409 ``invalid_transition`` unless it is proposed, 409 ``seed_empty`` without
    keywords, 412 on a stale etag, 422 for an unknown pillar or keyword."""
    request = body if body is not None else KbSeedApproveIn()
    now = clock.now()
    stamp = to_iso(now)
    with problems():
        seed = (await _current(store, seed_id, if_match)).doc
        REVIEW_MACHINE.transition(seed.status, ReviewStatus.APPROVED)  # before anything changes
        keywords = _chosen_keywords(seed, request.keywords)
        pillar_id = request.pillar or seed.suggested_pillar
        if pillar_id is None:
            raise invalid([Invalid(("pillar",), "Choose the pillar these keywords belong to.")])
        pillars, _ = await read_settings(store, PillarSettings, now=stamp)
        if pillars.get(pillar_id) is None:  # checked again when saving
            raise invalid([Invalid(("pillar",), "Not one of your pillars.")])
        blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
        check_texts({f"keywords.{index}": term for index, term in enumerate(keywords)}, blocklist)
        added = await add_pillar_vocabulary(
            store, pillar_id, keywords, blocklist=blocklist, now=stamp
        )
        approved = reviewed(seed, ReviewStatus.APPROVED, now=now).model_copy(
            update={"applied_pillar": pillar_id, "applied_keywords": keywords}
        )
        stored = await _replace(store, approved, if_match)
    _logger.info("kb_seed_approved", extra={"doc_id": stored.doc.id, "count": added})
    return versioned(stored, response)


async def _decide(
    store: Store,
    clock: Clock,
    seed_id: str,
    if_match: str,
    response: Response,
    target: ReviewStatus,
) -> Versioned[KbSeed]:
    with problems():
        current = await _current(store, seed_id, if_match)
        stored = await _replace(store, reviewed(current.doc, target, now=clock.now()), if_match)
    _logger.info("kb_seed_reviewed", extra={"doc_id": stored.doc.id, "status": target.value})
    return versioned(stored, response)


async def dismiss_seed(
    seed_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
) -> Versioned[KbSeed]:
    """Dismiss a proposed seed (``proposed -> rejected``); nothing is applied."""
    return await _decide(store, clock, seed_id, if_match, response, ReviewStatus.REJECTED)


async def restore_seed(
    seed_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
) -> Versioned[KbSeed]:
    """Put a dismissed seed back in review (``rejected -> proposed``)."""
    return await _decide(store, clock, seed_id, if_match, response, ReviewStatus.PROPOSED)
