"""What an illustration is planned from, and the requests the job sends (pure, no I/O but the
settings reads in :func:`load_subject`).

**Public data only.** The plan sees the article's title, excerpt, pillar name and section
headings, the pillar's art brief, the brand palette and the owner's short direction: never the
body, stories, POVs, interview answers or anything else anonymized or private. Every request is
``Sensitivity.PUBLIC`` (the ``image`` route's ceiling: the policy refuses anything else before a
call) and is screened against the blocklist before it is sent (:meth:`Subject.texts`).

Every image prompt is the plan's description plus fixed rules added in code (the art brief, the
palette, the format, and no text, logos, faces or unsafe content), so a plan that forgets a rule
cannot drop it. The one automatic retry uses a simpler, more abstract prompt
(:attr:`~blogwriter.illustrations.models.PlannedImage.retry_prompt`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from blogwriter.ai.types import ImagePart, Message, Request, Sensitivity, TextPart
from blogwriter.articles import markdown_headings
from blogwriter.db import Store
from blogwriter.domain.articles import MAX_ILLUSTRATION_ALT, Article, IllustrationRole
from blogwriter.domain.settings import BrandColors, BrandKit, Pillar, PillarSettings
from blogwriter.illustrations.models import PlannedImage, PlanOut, VerdictOut
from blogwriter.illustrations.prompting import CHECK, PLAN, fenced, load_prompt
from blogwriter.kb import art_brief_for, default_art_brief, read_settings

__all__ = [
    "HERO_SIZE",
    "IMAGE_RULES",
    "INLINE_SIZE",
    "MAX_HEADINGS",
    "PlanInvalid",
    "Subject",
    "check_request",
    "clip",
    "image_prompt",
    "load_subject",
    "palette",
    "plain_heading",
    "plan_request",
    "retry_prompt",
    "shape_plan",
]

HERO_SIZE: Final = "1536x1024"
INLINE_SIZE: Final = "1024x1024"
MAX_HEADINGS: Final = 16
MAX_DESCRIPTION: Final = 900
MAX_CONCEPT: Final = 200
MAX_ALT: Final = 200
IMAGE_RULES: Final = (
    "Strict rules: no text of any kind (no words, letters, numbers, labels, captions, signs, "
    "screens or documents with writing, watermarks or signatures); no logos, brand marks, "
    "product names or company branding; no faces and no recognisable real people; nothing "
    "violent, sexual or unsettling."
)
"""Appended to every image prompt in code."""

_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_LINK_RE: Final = re.compile(r"!?\[((?:\\.|[^\]\\])*)\]\([^)]*\)")
_MARKUP_RE: Final = re.compile(r"[`*_~]")
_INLINE_RE: Final = re.compile(r"\\(.)|[`*_~]")
_FALLBACK_PILLAR: Final = Pillar(id="article", name="the article's idea")


class PlanInvalid(ValueError):
    """The plan holds no usable hero illustration."""


def clip(text: str, limit: int) -> str:
    """One plain line of at most ``limit`` characters (cut at a word when one is near)."""
    flat = " ".join(_CONTROL_RE.sub(" ", text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def plain_heading(text: str) -> str:
    """A Markdown heading as the reader sees it: links become their text, emphasis and code
    marks go, escaped characters stay as the characters."""
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_RE.sub(lambda match: match.group(1) or "", text)
    return " ".join(text.split())


def _key(text: str) -> str:
    """A heading for comparison: markup, backslashes and case ignored."""
    return " ".join(_MARKUP_RE.sub("", plain_heading(text)).replace("\\", "").split()).casefold()


def palette(colors: BrandColors) -> str:
    """The brand palette as the image model reads it (hex values)."""
    return (
        f"deep {colors.ink}, slate {colors.muted}, soft {colors.muted_on_ink}, "
        f"light {colors.rule}, ground {colors.paper}, accent {colors.signal} (sparingly)"
    )


@dataclass(frozen=True, slots=True)
class Subject:
    """The public material one article's illustrations are planned from."""

    title: str
    excerpt: str | None
    pillar: str | None
    headings: tuple[str, ...]
    art_brief: str
    palette: str
    direction: str | None

    def texts(self) -> dict[str, str]:
        """Every text that reaches a model, by field (for the blocklist screen)."""
        texts: dict[str, str] = {"title": self.title, "artBrief": self.art_brief}
        if self.excerpt:
            texts["excerpt"] = self.excerpt
        if self.pillar:
            texts["pillar"] = self.pillar
        if self.direction:
            texts["direction"] = self.direction
        for index, heading in enumerate(self.headings):
            texts[f"headings[{index}]"] = heading
        return texts


async def load_subject(
    store: Store, article: Article, *, direction: str | None, now: str
) -> Subject:
    """The article's public material: its metadata and headings (never its body), the
    pillar's art brief (or the default) and the brand palette. ``article.meta.title`` must be
    set (callers refuse without it)."""
    title = article.meta.title
    if title is None:
        raise ValueError("an illustrated article needs a title")
    pillars, _ = await read_settings(store, PillarSettings, now=now)
    brand, _ = await read_settings(store, BrandKit, now=now)
    pillar = pillars.get(article.meta.pillar) if article.meta.pillar else None
    brief = (
        art_brief_for(brand, pillar) if pillar is not None else default_art_brief(_FALLBACK_PILLAR)
    )
    headings: list[str] = []
    for raw in markdown_headings(article.markdown):
        heading = clip(plain_heading(raw), 200)
        if heading and heading not in headings:
            headings.append(heading)
    cleaned_direction = clip(direction, 300) if direction else None
    return Subject(
        title=clip(title, 200),
        excerpt=clip(article.meta.excerpt, 400) if article.meta.excerpt else None,
        pillar=clip(pillar.name, 80) if pillar is not None else None,
        headings=tuple(headings[:MAX_HEADINGS]),
        art_brief=clip(brief, 500),
        palette=palette(brand.colors),
        direction=cleaned_direction or None,
    )


