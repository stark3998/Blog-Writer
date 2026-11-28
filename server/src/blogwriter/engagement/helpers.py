"""Reply and comment helpers: items with other members' text, angles, screenshots, finalize.

Nothing is automated on LinkedIn. The owner pastes (or screenshots) what they want to answer,
gets up to three angles grounded in their own material, drafts and lints the reply, posts it
themselves and finalizes it here.

**Other members' text never outlives 48 hours.** An :class:`EngagementItem` holds it with
``expiresAt = createdAt + 48 h`` and a Cosmos ``ttl`` (garbage collection only). Expiry is
checked in code (:func:`is_expired`, which also caps an item at 48 hours from ``createdAt``
whatever ``expiresAt`` says): lists hide expired items, the item routes answer 410 and delete
what they find expired, ``engagement.purge`` (:func:`purge_expired`) deletes every expired item
every 6 hours, and finalizing or dismissing deletes the item at once. Only the owner's final
text and the post URL are kept (:class:`EngagementRecord`); backups never include items
(``domain.backups.BACKUP_KINDS``). Screenshots are never stored: they live in the request and
the extraction call only.

**Untrusted text gets no tools and at most anonymized data.** Every ENGAGEMENT request is
tool-free, ``Sensitivity.ANONYMIZED`` and ``origin="owner"``, and carries other members' text
inside a delimited section that the system prompt declares to be data. The owner's AI-use
confirmation is required first (anonymized owner material and other members' text go to the
model) and the blocklist must clear the pasted text. The owner material is the usable story and
POV banks (``stories.load_bank``), research briefs and the active voice guide's rules; any
part the blocklist does not clear is left out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from blogwriter.ai.router import RouterResult
from blogwriter.ai.types import (
    MAX_IMAGE_BYTES,
    ImageMediaType,
    ImagePart,
    Message,
    Request,
    Sensitivity,
    Task,
    TextPart,
)
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store, StoreUnavailable
from blogwriter.domain.articles import Article
from blogwriter.domain.base import Document, derived_id, parse_iso, to_iso
from blogwriter.domain.common import Finding, ServedByRecord
from blogwriter.domain.confidentiality import blocklist_findings, check_texts, require_ai_use
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    Stored,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    update_doc,
)
from blogwriter.domain.engagement import (
    THIRD_PARTY_TTL,
    Angle,
    EngagementItem,
    EngagementRecord,
    WatchEntry,
    third_party_expiry,
)
from blogwriter.domain.ports import ModelRunner
from blogwriter.domain.posts import Post
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import Blocklist, EmployerPolicy, VoicePreferences
from blogwriter.domain.states import ENGAGEMENT_MACHINE, EngagementStatus, GuideStatus, transition
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.voice import StyleGuide
from blogwriter.engagement._prompts import ANGLES, EXTRACT, Prompt, defuse, load_prompt, section
from blogwriter.engagement._text import (
    MAX_PASTED_CHARS,
    clip,
    normalize_model_text,
    normalize_owner_text,
    normalize_third_party,
    relevance_terms,
    words,
)
from blogwriter.engagement.lint import blocking_findings, is_substantive
from blogwriter.engagement.models import AnglesReply, ExtractReply, ItemKind
from blogwriter.storage import MediaRejected, ingest_image
from blogwriter.stories import load_bank

__all__ = [
    "ANGLES_STEP",
    "ANGLE_MAX_CHARS",
    "MAX_ANGLES",
    "MAX_BRIEFS",
    "MAX_POVS",
    "MAX_SERVED_BY",
    "MAX_STORIES",
    "MODEL_TIMEOUT_SECONDS",
    "SCREENSHOT_CONTENT_TYPES",
    "SCREENSHOT_MAX_PIXELS",
    "Extracted",
    "Grounding",
    "GroundingEntry",
    "InvalidField",
    "ItemExpired",
    "ModelTimeout",
    "NoOwnerMaterial",
    "NothingExtracted",
    "angles_request",
    "brief_material",
    "clean_angles",
    "create_item",
    "dismiss_item",
    "expiry_deadline",
    "extract_screenshot",
    "finalize_item",
    "gather_grounding",
    "generate_angles",
    "is_expired",
    "mark_engaged",
    "owner_post_text",
    "pov_material",
    "purge_expired",
    "read_live_item",
    "record_id_for",
    "remaining_ttl",
    "reply_sources",
    "story_material",
]

MAX_ANGLES: Final = 3
ANGLE_MAX_CHARS: Final = 1000
"""``Angle.text`` holds at most this many characters."""
MAX_GROUNDING: Final = 5
"""``Angle.groundedIn`` holds at most this many ids."""
MAX_SERVED_BY: Final = 5
"""``EngagementItem.servedBy`` keeps the newest records only."""
MAX_STORIES: Final = 6
MAX_POVS: Final = 6
MAX_BRIEFS: Final = 2
RECENT_BRIEFS: Final = 10
"""Briefs read to look for one that fits a comment (newest first)."""
MIN_BRIEF_OVERLAP: Final = 3
"""Content words a brief must share with the pasted text to be offered (unless the reply's
post links to it)."""
ENTRY_MAX_CHARS: Final = 1200
OWNER_POST_MAX_CHARS: Final = 1500
SCREENSHOT_MAX_PIXELS: Final = 12_000_000
MODEL_TIMEOUT_SECONDS: Final = 90
"""An ENGAGEMENT call (low effort) that takes longer answers 504 instead of holding the
request near the ingress limit."""
ANGLES_STEP: Final = "angles"
PURGE_BATCH: Final = 100
PURGE_MAX_ROUNDS: Final = 100

_IMAGE_TYPES: Final[dict[str, ImageMediaType]] = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/webp": "image/webp",
}
SCREENSHOT_CONTENT_TYPES: Final = frozenset(_IMAGE_TYPES)

_ASK: Final[dict[ItemKind, str]] = {
    "reply": (
        "Someone commented on my post; their comment is inside <pasted_text>. Write up to three "
        "angles for my reply."
    ),
    "comment": (
        "This is someone else's post or comment, inside <pasted_text>. Write up to three angles "
        "for a comment of my own."
    ),
}
_EXTRACT_ASK: Final = "Transcribe the post or comment text in this screenshot."

_logger: Final = logging.getLogger("blogwriter.engagement")
register_log_fields(
    "item_id",
    "record_id",
    "angle_count",
    "material_count",
    "deleted_count",
    "image_bytes",
)

type MaterialKind = Literal["story", "pov", "brief"]


class ItemExpired(Exception):
    """The item is past its 48 hours (410 ``expired``); it has been deleted."""

    code: Final = "expired"

    def __init__(self) -> None:
        super().__init__("the item expired")


class NoOwnerMaterial(Exception):
    """No usable story, POV or brief to ground angles in (409 ``no_owner_material``)."""

    code: Final = "no_owner_material"

    def __init__(self) -> None:
        super().__init__("no owner material")


class NothingExtracted(Exception):
    """The screenshot showed no readable post or comment text (422 ``nothing_extracted``)."""

    code: Final = "nothing_extracted"

    def __init__(self) -> None:
        super().__init__("nothing extracted")


class ModelTimeout(TimeoutError):
    """The model did not answer within :data:`MODEL_TIMEOUT_SECONDS` (504 ``timeout``)."""

    code: Final = "timeout"

    def __init__(self) -> None:
        super().__init__("the model did not answer in time")


class InvalidField(Exception):
    """A request field points at nothing or holds nothing usable (422 on that field)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(field)
        self.field = field
        self.message = message


