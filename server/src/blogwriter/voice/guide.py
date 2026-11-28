"""The versioned Voice & Style Guide (container ``stories``, kind ``style_guide``).

Versions are monotonic (``style-guide-0001``, ``-0002``...; a create that loses the race for a
version number takes the next one) and exactly one version is ``active``: activating a version
supersedes every other active one and points ``VoicePreferences.activeGuideId`` at it. Readers
use :func:`load_active_guide`, which picks the most recently activated version, so even a crash
between the two writes of an activation never leaves two guides in use.

:func:`guide_prompt` renders a version as the drafting prefix: byte-identical for the same
version (prompt caching, 1 h TTL), with the body, the rules and the exemplars.

The ``voice.build-guide`` job synthesizes a new ``proposed`` version from the corpus
(:func:`guide_request`, :func:`assemble_guide`). Exemplars are verbatim passages of samples that
nothing flagged and the confidentiality classifier passed (:func:`select_exemplars`); flagged
passages reach the model marked as topics-only and are never quoted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import Field

from blogwriter.ai.types import Message, Request, Sensitivity
from blogwriter.db import Conflict, Filter, OrderBy, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import Finding, ServedByRecord
from blogwriter.domain.confidentiality import blocklist_findings
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    find_docs,
    replace_doc,
    update_doc,
    update_singleton,
)
from blogwriter.domain.settings import Blocklist, VoicePreferences
from blogwriter.domain.states import GUIDE_MACHINE, GuideStatus
from blogwriter.domain.voice import Exemplar, GuideRules, StyleGuide, VoiceSample
from blogwriter.voice._prompts import Prompt
from blogwriter.voice.text import (
    clip,
    dedupe_casefold,
    leading_excerpt,
    neutralize_tags,
    paragraph_spans,
    verbatim_span,
)

__all__ = [
    "BASELINE_BANNED_PHRASES",
    "CORPUS_CHAR_BUDGET",
    "EXCERPT_MAX_CHARS",
    "EXCERPT_MIN_CHARS",
    "FIRST_QUARTER",
    "MAX_CANDIDATES",
    "MAX_EXEMPLARS",
    "MIN_EXEMPLARS",
    "ExemplarPick",
    "GuideDraft",
    "RulesFull",
    "SuggestionKind",
    "VocabularyItem",
    "activate",
    "assemble_guide",
    "built_by_synthesis",
    "create_version",
    "derived_version",
    "exemplar_candidates",
    "flagged_passages",
    "guide_blocklist_findings",
    "guide_prompt",
    "guide_request",
    "guide_texts",
    "list_guides",
    "load_active_guide",
    "load_active_stored",
    "next_version",
    "reject",
    "rules_with",
    "select_exemplars",
]

FIRST_QUARTER: Final = timedelta(days=91)
"""The digest runs weekly for this long after the first activation, then monthly."""
MAX_CANDIDATES: Final = 24
"""Exemplar candidates the classifier checks per build (heaviest first)."""
MAX_EXEMPLARS: Final = 6
MIN_EXEMPLARS: Final = 3
"""When the model's picks do not survive validation, the leading passages of the heaviest
approved samples fill the guide up to this many exemplars."""
EXCERPT_MAX_CHARS: Final = 1200
EXCERPT_MIN_CHARS: Final = 80
CORPUS_CHAR_BUDGET: Final = 160_000
"""Characters of sample text one synthesis request carries (heaviest samples first)."""
MAX_SOURCE_SAMPLES: Final = 200
MAX_BODY_CHARS: Final = 50_000
MAX_RULE_ITEMS: Final = 50
MAX_BANNED: Final = 200
MAX_VOCABULARY: Final = 200

_DATA_TAGS: Final = (
    "corpus",
    "sample",
    "flagged",
    "exemplar_candidates",
    "current_rules",
    "banned_baseline",
    "voice_guide",
    "exemplar",
)

BASELINE_BANNED_PHRASES: Final[tuple[str, ...]] = (
    # Formula openers and clichés (plan: "LinkedIn Studio / Hooks").
    "unpopular opinion",
    "here's the thing",
    "let that sink in",
    "X is dead (as an opener)",
    "most people get X wrong",
    "nobody talks about",
    # AI tells (the linkedin lint's list, as plain phrases).
    "let's dive in",
    "let's explore",
    "let's break this down",
    "in today's rapidly evolving",
    "in the ever-changing landscape",
    "game-changer",
    "game-changing",
    "revolutionary",
    "groundbreaking",
    "paradigm shift",
    "landscape",
    "leverage",
    "utilize",
    "harness",
    "it's worth noting",
    "it's important to note",
    "notably",
    "crucial",
    "vital",
    "essential",
    "robust",
    "seamless",
    "comprehensive",
    "cutting-edge",
    "state-of-the-art",
    "empower",
    "elevate",
    "unlock the power of",
    "supercharge",
    "here's why this matters",
    "i'm excited to share",
    "thrilled to announce",
    "tl;dr",
    # Engagement bait LinkedIn demotes.
    "comment yes",
    "follow for more",
    "agree?",
    "tag someone",
    "drop a comment",
    "save this post",
)
"""Phrases every guide bans, whatever the corpus or the model says (merged into
``rules.bannedPhrases`` of every synthesized version)."""

type SuggestionKind = Literal["prefer", "avoid", "banned_phrase", "vocabulary"]


# --------------------------------------------------------------------------------------------
# Reading and versioning
# --------------------------------------------------------------------------------------------


async def load_active_stored(store: Store) -> Stored[StyleGuide] | None:
    """The guide in use with its etag: the most recently activated ``active`` version.

    Strict: an unreadable guide document fails the read instead of silently falling back to an
    older version.
    """
    found = await find_docs(
        store, StyleGuide, [Filter("status", "==", GuideStatus.ACTIVE.value)], strict=True
    )
    if not found:
        return None
    return max(found, key=lambda stored: (stored.doc.status_changed_at, stored.doc.version))


async def load_active_guide(store: Store) -> StyleGuide | None:
    """The active Voice & Style Guide, or ``None`` before the owner activated one."""
    stored = await load_active_stored(store)
    return stored.doc if stored is not None else None


async def list_guides(store: Store, *, limit: int = 100) -> list[Stored[StyleGuide]]:
    """Guide versions, newest first."""
    return await find_docs(
        store, StyleGuide, order_by=OrderBy("version", descending=True), limit=limit
    )


async def next_version(store: Store) -> int:
    """One more than the highest version stored (1 for the first guide)."""
    top = await find_docs(
        store, StyleGuide, order_by=OrderBy("version", descending=True), limit=1, strict=True
    )
    return top[0].doc.version + 1 if top else 1


async def create_version(
    store: Store, build: Callable[[int], StyleGuide], *, attempts: int = 5
) -> Stored[StyleGuide]:
    """Create ``build(n)`` under the next version number ``n``; a lost race for ``n`` (another
    writer created it first) retries with the next number."""
    for _ in range(attempts):
        version = await next_version(store)
        try:
            return await create_doc(store, build(version))
        except Conflict:
            continue
    raise Conflict(f"stories/{StyleGuide.KIND}: lost {attempts} races for a version number")


async def derived_version(
    store: Store,
    base: StyleGuide,
    *,
    body: str,
    rules: GuideRules,
    now: datetime,
    edited_by_owner: bool,
) -> Stored[StyleGuide]:
    """A new ``proposed`` version from ``base`` with another body and rules (an owner edit, or
    an accepted digest suggestion). Exemplars and sources carry over; no AI is involved."""
    stamp = to_iso(now)

    def build(version: int) -> StyleGuide:
        return StyleGuide(
            id=StyleGuide.id_for(version),
            created_at=stamp,
            updated_at=stamp,
            version=version,
            status=GuideStatus.PROPOSED,
            status_changed_at=stamp,
            body=body,
            rules=rules,
            exemplars=list(base.exemplars),
            source_sample_ids=list(base.source_sample_ids),
            based_on_version=base.version,
            served_by=[],
            edited_by_owner=edited_by_owner,
        )

    return await create_version(store, build)


# --------------------------------------------------------------------------------------------
# Status changes
# --------------------------------------------------------------------------------------------


def guide_texts(guide: StyleGuide) -> dict[str, str]:
    """Every owner-editable or model-written text of a guide, by field (blocklist checks)."""
    fields: dict[str, str] = {"body": guide.body}
    for name in ("prefer", "avoid"):
        items: list[str] = getattr(guide.rules, name)
        for index, item in enumerate(items):
            fields[f"rules.{name}[{index}]"] = item
    for index, phrase in enumerate(guide.rules.banned_phrases):
        fields[f"rules.bannedPhrases[{index}]"] = phrase
    for index, (term, note) in enumerate(guide.rules.vocabulary.items()):
        fields[f"rules.vocabulary[{index}]"] = f"{term}: {note}"
    for index, exemplar in enumerate(guide.exemplars):
        fields[f"exemplars[{index}]"] = exemplar.excerpt
    return fields


def guide_blocklist_findings(guide: StyleGuide, blocklist: Blocklist) -> dict[str, list[Finding]]:
    """Blocklist findings per field of ``guide`` (empty when clean). An unconfigured blocklist
    gives the ``blocklist-not-configured`` finding (fail closed)."""
    hits: dict[str, list[Finding]] = {}
    for name, text in guide_texts(guide).items():
        findings = blocklist_findings(text, blocklist)
        if findings:
            hits[name] = findings
    return hits


async def activate(
    store: Store, current: Stored[StyleGuide], *, if_match: str, now: datetime
) -> Stored[StyleGuide]:
    """Make ``current`` the active version (If-Match ``if_match``).

    ``TransitionError`` unless it is ``proposed`` or ``superseded``. The target is written first
    (the owner's precondition is the commit point), then every other active version is
    superseded and the preferences point at it; the first activation ever also starts the
    digest's weekly quarter (``weeklyUntil`` = now + 91 days).
    """
    guide = current.doc
    target = GUIDE_MACHINE.transition(guide.status, GuideStatus.ACTIVE)
    stamp = to_iso(now)
    changed = guide.model_copy(
        update={
            "status": target,
            "status_changed_at": stamp,
            "updated_at": stamp,
            "approved_at": guide.approved_at or stamp,
        }
    )
    stored = await replace_doc(store, changed, if_match=if_match)
    others = await find_docs(
        store, StyleGuide, [Filter("status", "==", GuideStatus.ACTIVE.value)], strict=True
    )
    for other in others:
        if other.doc.id != guide.id:
            await update_doc(store, StyleGuide, other.doc.id, _superseded(stamp))
    await update_singleton(store, VoicePreferences, _pointing_at(guide.id, now), now=stamp)
    return stored


def _superseded(stamp: str) -> Callable[[StyleGuide], StyleGuide | None]:
    def mutate(guide: StyleGuide) -> StyleGuide | None:
        if guide.status is not GuideStatus.ACTIVE:
            return None
        target = GUIDE_MACHINE.transition(guide.status, GuideStatus.SUPERSEDED)
        return guide.model_copy(
            update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
        )

    return mutate


def _pointing_at(
    guide_id: str, now: datetime
) -> Callable[[VoicePreferences], VoicePreferences | None]:
    def mutate(preferences: VoicePreferences) -> VoicePreferences | None:
        update: dict[str, object] = {}
        if preferences.active_guide_id != guide_id:
            update["active_guide_id"] = guide_id
        if preferences.weekly_until is None:
            update["weekly_until"] = to_iso(now + FIRST_QUARTER)
        if not update:
            return None
        update["updated_at"] = to_iso(now)
        return preferences.model_copy(update=update)

    return mutate


async def reject(
    store: Store, current: Stored[StyleGuide], *, if_match: str, now: datetime
) -> Stored[StyleGuide]:
    """Reject a ``proposed`` version (``TransitionError`` otherwise)."""
    target = GUIDE_MACHINE.transition(current.doc.status, GuideStatus.REJECTED)
    stamp = to_iso(now)
    changed = current.doc.model_copy(
        update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
    )
    return await replace_doc(store, changed, if_match=if_match)


# --------------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------------


class RulesFull(Exception):
    """A rule list of the guide is at its maximum (409 ``guide_rules_full``)."""

    code: Final = "guide_rules_full"

    def __init__(self) -> None:
        super().__init__("the guide has no room for another rule of this kind")


def rules_with(rules: GuideRules, kind: SuggestionKind, text: str) -> GuideRules | None:
    """``rules`` with one more rule (deterministic, no AI); ``None`` when it is already there.

    ``vocabulary`` rules are written ``term: note``. Raises :class:`RulesFull` when the list is
    at its maximum.
    """
    value = " ".join(text.split())
    if not value:
        return None
    match kind:
        case "prefer" | "avoid":
            items: list[str] = list(getattr(rules, kind))
            rule = clip(value, 300)
            if any(item.casefold() == rule.casefold() for item in items):
                return None
            if len(items) >= MAX_RULE_ITEMS:
                raise RulesFull()
            return rules.model_copy(update={kind: [*items, rule]})
        case "banned_phrase":
            phrase = clip(value, 100)
            if any(item.casefold() == phrase.casefold() for item in rules.banned_phrases):
                return None
            if len(rules.banned_phrases) >= MAX_BANNED:
                raise RulesFull()
            return rules.model_copy(update={"banned_phrases": [*rules.banned_phrases, phrase]})
        case "vocabulary":
            term_text, _, note_text = value.partition(":")
            term, note = clip(term_text, 100), clip(note_text, 300)
            if not term:
                return None
            vocabulary = dict(rules.vocabulary)
            existing = next((key for key in vocabulary if key.casefold() == term.casefold()), None)
            if existing is not None:
                if vocabulary[existing] == note:
                    return None
                del vocabulary[existing]
            elif len(vocabulary) >= MAX_VOCABULARY:
                raise RulesFull()
            vocabulary[term] = note
            return rules.model_copy(update={"vocabulary": vocabulary})


def _rule_lines(rules: GuideRules) -> Iterator[str]:
    sections: list[tuple[str, list[str]]] = [
        ("Prefer:", list(rules.prefer)),
        ("Avoid:", list(rules.avoid)),
        ("Never use these phrases:", list(rules.banned_phrases)),
        ("Vocabulary:", _vocabulary_lines(rules.vocabulary)),
    ]
    first = True
    for heading, items in sections:
        if not items:
            continue
        if not first:
            yield ""
        first = False
        yield heading
        for item in items:
            yield f"- {item}"


def _vocabulary_lines(vocabulary: Mapping[str, str]) -> list[str]:
    # Sorted, so the rendering does not depend on the stored key order (prompt caching).
    terms = sorted(vocabulary, key=lambda term: (term.casefold(), term))
    return [f"{term}: {vocabulary[term]}" if vocabulary[term] else term for term in terms]


# --------------------------------------------------------------------------------------------
# The drafting prefix
# --------------------------------------------------------------------------------------------


def guide_prompt(guide: StyleGuide) -> str:
    """The cached drafting prefix of ``guide``: its body, rules and exemplars.

    Deterministic: the same version always renders to the same bytes (no timestamps, sorted
    vocabulary), so the prompt cache hits across drafts. Exemplars are delimited as data.
    """
    lines: list[str] = [
        f'<voice_guide version="{guide.version}">',
        f"# Voice & Style Guide (version {guide.version})",
        "",
        neutralize_tags(guide.body.strip(), _DATA_TAGS),
    ]
    rules = list(_rule_lines(guide.rules))
    if rules:
        lines += ["", "## Rules", "", *(neutralize_tags(line, _DATA_TAGS) for line in rules)]
    if guide.exemplars:
        lines += [
            "",
            "## Exemplars",
            "",
            "Passages the writer wrote. Match how they read: sentence length, openings, word "
            "choice, how numbers and decisions are stated. Never reuse their content or facts.",
        ]
        for exemplar in guide.exemplars:
            lines += [
                "",
                f'<exemplar sample="{exemplar.sample_id}">',
                neutralize_tags(exemplar.excerpt.strip(), _DATA_TAGS),
                "</exemplar>",
            ]
    lines.append("</voice_guide>")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# Synthesis (voice.build-guide)
# --------------------------------------------------------------------------------------------


class VocabularyItem(ApiModel):
    term: str = Field(description="A term the writer uses, or prefers over a common alternative.")
    note: str = Field(description="How the writer uses it, or what they write instead.")


class ExemplarPick(ApiModel):
    sample_id: str = Field(description="The id of a sample listed in <exemplar_candidates>.")
    excerpt: str = Field(description="300-1,200 characters copied verbatim from that sample.")


class GuideDraft(ApiModel):
    """The SYNTHESIS output of ``voice.build-guide`` (plain fields: it compiles for both
    providers; lengths are enforced here, not by the schema)."""

    body: str = Field(min_length=1, description="The guide as Markdown, 600-1,500 words.")
    prefer: list[str] = Field(description="Instructions a drafter follows, one sentence each.")
    avoid: list[str] = Field(description="Things a drafter must not do, one sentence each.")
    banned_phrases: list[str] = Field(description="Further phrases drafts must never contain.")
    vocabulary: list[VocabularyItem]
    exemplars: list[ExemplarPick]


def exemplar_candidates(samples: Sequence[VoiceSample]) -> list[VoiceSample]:
    """Samples that may be quoted (nothing flagged, the detectors ran), heaviest first, at most
    :data:`MAX_CANDIDATES` (the classifier still has to pass each one)."""
    eligible = [sample for sample in samples if sample.exemplar_eligible and not sample.flags]
    eligible.sort(key=_rank)
    return eligible[:MAX_CANDIDATES]


def _rank(sample: VoiceSample) -> tuple[float, bool, int, str]:
    return (-sample.weight, not sample.owner_unassisted, -sample.word_count, sample.id)


def _touches(finding: Finding, start: int, end: int) -> bool:
    """Whether ``finding`` flags the passage ``[start, end)`` (a finding without a span is about
    the whole text)."""
    if finding.start is None or finding.end is None:
        return True
    if finding.start == finding.end:
        return start <= finding.start <= end
    return finding.start < end and finding.end > start


def flagged_passages(sample: VoiceSample) -> list[tuple[int, int, bool]]:
    """The sample's paragraphs as ``(start, end, flagged)``: a paragraph is flagged when any
    AI-tell or slop finding touches it."""
    return [
        (start, end, any(_touches(finding, start, end) for finding in sample.flags))
        for start, end in paragraph_spans(sample.text)
    ]


def _render_sample(sample: VoiceSample) -> str:
    attributes = [
        f'id="{sample.id}"',
        f'source="{sample.source_type}"',
        f'weight="{sample.weight:g}"',
        f'unassisted="{"yes" if sample.owner_unassisted else "no"}"',
    ]
    if sample.pillar:
        attributes.append(f'pillar="{sample.pillar}"')
    if sample.title:
        title = neutralize_tags(sample.title, _DATA_TAGS).replace('"', "'")
        attributes.append(f'title="{title}"')
    blocks: list[str] = []
    for start, end, flagged in flagged_passages(sample):
        text = neutralize_tags(sample.text[start:end], _DATA_TAGS)
        blocks.append(f"<flagged>\n{text}\n</flagged>" if flagged else text)
    return f"<sample {' '.join(attributes)}>\n" + "\n\n".join(blocks) + "\n</sample>"


def _current_rules_block(base: StyleGuide) -> str:
    lines = list(_rule_lines(base.rules))
    if not lines:
        return ""
    body = "\n".join(neutralize_tags(line, _DATA_TAGS) for line in lines)
    return f'<current_rules version="{base.version}">\n{body}\n</current_rules>'


def guide_request(
    samples: Sequence[VoiceSample],
    *,
    candidates: Sequence[str],
    base: StyleGuide | None,
    prompt: Prompt,
    budget: int = CORPUS_CHAR_BUDGET,
) -> tuple[Request, list[str]]:
    """The SYNTHESIS request (``Sensitivity.ANONYMIZED``, tool-free) and the ids of the samples
    it carries (heaviest first, within ``budget`` characters of text)."""
    included: list[VoiceSample] = []
    used = 0
    for sample in sorted(samples, key=_rank):
        if len(included) >= MAX_SOURCE_SAMPLES:
            break
        if used + len(sample.text) > budget:
            continue
        included.append(sample)
        used += len(sample.text)
    if not included:
        raise ValueError("no sample fits the synthesis budget")
    allowed = [sample_id for sample_id in candidates if any(s.id == sample_id for s in included)]
    parts = [
        "<corpus>",
        *(_render_sample(sample) for sample in included),
        "</corpus>",
        "<exemplar_candidates>",
        *(allowed or ["none"]),
        "</exemplar_candidates>",
    ]
    if base is not None:
        block = _current_rules_block(base)
        if block:
            parts.append(block)
    parts += [
        "<banned_baseline>",
        "; ".join(BASELINE_BANNED_PHRASES),
        "</banned_baseline>",
        "Write the Voice & Style Guide from this corpus. Reply with the JSON object only.",
    ]
    request = Request(
        system=prompt.text,
        messages=(Message.user("\n".join(parts)),),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=GuideDraft,
    )
    return request, [sample.id for sample in included]


def select_exemplars(
    picks: Sequence[ExemplarPick],
    samples: Mapping[str, VoiceSample],
    approved: Sequence[str],
    *,
    limit: int = MAX_EXEMPLARS,
    target: int = MIN_EXEMPLARS,
) -> list[Exemplar]:
    """The exemplars a guide may carry.

    A pick survives only when its sample is in ``approved`` (unflagged, classifier passed) and
    its excerpt occurs verbatim in the sample (whitespace and quote style aside), 80-1,200
    characters; the stored excerpt is the sample's own text. When fewer than ``target`` survive,
    the leading passages of the heaviest approved samples fill up.
    """
    allowed = set(approved)
    out: list[Exemplar] = []
    seen: set[tuple[str, int, int]] = set()
    for pick in picks:
        if len(out) >= limit:
            break
        sample = samples.get(pick.sample_id)
        if sample is None or sample.id not in allowed or len(pick.excerpt) > 2 * EXCERPT_MAX_CHARS:
            continue
        span = verbatim_span(sample.text, pick.excerpt)
        if span is None:
            continue
        excerpt = sample.text[span[0] : span[1]]
        if not EXCERPT_MIN_CHARS <= len(excerpt) <= EXCERPT_MAX_CHARS:
            continue
        key = (sample.id, span[0], span[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(Exemplar(sample_id=sample.id, excerpt=excerpt))
    wanted = min(target, len(allowed), limit)
    used = {exemplar.sample_id for exemplar in out}
    for sample_id in approved:
        if len(out) >= wanted:
            break
        sample = samples.get(sample_id)
        if sample is None or sample_id in used:
            continue
        excerpt = leading_excerpt(sample.text, EXCERPT_MAX_CHARS, minimum=EXCERPT_MIN_CHARS)
        if excerpt is None:
            continue
        used.add(sample_id)
        out.append(Exemplar(sample_id=sample_id, excerpt=excerpt))
    return out


def assemble_guide(
    draft: GuideDraft,
    *,
    version: int,
    samples: Mapping[str, VoiceSample],
    approved: Sequence[str],
    included: Sequence[str],
    base: StyleGuide | None,
    served_by: ServedByRecord,
    now: str,
) -> StyleGuide:
    """A ``proposed`` guide version from the model's draft, every limit enforced here.

    The baseline banned phrases and the base version's banned phrases are always kept (a model
    never un-bans a phrase); ``ValueError`` for an empty body.
    """
    body = clip(draft.body, MAX_BODY_CHARS)
    if not body:
        raise ValueError("the synthesized guide has no body")
    banned = dedupe_casefold(
        [
            *BASELINE_BANNED_PHRASES,
            *(base.rules.banned_phrases if base is not None else ()),
            *(clip(phrase, 100) for phrase in draft.banned_phrases),
        ]
    )[:MAX_BANNED]
    vocabulary: dict[str, str] = {}
    folded: set[str] = set()
    for item in draft.vocabulary:
        term = clip(item.term, 100)
        if not term or term.casefold() in folded or len(vocabulary) >= MAX_VOCABULARY:
            continue
        folded.add(term.casefold())
        vocabulary[term] = clip(item.note, 300)
    rules = GuideRules(
        prefer=dedupe_casefold(clip(item, 300) for item in draft.prefer)[:MAX_RULE_ITEMS],
        avoid=dedupe_casefold(clip(item, 300) for item in draft.avoid)[:MAX_RULE_ITEMS],
        banned_phrases=banned,
        vocabulary=vocabulary,
    )
    return StyleGuide(
        id=StyleGuide.id_for(version),
        created_at=now,
        updated_at=now,
        version=version,
        status=GuideStatus.PROPOSED,
        status_changed_at=now,
        body=body,
        rules=rules,
        exemplars=select_exemplars(draft.exemplars, samples, approved),
        source_sample_ids=list(included)[:MAX_SOURCE_SAMPLES],
        based_on_version=base.version if base is not None else None,
        served_by=[served_by],
        edited_by_owner=False,
    )


def built_by_synthesis(guide: StyleGuide) -> bool:
    """Whether a stored version came from a ``voice.build-guide`` run (not an owner edit)."""
    return not guide.edited_by_owner and any(
        record.step == "synthesize" for record in guide.served_by
    )
