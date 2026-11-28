"""Week plans: the deterministic rules (pure; no I/O, no clock).

A plan holds the week's flagship and its post slots. Whatever proposes the content (the PLAN
model, or the deterministic default when AI is paused, not configured or not allowed), these
rules decide what the plan may contain:

**Modes** (``posts_for_mode``): normal = 3 posts plus the flagship, light = 2 posts and the
flagship optional, away = nothing (no flagship, no slots, nothing generated on approval).

**Lanes and mix** (``LaneSettings``): at most ``derivatives_max_per_week`` derivatives, and only
when a flagship article publishes on Monday of the week (derivatives re-cut it, on its pillar);
at least ``native_min_per_week`` native posts from the story and POV bank, on a pillar other than
the flagship's (``native_other_pillar``); quick takes only for an idea in the inbox; at least
``on_pillar_min_share`` of all posts on a pillar; pillars balanced against their target shares
over the recent weeks and spread across the week (never the same pillar twice in a row when
another fits).

**Slots and spacing**: the week's template slots (``domain.slots`` and the DST rule of
``domain.weeks``) on free, future days only, so a plan never stacks a post on a day another post
holds; two posts on one local day block approval (``slot-same-day``), less than
``min_gap_hours_warn`` apart warns (``slot-too-close``).

The findings (:func:`plan_findings`) go to ``WeekPlan.warnings``; ``block`` findings stop the
approval (409 ``slot_conflict`` for spacing, ``mix_rules`` for the mix).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import pairwise
from typing import Final, Literal

from pydantic import Field

from blogwriter.domain.articles import Article
from blogwriter.domain.base import DocId, DomainModel, parse_iso
from blogwriter.domain.common import Finding, Severity, cap_findings
from blogwriter.domain.planner import (
    MAX_SLOT_REFS,
    FlagshipPlan,
    PlannedSlot,
    WeekPlan,
    native_avoid_pillar,
)
from blogwriter.domain.posts import Lane, PostFormat, Slot
from blogwriter.domain.settings import (
    CadenceSettings,
    FormatPacks,
    LaneSettings,
    PillarSettings,
    WeekMode,
)
from blogwriter.domain.signals import Idea
from blogwriter.domain.slots import SAME_DAY_CODE, posts_for_mode, spacing_findings
from blogwriter.domain.states import IdeaStatus, PlanStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.linkedin.lint import AI_TELLS
from blogwriter.planner.slots import TakenSlot, free_week_slots

__all__ = [
    "FLAGSHIP_MISSING",
    "MAX_RATIONALE",
    "MAX_TITLE",
    "MIX_BLOCKING",
    "MIX_DERIVATIVES",
    "MIX_NATIVE",
    "MIX_NATIVE_PILLAR",
    "MIX_OFF_PILLAR",
    "MODE_AWAY",
    "MODE_POSTS",
    "MODE_POSTS_MAX",
    "PLAN_POST_FORMATS",
    "SLOTS_SHORT",
    "SLOT_INCOMPLETE",
    "SLOT_PAST",
    "SPACING_BLOCKING",
    "FlagshipPick",
    "Material",
    "PlanRules",
    "PostPick",
    "Proposal",
    "SlotRefs",
    "TitleCheck",
    "assign_slots",
    "clean_title",
    "default_proposal",
    "exclude_pillar",
    "normalize",
    "pillar_balance",
    "plan_findings",
    "slot_refs",
    "slot_suffix",
    "title_is_plain",
    "with_refs",
]

PLAN_POST_FORMATS: Final[tuple[PostFormat, ...]] = ("carousel", "text", "image_card")
"""Formats the planner proposes (polls are rare and always the owner's choice)."""
MAX_TITLE: Final = 150
"""Working titles are cut to this (the flagship's becomes ``ArticleMeta.title``, max 150)."""
MAX_RATIONALE: Final = 1200

MIX_DERIVATIVES: Final = "mix-derivatives"
MIX_NATIVE: Final = "mix-native"
MIX_NATIVE_PILLAR: Final = "mix-native-pillar"
MIX_OFF_PILLAR: Final = "mix-off-pillar"
MODE_POSTS: Final = "mode-posts"
MODE_POSTS_MAX: Final = "mode-posts-max"
MODE_AWAY: Final = "mode-away"
FLAGSHIP_MISSING: Final = "flagship-missing"
SLOT_PAST: Final = "slot-past"
SLOTS_SHORT: Final = "slots-short"
SLOT_INCOMPLETE: Final = "slot-incomplete"
MIX_BLOCKING: Final = frozenset({MIX_DERIVATIVES, MIX_OFF_PILLAR, MODE_POSTS_MAX, SLOT_INCOMPLETE})
"""Mix findings that block approval (409 ``mix_rules``)."""
SPACING_BLOCKING: Final = frozenset({SAME_DAY_CODE})
"""Spacing findings that block approval (409 ``slot_conflict``) when their severity is block."""


# --------------------------------------------------------------------------------------------
# Settings bundle and material
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanRules:
    """The settings one plan computation reads (cadence in the plan's zone)."""

    cadence: CadenceSettings
    lanes: LaneSettings
    pillars: PillarSettings
    packs: FormatPacks

    def in_zone(self, zone: str) -> PlanRules:
        """The same rules with the cadence's zone set to ``zone`` (a plan keeps its zone)."""
        if zone == self.cadence.timezone:
            return self
        return replace(self, cadence=self.cadence.model_copy(update={"timezone": zone}))

    @property
    def pillar_ids(self) -> tuple[str, ...]:
        return tuple(pillar.id for pillar in self.pillars.pillars)

    def post_formats(self) -> tuple[PostFormat, ...]:
        if self.lanes.formats.image_card_enabled:
            return PLAN_POST_FORMATS
        return tuple(fmt for fmt in PLAN_POST_FORMATS if fmt != "image_card")


@dataclass(frozen=True, slots=True)
class Material:
    """What a plan can draw on, as documents (the plan job loads them from collected ids)."""

    ideas: Mapping[str, Idea] = field(default_factory=dict[str, Idea])
    stories: Mapping[str, StoryEntry] = field(default_factory=dict[str, StoryEntry])
    povs: Mapping[str, PovEntry] = field(default_factory=dict[str, PovEntry])
    source: Article | None = None
    """The flagship article that publishes on Monday of the week (derivatives re-cut it)."""
    source_pillar: str | None = None
    recent_story_ids: frozenset[str] = frozenset()
    recent_pov_ids: frozenset[str] = frozenset()
    recent_post_pillars: Mapping[str, int] = field(default_factory=dict[str, int])
    """Posts per pillar over the recent weeks (pillar balance)."""
    recent_flagships: tuple[tuple[str, str], ...] = ()
    """``(pillar, format)`` of the recent flagships, newest first."""


class SlotRefs(DomainModel):
    """What a planned slot draws on: stories, POVs, an idea, the derivative source (the
    ``PlannedSlot`` fields ``storyIds``, ``povIds``, ``ideaId`` and ``articleId``)."""

    story_ids: list[DocId] = Field(default_factory=list[str], max_length=MAX_SLOT_REFS)
    pov_ids: list[DocId] = Field(default_factory=list[str], max_length=MAX_SLOT_REFS)
    idea_id: DocId | None = None
    article_id: DocId | None = None


def slot_refs(slot: PlannedSlot) -> SlotRefs:
    """The slot's references."""
    return SlotRefs(
        story_ids=list(slot.story_ids),
        pov_ids=list(slot.pov_ids),
        idea_id=slot.idea_id,
        article_id=slot.article_id,
    )


def with_refs(slot: PlannedSlot, refs: SlotRefs) -> PlannedSlot:
    """A copy of ``slot`` carrying ``refs`` (validated)."""
    body = slot.model_dump(mode="json", by_alias=True)
    body.update(refs.model_dump(mode="json", by_alias=True))
    return PlannedSlot.model_validate(body)


# --------------------------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostPick:
    """One proposed post (before it gets a time)."""

    lane: Lane
    pillar: str | None
    format: PostFormat
    title: str
    story_ids: tuple[str, ...] = ()
    pov_ids: tuple[str, ...] = ()
    idea_id: str | None = None
    article_id: str | None = None

    @property
    def refs(self) -> SlotRefs:
        return SlotRefs(
            story_ids=list(self.story_ids),
            pov_ids=list(self.pov_ids),
            idea_id=self.idea_id,
            article_id=self.article_id,
        )


@dataclass(frozen=True, slots=True)
class FlagshipPick:
    """The proposed flagship (raw until :func:`normalize` checks it)."""

    title: str
    pillar: str | None
    format: str | None
    idea_id: str | None = None
    optional: bool = False

    def to_plan(self) -> FlagshipPlan:
        """The stored form (only for a normalized pick: pillar and format are known ids)."""
        return FlagshipPlan(
            title=self.title,
            pillar=self.pillar or "",
            format=self.format or "",
            idea_id=self.idea_id,
            optional=self.optional,
        )


@dataclass(frozen=True, slots=True)
class Proposal:
    """A week's content before it gets times: the flagship and the posts."""

    flagship: FlagshipPick | None
    posts: tuple[PostPick, ...]
    rationale: str | None = None
    source: Literal["ai", "deterministic"] = "deterministic"


# --------------------------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------------------------

_WS: Final = re.compile(r"\s+")
_OPENERS: Final = tuple(
    re.compile(rf"(?i)^[\s\W]{{0,4}}{pattern}")
    for pattern in (
        r"unpopular\s+opinion\b",
        r"here(?:'s|\s+is)\s+the\s+thing\b",
        r"let\s+that\s+sink\s+in\b",
        r"nobody\s+(?:is\s+)?talk(?:s|ing)\s+about\b",
        r"most\s+people\s+get\s+.{1,60}?\s+wrong\b",
        r"[\w .#+-]{1,40}?\s+is\s+dead\b",
    )
)
_TELLS: Final = tuple(
    re.compile(rf"(?i)(?<![^\W_]){pattern}(?![^\W_])")
    for pattern in (*AI_TELLS, r"deep\s+dive", r"unlock(?:s|ed|ing)?")
)
_LINKISH: Final = re.compile(r"(?i)(?:https?://|www\.|#\w|\?)")
_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})

