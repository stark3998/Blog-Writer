"""The three post checks that gate approval (plan: "LinkedIn Studio / Lint"):

``lint``
    Deterministic, no I/O: :func:`lint_post_text` over the display text **and** every published
    text (document title, each carousel slide, poll): ``blogwriter.linkedin.lint`` (length on
    the escaped payload, fold, links and domain-like tokens, hashtags, emoji, Unicode styling,
    bait and formula openers block; AI tells warn), the blocklist (an unconfigured blocklist is
    the hard ``blocklist-not-configured`` block), standards ids
    (``gates.validate_standard_ids``), deprecated names from the pillar renames, the slop
    detectors (warn), the carousel rules of ``blogwriter.render`` (block), the link exception,
    hashtags against the pillar, format lengths and hook rules.
``derived_claims``
    Every number in the published text appears in the material the post came from (article,
    brief, quick search, story, POV, take) with the same digits and unit; every claim maps to
    an item of that material (``Task.FACT_CHECK``, anonymized, tool-free); research-derived
    claims carry an inline attribution (publisher and title, never a URL; carousels a sources
    slide). A check that cannot run (no model, AI use not confirmed, a blocklisted text, a
    missing source) fails closed.
``near_duplicate``
    Word 5-gram Jaccard similarity against every post of the last 90 days; at or above
    ``ModelPreferences.nearDuplicateMaxSimilarity`` blocks.

Each result is a :class:`~blogwriter.domain.posts.CheckState` bound to the SHA-256 of exactly
the content that check read (:func:`lint_content_sha256`, :func:`claims_content_sha256`,
:func:`near_duplicate_content_sha256`). Approval needs all three passed and current
(:func:`failing_checks`). Findings carry an extra ``field`` (``displayText``, ``slides[2]``...)
so the composer can highlight them.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import regex
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import canonical_json, sha256_hex, to_iso
from blogwriter.domain.common import (
    MAX_STORED_FINDINGS,
    Finding,
    ServedByRecord,
    blocking,
    cap_findings,
    from_linkedin_finding,
)
from blogwriter.domain.confidentiality import blocklist_findings, forbidden_topic_findings
from blogwriter.domain.docstore import InvalidStoredDocument, find_docs, read_doc, read_singleton
from blogwriter.domain.jobflow import defer_on_brake
from blogwriter.domain.planner import WeekPlan, native_avoid_pillar
from blogwriter.domain.ports import Services, SlopChecker
from blogwriter.domain.posts import CheckState, Post
from blogwriter.domain.settings import (
    Blocklist,
    CadenceSettings,
    EmployerPolicy,
    LaneSettings,
    ModelPreferences,
    PillarSettings,
    StandardsCatalog,
)
from blogwriter.domain.states import PostStatus
from blogwriter.domain.weeks import local_date_time, plan_id, week_start
from blogwriter.gates import validate_standard_ids
from blogwriter.linkedin.lint import lint_post
from blogwriter.linkedin.posts._logging import logger
from blogwriter.linkedin.posts._material import Sources, load_sources, neutral, render_sources
from blogwriter.linkedin.posts._post import add_served_by, finding_field, located
from blogwriter.linkedin.posts._prompts import load_prompt
from blogwriter.linkedin.posts.carousel import (
    MEDIA_FORMATS,
    InvalidSpec,
    diagram_sources,
    parse_spec,
    slide_texts,
    spec_findings,
)
from blogwriter.linkedin.posts.serialize import payload_matches
from blogwriter.render.spec import CarouselSpec, SourcesSlide

__all__ = [
    "CHECK_NAMES",
    "DERIVED_CLAIMS_CHECK",
    "LINT_CHECK",
    "NEAR_DUPLICATE_CHECK",
    "NEAR_DUPLICATE_WINDOW",
    "CheckSettings",
    "ClaimCheck",
    "ClaimsOut",
    "NumberToken",
    "PollSpec",
    "check_post",
    "claims_content_sha256",
    "derived_claims_state",
    "failing_checks",
    "forbidden_topic_post_findings",
    "lane_findings",
    "lint_content_sha256",
    "lint_post_text",
    "lint_state",
    "load_check_settings",
    "near_duplicate",
    "near_duplicate_content_sha256",
    "near_duplicate_state",
    "number_tokens",
    "published_texts",
    "store_lint_state",
    "unsupported_numbers",
]

LINT_CHECK: Final = "lint"
DERIVED_CLAIMS_CHECK: Final = "derived_claims"
NEAR_DUPLICATE_CHECK: Final = "near_duplicate"
CHECK_NAMES: Final = (LINT_CHECK, DERIVED_CLAIMS_CHECK, NEAR_DUPLICATE_CHECK)
NEAR_DUPLICATE_WINDOW: Final = timedelta(days=90)
SHINGLE_WORDS: Final = 5
CAROUSEL_CAPTION_MAX: Final = 600
"""Plan: "caption of 600 characters or fewer" (the commentary of a carousel post)."""
_CLOSED: Final = frozenset(
    {PostStatus.ARCHIVED, PostStatus.DELETED, PostStatus.EXPIRED, PostStatus.GENERATING}
)
_URL: Final = regex.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()\[\]{}]+")


# --------------------------------------------------------------------------------------------
# Content hashes (each check is bound to exactly what it read)
# --------------------------------------------------------------------------------------------


def _sha(material: Mapping[str, object]) -> str:
    return sha256_hex(canonical_json(dict(material)))


def lint_content_sha256(post: Post) -> str:
    """Everything :func:`lint_post_text` reads from the post."""
    return _sha(
        {
            "v": 1,
            "displayText": post.display_text,
            "payload": post.payload,
            "documentTitle": post.document_title,
            "carouselSpec": post.carousel_spec,
            "poll": post.poll,
            "hashtags": list(post.hashtags),
            "format": post.format,
            "pillar": post.pillar,
            "lane": post.lane,
            "link": post.link_exception.url if post.link_exception is not None else None,
            "attributions": [a.model_dump(mode="json") for a in post.attributions],
            "hooks": [h.model_dump(mode="json") for h in post.hook_variants],
            "selectedHookId": post.selected_hook_id,
            "hookType": post.hook_type.value if post.hook_type is not None else None,
            "media": [
                {"role": m.role, "name": m.name, "altText": m.alt_text, "filename": m.filename}
                for m in post.media
            ],
        }
    )


def claims_content_sha256(post: Post) -> str:
    """The published text, the attributions and the references to the material."""
    extra = post.model_extra or {}
    return _sha(
        {
            "v": 1,
            "displayText": post.display_text,
            "documentTitle": post.document_title,
            "carouselSpec": post.carousel_spec,
            "poll": post.poll,
            "format": post.format,
            "lane": post.lane,
            "attributions": [a.model_dump(mode="json") for a in post.attributions],
            "sources": {
                "articleId": post.article_id,
                "briefId": post.brief_id,
                "quickSearchId": post.quick_search_id,
                "ideaId": post.idea_id,
                "storyIds": list(post.story_ids),
                "povIds": list(post.pov_ids),
                "ownerTake": extra.get("ownerTake"),
            },
        }
    )


def near_duplicate_content_sha256(post: Post) -> str:
    return _sha({"v": 1, "text": corpus_text(post)})


# --------------------------------------------------------------------------------------------
# Settings the checks read
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckSettings:
    blocklist: Blocklist
    pillars: PillarSettings
    catalog: StandardsCatalog
    lanes: LaneSettings
    preferences: ModelPreferences
    policy: EmployerPolicy


async def load_check_settings(store: Store, *, now: datetime) -> CheckSettings:
    """The settings singletons the checks need (defaults when never saved)."""
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    catalog, _ = await read_singleton(store, StandardsCatalog, now=stamp)
    lanes, _ = await read_singleton(store, LaneSettings, now=stamp)
    preferences, _ = await read_singleton(store, ModelPreferences, now=stamp)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    return CheckSettings(blocklist, pillars, catalog, lanes, preferences, policy)


# --------------------------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------------------------


class PollSpec(BaseModel):
    """A LinkedIn poll as ``post.poll`` stores it (the transport builds the API body from it)."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=140)
    options: list[str] = Field(min_length=2, max_length=4)
    duration: Literal["ONE_DAY", "THREE_DAYS", "SEVEN_DAYS", "FOURTEEN_DAYS"] = "THREE_DAYS"

    def texts(self) -> list[tuple[str, str]]:
        return [
            ("poll.question", self.question),
            *((f"poll.options[{i}]", option) for i, option in enumerate(self.options)),
        ]