# --------------------------------------------------------------------------------------------
# Items and expiry
# --------------------------------------------------------------------------------------------


def expiry_deadline(item: EngagementItem) -> datetime:
    """When the item's text must be gone: ``expiresAt``, and never later than 48 hours after
    ``createdAt`` (defense in depth against a wrong ``expiresAt``)."""
    return min(parse_iso(item.expires_at), parse_iso(item.created_at) + THIRD_PARTY_TTL)


def is_expired(item: EngagementItem, now: datetime) -> bool:
    """Whether the item's 48 hours are over (checked in code; Cosmos TTL only collects)."""
    return expiry_deadline(item) <= now


def remaining_ttl(item: EngagementItem, now: datetime) -> int:
    """The Cosmos ``ttl`` (seconds, at least 1) to write with an update: Cosmos counts TTL from
    the last write, so every update shortens it to what is left of the 48 hours."""
    return max(1, math.ceil((expiry_deadline(item) - now).total_seconds()))


async def create_item(
    store: Store,
    *,
    item_kind: ItemKind,
    pasted_text: str,
    now: datetime,
    post_id: str | None = None,
    target_url: str | None = None,
) -> Stored[EngagementItem]:
    """Store other members' text for 48 hours (``expiresAt`` and ``ttl``).

    The text is normalized (:func:`~blogwriter.engagement._text.normalize_third_party`). Raises
    :class:`InvalidField` for an empty text or an unknown ``postId``.
    """
    text = clip(normalize_third_party(pasted_text), MAX_PASTED_CHARS)
    if not text:
        raise InvalidField("pastedText", "The text is empty once invisible characters are removed.")
    if post_id is not None and await _optional(store, Post, post_id) is None:
        raise InvalidField("postId", "No post with this id.")
    stamp = to_iso(now)
    expires_at, ttl = third_party_expiry(now)
    item = EngagementItem(
        id=EngagementItem.new_id(now=now),
        created_at=stamp,
        updated_at=stamp,
        expires_at=expires_at,
        ttl=ttl,
        item_kind=item_kind,
        status=EngagementStatus.OPEN,
        status_changed_at=stamp,
        post_id=post_id,
        target_url=target_url,
        pasted_text=text,
    )
    stored = await create_doc(store, item)
    _logger.info("engagement_item_created", extra={"item_id": item.id})
    return stored


