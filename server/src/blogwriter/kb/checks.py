"""Checks kb runs before saving settings: brand rendering rules, ranges and cross-field rules the
domain models leave open, and the texts screened against the blocklist.

Problems point at request fields by location (camelCase, list indexes), never by content: a
location or message never quotes what the owner typed, so a blocklisted term cannot reach a
problem response through them.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from blogwriter.domain.base import WEEKDAYS
from blogwriter.domain.common import Finding, MediaRef
from blogwriter.domain.settings import (
    BrandKit,
    CadenceSettings,
    EmployerPolicy,
    FormatPacks,
    LaneSettings,
    PillarSettings,
    WeeklyTime,
)
from blogwriter.render import BrandColors as RenderColors
from blogwriter.render import RenderBrand, contrast_ratio
from blogwriter.render.brand import AA_NORMAL_TEXT

__all__ = [
    "BRAND_CONTRAST_CODE",
    "BRAND_INCOMPLETE_CODE",
    "BRAND_MEDIA_CODE",
    "BRAND_TEXT_CODE",
    "MAX_POST_CHARS",
    "Invalid",
    "brand_texts",
    "cadence_problems",
    "format_texts",
    "invalid",
    "lane_problems",
    "lane_texts",
    "pillar_problems",
    "pillar_texts",
    "policy_texts",
    "validate_brand",
    "validation_error",
]

BRAND_CONTRAST_CODE: Final = "brand-contrast"
BRAND_TEXT_CODE: Final = "brand-text"
BRAND_MEDIA_CODE: Final = "brand-media"
BRAND_INCOMPLETE_CODE: Final = "brand-incomplete"

MAX_POST_CHARS: Final = 3000
"""LinkedIn's commentary limit (``linkedin.lint.MAX_COMMENTARY_UNITS``)."""
_MAX_RENAME_CHARS: Final = 100
_MAX_PER_WEEK: Final = 7
_MAX_QUICK_TAKE_HOURS: Final = 168
_MAX_REPURPOSE_WEEKS: Final = 104
_MAX_POLLS_PER_MONTH: Final = 31
_MAX_REPLY_MINUTES: Final = 1440
_MAX_SNAPSHOT_HOURS: Final = 720

type Loc = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class Invalid:
    """One request problem: where (``loc`` without the leading ``body``) and why."""

    loc: Loc
    msg: str
    type: str = "value_error"


def invalid(problems: Sequence[Invalid]) -> RequestValidationError:
    """The 422 ``validation_failed`` problem (core's handler) for ``problems``."""
    return RequestValidationError(
        [
            {"loc": ("body", *problem.loc), "msg": problem.msg, "type": problem.type}
            for problem in problems
        ]
    )