def _finding(
    code: str,
    message: str,
    *,
    severity: Literal["block", "warn", "info"] = "block",
    field: str | None = None,
    start: int | None = None,
    end: int | None = None,
    ref: str | None = None,
) -> Finding:
    return located(
        Finding(code=code, severity=severity, message=message, start=start, end=end, ref=ref),
        field,
    )


def _deprecated_names(text: str, pillars: PillarSettings) -> list[Finding]:
    """Deprecated product names from the pillars' renames (the gates' rule: warn)."""
    renames: dict[str, str] = {}
    for pillar in pillars.pillars:
        renames.update(pillar.renames)
    findings: list[Finding] = []
    taken: list[tuple[int, int]] = []
    for old, new in sorted(renames.items(), key=lambda item: -len(item[0])):
        if not old.strip():
            continue
        pattern = regex.compile(rf"(?<![\w-]){regex.escape(old)}(?![\w-])", regex.IGNORECASE)
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in taken):
                continue
            taken.append((match.start(), match.end()))
            findings.append(
                Finding(
                    code="deprecated-name",
                    severity="warn",
                    message=f"Deprecated name. Use “{new[:200]}”.",
                    start=match.start(),
                    end=match.end(),
                    overridable=True,
                )
            )
    return findings


_BLOCKING_HERE: Final = frozenset({"too-many-emoji"})
"""LinkedIn lint warnings the posts lint blocks on."""


def _escalated(finding: Finding) -> Finding:
    if finding.code in _BLOCKING_HERE and finding.severity != "block":
        return finding.model_copy(update={"severity": "block"})
    return finding


