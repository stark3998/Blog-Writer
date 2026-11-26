"""Deterministic lint for LinkedIn post text, run before a post can be approved.

The input is the **display text**: what the owner sees in the composer and what the kit copies.
Every finding has a severity:

``block``
    Approval is refused until the text is fixed.
``warn``
    Shown to the owner; approval is still allowed.

Offsets (``start``/``end``) are code-point indexes into the display text, so the UI can highlight
the span.

Length is counted on the **escaped API payload**
(:func:`~blogwriter.linkedin.littletext.api_commentary`), in UTF-16 code units. LinkedIn
documents a 3,000-character limit but not how it counts. Counting escapes and astral characters
(emoji) as two units is the conservative choice until the live Phase 0 test settles it.

The hook check models LinkedIn's "...see more" fold on mobile:
- The fold is at about 140 characters or 3 rendered lines, whichever comes first.
- Blank lines count as lines.
- A rendered line wraps every :data:`MOBILE_LINE_CHARS` characters (a 360 px-wide phone at
  LinkedIn's body size; a heuristic).
- The hook is the first line of the post. It must end before the fold.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import regex

from blogwriter.linkedin.littletext import (
    Hashtag,
    LittleTextError,
    Token,
    api_commentary,
    parse_display,
)

__all__ = [
    "AI_TELLS",
    "ENGAGEMENT_BAIT",
    "HOOK_MAX_CHARS",
    "HOOK_MAX_LINES",
    "MAX_COMMENTARY_UNITS",
    "MAX_EMOJI",
    "MAX_HASHTAGS",
    "MOBILE_LINE_CHARS",
    "Finding",
    "Severity",
    "has_blocking",
    "lint_post",
]


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    message: str
    start: int
    end: int


#: LinkedIn's documented commentary limit, applied to the escaped payload in UTF-16 units.
MAX_COMMENTARY_UNITS: Final = 3000
#: About 140 characters are visible before "...see more" on mobile.
HOOK_MAX_CHARS: Final = 140
#: LinkedIn shows about 3 rendered lines before the fold on mobile.
HOOK_MAX_LINES: Final = 3
#: Heuristic characters per rendered mobile line.
MOBILE_LINE_CHARS: Final = 48
MAX_HASHTAGS: Final = 3
MAX_EMOJI: Final = 2

# --------------------------------------------------------------------------------------------
# Links. LinkedIn auto-links URLs and words that look like domains (README.md, x.ai, foo.io).
# Domain-like means one or more labels, then a known TLD, at a word boundary. Ordinary prose
# (e.g., i.e., U.S., etc., version numbers, decimals, times) never ends in one of these TLDs, so it
# doesn't match.
# --------------------------------------------------------------------------------------------
_URL: Final = regex.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()\[\]{}]+")
# The lookbehind pins the start of the local part, keeping the scan linear.
_EMAIL: Final = regex.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_TLDS: Final = (
    "com|org|net|io|ai|dev|app|co|me|ly|so|sh|md|py|gg|tv|cloud|tech|info|biz|xyz|us|uk|in|de|"
    "gov|edu|mil|eu|ca|au|security|cyber|tools|run|page|site|online|blog|news"
)
_DOMAIN_LIKE: Final = regex.compile(
    rf"(?i)(?<![\w@/.-])(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{_TLDS})(?![\w-]|\.[a-z0-9])"
)
# Common file extensions that are not TLDs. LinkedIn probably does not link them; warn so the
# owner can rephrase.
_FILENAME_LIKE: Final = regex.compile(
    r"(?i)(?<![\w@/.-])[a-z0-9_-]+\.(?:json|ya?ml|txt|html?|toml|ini|cfg|conf|env|ps1|bat|exe|zip|tf|hcl|csv|xlsx|pdf)(?![\w-])"
)

# --------------------------------------------------------------------------------------------
# Characters.
# --------------------------------------------------------------------------------------------
_GRAPHEME: Final = regex.compile(r"\X")
_EMOJI_CLUSTER: Final = regex.compile(
    r"^(?:\p{Extended_Pictographic}|\p{Regional_Indicator}{2}|[0-9#*]\uFE0F?\u20E3)"
)
_MATH_ALNUM: Final = regex.compile(r"[\U0001D400-\U0001D7FF]+")
_IGNORABLE: Final = regex.compile(
    r"[\p{Default_Ignorable_Code_Point}\u00AD\u115F\u1160\u3164\uFFA0]"
)
# Inside an emoji cluster these are part of the emoji, not hidden text.
_EMOJI_SAFE_IGNORABLE: Final = frozenset("\u200d\ufe0f\ufe0e") | frozenset(
    chr(c) for c in range(0xE0020, 0xE0080)
)
_FULLWIDTH_HASH: Final = regex.compile(r"\uFF03\p{L}+")
_UNAPPROVED_HASHTAG: Final = regex.compile(r"(?<![^\s])#(\p{L}[\p{L}\p{M}\p{Nd}]*)")

# --------------------------------------------------------------------------------------------
# Phrases. Matched case-insensitively, with straight and curly apostrophes treated as equal.
# --------------------------------------------------------------------------------------------
#: Engagement bait LinkedIn demotes. Always blocks.
ENGAGEMENT_BAIT: Final[tuple[str, ...]] = (
    # Imperatives only count at the start of a sentence or line, so prose like "it looks like
    # if..." or "save this setting" never matches.
    r"(?:^|(?<=[.!?:])\s+)comment\s+[\"'\u201c\u2018]?yes\b",
    r"(?:^|(?<=[.!?:])\s+)type\s+[\"'\u201c\u2018]?yes\b",
    r"(?:^|(?<=[.!?:])\s+)save\s+this(?:\s+(?:post|for\s+later))?\s*(?:[.!]|$)",
    r"(?:^|(?<=[.!?:])\s+)follow\s+(?:me\s+)?for\s+more\b",
    r"(?:^|(?<=[.!?:])\s+)(?:repost|like|share)\s+if\b",
    r"(?:^|(?<=[.!?:])\s+)tag\s+(?:someone|a\s+friend|a\s+colleague)\b",
    r"(?:^|(?<=[.!?:])\s+)drop\s+(?:a\s+comment|your\s+thoughts)\b",
    r"(?:^|(?<=[.!?:])\s+)smash\s+that\b",
    r"\bagree\?",
)

#: Formula openers, checked at the start of the post.
_OPENERS: Final[tuple[str, ...]] = (
    r"unpopular\s+opinion\b",
    r"here(?:'s|\s+is)\s+the\s+thing\b",
    r"nobody\s+(?:is\s+)?talk(?:s|ing)\s+about\b",
    r"most\s+people\s+get\s+.{1,60}?\s+wrong\b",
    r"[\w .#+-]{1,40}?\s+is\s+dead(?=\s*(?:[.!:,]|$))",  # "X is dead." not "is dead weight"
    r"let\s+that\s+sink\s+in\b",
)
#: Clichés that block anywhere in the post.
_ANYWHERE_BLOCK: Final[tuple[str, ...]] = (r"let\s+that\s+sink\s+in\b",)

#: AI tells, ported from backend/prompts/humanizer_prompt.md (lines 20-37). They only warn.
#: "critical" is left out on purpose: in security writing it is a real severity level.
AI_TELLS: Final[tuple[str, ...]] = (
    r"let's\s+dive\s+in",
    r"let's\s+explore",
    r"let's\s+break\s+(?:this|it)\s+down",
    r"in\s+today's\s+rapidly\s+evolving",
    r"in\s+the\s+ever[-\s]changing\s+landscape",
    r"game[-\s]chang(?:er|ing)",
    r"revolutionary",
    r"groundbreaking",
    r"paradigm(?:\s+shift)?",
    r"landscape",
    r"leverag(?:e|es|ed|ing)",
    r"utiliz(?:e|es|ed|ing)",
    r"harness(?:es|ed|ing)?",
    r"it's\s+worth\s+noting",
    r"it's\s+important\s+to\s+note",
    r"notably",
    r"crucial",
    r"vital",
    r"essential",
    r"robust",
    r"seamless(?:ly)?",
    r"comprehensive",
    r"cutting[-\s]edge",
    r"state[-\s]of[-\s]the[-\s]art",
    r"empower(?:s|ed|ing)?",
    r"elevat(?:e|es|ed|ing)",
    r"unlock\s+the\s+power\s+of",
    r"supercharg(?:e|es|ed|ing)",
    r"here's\s+why\s+this\s+matters",
    r"i'm\s+(?:so\s+)?excited\s+to\s+share",
    r"thrilled\s+to\s+announce",
)

_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})


def _phrase(pattern: str) -> regex.Pattern[str]:
    return regex.compile(rf"(?i)(?<![\p{{L}}\p{{N}}]){pattern}(?![\p{{L}}\p{{N}}])")


_BAIT_RE: Final = tuple(regex.compile(rf"(?im){p}") for p in ENGAGEMENT_BAIT)
_OPENER_RE: Final = tuple(regex.compile(rf"(?i)^[\s\W]{{0,4}}{p}") for p in _OPENERS)
_ANYWHERE_RE: Final = tuple(_phrase(p) for p in _ANYWHERE_BLOCK)
_AI_TELL_RE: Final = tuple(_phrase(p) for p in AI_TELLS)


def lint_post(
    display: str, *, approved_hashtags: Iterable[str], allow_links: bool = False
) -> list[Finding]:
    """Return every finding for ``display``, sorted by position."""
    approved = list(approved_hashtags)
    findings: list[Finding] = []

    try:
        tokens = parse_display(display, approved)
        payload = api_commentary(tokens)
    except LittleTextError as exc:
        return [Finding("invalid-text", Severity.BLOCK, str(exc), 0, len(display))]

    units = len(payload.encode("utf-16-le")) // 2
    if units > MAX_COMMENTARY_UNITS:
        findings.append(
            Finding(
                "too-long",
                Severity.BLOCK,
                f"Escaped payload is {units} UTF-16 units; the limit is {MAX_COMMENTARY_UNITS}.",
                0,
                len(display),
            )
        )

    if display[:1].isspace():
        end = len(display) - len(display.lstrip())
        findings.append(
            Finding(
                "leading-blank",
                Severity.BLOCK,
                "Post starts with whitespace or a blank line.",
                0,
                end,
            )
        )

    findings.extend(_hook(display))
    findings.extend(_links(display, allow_links=allow_links))
    findings.extend(_hashtags(display, tokens))
    findings.extend(_characters(display))
    findings.extend(_phrases(display))
    return sorted(findings, key=lambda f: (f.start, f.end, f.code))


def has_blocking(findings: Iterable[Finding]) -> bool:
    return any(f.severity is Severity.BLOCK for f in findings)


# --------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------


def fold_index(display: str) -> int:
    """Index where the mobile "...see more" fold falls (see the module docstring)."""
    limit = min(HOOK_MAX_CHARS, len(display))
    lines = 0
    column = 0
    for index, char in enumerate(display[:limit]):
        if char == "\n":
            lines += 1
            column = 0
        else:
            column += 1
            if column > MOBILE_LINE_CHARS:
                lines += 1
                column = 1
        if lines >= HOOK_MAX_LINES:
            return index
    return limit


def _hook(display: str) -> list[Finding]:
    text = display.lstrip()
    offset = len(display) - len(text)
    hook_end = offset + (text.find("\n") if "\n" in text else len(text))
    fold = fold_index(display)
    if hook_end > fold:
        return [
            Finding(
                "hook-past-fold",
                Severity.WARN,
                f"The first line runs past the mobile fold (about {HOOK_MAX_CHARS} characters "
                f"or {HOOK_MAX_LINES} lines); readers see it cut off.",
                fold,
                hook_end,
            )
        ]
    return []


def _links(display: str, *, allow_links: bool) -> list[Finding]:
    findings: list[Finding] = []
    covered: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in covered)

    for pattern, code, message in (
        (_URL, "external-link", "External link"),
        (_EMAIL, "email-address", "Email address (LinkedIn links it)"),
        (_DOMAIN_LIKE, "domain-like", "Domain-like word (LinkedIn auto-links it)"),
    ):
        for m in pattern.finditer(display):
            if overlaps(m.start(), m.end()):
                continue
            covered.append((m.start(), m.end()))
            if allow_links and code == "external-link":
                continue
            findings.append(
                Finding(code, Severity.BLOCK, f"{message}: {m.group(0)!r}.", m.start(), m.end())
            )
    for m in _FILENAME_LIKE.finditer(display):
        if not overlaps(m.start(), m.end()):
            findings.append(
                Finding(
                    "filename-like",
                    Severity.WARN,
                    f"Filename-like word {m.group(0)!r}; check LinkedIn does not link it.",
                    m.start(),
                    m.end(),
                )
            )
    return findings


def _hashtags(display: str, tokens: Sequence[Token]) -> list[Finding]:
    findings: list[Finding] = []
    count = sum(isinstance(t, Hashtag) for t in tokens)
    if count > MAX_HASHTAGS:
        findings.append(
            Finding(
                "too-many-hashtags",
                Severity.BLOCK,
                f"{count} hashtags; use at most {MAX_HASHTAGS}.",
                0,
                len(display),
            )
        )
    approved_tags = {t.tag.casefold() for t in tokens if isinstance(t, Hashtag)}
    for m in _UNAPPROVED_HASHTAG.finditer(display):
        if m.group(1).casefold() not in approved_tags:
            findings.append(
                Finding(
                    "unapproved-hashtag",
                    Severity.WARN,
                    f"#{m.group(1)} is not an approved pillar hashtag; it will post as plain text.",
                    m.start(),
                    m.end(),
                )
            )
    for m in _FULLWIDTH_HASH.finditer(display):
        findings.append(
            Finding(
                "lookalike-hashtag",
                Severity.WARN,
                "Full-width number sign used like a hashtag.",
                m.start(),
                m.end(),
            )
        )
    return findings


def _characters(display: str) -> list[Finding]:
    findings: list[Finding] = []
    emoji = 0
    for cluster in _GRAPHEME.finditer(display):
        text = cluster.group(0)
        is_emoji = bool(_EMOJI_CLUSTER.match(text))
        emoji += is_emoji
        for offset, char in enumerate(text):
            if not _IGNORABLE.match(char):
                continue
            if is_emoji and char in _EMOJI_SAFE_IGNORABLE:
                continue
            index = cluster.start() + offset
            last = findings[-1] if findings else None
            if last is not None and last.code == "invisible-character" and last.end == index:
                # Merge a run of hidden characters into one finding.
                findings[-1] = Finding(
                    last.code, last.severity, last.message, last.start, index + 1
                )
                continue
            name = unicodedata.name(char, f"U+{ord(char):04X}")
            findings.append(
                Finding(
                    "invisible-character",
                    Severity.BLOCK,
                    f"Invisible character {name} (U+{ord(char):04X}).",
                    index,
                    index + 1,
                )
            )
    if emoji > MAX_EMOJI:
        findings.append(
            Finding(
                "too-many-emoji",
                Severity.WARN,
                f"{emoji} emoji; use at most {MAX_EMOJI}.",
                0,
                len(display),
            )
        )
    for m in _MATH_ALNUM.finditer(display):
        findings.append(
            Finding(
                "unicode-styled-text",
                Severity.BLOCK,
                "Unicode 'bold/italic' letters: screen readers and LinkedIn search can't "
                "read them.",
                m.start(),
                m.end(),
            )
        )
    bangs = display.count("!")
    if bangs > 1:
        findings.append(
            Finding(
                "exclamation",
                Severity.WARN,
                f"{bangs} exclamation marks; use at most 1.",
                0,
                len(display),
            )
        )
    dashes = display.count("\u2014")
    if dashes >= 3:
        findings.append(
            Finding(
                "em-dash-wall", Severity.WARN, f"{dashes} em dashes; rewrite some.", 0, len(display)
            )
        )
    return findings


def _phrases(display: str) -> list[Finding]:
    text = display.translate(_APOSTROPHES)  # same length: offsets stay valid
    findings: list[Finding] = []
    for pattern in _BAIT_RE:
        for m in pattern.finditer(text):
            findings.append(
                Finding(
                    "engagement-bait",
                    Severity.BLOCK,
                    f"Engagement bait: {m.group(0)!r}.",
                    m.start(),
                    m.end(),
                )
            )
    for pattern in _OPENER_RE:
        m = pattern.match(text)
        if m:
            findings.append(
                Finding(
                    "formula-opener",
                    Severity.BLOCK,
                    f"Formula opener: {m.group(0).strip()!r}.",
                    0,
                    m.end(),
                )
            )
            break
    for pattern in _ANYWHERE_RE:
        for m in pattern.finditer(text):
            if m.start() > 4:  # the opener check already reported it at the start
                findings.append(
                    Finding(
                        "formula-opener",
                        Severity.BLOCK,
                        f"Cliché: {m.group(0)!r}.",
                        m.start(),
                        m.end(),
                    )
                )
    for pattern in _AI_TELL_RE:
        for m in pattern.finditer(text):
            findings.append(
                Finding("ai-tell", Severity.WARN, f"AI tell: {m.group(0)!r}.", m.start(), m.end())
            )
    if regex.match(r"(?i)^\s*tl;?dr\b", text):
        findings.append(Finding("ai-tell", Severity.WARN, "Starts with TL;DR.", 0, 5))
    return findings
