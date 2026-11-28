"""Post generation: derivatives of a flagship article, native posts from the story and POV bank,
quick takes, repurposed posts, and regeneration of a post's hooks, text or carousel.

Every call is ``Task.DRAFT`` (``Task.REVISE`` when the owner gives an instruction) at
``Sensitivity.ANONYMIZED``, tool-free, after ``require_ai_use`` and a blocklist check of
everything that goes to the model (material, the current post, the instruction). Background
calls pause on the spend brake. The system prompt (``prompts/system.v1.md``) carries the plan's
rules (banned openers, AI tells, slop structures, zero links, exact numbers, inline
attribution, the hook taxonomy and fold, format limits) and the active Voice & Style Guide.

The model returns structured output: hook variants with a ``hookType`` and a concrete detail,
the body, hashtags (only the pillar's approved ones are kept), citation ids (attributions are
built from the citation's publisher and title, never from model text) and, for carousels and
image cards, ``blogwriter.render`` slides. A variant's ``usesOwnerWording`` is re-checked here:
it is true only when the variant shares four consecutive words with the owner's own material.

Drafting returns the field changes; the job writes them with ``generating -> draft`` in one CAS
write. A failure moves the post to ``needs_attention`` with ``lastError`` (never stranded in
``generating``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import regex
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blogwriter.ai.ledger import Origin
from blogwriter.ai.providers.base import ProviderRequestError
from blogwriter.ai.router import RouterResult
from blogwriter.ai.types import Message, Request, Sensitivity, Task, TextPart
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import ServedByRecord, review_only_reasons
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.docstore import find_docs, read_singleton, update_doc
from blogwriter.domain.golive import default_go_live_mode as golive_default_mode
from blogwriter.domain.jobflow import defer_on_brake
from blogwriter.domain.ports import Services
from blogwriter.domain.posts import Attribution, HookType, HookVariant, Post, PostFormat
from blogwriter.domain.settings import (
    Blocklist,
    EmployerPolicy,
    LaneSettings,
    Pillar,
    PillarSettings,
)
from blogwriter.domain.states import POST_MACHINE, ArticleStatus, PostStatus
from blogwriter.linkedin.littletext import LittleTextError
from blogwriter.linkedin.posts._logging import logger
from blogwriter.linkedin.posts._material import (
    Sources,
    citation_publisher,
    data,
    load_sources,
    neutral,
    render_sources,
)
from blogwriter.linkedin.posts._post import (
    add_served_by,
    approved_subset,
    merge_reasons,
    owner_take,
)
from blogwriter.linkedin.posts._prompts import PromptName, load_prompt
from blogwriter.linkedin.posts.carousel import (
    MEDIA_FORMATS,
    InvalidSpec,
    parse_spec,
    slide_texts,
    spec_json,
)
from blogwriter.linkedin.posts.serialize import clean_text, serialize
from blogwriter.render.spec import CarouselSpec, Slide
from blogwriter.stories import load_bank
from blogwriter.voice import guide_prompt, load_active_guide

__all__ = [
    "APPROVED_ARTICLE_STATUSES",
    "BANK_PICK",
    "DERIVABLE_STATUSES",
    "FATAL_CODES",
    "OWNER_WORDING_WORDS",
    "CarouselOut",
    "GenerationContext",
    "GenerationFailed",
    "HookOut",
    "HooksOut",
    "PostDraftOut",
    "TextOut",
    "build_attributions",
    "build_variants",
    "compose_display",
    "default_go_live_mode",
    "draft_post",
    "load_generation_context",
    "mark_generation_failed",
    "owner_wording",
    "replace_hook",
    "save_draft",
]

OWNER_WORDING_WORDS: Final = 4
"""A hook "reuses the owner's wording" when it shares this many consecutive words with it."""
BANK_PICK: Final = 2
"""Native posts without chosen material draw on at most this many stories and POVs."""
_RECENT_USE: Final = timedelta(days=30)
APPROVED_ARTICLE_STATUSES: Final = frozenset(
    {ArticleStatus.APPROVED, ArticleStatus.PUBLISHING, ArticleStatus.PUBLISHED}
)
"""Article states a derived post may be approved in (the owner approved the flagship)."""
DERIVABLE_STATUSES: Final = APPROVED_ARTICLE_STATUSES | {ArticleStatus.IN_REVIEW}
"""Article states posts may be derived from. ``in_review`` derives previews so the owner can
review an article and its posts together; such posts cannot be approved until the article is
(``APPROVED_ARTICLE_STATUSES``, checked in post approval)."""

type What = Literal["all", "hooks", "text", "carousel"]

FATAL_CODES: Final = frozenset(
    {
        "ai_use_not_confirmed",
        "ai_unavailable",
        "blocklist_not_configured",
        "blocklist_hit",
        "source_missing",
        "article_not_ready",
        "unknown_pillar",
    }
)
"""Failures that would fail every other post of the same job too."""


class GenerationFailed(Exception):
    """Drafting did not produce a post. ``code`` goes to ``lastError`` and ``JobFailed``;
    ``record`` and ``reasons`` (a refusal) are still stored on the post."""

    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        record: ServedByRecord | None = None,
        reasons: Sequence[str] = (),
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.record = record
        self.reasons = tuple(reasons)

    @property
    def fatal(self) -> bool:
        return self.code in FATAL_CODES


# --------------------------------------------------------------------------------------------
# Structured output
# --------------------------------------------------------------------------------------------


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HookOut(_Out):
    text: str = Field(min_length=1, max_length=400)
    hook_type: HookType
    concrete_detail: str = Field(min_length=1, max_length=300)
    uses_owner_wording: bool = False


class PostDraftOut(_Out):
    """A whole post."""

    angle: str = Field(min_length=1, max_length=300)
    format: Literal["text", "carousel", "image_card"] = "text"
    hooks: list[HookOut] = Field(min_length=3, max_length=5)
    body: str = Field(min_length=1, max_length=5000)
    hashtags: list[str] = Field(default_factory=list[str], max_length=3)
    document_title: str | None = Field(default=None, max_length=100)
    slides: list[Slide] | None = Field(default=None, max_length=20)
    citation_ids: list[str] = Field(default_factory=list[str], max_length=10)


class HooksOut(_Out):
    hooks: list[HookOut] = Field(min_length=3, max_length=5)


class TextOut(_Out):
    hooks: list[HookOut] = Field(min_length=3, max_length=5)
    body: str = Field(min_length=1, max_length=5000)
    hashtags: list[str] = Field(default_factory=list[str], max_length=3)
    citation_ids: list[str] = Field(default_factory=list[str], max_length=10)


class CarouselOut(_Out):
    document_title: str = Field(min_length=1, max_length=100)
    slides: list[Slide] = Field(min_length=1, max_length=20)


_SCHEMAS: Final[Mapping[What, type[BaseModel]]] = {
    "all": PostDraftOut,
    "hooks": HooksOut,
    "text": TextOut,
    "carousel": CarouselOut,
}


# --------------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenerationContext:
    now: datetime
    blocklist: Blocklist
    policy: EmployerPolicy
    pillars: PillarSettings
    lanes: LaneSettings
    guide: str | None
    """The active Voice & Style Guide as a cached prompt prefix (``None`` before one exists)."""


async def load_generation_context(store: Store, *, now: datetime) -> GenerationContext:
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    lanes, _ = await read_singleton(store, LaneSettings, now=stamp)
    guide = await load_active_guide(store)
    return GenerationContext(
        now=now,
        blocklist=blocklist,
        policy=policy,
        pillars=pillars,
        lanes=lanes,
        guide=guide_prompt(guide) if guide is not None else None,
    )


async def default_go_live_mode(store: Store, *, now: datetime) -> Literal["one_click", "kit"]:
    """One-click when LinkedIn is connected, else the ready-to-post kit (needs no API):
    ``domain.golive.default_go_live_mode``, the rule every way a post is created follows."""
    return await golive_default_mode(store, now=now)


# --------------------------------------------------------------------------------------------
# Building a post from model output
# --------------------------------------------------------------------------------------------

_WORD: Final = regex.compile(r"[\p{L}\p{N}]+(?:['\u2019][\p{L}]+)?")


def _word_runs(text: str, size: int) -> set[tuple[str, ...]]:
    words = [w.casefold().replace("\u2019", "'") for w in _WORD.findall(text)]
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def owner_wording(
    text: str, owner_texts: Iterable[str], *, words: int = OWNER_WORDING_WORDS
) -> bool:
    """Whether ``text`` repeats a run of ``words`` consecutive words of the owner's material."""
    mine = _word_runs(text, words)
    if not mine:
        return False
    return any(mine & _word_runs(owner, words) for owner in owner_texts)