def _text_findings(
    text: str,
    field: str,
    *,
    approved: Sequence[str],
    allow_links: bool,
    blocklist: Blocklist,
    slop: SlopChecker | None,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    now: datetime | None,
) -> list[Finding]:
    """The same lint for every published text: LinkedIn lint, blocklist, standards ids,
    deprecated names and the slop detectors, de-duplicated by code and span. More than two
    emoji block here (the plan's "Lint (blocks approval)": 0-2 emoji); the Phase 0 lint only
    warns. AI tells stay warnings (common words; posts have no override): an accepted
    deviation recorded in ``integration.md``."""
    found = [
        _escalated(from_linkedin_finding(f))
        for f in lint_post(text, approved_hashtags=list(approved), allow_links=allow_links)
    ]
    if blocklist.configured:
        found.extend(blocklist_findings(text, blocklist))
    found.extend(validate_standard_ids(text, catalog, now=now))
    found.extend(_deprecated_names(text, pillars))
    if slop is not None:
        found.extend(slop.check(text))
    seen: set[tuple[str, int | None, int | None]] = set()
    out: list[Finding] = []
    for finding in found:
        key = (finding.code, finding.start, finding.end)
        if key in seen:
            continue
        seen.add(key)
        out.append(located(finding, field))
    return out


def _blocklist_only(text: str, field: str, blocklist: Blocklist) -> list[Finding]:
    if not blocklist.configured or not text:
        return []
    return [located(f, field) for f in blocklist_findings(text, blocklist)]


def _link_findings(post: Post) -> list[Finding]:
    """The per-post link exception: only that URL, alone on its line (so the payload matches
    the serializer's link layout), logged for the link experiment."""
    exception = post.link_exception
    if exception is None:
        return []
    display = post.display_text
    findings = [
        _finding(
            "link-exception",
            "This post carries a link exception; it is logged for the link experiment.",
            severity="info",
            field="linkException",
        )
    ]
    used = False
    for match in _URL.finditer(display):
        url = match.group(0)
        if url != exception.url:
            findings.append(
                _finding(
                    "link-not-excepted",
                    "Only the excepted link may appear; remove this one.",
                    field="displayText",
                    start=match.start(),
                    end=match.end(),
                )
            )
            continue
        used = True
        line_start = display.rfind("\n", 0, match.start()) + 1
        line_end = display.find("\n", match.end())
        line_end = len(display) if line_end == -1 else line_end
        if display[line_start : match.start()].strip() or display[match.end() : line_end].strip():
            findings.append(
                _finding(
                    "link-own-line",
                    "Put the excepted link on a line of its own.",
                    field="displayText",
                    start=match.start(),
                    end=match.end(),
                )
            )
    if not used:
        findings.append(
            _finding(
                "link-exception-unused",
                "The link exception is set but the link is not in the text.",
                severity="warn",
                field="linkException",
            )
        )
    return findings


def _hashtag_findings(post: Post, pillars: PillarSettings) -> list[Finding]:
    pillar = pillars.get(post.pillar)
    if pillar is None:
        return [_finding("unknown-pillar", "Pick a pillar from Library > Pillars.", field="pillar")]
    approved = {tag.casefold() for tag in pillar.hashtags}
    return [
        _finding(
            "hashtag-not-approved",
            f"#{tag} is not on the pillar's approved hashtag list.",
            field="hashtags",
        )
        for tag in post.hashtags
        if tag.casefold() not in approved
    ]


def _format_findings(post: Post, lanes: LaneSettings | None) -> list[Finding]:
    findings: list[Finding] = []
    size = len(post.display_text)
    if post.format == "carousel" and size > CAROUSEL_CAPTION_MAX:
        findings.append(
            _finding(
                "caption-too-long",
                f"A carousel caption has at most {CAROUSEL_CAPTION_MAX} characters ({size}).",
                field="displayText",
            )
        )
    if lanes is not None:
        if post.format == "image_card" and not lanes.formats.image_card_enabled:
            findings.append(
                _finding("format-disabled", "Image cards are switched off in Library > Lanes.")
            )
        if post.lane == "quick_take":
            low, high = lanes.quick_take.min_chars, lanes.quick_take.max_chars
            if not low <= size <= high:
                findings.append(
                    _finding(
                        "quick-take-length",
                        f"Quick takes run {low}-{high} characters ({size}).",
                        severity="warn",
                        field="displayText",
                    )
                )
        elif post.format == "text":
            low, high = lanes.formats.text_min_chars, lanes.formats.text_max_chars
            if not low <= size <= high:
                findings.append(
                    _finding(
                        "text-length",
                        f"Text posts run {low}-{high} characters ({size}).",
                        severity="warn",
                        field="displayText",
                    )
                )
    return findings


