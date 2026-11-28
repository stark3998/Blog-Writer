"""What the plan job reads and the PLAN call.

``collect`` gathers **references and counts only** (:class:`PlanContext`, the step's output):
idea, story and POV ids, Monday's flagship article (the derivative source), recent pillar and
format use. ``propose`` loads the documents behind those ids (:class:`~.plan.Material`), drops
anything the blocklist matches, and asks PLAN (``gpt-6.2-sol``; ``anonymized``, tool-free,
structured output) for the flagship and the post mix. The answer is a proposal, never content:
the rules in :mod:`.plan` normalize it and nothing is generated before the owner approves.

No model is called (the plan is then built by the deterministic default, and its rationale says
why) when: autonomy is paused (background runs), the model is not configured, the owner has not
confirmed the employer AI-use policy (stories and POVs are anonymized owner material), the
blocklist was never saved, the settings text the prompt carries matches the blocklist, or the
model refuses, answers with something unusable or cannot be reached after the router's own
retries and fallback. Background calls run inside ``defer_on_brake``: a braked plan waits
instead of failing.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import Field, JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.limits import ModelCoolingDown
from blogwriter.ai.providers.base import ProviderError
from blogwriter.ai.schema import StructuredOutputError
from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import Article
from blogwriter.domain.base import Document, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.confidentiality import ConfidentialText, blocklist_hit, check_texts
from blogwriter.domain.docstore import find_docs, read_doc, read_singleton
from blogwriter.domain.jobflow import defer_on_brake
from blogwriter.domain.planner import WeekPlan
from blogwriter.domain.ports import ModelRunner
from blogwriter.domain.posts import Post, Slot
from blogwriter.domain.settings import (
    Blocklist,
    CadenceSettings,
    EmployerPolicy,
    FormatPacks,
    LaneSettings,
    PillarSettings,
    WeekMode,
)
from blogwriter.domain.signals import Idea
from blogwriter.domain.slots import posts_for_mode
from blogwriter.domain.states import ArticleStatus, IdeaStatus, PostStatus, ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.weeks import add_days, plan_id
from blogwriter.planner._prompts import load_prompt
from blogwriter.planner.plan import (
    FlagshipPick,
    Material,
    PlanRules,
    PostPick,
    Proposal,
    TitleCheck,
    clean_title,
    title_is_plain,
)
from blogwriter.planner.slots import local_label, week_bounds

__all__ = [
    "HISTORY_WEEKS",
    "MAX_IDEAS",
    "PER_PILLAR",
    "RECENT_DAYS",
    "AiOutcome",
    "FlagshipChoice",
    "PlanContext",
    "PlanOut",
    "PostChoice",
    "collect_context",
    "load_material",
    "load_rules",
    "plan_request",
    "propose_with_ai",
    "reason_text",
    "screen_material",
]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("week_start", "plan_id", "reason", "posts", "cost_usd")

HISTORY_WEEKS: Final = 4
RECENT_DAYS: Final = 56
"""Stories and POVs a post used this recently count as used (prefer fresh material)."""
MAX_IDEAS: Final = 20
PER_PILLAR: Final = 6
"""Stories and POVs per pillar the plan considers (newest first)."""
_QUERY_LIMIT: Final = 300
_SECTION_CHARS: Final = 400
_REASONS: Final[Mapping[str, str]] = {
    "paused": "autonomy is paused",
    "ai_unavailable": "AI is not configured",
    "ai_use_not_confirmed": "the employer AI-use policy is not confirmed in Settings > Policy",
    "blocklist_not_configured": "the blocklist has not been saved yet",
    "blocklist_hit": "some settings text matches the blocklist",
    "refused": "the model declined",
    "ai_failed": "the model could not be reached",
    "invalid_output": "the model's answer was unusable",
}


def reason_text(reason: str) -> str:
    """The owner-facing rationale of a plan built without a model."""
    return f"Planned without AI: {_REASONS.get(reason, 'AI was not used')}. Adjust it as you like."


async def load_rules(store: Store, *, now: datetime) -> PlanRules:
    stamp = to_iso(now)
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    lanes, _ = await read_singleton(store, LaneSettings, now=stamp)
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    packs, _ = await read_singleton(store, FormatPacks, now=stamp)
    return PlanRules(cadence=cadence, lanes=lanes, pillars=pillars, packs=packs)


# --------------------------------------------------------------------------------------------
# collect: references and counts
# --------------------------------------------------------------------------------------------


def _strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


@dataclass(frozen=True, slots=True)
class PlanContext:
    """The ``collect`` step's output: ids and counts, never content."""

    week_start: str
    mode: WeekMode
    posts: int
    idea_ids: tuple[str, ...] = ()
    story_ids: tuple[str, ...] = ()
    pov_ids: tuple[str, ...] = ()
    source_article_id: str | None = None
    source_pillar: str | None = None
    recent_story_ids: tuple[str, ...] = ()
    recent_pov_ids: tuple[str, ...] = ()
    recent_post_pillars: Mapping[str, int] | None = None
    recent_flagships: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "weekStart": self.week_start,
            "mode": self.mode,
            "posts": self.posts,
            "ideaIds": list(self.idea_ids),
            "storyIds": list(self.story_ids),
            "povIds": list(self.pov_ids),
            "sourceArticleId": self.source_article_id,
            "sourcePillar": self.source_pillar,
            "recentStoryIds": list(self.recent_story_ids),
            "recentPovIds": list(self.recent_pov_ids),
            "recentPostPillars": dict(self.recent_post_pillars or {}),
            "recentFlagships": [
                {"pillar": pillar, "format": fmt} for pillar, fmt in self.recent_flagships
            ],
        }

    @classmethod
    def from_json(cls, value: JsonValue) -> PlanContext | None:
        if not isinstance(value, dict):
            return None
        week = value.get("weekStart")
        mode = value.get("mode")
        posts = value.get("posts")
        if not isinstance(week, str) or mode not in ("normal", "light", "away"):
            return None
        if not isinstance(posts, int) or isinstance(posts, bool):
            return None
        pillars = value.get("recentPostPillars")
        counts = (
            {k: v for k, v in pillars.items() if isinstance(v, int) and not isinstance(v, bool)}
            if isinstance(pillars, dict)
            else {}
        )
        flagships: list[tuple[str, str]] = []
        raw_flagships = value.get("recentFlagships")
        if isinstance(raw_flagships, list):
            for entry in raw_flagships:
                if isinstance(entry, dict):
                    pillar, fmt = entry.get("pillar"), entry.get("format")
                    if isinstance(pillar, str) and isinstance(fmt, str):
                        flagships.append((pillar, fmt))
        source = value.get("sourceArticleId")
        source_pillar = value.get("sourcePillar")
        return cls(
            week_start=week,
            mode=mode,
            posts=posts,
            idea_ids=tuple(_strings(value.get("ideaIds"))),
            story_ids=tuple(_strings(value.get("storyIds"))),
            pov_ids=tuple(_strings(value.get("povIds"))),
            source_article_id=source if isinstance(source, str) else None,
            source_pillar=source_pillar if isinstance(source_pillar, str) else None,
            recent_story_ids=tuple(_strings(value.get("recentStoryIds"))),
            recent_pov_ids=tuple(_strings(value.get("recentPovIds"))),
            recent_post_pillars=counts,
            recent_flagships=tuple(flagships),
        )