def build_variants(
    hooks: Sequence[HookOut], owner_texts: Sequence[str], *, count: int
) -> list[HookVariant]:
    """Up to ``count`` hook variants (ids ``h1``...) with ``usesOwnerWording`` verified."""
    variants: list[HookVariant] = []
    for hook in hooks:
        text = clean_text(hook.text)
        detail = clean_text(hook.concrete_detail)
        if not text or not detail:
            continue
        variants.append(
            HookVariant(
                id=f"h{len(variants) + 1}",
                text=text[:400],
                hook_type=hook.hook_type,
                concrete_detail=detail[:300],
                uses_owner_wording=owner_wording(text, owner_texts),
            )
        )
        if len(variants) >= count:
            break
    return variants


def compose_display(hook: str, body: str, hashtags: Sequence[str]) -> str:
    """Hook, a blank line, the body, and the approved hashtags on the last line."""
    head = clean_text(hook)
    rest = clean_text(body)
    if head and rest.startswith(head):
        rest = rest[len(head) :].lstrip()
    text = "\n\n".join(part for part in (head, rest) if part)
    if hashtags:
        text += "\n\n" + " ".join(f"#{tag}" for tag in hashtags)
    return text


def replace_hook(display: str, hook: str) -> str:
    """``display`` with its first paragraph (the hook) replaced by ``hook``."""
    _, separator, rest = display.partition("\n\n")
    return clean_text(hook) + (separator + rest if separator else "")