def _hook_findings(post: Post, blocklist: Blocklist) -> list[Finding]:
    findings: list[Finding] = []
    variants = post.hook_variants
    if variants and not any(variant.uses_owner_wording for variant in variants):
        findings.append(
            _finding(
                "hooks-no-owner-wording",
                "No hook variant reuses your own words; consider one that does.",
                severity="warn",
                field="hookVariants",
            )
        )
    if post.hook_type is not None and post.hook_type.value == "question":
        findings.append(
            _finding(
                "question-hook",
                "A question hook only works when you really want the answer.",
                severity="warn",
                field="hookVariants",
            )
        )
    if post.selected_hook_id is not None and not any(
        variant.id == post.selected_hook_id for variant in variants
    ):
        findings.append(
            _finding(
                "hook-unknown",
                "The selected hook is not one of the variants.",
                severity="warn",
                field="selectedHookId",
            )
        )
    for index, variant in enumerate(variants):
        findings.extend(_blocklist_only(variant.text, f"hookVariants[{index}]", blocklist))
    return findings


def _spec_text_findings(
    post: Post,
    spec: CarouselSpec,
    *,
    blocklist: Blocklist,
    slop: SlopChecker | None,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    now: datetime | None,
) -> list[Finding]:
    findings = spec_findings(spec, card=post.format == "image_card")
    for index, text in enumerate(slide_texts(spec)):
        findings.extend(
            _text_findings(
                text,
                f"slides[{index}]",
                approved=(),
                allow_links=False,
                blocklist=blocklist,
                slop=slop,
                catalog=catalog,
                pillars=pillars,
                now=now,
            )
        )
    for index, source in diagram_sources(spec):
        findings.extend(_blocklist_only(source, f"slides[{index}].mermaid", blocklist))
    if post.format == "carousel" and post.document_title != spec.title:
        findings.append(
            _finding(
                "document-title-mismatch",
                "The document title and the carousel title differ; save the title again.",
                field="documentTitle",
            )
        )
    return findings


def lint_post_text(
    post: Post,
    *,
    hashtags: Sequence[str],
    blocklist: Blocklist,
    slop: SlopChecker | None,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    lanes: LaneSettings | None = None,
    now: datetime | None = None,
) -> list[Finding]:
    """Every deterministic finding for ``post`` (display text and every published text).

    ``hashtags`` is the approved list the payload was serialized with (``post.hashtags``).
    Without a slop checker the lint fails closed (``slop-unavailable`` blocks). ``lanes``
    adds the format and lane length rules; ``now`` pins the standards catalog's freshness.
    """
    findings: list[Finding] = []
    if not blocklist.configured:
        findings.extend(blocklist_findings("", blocklist))  # the hard not-configured block
    if slop is None:
        findings.append(
            _finding("slop-unavailable", "The slop detectors are not running; try again later.")
        )
    if not post.display_text.strip():
        findings.append(_finding("empty-text", "The post has no text yet.", field="displayText"))
    else:
        findings.extend(
            _text_findings(
                post.display_text,
                "displayText",
                approved=hashtags,
                allow_links=post.link_exception is not None,
                blocklist=blocklist,
                slop=slop,
                catalog=catalog,
                pillars=pillars,
                now=now,
            )
        )
    findings.extend(_link_findings(post))
    findings.extend(_hashtag_findings(post, pillars))
    findings.extend(_format_findings(post, lanes))
    findings.extend(_hook_findings(post, blocklist))
    if post.document_title is not None:
        findings.extend(
            _text_findings(
                post.document_title,
                "documentTitle",
                approved=(),
                allow_links=False,
                blocklist=blocklist,
                slop=None,
                catalog=catalog,
                pillars=pillars,
                now=now,
            )
        )
    elif post.format == "carousel":
        findings.append(
            _finding(
                "document-title-missing",
                "A carousel needs a document title.",
                field="documentTitle",
            )
        )
    if post.format in MEDIA_FORMATS:
        if post.carousel_spec is None:
            findings.append(
                _finding("carousel-spec-missing", "Add the slides first.", field="carouselSpec")
            )
        else:
            try:
                spec = parse_spec(post.carousel_spec)
            except InvalidSpec:
                findings.append(
                    _finding("invalid-spec", "The slides are not valid.", field="carouselSpec")
                )
            else:
                findings.extend(
                    _spec_text_findings(
                        post,
                        spec,
                        blocklist=blocklist,
                        slop=slop,
                        catalog=catalog,
                        pillars=pillars,
                        now=now,
                    )
                )
    if post.format == "poll":
        findings.extend(
            _poll_findings(
                post, blocklist=blocklist, slop=slop, catalog=catalog, pillars=pillars, now=now
            )
        )
    for index, media in enumerate(post.media):
        findings.extend(_blocklist_only(media.alt_text or "", f"media[{index}].altText", blocklist))
        findings.extend(
            _blocklist_only(media.filename or "", f"media[{index}].filename", blocklist)
        )
    for index, attribution in enumerate(post.attributions):
        field = f"attributions[{index}]"
        findings.extend(
            _blocklist_only(f"{attribution.publisher}\n{attribution.title}", field, blocklist)
        )
        if _URL.search(attribution.publisher) or _URL.search(attribution.title):
            findings.append(
                _finding("attribution-url", "Attributions never carry a URL.", field=field)
            )
    if post.display_text and not payload_matches(post):
        findings.append(
            _finding(
                "payload-mismatch",
                "The payload does not match the text; save the post again.",
                field="payload",
            )
        )
    return _ordered(findings)