async def _forget(store: Store, item_id: str) -> None:
    """Delete an item (missing is fine; a store outage leaves it to the purge job)."""
    try:
        await delete_doc(store, EngagementItem, item_id)
    except NotFound:
        pass
    except StoreUnavailable:
        _logger.warning("engagement_item_not_deleted", extra={"item_id": item_id})


async def read_live_item(store: Store, item_id: str, *, now: datetime) -> Stored[EngagementItem]:
    """An item that is still within its 48 hours. ``NotFound`` when missing; an expired item
    is deleted and :class:`ItemExpired` raised."""
    stored = await read_doc(store, EngagementItem, item_id)
    if is_expired(stored.doc, now):
        await _forget(store, item_id)
        raise ItemExpired()
    return stored


async def dismiss_item(store: Store, current: Stored[EngagementItem], *, now: datetime) -> None:
    """Delete an item the owner does not want to answer (``current`` carries the etag the owner
    saw). Expired items are deleted too."""
    item = current.doc
    if not is_expired(item, now):
        transition(ENGAGEMENT_MACHINE, item.status, EngagementStatus.DISMISSED)
    with contextlib.suppress(NotFound):
        await delete_doc(store, EngagementItem, item.id, if_match=current.etag)
    _logger.info("engagement_item_dismissed", extra={"item_id": item.id})


async def purge_expired(store: Store, *, now: datetime) -> int:
    """Delete every item past its 48 hours; returns how many were deleted.

    Reads raw documents (not models), so an item that no longer fits its model is purged too:
    ``expiresAt <= now``, ``createdAt`` 48 hours ago or more, or no ``expiresAt`` at all.
    """
    stamp = to_iso(now)
    cutoff = to_iso(now - THIRD_PARTY_TTL)
    kind = Filter("kind", "==", EngagementItem.KIND)
    deleted = 0
    for condition in (
        Filter("expiresAt", "<=", stamp),
        Filter("createdAt", "<=", cutoff),
        Filter("expiresAt", "exists", False),
    ):
        for _ in range(PURGE_MAX_ROUNDS):
            found = await store.find(EngagementItem.CONTAINER, [kind, condition], limit=PURGE_BATCH)
            for raw in found:
                try:
                    await store.delete(
                        EngagementItem.CONTAINER, raw.id, partition_key=raw.partition_key
                    )
                except NotFound:
                    continue
                deleted += 1
            if len(found) < PURGE_BATCH:
                break
    return deleted