def plan_request(subject: Subject, count: int) -> Request:
    """PLAN: one hero and ``count - 1`` inline illustrations (public data, tool-free)."""
    if not 1 <= count <= 3:
        raise ValueError("plan 1 to 3 illustrations")
    lines = [f"Title: {subject.title}"]
    if subject.excerpt:
        lines.append(f"Excerpt: {subject.excerpt}")
    if subject.pillar:
        lines.append(f"Pillar: {subject.pillar}")
    lines.append("Section headings:")
    lines.extend(f"- {heading}" for heading in subject.headings or ("(none yet)",))
    inline = count - 1
    ask = (
        "Plan 1 hero illustration and no inline illustrations."
        if inline == 0
        else f"Plan 1 hero illustration and {inline} inline illustration"
        f"{'s' if inline > 1 else ''}, each after a different section heading from the list."
    )
    parts = [
        ask,
        fenced("article", "\n".join(lines)),
        fenced("art_brief", f"{subject.art_brief}\nPalette: {subject.palette}."),
    ]
    if subject.direction:
        parts.append(fenced("direction", subject.direction))
    return Request(
        system=load_prompt(PLAN).text,
        messages=(Message.user("\n\n".join(parts)),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=PlanOut,
    )


def _format(role: IllustrationRole) -> str:
    if role == "hero":
        return "a wide landscape banner (3:2) shown above the article title"
    return "a square figure placed inside the article"


def image_prompt(description: str, subject: Subject, role: IllustrationRole) -> str:
    """The prompt sent to the image model: the plan's description plus the fixed rules."""
    return "\n\n".join(
        [
            clip(description, MAX_DESCRIPTION),
            f"Style: {subject.art_brief}",
            f"Palette: {subject.palette}.",
            f"Format: {_format(role)}; an editorial illustration.",
            IMAGE_RULES,
        ]
    )


def retry_prompt(description: str, concept: str, subject: Subject, role: IllustrationRole) -> str:
    """The prompt of the one automatic retry: simpler and more abstract, the concept stated
    plainly, and the same fixed rules."""
    adjust = (
        "Second attempt: the first image was rejected. Make it simpler and more abstract: fewer "
        "objects, larger shapes, plain surfaces that cannot carry writing (no screens, papers, "
        "signs, labels or interfaces), no human figures or faces, no logos. The main subject "
        f"must show this idea unmistakably: {clip(concept, MAX_CONCEPT)}"
    )
    return "\n\n".join([adjust, image_prompt(description, subject, role)])


def _section(raw: str, subject: Subject, used: set[str]) -> str | None:
    wanted = _key(raw)
    if not wanted:
        return None
    for heading in subject.headings:
        if _key(heading) == wanted and heading not in used:
            used.add(heading)
            return heading
    return None


def shape_plan(plan: PlanOut, subject: Subject, count: int) -> list[PlannedImage]:
    """The plan as at most ``count`` images, hero first: lengths trimmed, inline sections
    matched to real headings (``None`` otherwise), the fixed rules added to every prompt.
    Raises :class:`PlanInvalid` without a usable hero."""
    items = list(plan.illustrations)
    if not items:
        raise PlanInvalid("the plan is empty")
    hero = next((item for item in items if item.role == "hero"), items[0])
    others = [item for item in items if item is not hero][: max(count - 1, 0)]
    used: set[str] = set()
    planned: list[PlannedImage] = []
    for item in [hero, *others]:
        role: IllustrationRole = "hero" if not planned else "inline"
        concept = clip(item.concept or item.alt_text, MAX_CONCEPT)
        description = clip(item.prompt or concept, MAX_DESCRIPTION)
        if not concept or not description:
            if role == "hero":
                raise PlanInvalid("the hero has no concept")
            continue
        alt = clip(item.alt_text or concept, min(MAX_ALT, MAX_ILLUSTRATION_ALT))
        size: Literal["1536x1024", "1024x1024"] = HERO_SIZE if role == "hero" else INLINE_SIZE
        planned.append(
            PlannedImage(
                index=len(planned),
                role=role,
                size=size,
                concept=concept,
                prompt=image_prompt(description, subject, role),
                retry_prompt=retry_prompt(description, concept, subject, role),
                alt_text=alt,
                section=_section(item.section, subject, used) if role == "inline" else None,
            )
        )
    return planned


def check_request(concept: str, image: ImagePart) -> Request:
    """FACT_CHECK with the image: does it show ``concept``, with no text, logos, recognisable
    people or unsafe content? Public data (the generated image and its planned concept)."""
    text = "\n\n".join(
        [
            fenced("concept", clip(concept, MAX_CONCEPT)),
            "Check the attached image against the concept and every rule.",
        ]
    )
    return Request(
        system=load_prompt(CHECK).text,
        messages=(Message("user", (TextPart(text), image)),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=VerdictOut,
    )