def _poll_findings(
    post: Post,
    *,
    blocklist: Blocklist,
    slop: SlopChecker | None,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    now: datetime | None,
) -> list[Finding]:
    if post.poll is None:
        return [_finding("poll-missing", "A poll needs a question and options.", field="poll")]
    try:
        poll = PollSpec.model_validate(post.poll)
    except ValidationError:
        return [
            _finding(
                "poll-invalid",
                "A poll has a question of at most 140 characters and 2-4 options of at most 30.",
                field="poll",
            )
        ]
    findings: list[Finding] = []
    for field, text in poll.texts():
        findings.extend(
            _text_findings(
                text,
                field,
                approved=(),
                allow_links=False,
                blocklist=blocklist,
                slop=slop,
                catalog=catalog,
                pillars=pillars,
                now=now,
            )
        )
    return findings


_SEVERITY_ORDER: Final = {"block": 0, "warn": 1, "info": 2}


def _ordered(findings: Iterable[Finding]) -> list[Finding]:
    """Display text first, then the other fields; by position inside a field."""

    def key(finding: Finding) -> tuple[int, str, int, int]:
        field = finding_field(finding) or ""
        return (
            0 if field == "displayText" else 1,
            field,
            finding.start if finding.start is not None else -1,
            _SEVERITY_ORDER[finding.severity],
        )

    return sorted(findings, key=key)


def _state(findings: Sequence[Finding], *, content_sha256: str, now: datetime) -> CheckState:
    kept, cut = cap_findings(findings, MAX_STORED_FINDINGS)
    return CheckState(
        ran_at=to_iso(now),
        content_sha256=content_sha256,
        passed=not blocking(findings),
        findings=_ordered(kept),
        truncated=cut,
    )


def forbidden_topic_post_findings(post: Post, policy: EmployerPolicy) -> list[Finding]:
    """The employer policy's forbidden topics in every text of ``post`` readers see (display
    text, document title, slides, poll) and in its hook variants: ``forbidden-topic`` blocks
    approval (named by topic number, never quoting it). A post goes out with one tap and never
    passes gate 3, so the posts lint applies the articles' rule
    (``domain.confidentiality.forbidden_topic_findings``)."""
    if not policy.forbidden_topics:
        return []
    texts = [
        *published_texts(post),
        *((f"hookVariants[{index}]", hook.text) for index, hook in enumerate(post.hook_variants)),
    ]
    return [
        located(finding, field)
        for field, text in texts
        for finding in forbidden_topic_findings(text, policy)
    ]


def lint_state(
    post: Post,
    settings: CheckSettings,
    *,
    slop: SlopChecker | None,
    now: datetime,
    extra: Sequence[Finding] = (),
) -> CheckState:
    """The stored lint result for ``post`` (findings capped at 50, blockers kept first);
    ``extra`` adds findings that need the store (:func:`lane_findings`). The employer policy's
    forbidden topics block (:func:`forbidden_topic_post_findings`)."""
    findings = lint_post_text(
        post,
        hashtags=post.hashtags,
        blocklist=settings.blocklist,
        slop=slop,
        catalog=settings.catalog,
        pillars=settings.pillars,
        lanes=settings.lanes,
        now=now,
    )
    forbidden = forbidden_topic_post_findings(post, settings.policy)
    return _state(
        [*findings, *forbidden, *extra], content_sha256=lint_content_sha256(post), now=now
    )


async def lane_findings(
    store: Store, post: Post, *, lanes: LaneSettings, now: datetime
) -> list[Finding]:
    """Native posts go around the pillar the planner keeps them off (``lanes.nativeOtherPillar``;
    ``domain.planner.native_avoid_pillar``: the week's derivatives' pillar, else its flagship's;
    a warning: the planner places posts, the owner may still choose)."""
    if post.lane != "native" or not lanes.native_other_pillar:
        return []
    plan_key = post.week_plan_id
    if plan_key is None:
        cadence, _ = await read_singleton(store, CadenceSettings, now=to_iso(now))
        moment = post.slot.utc if post.slot is not None else to_iso(now)
        plan_key = plan_id(week_start(local_date_time(moment, cadence.timezone)[0]))
    try:
        plan = (await read_doc(store, WeekPlan, plan_key)).doc
    except NotFound:
        return []
    avoid = native_avoid_pillar(
        [(slot.lane, slot.pillar) for slot in plan.slots],
        plan.flagship.pillar if plan.flagship is not None else None,
    )
    if avoid is None or avoid != post.pillar:
        return []
    return [
        _finding(
            "native-same-pillar",
            "Native posts go on another pillar than the flagship this week promotes.",
            severity="warn",
            field="pillar",
        )
    ]


async def store_lint_state(
    services: Services, post: Post, settings: CheckSettings, *, now: datetime
) -> CheckState:
    """:func:`lint_state` with the store-backed lane rule (what routes and jobs save)."""
    extra = await lane_findings(services.store, post, lanes=settings.lanes, now=now)
    return lint_state(post, settings, slop=services.slop, now=now, extra=extra)


# --------------------------------------------------------------------------------------------
# Near-duplicates
# --------------------------------------------------------------------------------------------