# --------------------------------------------------------------------------------------------
# Grounding material
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroundingEntry:
    """One piece of the owner's material, labelled ``ref`` (``s1``, ``p2``, ``b1``) in the
    prompt so the model can cite it without copying document ids."""

    ref: str
    doc_id: str
    kind: MaterialKind
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Grounding:
    """What angles may draw on: entries, the owner's own post (replies) and voice rules."""

    entries: tuple[GroundingEntry, ...] = ()
    owner_post: str | None = field(default=None, repr=False)
    voice: str | None = field(default=None, repr=False)

    def doc_id(self, ref: str) -> str | None:
        """The document id behind a prompt ref, or ``None`` for a ref that was never given."""
        key = ref.strip().casefold()
        return next((entry.doc_id for entry in self.entries if entry.ref == key), None)


@dataclass(frozen=True, slots=True)
class _Candidate:
    doc_id: str
    text: str = field(repr=False)
    preferred: bool
    on_pillar: bool
    overlap: int
    index: int


def _top(candidates: Sequence[_Candidate], limit: int) -> list[_Candidate]:
    """Entries the reply's post uses first, then same pillar, then shared words, then newest."""
    return sorted(
        candidates,
        key=lambda c: (not c.preferred, not c.on_pillar, -c.overlap, c.index),
    )[:limit]


def _clear(text: str, blocklist: Blocklist) -> bool:
    return not blocklist_findings(text, blocklist)


def story_material(story: StoryEntry) -> str:
    """A story as the prompt shows it (clipped)."""
    metrics = "; ".join(f"{metric.label}: {metric.value}" for metric in story.metrics)
    rows = (
        ("Title", story.title),
        ("Problem", story.problem),
        ("Actions", story.actions),
        ("Decisions and trade-offs", story.decisions),
        ("Metrics", metrics),
        ("Lessons", story.lessons),
    )
    body = "\n".join(f"{label}: {value.strip()}" for label, value in rows if value.strip())
    return clip(body, ENTRY_MAX_CHARS)


def pov_material(pov: PovEntry) -> str:
    """A POV entry as the prompt shows it (clipped)."""
    rows = (("Position", pov.statement), ("Context", pov.context or ""))
    body = "\n".join(f"{label}: {value.strip()}" for label, value in rows if value.strip())
    return clip(body, ENTRY_MAX_CHARS)


def _source_label(brief: Brief, citation_ids: Sequence[str]) -> str:
    for citation_id in citation_ids:
        citation = brief.citation(citation_id)
        if citation is not None:
            return f" (source: {clip(citation.publisher or citation.title, 80)})"
    return ""


def _brief_text(brief: Brief) -> str:
    parts = [brief.topic, brief.executive_summary]
    parts.extend(finding.text for finding in brief.findings)
    parts.extend(stat.label for stat in brief.stats)
    return "\n".join(parts)


def brief_material(brief: Brief, terms: frozenset[str]) -> str:
    """A brief as the prompt shows it: topic, summary and the four findings or stats that share
    the most words with the pasted text, each with its publisher (never a URL)."""
    rows: list[tuple[int, int, str]] = []
    for index, finding in enumerate(brief.findings):
        line = f"- {clip(finding.text, 280)}{_source_label(brief, finding.citation_ids)}"
        rows.append((len(terms & relevance_terms(finding.text)), index, line))
    for index, stat in enumerate(brief.stats, start=len(brief.findings)):
        label = clip(f"{stat.value} {stat.label}", 280)
        line = f"- {label}{_source_label(brief, stat.citation_ids)}"
        rows.append((len(terms & relevance_terms(stat.label)), index, line))
    chosen = [line for _, _, line in sorted(rows, key=lambda row: (-row[0], row[1]))[:4]]
    lines = [f"Topic: {brief.topic}", f"Summary: {clip(brief.executive_summary, 500)}"]
    if chosen:
        lines.append("Findings:")
        lines.extend(chosen)
    return clip("\n".join(lines), ENTRY_MAX_CHARS)