def build_attributions(citation_ids: Iterable[str], sources: Sources) -> list[Attribution]:
    """Attributions for the citations the draft used, from the citations themselves."""
    out: list[Attribution] = []
    for citation_id in dict.fromkeys(citation_ids):
        citation = sources.citations.get(citation_id)
        if citation is None:
            continue
        out.append(
            Attribution(
                publisher=citation_publisher(citation),
                title=citation.title[:300],
                citation_id=citation.id,
            )
        )
    return out[:10]


def _spec_from(
    title: str | None, slides: Sequence[Slide] | None, *, fallback_title: str, card: bool
) -> dict[str, object] | None:
    if not slides:
        return None
    chosen = list(slides[:1]) if card else list(slides)
    raw_title = clean_text(title or "") or clean_text(fallback_title)
    try:
        spec = CarouselSpec(title=raw_title[:100] or "Carousel", slides=chosen)
    except ValidationError:
        return None
    return spec_json(spec)


def _serialized(text: str, hashtags: Sequence[str]) -> tuple[str, str]:
    try:
        return serialize(text, hashtags)
    except LittleTextError:
        raise GenerationFailed("invalid_text", "The draft could not be serialized.") from None


# --------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------

_LANE_PROMPTS: Final[Mapping[str, PromptName]] = {
    "derivative": "derive",
    "native": "native",
    "quick_take": "quick_take",
    "repurpose": "repurpose",
}


def _format_line(fixed: PostFormat | None, lanes: LaneSettings) -> list[str]:
    if fixed is not None:
        return [f"format: {fixed} (use exactly this format)"]
    allowed = ["text", "carousel"]
    if lanes.formats.image_card_enabled:
        allowed.append("image_card")
    return [f"format: choose one of {', '.join(allowed)} (set `format`)"]