_WORD: Final = regex.compile(r"[\p{L}\p{N}]+")


def _words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _shingles(words: Sequence[str], size: int = SHINGLE_WORDS) -> set[tuple[str, ...]]:
    if not words:
        return set()
    if len(words) < size:
        return {tuple(words)}
    return {tuple(words[index : index + size]) for index in range(len(words) - size + 1)}


def near_duplicate(text: str, corpus: Sequence[tuple[str, str]]) -> tuple[float, str | None]:
    """The highest word 5-gram Jaccard similarity of ``text`` to ``corpus`` (``(post id,
    text)`` pairs) and the id it was found with (``(0.0, None)`` for an empty corpus).

    Words are runs of letters and digits, case-folded; a text shorter than five words is one
    shingle.
    """
    mine = _shingles(_words(text))
    best, best_id = 0.0, None
    if not mine:
        return best, best_id
    for doc_id, other in corpus:
        theirs = _shingles(_words(other))
        if not theirs:
            continue
        score = len(mine & theirs) / len(mine | theirs)
        if score > best:
            best, best_id = score, doc_id
    return best, best_id


def corpus_text(post: Post) -> str:
    """What near-duplicates compare: the display text and the slides' text."""
    parts = [post.display_text]
    if post.carousel_spec is not None:
        with contextlib.suppress(InvalidSpec):
            parts.extend(slide_texts(parse_spec(post.carousel_spec)))
    return "\n".join(part for part in parts if part)


async def near_duplicate_state(
    store: Store, post: Post, *, threshold: float, now: datetime
) -> CheckState:
    """Compare ``post`` with every other post created or posted in the last 90 days (archived,
    deleted, expired and still-generating posts aside). An unreadable post fails the check."""
    content_sha = near_duplicate_content_sha256(post)
    cutoff = to_iso(now - NEAR_DUPLICATE_WINDOW)
    try:
        created = await find_docs(store, Post, [Filter("createdAt", ">=", cutoff)], strict=True)
        posted = await find_docs(store, Post, [Filter("postedAt", ">=", cutoff)], strict=True)
    except InvalidStoredDocument:
        finding = _finding(
            "near-duplicate-unavailable",
            "Recent posts could not all be read; the duplicate check did not run.",
        )
        return _state([finding], content_sha256=content_sha, now=now)
    corpus: dict[str, str] = {}
    for stored in (*created, *posted):
        other = stored.doc
        if other.id == post.id or other.status in _CLOSED:
            continue
        text = corpus_text(other)
        if text:
            corpus[other.id] = text
    score, other_id = near_duplicate(corpus_text(post), sorted(corpus.items()))
    findings: list[Finding] = []
    if other_id is not None and score >= threshold:
        findings.append(
            _finding(
                "near-duplicate",
                f"{round(score * 100)}% the same as a post from the last 90 days.",
                ref=other_id,
            )
        )
    return _state(findings, content_sha256=content_sha, now=now)


# --------------------------------------------------------------------------------------------
# Derived claims
# --------------------------------------------------------------------------------------------

_NUMBER: Final = regex.compile(
    r"(?<![\p{L}\p{N}_.,])"
    r"(?:[$€£]\s?)?"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.(?P<frac>\d+))?"
    r"(?:\s?(?P<unit>%|percent\b|per\s?cent\b|million\b|billion\b|thousand\b|bn\b|[kKmMbB]\b|x\b))?"
    r"(?![\p{L}\p{N}_])"
)
_LIST_MARKER: Final = regex.compile(r"(?m)^[ \t]*(?P<n>\d{1,2})[.)](?=\s)")
_UNITS: Final = {
    "%": "%",
    "percent": "%",
    "k": "k",
    "thousand": "k",
    "m": "m",
    "million": "m",
    "b": "b",
    "bn": "b",
    "billion": "b",
    "x": "x",
}


@dataclass(frozen=True, slots=True)
class NumberToken:
    """A number as written (``start``/``end`` into the text) and its canonical form: digits
    without grouping commas, the fraction, and the unit (``%``, ``k``, ``m``, ``b``, ``x``)."""

    canonical: str
    start: int
    end: int


def _canonical(integer: str, fraction: str | None, unit: str | None) -> str:
    whole = str(int(integer.replace(",", "")))
    value = f"{whole}.{fraction}" if fraction else whole
    folded = "".join(unit.casefold().split()) if unit is not None else ""
    return value + _UNITS.get(folded, "")


def number_tokens(text: str) -> list[NumberToken]:
    """Every number in ``text`` except list markers at the start of a line and digits inside
    words or identifiers (``LLM01``, ``ASI03``)."""
    markers = {match.start("n") for match in _LIST_MARKER.finditer(text)}
    tokens: list[NumberToken] = []
    for match in _NUMBER.finditer(text):
        if match.start("int") in markers:
            continue
        tokens.append(
            NumberToken(
                canonical=_canonical(match.group("int"), match.group("frac"), match.group("unit")),
                start=match.start(),
                end=match.end(),
            )
        )
    return tokens