async def _optional[D: Document](store: Store, cls: type[D], doc_id: str | None) -> D | None:
    """The document, or ``None`` when there is no id, no document or an unreadable one."""
    if doc_id is None:
        return None
    try:
        return (await read_doc(store, cls, doc_id)).doc
    except (NotFound, InvalidStoredDocument):
        return None


async def _linked_brief_id(store: Store, post: Post | None) -> str | None:
    if post is None:
        return None
    if post.brief_id is not None:
        return post.brief_id
    article = await _optional(store, Article, post.article_id)
    return article.brief_id if article is not None else None


async def _brief_candidates(
    store: Store, post: Post | None, terms: frozenset[str]
) -> list[_Candidate]:
    briefs: list[tuple[Brief, bool]] = []
    linked_id = await _linked_brief_id(store, post)
    linked = await _optional(store, Brief, linked_id)
    if linked is not None:
        briefs.append((linked, True))
    recent = await find_docs(
        store, Brief, order_by=OrderBy("updatedAt", descending=True), limit=RECENT_BRIEFS
    )
    briefs.extend((stored.doc, False) for stored in recent if stored.doc.id != linked_id)
    candidates: list[_Candidate] = []
    for index, (brief, is_linked) in enumerate(briefs):
        overlap = len(terms & relevance_terms(_brief_text(brief)))
        if not is_linked and overlap < MIN_BRIEF_OVERLAP:
            continue
        candidates.append(
            _Candidate(brief.id, brief_material(brief, terms), is_linked, False, overlap, index)
        )
    return candidates


async def _voice_note(store: Store, blocklist: Blocklist, *, now: str) -> str | None:
    """The active voice guide's rules and two short exemplars, when there is a guide."""
    preferences, _ = await read_singleton(store, VoicePreferences, now=now)
    guide = await _optional(store, StyleGuide, preferences.active_guide_id)
    if guide is None or guide.status is not GuideStatus.ACTIVE:
        return None
    rules = guide.rules
    lines: list[str] = []
    if rules.prefer:
        lines.append("Prefer: " + "; ".join(clip(rule, 160) for rule in rules.prefer[:8]))
    if rules.avoid:
        lines.append("Avoid: " + "; ".join(clip(rule, 160) for rule in rules.avoid[:8]))
    if rules.banned_phrases:
        lines.append("Never write: " + "; ".join(rules.banned_phrases[:30]))
    for exemplar in guide.exemplars[:2]:
        lines.append("How I write: " + clip(normalize_owner_text(exemplar.excerpt), 400))
    cleared = [line for line in lines if _clear(line, blocklist)]
    return "\n".join(cleared) or None


async def gather_grounding(
    store: Store, item: EngagementItem, *, blocklist: Blocklist, now: str
) -> Grounding:
    """The owner material for one item: usable stories and POVs (the reply's post's own first,
    then its pillar, then shared words), up to two briefs (the post's, or recent ones that
    share words with the pasted text), the owner's post for a reply and the voice rules.
    Anything the blocklist does not clear is left out."""
    post = await _optional(store, Post, item.post_id)
    terms = relevance_terms(item.pasted_text)
    pillar = post.pillar if post is not None else None
    preferred = set(post.story_ids) | set(post.pov_ids) if post is not None else set[str]()
    bank = await load_bank(store)
    stories = [
        _Candidate(
            story.id,
            text,
            story.id in preferred,
            pillar is not None and pillar in story.pillars,
            len(terms & relevance_terms(text)),
            index,
        )
        for index, story in enumerate(bank.stories)
        if (text := story_material(story))
    ]
    povs = [
        _Candidate(
            pov.id,
            text,
            pov.id in preferred,
            pov.pillar == pillar,
            len(terms & relevance_terms(text)),
            index,
        )
        for index, pov in enumerate(bank.povs)
        if (text := pov_material(pov))
    ]
    briefs = await _brief_candidates(store, post, terms)
    entries: list[GroundingEntry] = []
    pools: tuple[tuple[MaterialKind, list[_Candidate], int], ...] = (
        ("story", stories, MAX_STORIES),
        ("pov", povs, MAX_POVS),
        ("brief", briefs, MAX_BRIEFS),
    )
    for kind, pool, limit in pools:
        cleared = [candidate for candidate in pool if _clear(candidate.text, blocklist)]
        for number, candidate in enumerate(_top(cleared, limit), start=1):
            entries.append(
                GroundingEntry(f"{kind[0]}{number}", candidate.doc_id, kind, candidate.text)
            )
    owner_post: str | None = None
    if post is not None:
        posted = normalize_owner_text(post.final_text or post.display_text)
        if posted and _clear(posted, blocklist):
            owner_post = clip(posted, OWNER_POST_MAX_CHARS)
    voice = await _voice_note(store, blocklist, now=now)
    return Grounding(tuple(entries), owner_post, voice)