def _task_block(
    post: Post,
    pillar: Pillar,
    ctx: GenerationContext,
    *,
    fixed_format: PostFormat | None,
    what: What,
) -> str:
    lanes = ctx.lanes
    lines = ["<task>", f"lane: {post.lane}", f"pillar: {neutral(pillar.name)}"]
    if pillar.topics:
        lines.append(f"pillar topics: {data(pillar.topics)}")
    if pillar.audience:
        lines.append(f"audience: {data(pillar.audience)}")
    if pillar.vocabulary:
        lines.append("canonical terms: " + "; ".join(data(term) for term in pillar.vocabulary))
    if pillar.renames:
        renames = "; ".join(
            f"{data(new)} (never {data(old)})" for old, new in pillar.renames.items()
        )
        lines.append(f"current names: {renames}")
    lines.append(f"hook variants: {lanes.hook_variants}")
    if what in ("all", "text", "carousel"):
        lines.extend(_format_line(fixed_format, lanes))
    if what in ("all", "text"):
        tags = " ".join(f"#{tag}" for tag in pillar.hashtags)
        lines.append(
            f"approved hashtags (pick 0-3, exact spelling): {tags}"
            if tags
            else "approved hashtags: none (return an empty list)"
        )
        if post.lane == "quick_take":
            quick = lanes.quick_take
            lines.append(f"length: {quick.min_chars}-{quick.max_chars} characters in total")
        else:
            formats = lanes.formats
            lines.append(
                f"text length: {formats.text_min_chars}-{formats.text_max_chars} characters in "
                "total for text and image_card posts; carousel captions at most 450"
            )
            lines.append(
                f"carousel slides: {formats.carousel_min_slides}-{formats.carousel_max_slides}"
            )
    lines.append("</task>")
    return "\n".join(lines)


def _current_post_block(post: Post) -> str:
    parts = [f'<current_post format="{post.format}">', data(post.display_text)]
    if post.carousel_spec is not None:
        try:
            spec = parse_spec(post.carousel_spec)
        except InvalidSpec:
            pass
        else:
            parts.append(f"<document_title>{data(spec.title)}</document_title>")
            for index, text in enumerate(slide_texts(spec), start=1):
                parts.append(f'<slide n="{index}">{data(text)}</slide>')
    parts.append("</current_post>")
    return "\n".join(parts)


def _siblings_block(siblings: Sequence[Post]) -> str:
    if not siblings:
        return ""
    items = [
        f'<other_post format="{other.format}">{data(other.display_text.partition(chr(10))[0])}'
        "</other_post>"
        for other in siblings
        if other.display_text
    ]
    return "<other_posts>\n" + "\n".join(items) + "\n</other_posts>" if items else ""


def _brief_ideas(sources: Sources) -> str:
    brief = sources.brief
    if brief is None or not (brief.angles or brief.hook_seeds):
        return ""
    lines = ["<brief_angles>"]
    lines.extend(f"- {data(angle)}" for angle in brief.angles)
    lines.extend(f"- hook seed: {data(seed)}" for seed in brief.hook_seeds)
    lines.append("</brief_angles>")
    return "\n".join(lines)


def _request(
    post: Post,
    pillar: Pillar,
    sources: Sources,
    ctx: GenerationContext,
    *,
    what: What,
    fixed_format: PostFormat | None,
    instruction: str | None,
    siblings: Sequence[Post],
) -> tuple[Request, list[str]]:
    system_prompt = load_prompt("system")
    system = system_prompt.text if ctx.guide is None else f"{system_prompt.text}\n\n{ctx.guide}"
    task_prompt = load_prompt("regenerate" if what != "all" else _LANE_PROMPTS[post.lane])
    material = "\n\n".join(
        part for part in (render_sources(sources), _brief_ideas(sources)) if part
    )
    task_parts = [
        task_prompt.text,
        _task_block(post, pillar, ctx, fixed_format=fixed_format, what=what),
    ]
    if what != "all":
        task_parts.append(f"<regenerate>{what}</regenerate>")
        task_parts.append(_current_post_block(post))
    if siblings:
        task_parts.append(_siblings_block(siblings))
    if instruction:
        task_parts.append(f"<instruction>\n{data(instruction)}\n</instruction>")
    request = Request(
        system=system,
        messages=(
            Message(
                "user",
                (
                    TextPart(
                        f"<material>\n{material or '(none)'}\n</material>", cache_breakpoint=True
                    ),
                    TextPart("\n\n".join(part for part in task_parts if part)),
                ),
            ),
        ),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=_SCHEMAS[what],
    )
    return request, [system_prompt.version, task_prompt.version]