def _rank_ideas(ideas: Sequence[Idea], *, now: datetime) -> list[Idea]:
    stamp = to_iso(now)
    live = [i for i in ideas if i.expires_at is None or i.expires_at > stamp]

    def key(idea: Idea) -> tuple[int, int, float, str]:
        accepted = 0 if idea.status is IdeaStatus.ACCEPTED else 1
        urgent = 0 if idea.time_sensitive else 1
        return accepted, urgent, -(idea.relevance or 0.0), idea.id

    return sorted(live, key=key)[:MAX_IDEAS]


def _newest_per_pillar(entries: Sequence[tuple[str, Sequence[str], str]], limit: int) -> list[str]:
    """Ids of ``(id, pillars, updatedAt)`` entries, the newest ``limit`` per pillar."""
    picked: list[str] = []
    per: Counter[str] = Counter()
    for entry_id, pillars, _stamp in sorted(entries, key=lambda e: e[2], reverse=True):
        if any(per[pillar] < limit for pillar in pillars) and entry_id not in picked:
            picked.append(entry_id)
            per.update(pillars)
    return picked


async def _source_article(
    store: Store, previous: WeekPlan | None, *, week_start: str, zone: str
) -> tuple[Article | None, str | None]:
    """The flagship article that publishes on Monday of the week: last week's planned flagship,
    else an approved or published article whose publish time falls on that Monday."""
    usable = {
        ArticleStatus.IN_REVIEW,
        ArticleStatus.APPROVED,
        ArticleStatus.PUBLISHING,
        ArticleStatus.PUBLISHED,
    }
    hint = previous.flagship.pillar if previous and previous.flagship else None
    article_id = previous.flagship.article_id if previous and previous.flagship else None
    if article_id is not None:
        try:
            article = (await read_doc(store, Article, article_id)).doc
        except NotFound:
            article = None
        if article is not None and article.status in usable and article.purpose == "flagship":
            return article, article.meta.pillar or hint
    start, _end = week_bounds(week_start, zone)
    end = week_bounds(add_days(week_start, 1), zone)[0]
    found = await find_docs(
        store,
        Article,
        [
            Filter(
                "status",
                "in",
                [
                    ArticleStatus.APPROVED.value,
                    ArticleStatus.PUBLISHING.value,
                    ArticleStatus.PUBLISHED.value,
                ],
            ),
            Filter("publish.publishAt", ">=", start),
            Filter("publish.publishAt", "<", end),
        ],
        limit=10,
    )
    for stored in found:
        if stored.doc.purpose == "flagship":
            return stored.doc, stored.doc.meta.pillar
    return None, None