async def owner_post_text(store: Store, item: EngagementItem) -> str | None:
    """The owner's post a reply belongs to (final text, else display text), or ``None``."""
    post = await _optional(store, Post, item.post_id)
    if post is None:
        return None
    return normalize_owner_text(post.final_text or post.display_text) or None


def reply_sources(item: EngagementItem, grounding: Grounding | None = None) -> list[str]:
    """The texts a reply must not restate: the pasted text and, for a reply, the owner's post."""
    sources = [item.pasted_text]
    if grounding is not None and grounding.owner_post:
        sources.append(grounding.owner_post)
    return sources


# --------------------------------------------------------------------------------------------
# Angles
# --------------------------------------------------------------------------------------------


async def _engagement_call(runner: ModelRunner, request: Request) -> RouterResult:
    """One owner-origin ENGAGEMENT call, bounded by :data:`MODEL_TIMEOUT_SECONDS`."""
    try:
        async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
            return await runner.run(Task.ENGAGEMENT, request, origin="owner")
    except TimeoutError:
        raise ModelTimeout() from None


def angles_request(item: EngagementItem, grounding: Grounding, prompt: Prompt) -> Request:
    """The ENGAGEMENT request: tool-free, anonymized, the pasted text in its own section."""
    material = "\n\n".join(
        section("entry", defuse(entry.text), ref=entry.ref, kind=entry.kind)
        for entry in grounding.entries
    )
    blocks = [section("owner_material", material)]
    if grounding.voice:
        blocks.append(section("voice", defuse(grounding.voice)))
    if grounding.owner_post:
        blocks.append(section("my_post", defuse(grounding.owner_post)))
    blocks.append(section("pasted_text", defuse(item.pasted_text), source="untrusted"))
    blocks.append(_ASK[item.item_kind])
    return Request(
        system=prompt.text,
        messages=(Message.user("\n\n".join(blocks)),),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=AnglesReply,
    )


def clean_angles(
    reply: AnglesReply | None,
    grounding: Grounding,
    *,
    sources: Sequence[str],
    blocklist: Blocklist,
    policy: EmployerPolicy | None = None,
) -> list[Angle]:
    """The model's angles that are usable as they stand, at most :data:`MAX_ANGLES`.

    An angle is dropped when it cites no entry it was given (it is not grounded in the owner's
    material), when a blocking lint rule fires (blocklist, a link, engagement bait, a long run
    copied from the text it answers) or when it repeats another angle. Texts are trimmed to
    :data:`ANGLE_MAX_CHARS`; groundings become document ids.
    """
    if reply is None:
        return []
    angles: list[Angle] = []
    seen: set[str] = set()
    for draft in reply.angles:
        text = clip(normalize_model_text(draft.text), ANGLE_MAX_CHARS) if draft.text else ""
        if not text:
            continue
        grounded: list[str] = []
        for ref in draft.grounded_in:
            doc_id = grounding.doc_id(ref)
            if doc_id is not None and doc_id not in grounded:
                grounded.append(doc_id)
        if not grounded:
            continue
        if blocking_findings(text, sources=sources, blocklist=blocklist, policy=policy):
            continue
        key = " ".join(words(text))
        if key in seen:
            continue
        seen.add(key)
        angles.append(
            Angle(id=f"a{len(angles) + 1}", text=text, grounded_in=grounded[:MAX_GROUNDING])
        )
        if len(angles) == MAX_ANGLES:
            break
    return angles