def validation_error(exc: ValidationError) -> RequestValidationError:
    """A document that does not validate, as a 422 on the request body (no input echoed)."""
    return RequestValidationError(
        [
            {"loc": ("body", *error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
    )


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


# --------------------------------------------------------------------------------------------
# Brand
# --------------------------------------------------------------------------------------------

_BRAND_TEXT_FIELDS: Final[Mapping[str, tuple[str, str]]] = {
    "name_mark": ("authorName", "The author name"),
    "headline": ("authorHeadline", "The headline"),
    "site_name": ("siteName", "The site name"),
    "disclaimer": ("disclaimerShort", "The short disclaimer"),
}
_MEDIA_TYPES: Final = frozenset({"image/png", "image/jpeg", "image/webp"})
_MEDIA_PREFIXES: Final = ("uploads/", "brand/")
_MEDIA_ROLES: Final[Mapping[str, frozenset[str]]] = {
    "headshot": frozenset({"headshot", "upload", "image"}),
    "name_mark": frozenset({"name_mark", "upload", "image"}),
}


def _camel(token: str) -> str:
    head, *rest = token.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _contrast_findings(brand: BrandKit) -> list[Finding]:
    colors = brand.colors
    # Unvalidated on purpose: the render package's own pair list and ratio, failures reported.
    render_colors = RenderColors.model_construct(
        ink=colors.ink,
        paper=colors.paper,
        muted=colors.muted,
        muted_on_ink=colors.muted_on_ink,
        signal=colors.signal,
        signal_text=colors.signal_text,
        rule=colors.rule,
        rule_on_ink=colors.rule_on_ink,
    )
    findings: list[Finding] = []
    for name, (foreground, background) in render_colors.text_pairs().items():
        ratio = contrast_ratio(foreground, background)
        if ratio >= AA_NORMAL_TEXT:
            continue
        fg_token, _, bg_token = name.partition(" on ")
        findings.append(
            Finding(
                code=BRAND_CONTRAST_CODE,
                severity="block",
                message=(
                    f"{fg_token} text on {bg_token} has a contrast of {ratio:.2f}:1; "
                    f"WCAG AA needs at least {AA_NORMAL_TEXT}:1."
                ),
                ref=f"{_camel(fg_token)}:{_camel(bg_token)}",
            )
        )
    return findings


def _text_findings(brand: BrandKit, disclaimer: str | None) -> list[Finding]:
    values: dict[str, str] = {"name_mark": brand.author_name or "-"}
    if brand.author_headline is not None:
        values["headline"] = brand.author_headline
    if brand.site_name is not None:
        values["site_name"] = brand.site_name
    if disclaimer is not None:
        values["disclaimer"] = disclaimer
    try:
        RenderBrand.model_validate(values)  # default colours: only the text is under test
    except ValidationError as exc:
        failed = {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
    else:
        return []
    findings: list[Finding] = []
    for key, (field, label) in _BRAND_TEXT_FIELDS.items():
        if key in failed:
            findings.append(
                Finding(
                    code=BRAND_TEXT_CODE,
                    severity="block",
                    message=f"{label} has characters that cannot be shown on slides, or is blank.",
                    ref=field,
                )
            )
    return findings


def _media_findings(field: str, ref: MediaRef | None) -> list[Finding]:
    if ref is None:
        return []
    camel = _camel(field)
    segments = ref.name.split("/")
    if (
        ref.container != "media"
        or not ref.name.startswith(_MEDIA_PREFIXES)
        or ".." in segments
        or ref.content_type not in _MEDIA_TYPES
        or ref.role not in _MEDIA_ROLES[field]
    ):
        return [
            Finding(
                code=BRAND_MEDIA_CODE,
                severity="block",
                message="Upload a PNG, JPEG or WebP image here (Library > Brand kit).",
                ref=camel,
            )
        ]
    return []


def validate_brand(brand: BrandKit, *, disclaimer: str | None) -> list[Finding]:
    """What would stop ``brand`` (with the employer's short ``disclaimer``) from rendering.

    Through ``blogwriter.render``: every text colour pair the templates use must reach WCAG AA
    (``brand-contrast``, ``ref`` = ``<foreground>:<background>`` token names), the texts must be
    plain text ``RenderBrand`` accepts (``brand-text``, ``ref`` = the field), the headshot and
    name mark must be images in the media container (``brand-media``). A missing author name is
    a warning (``brand-incomplete``): renders fail with ``brand_incomplete`` until it is set,
    text posts and the kit still work. Findings never quote the texts.
    """
    findings = _contrast_findings(brand)
    findings.extend(_text_findings(brand, disclaimer))
    findings.extend(_media_findings("headshot", brand.headshot))
    findings.extend(_media_findings("name_mark", brand.name_mark))
    if brand.author_name is None:
        findings.append(
            Finding(
                code=BRAND_INCOMPLETE_CODE,
                severity="warn",
                message="Add your name: carousels, image cards and OG images need it.",
                ref="authorName",
            )
        )
    return findings


# --------------------------------------------------------------------------------------------
# Pillars, lanes, cadence
# --------------------------------------------------------------------------------------------


def _duplicates(values: Sequence[str]) -> list[int]:
    seen: set[str] = set()
    repeated: list[int] = []
    for index, value in enumerate(values):
        key = _fold(value)
        if key in seen:
            repeated.append(index)
        seen.add(key)
    return repeated


def pillar_problems(settings: PillarSettings) -> list[Invalid]:
    """Duplicates, unbounded or chained renames and hashtags that clash across pillars (the
    LinkedIn serializer refuses approved hashtags that differ only in case)."""
    problems: list[Invalid] = []
    spelling: dict[str, str] = {}
    for index, pillar in enumerate(settings.pillars):
        base: Loc = ("pillars", index)
        for name, values in (
            ("vocabulary", pillar.vocabulary),
            ("hashtags", pillar.hashtags),
            ("researchDomains", pillar.research_domains),
        ):
            problems.extend(
                Invalid((*base, name, position), "Listed twice.")
                for position in _duplicates(values)
            )
        for position, tag in enumerate(pillar.hashtags):
            known = spelling.setdefault(_fold(tag), tag)
            if known != tag:
                problems.append(
                    Invalid(
                        (*base, "hashtags", position),
                        "Another pillar spells this hashtag with different capitals.",
                    )
                )
        sources = {_fold(source) for source in pillar.renames}
        loc: Loc = (*base, "renames")
        for old, new in pillar.renames.items():
            if not old.strip() or not new.strip():
                problems.append(Invalid(loc, "Both names are required."))
            elif max(len(old), len(new)) > _MAX_RENAME_CHARS:
                problems.append(Invalid(loc, f"Names have at most {_MAX_RENAME_CHARS} characters."))
            elif _fold(old) == _fold(new):
                problems.append(Invalid(loc, "A name cannot be renamed to itself."))
            elif _fold(new) in sources:
                problems.append(Invalid(loc, "The current name is renamed again; rename once."))
    return problems


def lane_problems(lanes: LaneSettings, pillar_ids: Collection[str]) -> list[Invalid]:
    """Ranges the lane rules must respect and series on known pillars."""
    problems: list[Invalid] = []

    def at_most(loc: Loc, value: int, limit: int) -> None:
        if value > limit:
            problems.append(Invalid(loc, f"At most {limit}."))

    def ordered(loc: Loc, low: int, high: int) -> None:
        if low > high:
            problems.append(Invalid(loc, "The minimum is above the maximum."))

    at_most(("derivativesMaxPerWeek",), lanes.derivatives_max_per_week, _MAX_PER_WEEK)
    at_most(("nativeMinPerWeek",), lanes.native_min_per_week, _MAX_PER_WEEK)
    quick = lanes.quick_take
    ordered(("quickTake", "minChars"), quick.min_chars, quick.max_chars)
    at_most(("quickTake", "maxChars"), quick.max_chars, MAX_POST_CHARS)
    at_most(("quickTake", "maxAgeHours"), quick.max_age_hours, _MAX_QUICK_TAKE_HOURS)
    formats = lanes.formats
    ordered(
        ("formats", "carouselMinSlides"), formats.carousel_min_slides, formats.carousel_max_slides
    )
    ordered(("formats", "textMinChars"), formats.text_min_chars, formats.text_max_chars)
    at_most(("formats", "textMaxChars"), formats.text_max_chars, MAX_POST_CHARS)
    at_most(("formats", "pollMaxPerMonth"), formats.poll_max_per_month, _MAX_POLLS_PER_MONTH)
    repurpose = lanes.repurpose
    ordered(("repurpose", "minWeeks"), repurpose.min_weeks, repurpose.max_weeks)
    at_most(("repurpose", "maxWeeks"), repurpose.max_weeks, _MAX_REPURPOSE_WEEKS)
    seen: set[str] = set()
    for index, series in enumerate(lanes.series):
        if series.id in seen:
            problems.append(Invalid(("series", index, "id"), "Listed twice."))
        seen.add(series.id)
        if series.pillar not in pillar_ids:
            problems.append(Invalid(("series", index, "pillar"), "Not one of your pillars."))
    return problems


def _week_minutes(weekly: WeeklyTime) -> int:
    hours, minutes = weekly.time.split(":")
    return WEEKDAYS.index(weekly.day) * 1440 + int(hours) * 60 + int(minutes)


def _day_minutes(time: str) -> int:
    hours, minutes = time.split(":")
    return int(hours) * 60 + int(minutes)


def cadence_problems(cadence: CadenceSettings) -> list[Invalid]:
    """The calendar rules of the plan the cadence model does not enforce itself: business hours
    in order, the veto notice inside them and at least ``minVetoLeadHours`` (wall clock) before
    the article publish time, one slot per time (one per day when two posts on a day block),
    modes in order (light <= normal <= max) and reminder offsets in range."""
    problems: list[Invalid] = []
    start = _day_minutes(cadence.business_hours_start)
    end = _day_minutes(cadence.business_hours_end)
    if start >= end:
        problems.append(Invalid(("businessHoursEnd",), "Business hours must end after they start."))
    veto = _day_minutes(cadence.veto_notice.time)
    if start < end and not start <= veto <= end:
        problems.append(
            Invalid(("vetoNotice", "time"), "The veto notice goes out within business hours.")
        )
    lead = (_week_minutes(cadence.article_publish) - _week_minutes(cadence.veto_notice)) % (
        7 * 1440
    )
    if lead < cadence.min_veto_lead_hours * 60:
        problems.append(
            Invalid(
                ("vetoNotice",),
                f"The veto notice must go out at least {cadence.min_veto_lead_hours} hours "
                "before the article publishes.",
            )
        )
    times: set[tuple[str, str]] = set()
    days: set[str] = set()
    for index, slot in enumerate(cadence.post_slots):
        if (slot.day, slot.time) in times:
            problems.append(Invalid(("postSlots", index), "Listed twice."))
        elif cadence.same_day_block and slot.day in days:
            problems.append(
                Invalid(
                    ("postSlots", index, "day"),
                    "Two slots on one day while two posts on a day are blocked.",
                )
            )
        times.add((slot.day, slot.time))
        days.add(slot.day)
    per_week = cadence.posts_per_week
    if not per_week.light <= per_week.normal <= per_week.max:
        problems.append(
            Invalid(("postsPerWeek",), "Posts per week must be light <= normal <= max.")
        )
    for name, values, limit in (
        ("replyMinutes", cadence.reminders.reply_minutes, _MAX_REPLY_MINUTES),
        ("snapshotHours", cadence.reminders.snapshot_hours, _MAX_SNAPSHOT_HOURS),
    ):
        seen: set[int] = set()
        for index, value in enumerate(values):
            loc: Loc = ("reminders", name, index)
            if not 1 <= value <= limit:
                problems.append(Invalid(loc, f"Between 1 and {limit}."))
            elif value in seen:
                problems.append(Invalid(loc, "Listed twice."))
            seen.add(value)
    return problems


# --------------------------------------------------------------------------------------------
# Texts screened against the blocklist (field names carry positions, never content)
# --------------------------------------------------------------------------------------------


def pillar_texts(settings: PillarSettings) -> dict[str, str]:
    """Every pillar text that reaches a model, a search (allowlists) or a post (hashtags)."""
    texts: dict[str, str] = {}
    for index, pillar in enumerate(settings.pillars):
        base = f"pillars.{index}"
        texts[f"{base}.name"] = pillar.name
        texts[f"{base}.topics"] = pillar.topics
        texts[f"{base}.description"] = pillar.description
        texts[f"{base}.audience"] = pillar.audience
        for position, term in enumerate(pillar.vocabulary):
            texts[f"{base}.vocabulary.{position}"] = term
        for position, (old, new) in enumerate(pillar.renames.items()):
            texts[f"{base}.renames.{position}.from"] = old
            texts[f"{base}.renames.{position}.to"] = new
        for position, tag in enumerate(pillar.hashtags):
            texts[f"{base}.hashtags.{position}"] = tag
        for position, domain in enumerate(pillar.research_domains):
            texts[f"{base}.researchDomains.{position}"] = domain
    return texts


def format_texts(packs: FormatPacks) -> dict[str, str]:
    texts: dict[str, str] = {}
    for index, pack in enumerate(packs.packs):
        texts[f"packs.{index}.name"] = pack.name
        for position, item in enumerate(pack.must_contain):
            texts[f"packs.{index}.mustContain.{position}"] = item
        for position, item in enumerate(pack.banned):
            texts[f"packs.{index}.banned.{position}"] = item
    return texts


def lane_texts(lanes: LaneSettings) -> dict[str, str]:
    return {f"series.{index}.title": series.title for index, series in enumerate(lanes.series)}


def brand_texts(brand: BrandKit) -> dict[str, str | None]:
    texts: dict[str, str | None] = {
        "authorName": brand.author_name,
        "authorHeadline": brand.author_headline,
        "siteName": brand.site_name,
    }
    for pillar_id, brief in sorted(brand.art_briefs.items()):
        texts[f"artBriefs.{pillar_id}"] = brief
    return texts


def policy_texts(policy: EmployerPolicy) -> dict[str, str | None]:
    texts: dict[str, str | None] = {
        "disclaimerShort": policy.disclaimer_short,
        "disclaimerLong": policy.disclaimer_long,
        "clearanceStatement": policy.clearance_statement,
    }
    for index, topic in enumerate(policy.forbidden_topics):
        texts[f"forbiddenTopics.{index}"] = topic
    return texts