async def collect_context(
    store: Store, rules: PlanRules, *, week_start: str, mode: WeekMode, now: datetime
) -> PlanContext:
    """Ids and counts the plan draws on (``collect``)."""
    posts = posts_for_mode(rules.cadence, mode)
    if mode == "away":
        return PlanContext(week_start=week_start, mode=mode, posts=0)
    ideas = await find_docs(
        store,
        Idea,
        [Filter("status", "in", [IdeaStatus.NEW.value, IdeaStatus.ACCEPTED.value])],
        limit=_QUERY_LIMIT,
    )
    stories = await find_docs(
        store, StoryEntry, [Filter("status", "==", ReviewStatus.APPROVED.value)], limit=_QUERY_LIMIT
    )
    povs = await find_docs(
        store, PovEntry, [Filter("status", "==", ReviewStatus.APPROVED.value)], limit=_QUERY_LIMIT
    )
    since = to_iso(now - timedelta(days=RECENT_DAYS))
    recent = await find_docs(store, Post, [Filter("createdAt", ">=", since)], limit=_QUERY_LIMIT)
    balance_since = to_iso(now - timedelta(days=7 * HISTORY_WEEKS))
    gone = {PostStatus.ARCHIVED, PostStatus.EXPIRED, PostStatus.DELETED}
    live_posts = [s.doc for s in recent if s.doc.status not in gone]
    pillar_counts = Counter(p.pillar for p in live_posts if p.created_at >= balance_since)
    previous_plans: list[WeekPlan] = []
    for weeks_back in range(1, HISTORY_WEEKS + 1):
        try:
            previous_plans.append(
                (
                    await read_doc(store, WeekPlan, plan_id(add_days(week_start, -7 * weeks_back)))
                ).doc
            )
        except NotFound:
            continue
    last_week = next((p for p in previous_plans if p.week_start == add_days(week_start, -7)), None)
    source, source_pillar = await _source_article(
        store, last_week, week_start=week_start, zone=rules.cadence.timezone
    )
    usable_stories = [
        (s.doc.id, list(s.doc.pillars), s.doc.updated_at) for s in stories if s.doc.usable
    ]
    usable_povs = [(p.doc.id, [p.doc.pillar], p.doc.updated_at) for p in povs if p.doc.usable]
    return PlanContext(
        week_start=week_start,
        mode=mode,
        posts=posts,
        idea_ids=tuple(i.id for i in _rank_ideas([s.doc for s in ideas], now=now)),
        story_ids=tuple(_newest_per_pillar(usable_stories, PER_PILLAR)),
        pov_ids=tuple(_newest_per_pillar(usable_povs, PER_PILLAR)),
        source_article_id=source.id if source is not None else None,
        source_pillar=source_pillar,
        recent_story_ids=tuple(sorted({sid for p in live_posts for sid in p.story_ids})),
        recent_pov_ids=tuple(sorted({pid for p in live_posts for pid in p.pov_ids})),
        recent_post_pillars=dict(pillar_counts),
        recent_flagships=tuple(
            (p.flagship.pillar, p.flagship.format) for p in previous_plans if p.flagship
        ),
    )