async def generate_angles(
    store: Store, runner: ModelRunner, item_id: str, *, now: datetime
) -> Stored[EngagementItem]:
    """Up to three angles for an item, stored on it (``angles`` and a ``servedBy`` record).

    In order: the item must be within its 48 hours (``NotFound``, :class:`ItemExpired`), the
    owner must have confirmed the AI-use policy (``AiUseNotConfirmed``), the blocklist must clear
    the pasted text (``BlocklistNotConfigured``, ``ConfidentialText``) and there must be owner
    material (:class:`NoOwnerMaterial`). Nothing goes to a model before all of that. A refused
    call keeps the angles the item had and records the refusal.
    """
    stamp = to_iso(now)
    current = await read_live_item(store, item_id, now=now)
    item = current.doc
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    require_ai_use(policy)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    check_texts({"pastedText": item.pasted_text}, blocklist)
    grounding = await gather_grounding(store, item, blocklist=blocklist, now=stamp)
    if not grounding.entries:
        raise NoOwnerMaterial()
    prompt = load_prompt(ANGLES)
    result = await _engagement_call(runner, angles_request(item, grounding, prompt))
    reply = result.value if isinstance(result.value, AnglesReply) else None
    answered = not result.refused and reply is not None
    angles = clean_angles(
        reply if answered else None,
        grounding,
        sources=reply_sources(item, grounding),
        blocklist=blocklist,
        policy=policy,
    )
    record = ServedByRecord.from_served_by(
        result.served_by,
        step=ANGLES_STEP,
        at=stamp,
        refused=result.refused or bool(result.refusals),
        prompt_version=prompt.version,
        cost_usd=round(float(result.cost_usd), 6),
    )

    def apply(doc: EngagementItem) -> EngagementItem:
        return doc.model_copy(
            update={
                "angles": angles if answered else doc.angles,
                "served_by": [*doc.served_by, record][-MAX_SERVED_BY:],
                "updated_at": stamp,
                "ttl": remaining_ttl(doc, now),
            }
        )

    stored = await update_doc(store, EngagementItem, item.id, apply)
    _logger.info(
        "engagement_angles",
        extra={
            "item_id": item.id,
            "angle_count": len(angles),
            "material_count": len(grounding.entries),
        },
    )
    return stored


# --------------------------------------------------------------------------------------------
# Screenshots
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Extracted:
    """Text read from a screenshot (returned to the owner, never stored)."""

    text: str = field(repr=False)
    findings: list[Finding]


async def extract_screenshot(
    store: Store, runner: ModelRunner, data: bytes, *, content_type: str, now: datetime
) -> Extracted:
    """Read the post or comment text from the owner's screenshot (ENGAGEMENT with an image).

    The AI-use confirmation is required first. The image is re-encoded before it is sent
    (``storage.ingest_image``: pixels only, EXIF and other metadata dropped) and never stored.
    The text is normalized like pasted text and returned with its blocklist findings. Raises
    ``MediaRejected`` (413/415/422) and :class:`NothingExtracted`.
    """
    stamp = to_iso(now)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    require_ai_use(policy)
    image = await ingest_image(data, max_pixels=SCREENSHOT_MAX_PIXELS, max_bytes=MAX_IMAGE_BYTES)
    media_type = _IMAGE_TYPES.get(image.content_type)
    if media_type is None or image.content_type != content_type:
        raise MediaRejected("unsupported_media_type", "The file does not match its Content-Type.")
    prompt = load_prompt(EXTRACT)
    request = Request(
        system=prompt.text,
        messages=(Message("user", (TextPart(_EXTRACT_ASK), ImagePart(image.data, media_type))),),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=ExtractReply,
    )
    result = await _engagement_call(runner, request)
    reply = result.value if isinstance(result.value, ExtractReply) else None
    text = ""
    if reply is not None and not result.refused:
        text = clip(normalize_third_party(reply.text), MAX_PASTED_CHARS)
    _logger.info("engagement_screenshot_read", extra={"image_bytes": len(image.data)})
    if not text:
        raise NothingExtracted()
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    return Extracted(text=text, findings=blocklist_findings(text, blocklist))