def unsupported_numbers(texts: Sequence[tuple[str, str]], sources: Iterable[str]) -> list[Finding]:
    """A block for every number in ``texts`` (``(field, text)``) that no source states with
    the same digits and unit (``2,000`` and ``2000`` are the same; ``40`` and ``40%`` are
    not)."""
    known = {token.canonical for source in sources for token in number_tokens(source)}
    findings: list[Finding] = []
    for field, text in texts:
        for token in number_tokens(text):
            if token.canonical in known:
                continue
            findings.append(
                _finding(
                    "unsupported-number",
                    "This number is not in the material the post came from. Use the "
                    "source's figure exactly or remove it.",
                    field=field,
                    start=token.start,
                    end=token.end,
                )
            )
    return findings


def published_texts(post: Post) -> list[tuple[str, str]]:
    """``(field, text)`` of everything readers see: display text, document title, slides, poll."""
    texts: list[tuple[str, str]] = [("displayText", post.display_text)]
    if post.document_title:
        texts.append(("documentTitle", post.document_title))
    if post.carousel_spec is not None and post.format in MEDIA_FORMATS:
        try:
            spec = parse_spec(post.carousel_spec)
        except InvalidSpec:
            pass
        else:
            texts.extend((f"slides[{i}]", text) for i, text in enumerate(slide_texts(spec)))
    if post.format == "poll" and post.poll is not None:
        with contextlib.suppress(ValidationError):
            texts.extend(PollSpec.model_validate(post.poll).texts())
    return [(field, text) for field, text in texts if text.strip()]


class ClaimCheck(BaseModel):
    """One checkable claim of the post, as the checker maps it."""

    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=400)
    """The claim copied exactly from the post."""
    supported: bool
    source: str | None = Field(default=None, max_length=200)
    """The ``ref`` of the supporting item."""
    citation_id: str | None = Field(default=None, max_length=64)
    research: bool = False
    """The fact comes from research (brief or quick search), not the writer's own material."""


class ClaimsOut(BaseModel):
    """The checker's structured output."""

    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimCheck] = Field(default_factory=list[ClaimCheck], max_length=40)


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _locate(
    quote: str, texts: Sequence[tuple[str, str]]
) -> tuple[str | None, int | None, int | None]:
    for field, text in texts:
        index = text.find(quote)
        if index >= 0:
            return field, index, index + len(quote)
    return (texts[0][0] if texts else None), None, None


def _sources_slide(spec: CarouselSpec | None) -> str:
    if spec is None:
        return ""
    parts: list[str] = []
    for slide in spec.slides:
        if isinstance(slide, SourcesSlide):
            parts.extend(f"{source.publisher} {source.title}" for source in slide.sources)
    return _normalized("\n".join(parts))


def attribution_findings(post: Post) -> list[Finding]:
    """Inline attribution of research-derived claims: the publisher is named in the text (a
    carousel: on its sources slide or in the caption); the title should be (warn)."""
    if not post.attributions:
        return []
    spec: CarouselSpec | None = None
    if post.format == "carousel" and post.carousel_spec is not None:
        try:
            spec = parse_spec(post.carousel_spec)
        except InvalidSpec:
            spec = None
    findings: list[Finding] = []
    if post.format == "carousel" and (
        spec is None or not any(isinstance(s, SourcesSlide) for s in spec.slides)
    ):
        findings.append(
            _finding(
                "sources-slide-missing",
                "A carousel that uses research ends with a sources slide (publisher and title).",
                field="carouselSpec",
            )
        )
    visible = _normalized(post.display_text) + "\n" + _sources_slide(spec)
    for index, attribution in enumerate(post.attributions):
        field = f"attributions[{index}]"
        if _normalized(attribution.publisher) not in visible:
            findings.append(
                _finding(
                    "attribution-not-inline",
                    "Name the source's publisher where the claim is made (no link).",
                    field=field,
                    ref=attribution.citation_id,
                )
            )
        elif _normalized(attribution.title) not in visible:
            findings.append(
                _finding(
                    "attribution-title-missing",
                    "Name the source's title too, as the attribution gives it.",
                    severity="warn",
                    field=field,
                    ref=attribution.citation_id,
                )
            )
    return findings


def _claim_findings(post: Post, out: ClaimsOut, sources: Sources) -> list[Finding]:
    texts = published_texts(post)
    attributed = {a.citation_id for a in post.attributions if a.citation_id is not None}
    findings: list[Finding] = []
    for claim in out.claims:
        field, start, end = _locate(claim.quote, texts)
        if not claim.supported:
            findings.append(
                _finding(
                    "unsupported-claim",
                    "This claim does not match the article, brief or a story entry exactly.",
                    field=field,
                    start=start,
                    end=end,
                )
            )
            continue
        if not claim.research:
            continue
        citation_id = claim.citation_id
        if citation_id is None or citation_id not in sources.citations:
            findings.append(
                _finding(
                    "attribution-missing",
                    "This research claim has no source to attribute; check it against the brief.",
                    field=field,
                    start=start,
                    end=end,
                )
            )
        elif citation_id not in attributed:
            findings.append(
                _finding(
                    "attribution-missing",
                    "Attribute this research claim inline (publisher and title, no link).",
                    field=field,
                    start=start,
                    end=end,
                    ref=citation_id,
                )
            )
    return findings