# --------------------------------------------------------------------------------------------
# propose: the material and the PLAN call
# --------------------------------------------------------------------------------------------


async def _by_ids[D: Document](store: Store, cls: type[D], ids: Sequence[str]) -> dict[str, D]:
    if not ids:
        return {}
    found = await find_docs(store, cls, [Filter("id", "in", list(ids))])
    by_id = {stored.doc.id: stored.doc for stored in found}
    return {doc_id: by_id[doc_id] for doc_id in ids if doc_id in by_id}


async def load_material(store: Store, context: PlanContext) -> Material:
    """The documents behind the collected ids (missing ones are skipped)."""
    source: Article | None = None
    if context.source_article_id is not None:
        try:
            source = (await read_doc(store, Article, context.source_article_id)).doc
        except NotFound:
            source = None
    stories = {
        k: v for k, v in (await _by_ids(store, StoryEntry, context.story_ids)).items() if v.usable
    }
    povs = {k: v for k, v in (await _by_ids(store, PovEntry, context.pov_ids)).items() if v.usable}
    return Material(
        ideas=await _by_ids(store, Idea, context.idea_ids),
        stories=stories,
        povs=povs,
        source=source,
        source_pillar=context.source_pillar if source is not None else None,
        recent_story_ids=frozenset(context.recent_story_ids),
        recent_pov_ids=frozenset(context.recent_pov_ids),
        recent_post_pillars=dict(context.recent_post_pillars or {}),
        recent_flagships=context.recent_flagships,
    )


def screen_material(material: Material, blocklist: Blocklist) -> Material:
    """``material`` without the items whose text matches the blocklist (they never reach a
    model). An unsaved blocklist clears nothing: everything is dropped."""

    def clear(*texts: str | None) -> bool:
        return not blocklist_hit([text for text in texts if text], blocklist)

    source = material.source
    if source is not None and not clear(source.meta.title):
        source = None
    return replace(
        material,
        ideas={k: i for k, i in material.ideas.items() if clear(i.title, i.angle, i.note)},
        stories={k: s for k, s in material.stories.items() if clear(s.title)},
        povs={k: p for k, p in material.povs.items() if clear(p.statement)},
        source=source,
        source_pillar=material.source_pillar if source is not None else None,
    )


def _section(value: object) -> str:
    """JSON for a delimited prompt section; ``<`` and ``>`` escaped so no text can close it."""
    text = json.dumps(value, ensure_ascii=False, indent=1)
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


def _cut(text: str | None, limit: int = _SECTION_CHARS) -> str | None:
    if not text:
        return None
    return clean_title(text, limit=limit) or None


class FlagshipChoice(ApiModel):
    title: str = Field(min_length=1, max_length=200, description="At most 12 words.")
    pillar: str = Field(description="A pillar id from <pillars>.")
    format: str = Field(description="An article format id from <formats>.")
    idea_id: str | None = Field(default=None, description="An idea id from <ideas>, or null.")


class PostChoice(ApiModel):
    lane: Literal["derivative", "native", "quick_take"]
    pillar: str = Field(description="A pillar id from <pillars>.")
    format: Literal["carousel", "text", "image_card"]
    title: str = Field(min_length=1, max_length=200, description="At most 12 words.")
    story_id: str | None = Field(default=None, description="A story id from <stories>, or null.")
    pov_id: str | None = Field(default=None, description="A POV id from <povs>, or null.")
    idea_id: str | None = Field(default=None, description="An idea id from <ideas>, or null.")