# --------------------------------------------------------------------------------------------
# Finalize and the watchlist
# --------------------------------------------------------------------------------------------


def record_id_for(item_id: str) -> str:
    """The record an item becomes (derived: a retried finalize finds it instead of making a
    second one)."""
    return derived_id(EngagementRecord.ID_PREFIX, "engagement", item_id)


async def _save_record(store: Store, record: EngagementRecord) -> Stored[EngagementRecord]:
    """Create the record; a retry (the item still exists) updates it to the latest text."""
    try:
        return await create_doc(store, record)
    except Conflict:
        existing = await read_doc(store, EngagementRecord, record.id)
    current = existing.doc
    changes = {
        "final_text": record.final_text,
        "url": record.url,
        "watch_entry_id": record.watch_entry_id,
        "substantive": record.substantive,
    }
    if all(getattr(current, name) == value for name, value in changes.items()):
        return existing
    updated = current.model_copy(update={**changes, "updated_at": record.updated_at})
    return await replace_doc(store, updated, if_match=existing.etag)


async def mark_engaged(store: Store, entry_id: str, *, now: str) -> Stored[WatchEntry]:
    """Set a watch entry's ``lastEngagedAt`` (CAS; ``NotFound`` when missing)."""

    def engaged(entry: WatchEntry) -> WatchEntry:
        return entry.model_copy(update={"last_engaged_at": now, "updated_at": now})

    return await update_doc(store, WatchEntry, entry_id, engaged)


async def finalize_item(
    store: Store,
    current: Stored[EngagementItem],
    *,
    final_text: str,
    url: str | None,
    watch_entry_id: str | None,
    now: datetime,
) -> Stored[EngagementRecord]:
    """Keep what the owner posted (:class:`EngagementRecord`) and delete the item at once.

    ``current`` carries the etag the owner saw. The final text and URL pass the blocklist like
    every save of owner text (``BlocklistNotConfigured``, ``ConfidentialText``). The record holds
    no third-party text: kind, final text, URL, post id, time, the scorecard's ``substantive``
    flag and the watch entry. Crash-safe: the record id is derived from the item, so a retry
    finds it; the item is deleted only after the record exists.
    """
    item = current.doc
    if is_expired(item, now):
        await _forget(store, item.id)
        raise ItemExpired()
    transition(ENGAGEMENT_MACHINE, item.status, EngagementStatus.FINALIZED)
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    check_texts({"finalText": final_text, "url": url}, blocklist)
    if watch_entry_id is not None and await _optional(store, WatchEntry, watch_entry_id) is None:
        raise InvalidField("watchEntryId", "No watchlist entry with this id.")
    record = EngagementRecord(
        id=record_id_for(item.id),
        created_at=stamp,
        updated_at=stamp,
        item_kind=item.item_kind,
        final_text=final_text,
        url=url,
        post_id=item.post_id,
        posted_at=stamp,
        substantive=is_substantive(final_text, sources=[item.pasted_text]),
        watch_entry_id=watch_entry_id,
    )
    stored = await _save_record(store, record)
    with contextlib.suppress(NotFound):
        await delete_doc(store, EngagementItem, item.id, if_match=current.etag)
    if watch_entry_id is not None:
        with contextlib.suppress(NotFound):
            await mark_engaged(store, watch_entry_id, now=stamp)
    _logger.info(
        "engagement_item_finalized", extra={"item_id": item.id, "record_id": stored.doc.id}
    )
    return stored