def _fact_check_request(post: Post, sources: Sources) -> Request:
    prompt = load_prompt("fact_check")
    texts = published_texts(post)
    post_block = "\n\n".join(
        f'<text field="{field}">\n{neutral(text)}\n</text>' for field, text in texts
    )
    return Request(
        system=prompt.text,
        messages=(
            Message.user(
                f"<sources>\n{render_sources(sources)}\n</sources>",
                f"<post>\n{post_block}\n</post>",
            ),
        ),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=ClaimsOut,
    )


async def derived_claims_state(
    services: Services,
    post: Post,
    settings: CheckSettings,
    *,
    origin: Origin,
    job_id: str | None,
    now: datetime,
) -> tuple[CheckState, ServedByRecord | None]:
    """The derived-claims check and the ``servedBy`` record of its model call (if one ran).

    Fails closed: a source that cannot be read, a blocklist hit or an unconfigured blocklist,
    AI use not confirmed and a missing or refusing model are blocking findings. Background
    calls pause on the spend brake (``JobDeferred``).
    """
    content_sha = claims_content_sha256(post)
    texts = published_texts(post)
    if not post.display_text.strip():
        empty = _finding("empty-text", "The post has no text yet.", field="displayText")
        return _state([empty], content_sha256=content_sha, now=now), None
    sources = await load_sources(services.store, post)
    findings: list[Finding] = [
        _finding("source-missing", "A source of this post no longer exists.", ref=ref)
        for ref in sources.missing
    ]
    findings.extend(unsupported_numbers(texts, sources.texts))
    findings.extend(attribution_findings(post))
    record: ServedByRecord | None = None
    blocklist = settings.blocklist
    outgoing = [text for _, text in texts] + sources.texts
    if not blocklist.configured:
        findings.extend(blocklist_findings("", blocklist))
    elif any(blocklist_findings(text, blocklist) for text in outgoing):
        findings.append(
            _finding(
                "claims-blocklist-hit",
                "The post or its material matches the blocklist, so the claims were not "
                "checked. Remove the blocklisted text first.",
            )
        )
    elif not settings.policy.ai_use_confirmed:
        findings.append(
            _finding(
                "ai-use-not-confirmed",
                "Confirm the employer AI-use policy in Settings > Policy to check claims.",
            )
        )
    elif services.ai is None:
        findings.append(
            _finding("claims-unavailable", "The claim checker is not configured; try later.")
        )
    else:
        request = _fact_check_request(post, sources)
        with defer_on_brake(now):
            result = await services.ai.run(Task.FACT_CHECK, request, origin=origin, job_id=job_id)
        refused = result.refused or bool(result.refusals)
        record = ServedByRecord.from_served_by(
            result.served_by,
            step=DERIVED_CLAIMS_CHECK,
            at=to_iso(now),
            refused=refused,
            cost_usd=float(result.cost_usd),
            prompt_version=load_prompt("fact_check").version,
        )
        if result.refused or not isinstance(result.value, ClaimsOut):
            findings.append(
                _finding(
                    "claims-check-failed",
                    "The claim checker did not answer; run the check again.",
                )
            )
        else:
            findings.extend(_claim_findings(post, result.value, sources))
    logger.info(
        "posts_claims_checked",
        extra={"post_id": post.id, "findings_count": len(findings)},
    )
    return _state(findings, content_sha256=content_sha, now=now), record


# --------------------------------------------------------------------------------------------
# All checks
# --------------------------------------------------------------------------------------------


async def check_post(services: Services, post: Post, *, origin: Origin, job_id: str | None) -> Post:
    """``post`` with fresh ``lint``, ``derivedClaims`` and ``nearDuplicate`` results (and the
    checker's ``servedBy`` record). Nothing is written: the caller saves what is still current
    (:func:`blogwriter.linkedin.posts.jobs` compares the content hashes)."""
    now = services.clock.now()
    settings = await load_check_settings(services.store, now=now)
    lint = await store_lint_state(services, post, settings, now=now)
    near = await near_duplicate_state(
        services.store,
        post,
        threshold=settings.preferences.near_duplicate_max_similarity,
        now=now,
    )
    claims, record = await derived_claims_state(
        services, post, settings, origin=origin, job_id=job_id, now=now
    )
    update: dict[str, object] = {"lint": lint, "near_duplicate": near, "derived_claims": claims}
    if record is not None:
        update["served_by"] = add_served_by(post.served_by, record)
    return post.model_copy(update=update)


def failing_checks(post: Post) -> list[str]:
    """The checks that stop approval: missing, stale (the content changed since they ran) or
    failed. Empty: every check passed on exactly the current content."""
    expected = {
        LINT_CHECK: (post.lint, lint_content_sha256(post)),
        DERIVED_CLAIMS_CHECK: (post.derived_claims, claims_content_sha256(post)),
        NEAR_DUPLICATE_CHECK: (post.near_duplicate, near_duplicate_content_sha256(post)),
    }
    return [
        name
        for name, (state, sha) in expected.items()
        if state is None or state.content_sha256 != sha or not state.passed
    ]