class PlanOut(ApiModel):
    """The PLAN reply (structured output; plain fields only)."""

    flagship: FlagshipChoice | None = None
    posts: list[PostChoice] = Field(max_length=7)
    rationale: str = Field(default="", max_length=1500, description="At most 80 words.")


def plan_request(
    context: PlanContext,
    material: Material,
    rules: PlanRules,
    *,
    slots: Sequence[Slot],
) -> tuple[Request, dict[str, str | None]]:
    """The PLAN request and every owner-editable or model-written text it carries (for
    ``check_texts`` before the call)."""
    cadence = rules.cadence
    lanes = rules.lanes
    zone = cadence.timezone
    texts: dict[str, str | None] = {}
    pillars: list[dict[str, JsonValue]] = []
    for index, pillar in enumerate(rules.pillars.pillars):
        entry: dict[str, JsonValue] = {
            "id": pillar.id,
            "name": pillar.name,
            "targetShare": round(pillar.target_share, 2),
        }
        if pillar.topics.strip():
            entry["topics"] = _cut(pillar.topics)
        if pillar.audience.strip():
            entry["audience"] = _cut(pillar.audience)
        texts[f"pillars[{index}]"] = " ".join(str(v) for v in entry.values())
        pillars.append(entry)
    flagship_formats: list[JsonValue] = []
    for index, pack in enumerate(rules.packs.packs):
        flagship_formats.append(
            {
                "id": pack.id,
                "name": pack.name,
                "words": f"{pack.min_words}-{pack.max_words}",
                "ownerInput": pack.owner_input,
            }
        )
        texts[f"formats[{index}]"] = pack.name
    source = material.source
    source_entry: JsonValue = None
    if source is not None:
        source_entry = {
            "id": source.id,
            "title": _cut(source.meta.title),
            "pillar": material.source_pillar,
            "format": source.meta.format,
        }
        texts["derivativeSource"] = source.meta.title
    stories: list[JsonValue] = []
    for story in material.stories.values():
        stories.append(
            {
                "id": story.id,
                "title": _cut(story.title),
                "pillars": list[JsonValue](story.pillars),
                "usedRecently": story.id in material.recent_story_ids,
            }
        )
        texts[f"stories.{story.id}"] = story.title
    povs: list[JsonValue] = []
    for pov in material.povs.values():
        povs.append(
            {
                "id": pov.id,
                "pillar": pov.pillar,
                "statement": _cut(pov.statement, 280),
                "usedRecently": pov.id in material.recent_pov_ids,
            }
        )
        texts[f"povs.{pov.id}"] = pov.statement
    ideas: list[JsonValue] = []
    for idea in material.ideas.values():
        ideas.append(
            {
                "id": idea.id,
                "title": _cut(idea.title, 300),
                "pillar": idea.pillar,
                "suggestedLane": idea.suggested_lane,
                "suggestedFormat": idea.suggested_format,
                "timeSensitive": idea.time_sensitive,
                "angle": _cut(idea.angle, 300),
            }
        )
        texts[f"ideas.{idea.id}"] = " ".join(t for t in (idea.title, idea.angle, idea.note) if t)
    week: dict[str, JsonValue] = {
        "weekStart": context.week_start,
        "mode": context.mode,
        "flagshipOptional": context.mode == "light",
        "slots": [local_label(slot.utc, zone) for slot in slots],
    }
    rules_section: dict[str, JsonValue] = {
        "posts": len(slots),
        "derivativesMax": lanes.derivatives_max_per_week if source is not None else 0,
        "nativeMin": min(lanes.native_min_per_week, len(slots)),
        "nativeAvoidsPillar": (
            material.source_pillar if source is not None else "the flagship's pillar"
        )
        if lanes.native_other_pillar
        else None,
        "onPillarMinShare": lanes.on_pillar_min_share,
    }
    history: dict[str, JsonValue] = {
        "recentFlagships": [
            {"pillar": pillar, "format": fmt} for pillar, fmt in material.recent_flagships
        ],
        "recentPostsPerPillar": dict(material.recent_post_pillars),
    }
    formats: dict[str, JsonValue] = {
        "flagship": flagship_formats,
        "posts": list[JsonValue](rules.post_formats()),
    }
    sections = [
        "<week>\n" + _section(week) + "\n</week>",
        "<rules>\n" + _section(rules_section) + "\n</rules>",
        "<pillars>\n" + _section(pillars) + "\n</pillars>",
        "<formats>\n" + _section(formats) + "\n</formats>",
        "<history>\n" + _section(history) + "\n</history>",
        "<derivative_source>\n" + _section(source_entry) + "\n</derivative_source>",
        "<stories>\n" + _section(stories) + "\n</stories>",
        "<povs>\n" + _section(povs) + "\n</povs>",
        "<ideas>\n" + _section(ideas) + "\n</ideas>",
        f"Return the flagship and exactly {len(slots)} posts.",
    ]
    request = Request(
        system=load_prompt("plan").text,
        messages=(Message.user("\n\n".join(sections)),),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=PlanOut,
    )
    return request, texts