# --------------------------------------------------------------------------------------------
# Material per lane
# --------------------------------------------------------------------------------------------


async def _pick_bank(store: Store, post: Post, *, now: datetime) -> tuple[list[str], list[str]]:
    """Stories and POVs for a native post without chosen material: usable entries of its
    pillar, preferring ones no post used in the last 30 days."""
    bank = await load_bank(store, pillar=post.pillar)
    cutoff = to_iso(now - _RECENT_USE)
    recent = await find_docs(store, Post, [Filter("createdAt", ">=", cutoff)])
    used = {
        sid for p in recent for sid in (*p.doc.story_ids, *p.doc.pov_ids) if p.doc.id != post.id
    }
    stories = sorted(bank.stories, key=lambda story: story.id in used)
    povs = sorted(bank.povs, key=lambda pov: pov.id in used)
    return [s.id for s in stories[:BANK_PICK]], [p.id for p in povs[:BANK_PICK]]


async def _material(services: Services, post: Post, ctx: GenerationContext) -> tuple[Post, Sources]:
    """The post (with the material ids it will be checked against) and its material."""
    store = services.store
    if post.lane == "native" and not post.story_ids and not post.pov_ids:
        story_ids, pov_ids = await _pick_bank(store, post, now=ctx.now)
        post = post.model_copy(update={"story_ids": story_ids, "pov_ids": pov_ids})
    sources = await load_sources(store, post, usable_only=True)
    if sources.missing:
        raise GenerationFailed("source_missing", "Something this post draws on no longer exists.")
    if sources.unusable:
        raise GenerationFailed(
            "owner_material_unusable",
            "A story or POV is not approved or not cleared by the classifier yet.",
        )
    match post.lane:
        case "derivative":
            article = sources.article
            if article is None or article.status not in DERIVABLE_STATUSES:
                raise GenerationFailed(
                    "article_not_ready", "The article is not in review or approved."
                )
            if post.brief_id is None and article.brief_id is not None:
                post = post.model_copy(update={"brief_id": article.brief_id})
            signals = [s for s in article.owner_signals if s.kind in ("story", "pov")]
            post = post.model_copy(
                update={
                    "story_ids": list(
                        dict.fromkeys(
                            [*post.story_ids, *(s.id for s in signals if s.kind == "story")]
                        )
                    )[:10],
                    "pov_ids": list(
                        dict.fromkeys([*post.pov_ids, *(s.id for s in signals if s.kind == "pov")])
                    )[:10],
                }
            )
        case "native":
            if not sources.stories and not sources.povs:
                raise GenerationFailed(
                    "no_owner_material",
                    "No approved story or POV for this pillar yet. Add one in Library.",
                )
        case "quick_take":
            if sources.idea is None:
                raise GenerationFailed("idea_missing", "A quick take starts from an idea.")
            if ctx.lanes.quick_take.owner_take_required and not (owner_take(post) or sources.povs):
                raise GenerationFailed("owner_take_required", "Add your take first.")
        case "repurpose":
            if sources.source_post is None:
                raise GenerationFailed(
                    "repurpose_source_missing", "The post to re-cut was not found."
                )
    return post, sources


def _blocklist_fields(
    post: Post, sources: Sources, instruction: str | None, what: What
) -> dict[str, str | None]:
    fields: dict[str, str | None] = {item.ref: item.text for item in sources.items}
    fields["instruction"] = instruction
    if what != "all":
        fields["displayText"] = post.display_text
        if post.carousel_spec is not None:
            try:
                spec = parse_spec(post.carousel_spec)
            except InvalidSpec:
                pass
            else:
                fields["documentTitle"] = spec.title
                for index, text in enumerate(slide_texts(spec)):
                    fields[f"slides[{index}]"] = text
    return fields


def _refuse_confidential(fields: Mapping[str, str | None], blocklist: Blocklist) -> None:
    try:
        check_texts(fields, blocklist)
    except BlocklistNotConfigured:
        raise GenerationFailed(
            "blocklist_not_configured", "Save the blocklist once in Settings > Policy first."
        ) from None
    except ConfidentialText:
        raise GenerationFailed(
            "blocklist_hit", "Something this post draws on matches the blocklist."
        ) from None