type TitleCheck = Callable[[str], bool]


def clean_title(text: str | None, *, limit: int = MAX_TITLE) -> str:
    """One line of plain text, at most ``limit`` characters (cut at a word boundary)."""
    if not text:
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in text)
    line = _WS.sub(" ", printable).strip()
    if len(line) <= limit:
        return line
    cut = line[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def title_is_plain(title: str) -> bool:
    """A model-written working title without formula openers, AI tells, hype, links,
    hashtags or questions (the plan's slop rules)."""
    folded = title.translate(_APOSTROPHES)
    if not folded.strip():
        return False
    if any(pattern.match(folded) for pattern in _OPENERS):
        return False
    if any(pattern.search(folded) for pattern in _TELLS):
        return False
    return not _LINKISH.search(folded)


# --------------------------------------------------------------------------------------------
# The deterministic default
# --------------------------------------------------------------------------------------------


def _pillar_name(rules: PlanRules, pillar_id: str | None) -> str:
    pillar = rules.pillars.get(pillar_id) if pillar_id is not None else None
    return pillar.name if pillar is not None else "Open pillar"


def _deficit_order(
    rules: PlanRules, recent: Mapping[str, int], planned: Iterable[str | None] = ()
) -> list[str]:
    """Pillar ids, the furthest below its target share first (recent posts plus ``planned``)."""
    pillars = rules.pillars.pillars
    counts: Counter[str] = Counter({key: value for key, value in recent.items() if value > 0})
    counts.update(pillar for pillar in planned if pillar is not None)
    total = sum(counts[pillar.id] for pillar in pillars)
    target_total = sum(pillar.target_share for pillar in pillars) or 1.0

    def deficit(index: int) -> float:
        pillar = pillars[index]
        actual = counts[pillar.id] / total if total else 0.0
        return pillar.target_share / target_total - actual

    order = sorted(range(len(pillars)), key=lambda index: (-deficit(index), index))
    return [pillars[index].id for index in order]


def exclude_pillar(
    lanes: Sequence[tuple[Lane | None, str | None]], flagship: str | None
) -> str | None:
    """The pillar native posts avoid (``domain.planner.native_avoid_pillar``: the derivatives'
    (Monday's flagship), else this week's flagship's)."""
    return native_avoid_pillar(lanes, flagship)


def _pov_title(pov: PovEntry) -> str:
    return clean_title(f"Your take: {pov.statement}", limit=90)


def _bank_pick(
    material: Material, pillar: str, used: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """A story or POV of ``pillar`` for a native post: one no recent post used first, never one
    this plan already uses. Returns ``(story_ids, pov_ids, title)``."""
    stories = [s for s in material.stories.values() if pillar in s.pillars and s.id not in used]
    povs = [p for p in material.povs.values() if p.pillar == pillar and p.id not in used]
    fresh_story = next((s for s in stories if s.id not in material.recent_story_ids), None)
    if fresh_story is not None:
        return (fresh_story.id,), (), clean_title(fresh_story.title)
    fresh_pov = next((p for p in povs if p.id not in material.recent_pov_ids), None)
    if fresh_pov is not None:
        return (), (fresh_pov.id,), _pov_title(fresh_pov)
    if stories:
        return (stories[0].id,), (), clean_title(stories[0].title)
    if povs:
        return (), (povs[0].id,), _pov_title(povs[0])
    return (), (), None


def _title_for_refs(material: Material, story_ids: Sequence[str], pov_ids: Sequence[str]) -> str:
    if story_ids and story_ids[0] in material.stories:
        return clean_title(material.stories[story_ids[0]].title)
    if pov_ids and pov_ids[0] in material.povs:
        return _pov_title(material.povs[pov_ids[0]])
    return ""


def _has_material(material: Material, pillar: str) -> bool:
    return any(pillar in s.pillars for s in material.stories.values()) or any(
        p.pillar == pillar for p in material.povs.values()
    )


def _material_elsewhere(material: Material, rules: PlanRules, avoid: str) -> bool:
    """Whether a native post can be drafted from the bank on a pillar other than ``avoid``."""
    return any(_has_material(material, p) for p in rules.pillar_ids if p != avoid)


def _native_default_title(rules: PlanRules, pillar: str) -> str:
    return f"{_pillar_name(rules, pillar)}: a story or take from your bank"


def _native_pick(
    material: Material,
    rules: PlanRules,
    *,
    planned: Sequence[str | None],
    avoid: str | None,
    used: set[str],
) -> PostPick:
    """A native post on the pillar furthest below its share with a story or POV of that
    pillar: a pillar the bank can draft from first, not ``avoid`` when another has material.

    Only when no other pillar has material does the native stay on ``avoid`` (a
    ``mix-native-pillar`` warning): a native post on a pillar without any story or POV is one
    that generation can only refuse (``no_owner_material``) once the plan is approved."""
    order = _deficit_order(rules, material.recent_post_pillars, planned)
    candidates = [p for p in order if p != avoid] or order
    with_material = [p for p in candidates if _has_material(material, p)]
    if not with_material and avoid is not None and _has_material(material, avoid):
        with_material = [avoid]
    pillar = with_material[0] if with_material else candidates[0]
    story_ids, pov_ids, title = _bank_pick(material, pillar, used)
    used.update(story_ids)
    used.update(pov_ids)
    return PostPick(
        lane="native",
        pillar=pillar,
        format="text",
        title=title or _native_default_title(rules, pillar),
        story_ids=story_ids,
        pov_ids=pov_ids,
    )


def _ranked_ideas(material: Material) -> list[Idea]:
    def key(idea: Idea) -> tuple[int, float, str]:
        accepted = 0 if idea.status is IdeaStatus.ACCEPTED else 1
        return accepted, -(idea.relevance or 0.0), idea.id

    return sorted(material.ideas.values(), key=key)


def _default_format(rules: PlanRules, material: Material) -> str:
    """The article format used least recently (never used first, then pack order)."""
    used = [fmt for _pillar, fmt in material.recent_flagships]
    packs = [pack.id for pack in rules.packs.packs]

    def recency(pack_id: str) -> tuple[int, int]:
        last = used.index(pack_id) if pack_id in used else len(used) + 1
        return last, -packs.index(pack_id)

    return max(packs, key=recency)


def _default_flagship(
    material: Material, rules: PlanRules, *, mode: WeekMode
) -> FlagshipPick | None:
    if mode == "away":
        return None
    packs = {pack.id for pack in rules.packs.packs}
    order = _deficit_order(rules, material.recent_post_pillars)
    for idea in _ranked_ideas(material):
        title = clean_title(idea.title)
        if idea.suggested_lane != "flagship" or not title:
            continue
        pillar = idea.pillar if idea.pillar in rules.pillar_ids else order[0]
        fmt = idea.suggested_format if idea.suggested_format in packs else None
        return FlagshipPick(
            title=title,
            pillar=pillar,
            format=fmt or _default_format(rules, material),
            idea_id=idea.id,
            optional=mode == "light",
        )
    last = material.recent_flagships[0][0] if material.recent_flagships else None
    pillar = next((p for p in order if p != last), order[0])
    return FlagshipPick(
        title=f"{_pillar_name(rules, pillar)}: choose this week's flagship topic",
        pillar=pillar,
        format=_default_format(rules, material),
        optional=mode == "light",
    )


def default_proposal(
    material: Material, rules: PlanRules, *, mode: WeekMode, posts: int
) -> Proposal:
    """The plan without a model: Monday's flagship re-cut (up to the derivative cap, leaving
    room for the native minimum), one quick take when a time-sensitive idea waits, and native
    posts from the story and POV bank for the rest."""
    if mode == "away":
        return Proposal(flagship=None, posts=())
    flagship = _default_flagship(material, rules, mode=mode)
    lanes = rules.lanes
    native_min = min(lanes.native_min_per_week, posts)
    picks: list[PostPick] = []
    source = material.source
    if source is not None:
        derivatives = min(lanes.derivatives_max_per_week, max(0, posts - native_min))
        source_title = clean_title(source.meta.title or "Monday's article", limit=100)
        formats: tuple[PostFormat, ...] = ("carousel", "text")
        picks.extend(
            PostPick(
                lane="derivative",
                pillar=material.source_pillar,
                format=formats[index % len(formats)],
                title=f"From the flagship: {source_title}",
                article_id=source.id,
            )
            for index in range(derivatives)
        )
    if posts - len(picks) - native_min >= 1:
        quick = next((idea for idea in _ranked_ideas(material) if idea.time_sensitive), None)
        if quick is not None:
            pillar = quick.pillar if quick.pillar in rules.pillar_ids else None
            picks.append(
                PostPick(
                    lane="quick_take",
                    pillar=pillar,
                    format="text",
                    title=clean_title(f"Quick take: {quick.title}"),
                    idea_id=quick.id,
                )
            )
    flagship_pillar = flagship.pillar if flagship is not None else None
    avoid = exclude_pillar([(p.lane, p.pillar) for p in picks], flagship_pillar)
    if not lanes.native_other_pillar:
        avoid = None
    used: set[str] = set()
    while len(picks) < posts:
        planned = [pick.pillar for pick in picks]
        picks.append(_native_pick(material, rules, planned=planned, avoid=avoid, used=used))
    return Proposal(flagship=flagship, posts=tuple(picks))


# --------------------------------------------------------------------------------------------
# Normalization (the rules applied to any proposal)
# --------------------------------------------------------------------------------------------


def _normal_flagship(
    flagship: FlagshipPick | None,
    material: Material,
    rules: PlanRules,
    *,
    mode: WeekMode,
    check: TitleCheck,
) -> FlagshipPick | None:
    if mode == "away":
        return None
    fallback = _default_flagship(material, rules, mode=mode)
    if flagship is None:
        return None if mode == "light" else fallback
    if fallback is None:  # pragma: no cover - only away weeks have no default flagship
        return None
    packs = {pack.id for pack in rules.packs.packs}
    title = clean_title(flagship.title)
    pillar = flagship.pillar if flagship.pillar in rules.pillar_ids else fallback.pillar
    fmt = flagship.format if flagship.format in packs else _default_format(rules, material)
    return FlagshipPick(
        title=title if title and check(title) else fallback.title,
        pillar=pillar,
        format=fmt,
        idea_id=flagship.idea_id if flagship.idea_id in material.ideas else None,
        optional=mode == "light",
    )


def _clean_pick(
    pick: PostPick, material: Material, rules: PlanRules, *, check: TitleCheck
) -> PostPick | None:
    """The pick with only known references and allowed values, or ``None`` when its lane has
    no material (a derivative without a source, a quick take without a known idea)."""
    fmt: PostFormat = pick.format if pick.format in rules.post_formats() else "text"
    pillar = pick.pillar if pick.pillar in rules.pillar_ids else None
    title = clean_title(pick.title)
    if title and not check(title):
        title = ""
    if pick.lane == "derivative":
        source = material.source
        if source is None:
            return None
        source_title = clean_title(source.meta.title or "Monday's article", limit=100)
        return PostPick(
            lane="derivative",
            pillar=material.source_pillar,
            format=fmt,
            title=title or f"From the flagship: {source_title}",
            article_id=source.id,
        )
    if pick.lane == "quick_take":
        idea = material.ideas.get(pick.idea_id) if pick.idea_id is not None else None
        if idea is None:
            return None
        if pillar is None and idea.pillar in rules.pillar_ids:
            pillar = idea.pillar
        return PostPick(
            lane="quick_take",
            pillar=pillar,
            format="text",
            title=title or clean_title(f"Quick take: {idea.title}"),
            idea_id=idea.id,
        )
    if pick.lane != "native":
        return None
    stories = tuple(s for s in pick.story_ids if s in material.stories)[:1]
    povs = tuple(p for p in pick.pov_ids if p in material.povs)[:1]
    if pillar is None and stories:
        pillar = next(
            (p for p in material.stories[stories[0]].pillars if p in rules.pillar_ids), None
        )
    if pillar is None and povs and material.povs[povs[0]].pillar in rules.pillar_ids:
        pillar = material.povs[povs[0]].pillar
    return PostPick(
        lane="native", pillar=pillar, format=fmt, title=title, story_ids=stories, pov_ids=povs
    )


def normalize(
    proposal: Proposal,
    material: Material,
    rules: PlanRules,
    *,
    mode: WeekMode,
    posts: int,
    check: TitleCheck = title_is_plain,
) -> Proposal:
    """``proposal`` made to follow the rules: the mode's post count, the derivative cap (and
    only with a source), at least the native minimum on another pillar than the flagship's,
    only known pillars, formats, stories, POVs and ideas, and plain working titles (``check``)."""
    if mode == "away":
        return Proposal(flagship=None, posts=(), rationale=None, source=proposal.source)
    lanes = rules.lanes
    flagship = _normal_flagship(proposal.flagship, material, rules, mode=mode, check=check)
    kept: list[PostPick] = []
    derivatives = 0
    for raw in proposal.posts:
        if len(kept) >= posts:
            break
        pick = _clean_pick(raw, material, rules, check=check)
        if pick is None:
            continue
        if pick.lane == "derivative":
            if derivatives >= lanes.derivatives_max_per_week:
                continue
            derivatives += 1
        kept.append(pick)
    native_min = min(lanes.native_min_per_week, posts)
    missing = native_min - sum(p.lane == "native" for p in kept) - (posts - len(kept))
    while missing > 0:
        lanes_left = [p.lane for p in kept]
        victim = next(
            (i for i in reversed(range(len(kept))) if lanes_left[i] == "quick_take"),
            next((i for i in reversed(range(len(kept))) if lanes_left[i] == "derivative"), None),
        )
        if victim is None:
            break
        kept[victim] = PostPick(lane="native", pillar=None, format="text", title="")
        missing -= 1
    flagship_pillar = flagship.pillar if flagship is not None else None
    avoid = exclude_pillar([(p.lane, p.pillar) for p in kept], flagship_pillar)
    if not lanes.native_other_pillar:
        avoid = None
    used: set[str] = {ref for p in kept for ref in (*p.story_ids, *p.pov_ids)}
    out: list[PostPick] = []
    for index, pick in enumerate(kept):
        if pick.lane != "native":
            out.append(pick)
            continue
        planned = [p.pillar for p in out] + [p.pillar for p in kept[index + 1 :]]
        moves = (
            avoid is not None
            and pick.pillar == avoid
            and _material_elsewhere(material, rules, avoid)
        )
        if pick.pillar is None or moves:
            fresh = _native_pick(material, rules, planned=planned, avoid=avoid, used=used)
            out.append(replace(fresh, format=pick.format))
            continue
        if not pick.title:
            if pick.story_ids or pick.pov_ids:
                title = _title_for_refs(material, pick.story_ids, pick.pov_ids)
                pick = replace(pick, title=title or _native_default_title(rules, pick.pillar))
            else:
                story_ids, pov_ids, title = _bank_pick(material, pick.pillar, used)
                used.update(story_ids)
                used.update(pov_ids)
                pick = replace(
                    pick,
                    story_ids=story_ids,
                    pov_ids=pov_ids,
                    title=title or _native_default_title(rules, pick.pillar),
                )
        out.append(pick)
    while len(out) < posts:
        planned = [p.pillar for p in out]
        out.append(_native_pick(material, rules, planned=planned, avoid=avoid, used=used))
    rationale = clean_title(proposal.rationale, limit=MAX_RATIONALE) if proposal.rationale else ""
    if rationale and not check(rationale.replace("?", ".")):
        rationale = ""
    return Proposal(
        flagship=flagship, posts=tuple(out), rationale=rationale or None, source=proposal.source
    )


# --------------------------------------------------------------------------------------------
# Slots
# --------------------------------------------------------------------------------------------


def slot_suffix(seed: str) -> str:
    """6 hex characters for slot ids, unique per generation (``s1-<suffix>``), so the posts of
    an earlier approval never attach to a regenerated slot."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]


def _alternate(picks: Sequence[PostPick]) -> list[PostPick]:
    """Order picks so the same pillar never takes two slots in a row when another fits: at each
    step the pillar with the most picks left (not the previous one) goes next."""
    remaining = list(picks)
    ordered: list[PostPick] = []
    while remaining:
        previous = ordered[-1].pillar if ordered else None
        left = Counter(pick.pillar for pick in remaining)
        choices = [i for i, pick in enumerate(remaining) if pick.pillar != previous] or list(
            range(len(remaining))
        )
        best = min(choices, key=lambda i: (-left[remaining[i].pillar], i))
        ordered.append(remaining.pop(best))
    return ordered


def _keep_first(picks: Sequence[PostPick], *, native_min: int) -> list[PostPick]:
    """Picks in keep order for a week with fewer free slots: the native minimum first."""
    natives = [p for p in picks if p.lane == "native"][:native_min]
    rest = [p for p in picks if all(p is not n for n in natives)]
    return [*natives, *rest]


def assign_slots(
    proposal: Proposal,
    rules: PlanRules,
    *,
    week_start: str,
    taken_days: Iterable[str] = (),
    after: datetime | None = None,
    suffix: str,
) -> tuple[list[PlannedSlot], int]:
    """The planned slots: picks on the week's free template slots, pillars alternated, ids
    ``s<n>-<suffix>``. Returns the slots and how many picks found no free slot."""
    slots = free_week_slots(
        rules.cadence, week_start, len(proposal.posts), taken_days=set(taken_days), after=after
    )
    native_min = min(rules.lanes.native_min_per_week, len(slots))
    chosen = _alternate(_keep_first(proposal.posts, native_min=native_min)[: len(slots)])
    planned: list[PlannedSlot] = []
    for index, (slot, pick) in enumerate(zip(slots, chosen, strict=True)):
        base = PlannedSlot(
            id=f"s{index + 1}-{suffix}",
            slot=slot,
            lane=pick.lane,
            pillar=pick.pillar,
            format=pick.format,
            title=pick.title or None,
        )
        planned.append(with_refs(base, pick.refs))
    return planned, len(proposal.posts) - len(planned)


def pillar_balance(slots: Sequence[PlannedSlot], rules: PlanRules) -> dict[str, int]:
    """Posts per pillar over the whole week (every pillar listed; ``other`` for off-pillar)."""
    counts = dict.fromkeys(rules.pillar_ids, 0)
    other = 0
    for slot in slots:
        if slot.pillar is not None and slot.pillar in counts:
            counts[slot.pillar] += 1
        else:
            other += 1
    if other:
        counts["other"] = other
    return counts


# --------------------------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------------------------


def _finding(code: str, severity: Severity, message: str, ref: str | None = None) -> Finding:
    return Finding(code=code, severity=severity, message=message, ref=ref)


def _spacing(
    slots: Sequence[PlannedSlot], taken: Sequence[TakenSlot], cadence: CadenceSettings
) -> list[Finding]:
    """Spacing findings for every neighbouring pair (in time) that involves a plan slot."""
    marked: list[tuple[Slot, bool]] = [(s.slot, True) for s in slots]
    marked.extend((t.slot, False) for t in taken)
    marked.sort(key=lambda item: item[0].utc)
    findings: list[Finding] = []
    for (first, mine_a), (second, mine_b) in pairwise(marked):
        if mine_a or mine_b:
            findings.extend(spacing_findings([first, second], cadence=cadence))
    return findings


def plan_findings(
    plan: WeekPlan,
    rules: PlanRules,
    *,
    taken: Sequence[TakenSlot] = (),
    now: datetime | None = None,
    short: int = 0,
) -> list[Finding]:
    """Spacing (between the plan's slots and against ``taken`` posts outside the plan), the
    lane and mix rules, the mode, and the past slots of a plan that is not approved yet."""
    rules = rules.in_zone(plan.timezone)
    cadence = rules.cadence
    lanes = rules.lanes
    slots = plan.slots
    findings = _spacing(slots, taken, cadence)
    count = len(slots)
    natives = [s for s in slots if s.lane == "native"]
    if sum(s.lane == "derivative" for s in slots) > lanes.derivatives_max_per_week:
        findings.append(
            _finding(
                MIX_DERIVATIVES,
                "block",
                f"At most {lanes.derivatives_max_per_week} derivative posts a week.",
            )
        )
    if count and len(natives) < min(lanes.native_min_per_week, count):
        findings.append(
            _finding(
                MIX_NATIVE,
                "warn",
                f"Plan at least {lanes.native_min_per_week} native post from your stories "
                "or takes.",
            )
        )
    flagship_pillar = plan.flagship.pillar if plan.flagship is not None else None
    avoid = exclude_pillar([(s.lane, s.pillar) for s in slots], flagship_pillar)
    if lanes.native_other_pillar and avoid is not None:
        findings.extend(
            _finding(
                MIX_NATIVE_PILLAR,
                "warn",
                "Native posts belong on another pillar than the flagship's.",
                slot.id,
            )
            for slot in natives
            if slot.pillar == avoid
        )
    if count:
        on_pillar = sum(s.pillar in rules.pillar_ids for s in slots)
        if on_pillar / count < lanes.on_pillar_min_share - 1e-9:
            share = round(lanes.on_pillar_min_share * 100)
            findings.append(
                _finding(
                    MIX_OFF_PILLAR, "block", f"At least {share}% of the posts must be on a pillar."
                )
            )
    findings.extend(
        _finding(SLOT_INCOMPLETE, "block", "Pick a pillar for every native post.", slot.id)
        for slot in natives
        if slot.pillar not in rules.pillar_ids
    )
    expected = posts_for_mode(cadence, plan.mode)
    if plan.mode == "away":
        if slots or plan.flagship is not None:
            findings.append(
                _finding(MODE_AWAY, "warn", "Away weeks generate nothing: these stay empty.")
            )
    elif count > cadence.posts_per_week.max:
        findings.append(
            _finding(
                MODE_POSTS_MAX, "block", f"At most {cadence.posts_per_week.max} posts in a week."
            )
        )
    elif count != expected:
        findings.append(
            _finding(
                MODE_POSTS,
                "warn",
                f"A {plan.mode} week has {expected} posts; this plan has {count}.",
            )
        )
    if plan.mode == "normal" and plan.flagship is None:
        findings.append(_finding(FLAGSHIP_MISSING, "warn", "A normal week has a flagship article."))
    if short:
        findings.append(
            _finding(SLOTS_SHORT, "warn", f"{short} planned post(s) found no free slot this week.")
        )
    if now is not None and plan.status is not PlanStatus.APPROVED:
        findings.extend(
            _finding(SLOT_PAST, "warn", "This slot's time has passed.", slot.id)
            for slot in slots
            if parse_iso(slot.slot.utc) <= now
        )
    kept, _dropped = cap_findings(findings)
    return kept