def _proposal(out: PlanOut) -> Proposal:
    flagship = (
        FlagshipPick(
            title=out.flagship.title,
            pillar=out.flagship.pillar,
            format=out.flagship.format,
            idea_id=out.flagship.idea_id,
        )
        if out.flagship is not None
        else None
    )
    posts = tuple(
        PostPick(
            lane=choice.lane,
            pillar=choice.pillar,
            format=choice.format,
            title=choice.title,
            story_ids=(choice.story_id,) if choice.story_id else (),
            pov_ids=(choice.pov_id,) if choice.pov_id else (),
            idea_id=choice.idea_id if choice.lane == "quick_take" else None,
        )
        for choice in out.posts
    )
    return Proposal(flagship=flagship, posts=posts, rationale=out.rationale, source="ai")


@dataclass(frozen=True, slots=True)
class AiOutcome:
    """The PLAN step's result: a proposal, or why there is none (``reason``)."""

    proposal: Proposal | None
    reason: str | None = None
    served_by: ServedByRecord | None = None
    check: TitleCheck = title_is_plain


async def propose_with_ai(
    store: Store,
    ai: ModelRunner | None,
    context: PlanContext,
    material: Material,
    rules: PlanRules,
    *,
    slots: Sequence[Slot],
    origin: Origin,
    job_id: str | None,
    now: datetime,
) -> AiOutcome:
    """Ask PLAN for the flagship and post mix (see the module docs for when it is skipped)."""
    if ai is None:
        return AiOutcome(None, "ai_unavailable")
    stamp = to_iso(now)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    if not policy.ai_use_confirmed:
        return AiOutcome(None, "ai_use_not_confirmed")
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    if not blocklist.configured:
        return AiOutcome(None, "blocklist_not_configured")
    screened = screen_material(material, blocklist)
    request, texts = plan_request(context, screened, rules, slots=slots)
    try:
        check_texts(texts, blocklist)
        check_texts({"prompt": request.messages[0].text}, blocklist)
    except ConfidentialText:
        return AiOutcome(None, "blocklist_hit")

    def check(title: str) -> bool:
        return title_is_plain(title) and not blocklist_hit([title], blocklist)

    try:
        with defer_on_brake(now):
            result = await ai.run(Task.PLAN, request, origin=origin, job_id=job_id)
    except StructuredOutputError as exc:
        _logger.warning("planner_plan_unusable", extra={"reason": exc.reason})
        return AiOutcome(None, "invalid_output")
    except (ProviderError, ModelCoolingDown) as exc:
        # Both models failed after the router's retries: a plan without AI beats no plan.
        _logger.warning("planner_plan_ai_failed", extra={"reason": type(exc).__name__})
        return AiOutcome(None, "ai_failed")
    served_by = ServedByRecord.from_served_by(
        result.served_by,
        step="propose",
        at=stamp,
        refused=bool(result.refusals) or result.refused,
        prompt_version=load_prompt("plan").version,
        cost_usd=float(result.cost_usd),
    )
    if result.refused:
        return AiOutcome(None, "refused", served_by)
    try:
        out = result.output(PlanOut)
    except TypeError:
        return AiOutcome(None, "invalid_output", served_by)
    _logger.info(
        "planner_plan_proposed",
        extra={"posts": len(out.posts), "cost_usd": float(result.cost_usd)},
    )
    return AiOutcome(_proposal(out), None, served_by, check)