# --------------------------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Outcome:
    record: ServedByRecord
    reasons: tuple[str, ...]
    value: BaseModel


async def _call(
    services: Services,
    request: Request,
    *,
    task: Task,
    origin: Origin,
    job_id: str | None,
    now: datetime,
    step: str,
    prompt_version: str,
) -> _Outcome:
    runner = services.ai
    if runner is None:
        raise GenerationFailed("ai_unavailable", "Models are not configured.")
    try:
        with defer_on_brake(now):
            result: RouterResult = await runner.run(task, request, origin=origin, job_id=job_id)
    except ProviderRequestError:
        # Not retryable (the service rejected the request itself): fail the post visibly
        # (needs_attention) instead of letting the job retry into quarantine with the post
        # stranded in "generating".
        raise GenerationFailed(
            "generation_failed",
            "The model service rejected this request. Try again; if it keeps failing, "
            "shorten the material.",
        ) from None
    reasons = review_only_reasons(result)
    record = ServedByRecord.from_served_by(
        result.served_by,
        step=step,
        at=to_iso(now),
        refused=result.refused or bool(result.refusals),
        cost_usd=float(result.cost_usd),
        prompt_version=prompt_version,
    )
    if result.refused or result.value is None:
        raise GenerationFailed(
            "generation_refused" if result.refused else "generation_failed",
            "The model did not write this post. Edit the material or try again.",
            record=record,
            reasons=reasons or ("refusal",),
        )
    return _Outcome(record=record, reasons=reasons, value=result.value)


async def draft_post(
    services: Services,
    post: Post,
    ctx: GenerationContext,
    *,
    origin: Origin,
    job_id: str | None,
    what: What = "all",
    instruction: str | None = None,
    fixed_format: PostFormat | None = None,
    siblings: Sequence[Post] = (),
) -> dict[str, object]:
    """Draft ``post`` (or one part of it) and return the field changes. Raises
    :class:`GenerationFailed`; lets ``JobDeferred`` (the brake) through."""
    try:
        require_ai_use(ctx.policy)
    except AiUseNotConfirmed:
        raise GenerationFailed(
            "ai_use_not_confirmed", "Confirm the employer AI-use policy in Settings > Policy."
        ) from None
    if services.ai is None:
        raise GenerationFailed("ai_unavailable", "Models are not configured.")
    pillar = ctx.pillars.get(post.pillar)
    if pillar is None:
        raise GenerationFailed("unknown_pillar", "The post's pillar no longer exists.")
    post, sources = await _material(services, post, ctx)
    _refuse_confidential(_blocklist_fields(post, sources, instruction, what), ctx.blocklist)
    request, versions = _request(
        post,
        pillar,
        sources,
        ctx,
        what=what,
        fixed_format=fixed_format,
        instruction=instruction,
        siblings=siblings,
    )
    task = Task.REVISE if instruction else Task.DRAFT
    outcome = await _call(
        services,
        request,
        task=task,
        origin=origin,
        job_id=job_id,
        now=ctx.now,
        step="regenerate" if what != "all" else "generate",
        prompt_version="+".join(versions),
    )
    changes = _apply(
        post, outcome.value, pillar, sources, ctx, what=what, fixed_format=fixed_format
    )
    changes["served_by"] = add_served_by(post.served_by, outcome.record)
    if outcome.reasons:
        changes["review_only"] = True
        changes["review_only_reasons"] = merge_reasons(post.review_only_reasons, outcome.reasons)
    changes["story_ids"] = list(post.story_ids)
    changes["pov_ids"] = list(post.pov_ids)
    changes["brief_id"] = post.brief_id
    changes["last_error"] = None
    logger.info(
        "posts_drafted",
        extra={
            "post_id": post.id,
            "post_lane": post.lane,
            "post_format": changes.get("format", post.format),
        },
    )
    return changes


def _apply(
    post: Post,
    value: BaseModel,
    pillar: Pillar,
    sources: Sources,
    ctx: GenerationContext,
    *,
    what: What,
    fixed_format: PostFormat | None,
) -> dict[str, object]:
    owner_texts = sources.owner_texts
    count = ctx.lanes.hook_variants
    changes: dict[str, object] = {}
    if isinstance(value, PostDraftOut | TextOut):
        variants = build_variants(value.hooks, owner_texts, count=count)
        if not variants:
            raise GenerationFailed("no_hooks", "The draft had no usable hook.")
        hashtags = approved_subset(value.hashtags, pillar)
        display, payload = _serialized(
            compose_display(variants[0].text, value.body, hashtags), hashtags
        )
        changes.update(
            hook_variants=variants,
            selected_hook_id=variants[0].id,
            hook_type=variants[0].hook_type,
            hashtags=hashtags,
            display_text=display,
            payload=payload,
            ai_draft_text=display,
            attributions=build_attributions(value.citation_ids, sources),
        )
        if isinstance(value, PostDraftOut):
            fmt: PostFormat = fixed_format or value.format
            if fmt == "image_card" and not ctx.lanes.formats.image_card_enabled:
                fmt = "text"
            spec = (
                _spec_from(
                    value.document_title,
                    value.slides,
                    fallback_title=variants[0].text,
                    card=fmt == "image_card",
                )
                if fmt in MEDIA_FORMATS
                else None
            )
            title = spec.get("title") if spec is not None else None
            changes.update(
                format=fmt,
                carousel_spec=spec,
                document_title=title if isinstance(title, str) else None,
                media=[],
            )
    elif isinstance(value, HooksOut):
        variants = build_variants(value.hooks, owner_texts, count=count)
        if not variants:
            raise GenerationFailed("no_hooks", "The draft had no usable hook.")
        display, payload = _serialized(
            replace_hook(post.display_text, variants[0].text), post.hashtags
        )
        changes.update(
            hook_variants=variants,
            selected_hook_id=variants[0].id,
            hook_type=variants[0].hook_type,
            display_text=display,
            payload=payload,
        )
    elif isinstance(value, CarouselOut):
        spec = _spec_from(
            value.document_title,
            value.slides,
            fallback_title=value.document_title,
            card=post.format == "image_card",
        )
        if spec is None:
            raise GenerationFailed("generation_failed", "The slides were not valid.")
        title = spec.get("title")
        changes.update(
            carousel_spec=spec,
            document_title=title if isinstance(title, str) else post.document_title,
            media=[],
        )
    return changes


# --------------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------------


async def save_draft(
    store: Store, post_id: str, changes: Mapping[str, object], *, now: datetime
) -> Post | None:
    """Write the drafted fields and move ``generating -> draft`` (one CAS write). ``None`` when
    the post is no longer generating (archived meanwhile): nothing is written."""
    stamp = to_iso(now)

    def mutate(current: Post) -> Post | None:
        if current.status is not PostStatus.GENERATING:
            return None
        target = POST_MACHINE.transition(current.status, PostStatus.DRAFT)
        return current.model_copy(
            update={
                **changes,
                "status": target,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "approval": None,
                "staging": None,
            }
        )

    try:
        stored = await update_doc(store, Post, post_id, mutate)
    except NotFound:
        return None
    return stored.doc if stored.doc.status is PostStatus.DRAFT else None


async def mark_generation_failed(
    store: Store, post_id: str, failure: GenerationFailed, *, now: datetime
) -> None:
    """``generating -> needs_attention`` with ``lastError`` (and the refused call's record), so
    a failed draft is never stranded. A post that is no longer generating is left alone."""
    stamp = to_iso(now)

    def mutate(current: Post) -> Post | None:
        if current.status is not PostStatus.GENERATING:
            return None
        target = POST_MACHINE.transition(current.status, PostStatus.NEEDS_ATTENTION)
        update: dict[str, object] = {
            "status": target,
            "status_changed_at": stamp,
            "updated_at": stamp,
            "last_error": failure.code,
        }
        if failure.record is not None:
            update["served_by"] = add_served_by(current.served_by, failure.record)
        if failure.reasons:
            update["review_only"] = True
            update["review_only_reasons"] = merge_reasons(
                current.review_only_reasons, failure.reasons
            )
        return current.model_copy(update=update)

    try:
        await update_doc(store, Post, post_id, mutate)
    except NotFound:
        return
    logger.info("posts_generation_failed", extra={"post_id": post_id, "error_code": failure.code})
