"""Deterministic lint for replies and comments (the Engage panel's draft check). No I/O.

Plan, "Planner, engagement, insights": "Lint checks for restating, links, AI tells and length
over 1,000 characters." LinkedIn shows generic, AI-sounding comments and comments that restate
the post to fewer people, so the rules aim at what the owner adds:

=====================  =======  ===========================================================
code                   level    what
=====================  =======  ===========================================================
``blocklisted-term``   block    a blocklisted name (term id in ``ref``, never the term)
``blocklist-not-       block    the owner never saved the blocklist: nothing can be cleared
configured``                    (fail closed)
``external-link``      block    a URL (zero links: name the source instead)
``email-address``      block    an e-mail address
``domain-like``        block    a word LinkedIn turns into a link (``README.md``, ``x.ai``)
``engagement-bait``    block    ``blogwriter.linkedin.lint.ENGAGEMENT_BAIT``
``restates-post``      block    a run of :data:`COPIED_RUN_BLOCK_WORDS`+ words copied from the
                                text being answered (or the owner's own post)
``restates-post``      warn     a shorter copied run (:data:`COPIED_RUN_WARN_WORDS`+ words), or
                                most of the draft's word trigrams repeat that text
``too-long``           warn     over :data:`MAX_COMMENT_CHARS` characters (span: the overflow)
``generic-opener``     warn     opens with praise or agreement ("Great post", "Spot on")
``ai-tell``            warn     ``linkedin.lint.ai_tell_findings`` (its ``AI_TELLS``)
(slop codes)           warn     the ``SlopChecker`` port (``services.slop``)
``slop-unavailable``   warn     the slop checks could not run (never a silent pass)
=====================  =======  ===========================================================

Spans are code-point offsets into the draft. Messages never quote the draft, except the
``ai-tell`` messages, which name the phrase from the fixed list exactly as ``linkedin.lint``
does (the findings are equal to its ``ai-tell`` findings on the same text).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Final

import regex

from blogwriter.core.logging import describe_exception
from blogwriter.domain.common import Finding, blocking, from_linkedin_finding
from blogwriter.domain.confidentiality import blocklist_findings, forbidden_topic_findings
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.settings import Blocklist, EmployerPolicy
from blogwriter.engagement._text import word_spans, words
from blogwriter.linkedin.lint import ENGAGEMENT_BAIT
from blogwriter.linkedin.lint import ai_tell_findings as linkedin_ai_tell_findings

__all__ = [
    "AI_TELL_CODE",
    "BAIT_CODE",
    "COPIED_RUN_BLOCK_WORDS",
    "COPIED_RUN_WARN_WORDS",
    "DOMAIN_CODE",
    "EMAIL_CODE",
    "LINK_CODE",
    "MAX_COMMENT_CHARS",
    "MIN_OVERLAP_TRIGRAMS",
    "OPENER_CODE",
    "OVERLAP_SHARE",
    "RESTATES_CODE",
    "SLOP_UNAVAILABLE_CODE",
    "SUBSTANTIVE_MIN_WORDS",
    "TOO_LONG_CODE",
    "ai_tell_findings",
    "bait_findings",
    "blocking_findings",
    "copied_runs",
    "is_substantive",
    "length_findings",
    "link_findings",
    "lint_reply",
    "opener_findings",
    "overlap_share",
    "restating_findings",
    "slop_findings",
]

MAX_COMMENT_CHARS: Final = 1000
"""Plan: comments and replies stay under 1,000 characters (LinkedIn allows 1,250)."""
COPIED_RUN_WARN_WORDS: Final = 8
COPIED_RUN_BLOCK_WORDS: Final = 12
OVERLAP_SHARE: Final = 0.5
"""Share of the draft's word trigrams found in the answered text that reads as restating."""
MIN_OVERLAP_TRIGRAMS: Final = 6
"""Drafts with fewer word trigrams are too short for the overlap measure."""
SUBSTANTIVE_MIN_WORDS: Final = 12
"""A final comment counts as substantive on the scorecard from this many words (and when it
does not restate what it answers)."""

LINK_CODE: Final = "external-link"
EMAIL_CODE: Final = "email-address"
DOMAIN_CODE: Final = "domain-like"
TOO_LONG_CODE: Final = "too-long"
RESTATES_CODE: Final = "restates-post"
OPENER_CODE: Final = "generic-opener"
BAIT_CODE: Final = "engagement-bait"
AI_TELL_CODE: Final = "ai-tell"
SLOP_UNAVAILABLE_CODE: Final = "slop-unavailable"

_logger: Final = logging.getLogger("blogwriter.engagement")

_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})

# Links: the same patterns as ``blogwriter.linkedin.lint`` (a test keeps them equal on a corpus).
_URL: Final = regex.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()\[\]{}]+")
_EMAIL: Final = regex.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_TLDS: Final = (
    "com|org|net|io|ai|dev|app|co|me|ly|so|sh|md|py|gg|tv|cloud|tech|info|biz|xyz|us|uk|in|de|"
    "gov|edu|mil|eu|ca|au|security|cyber|tools|run|page|site|online|blog|news"
)
_DOMAIN_LIKE: Final = regex.compile(
    rf"(?i)(?<![\w@/.-])(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{_TLDS})(?![\w-]|\.[a-z0-9])"
)
_LINK_RULES: Final[tuple[tuple[regex.Pattern[str], str, str], ...]] = (
    (
        _URL,
        LINK_CODE,
        "A link: LinkedIn shows comments with links to fewer people. Name the source instead.",
    ),
    (_EMAIL, EMAIL_CODE, "An e-mail address: leave contact details out of public comments."),
    (_DOMAIN_LIKE, DOMAIN_CODE, "LinkedIn turns this word into a link. Rephrase it."),
)

_BAIT_RE: Final = tuple(regex.compile(rf"(?im){pattern}") for pattern in ENGAGEMENT_BAIT)

_GENERIC_OPENERS: Final = (
    r"great\s+(?:post|point|points|share|insights?|read|article|take|question|thread|"
    r"perspective|breakdown|write[-\s]?up|summary|reminder|stuff|work)",
    r"(?:thanks|thank\s+you)(?:\s+so\s+much)?\s+for\s+(?:sharing|posting|this|writing\s+this|"
    r"the\s+(?:post|insights?|write[-\s]?up|reminder))",
    r"love\s+(?:this|that|it|the\s+(?:post|take|framing|perspective))",
    r"well\s+(?:said|put|written|articulated)",
    r"spot\s+on",
    r"so\s+true",
    r"(?:couldn't|could\s+not)\s+agree\s+more",
    r"(?:totally|completely|absolutely|fully|strongly)\s+agree",
    r"this\s+is\s+(?:gold|so\s+true|spot\s+on|brilliant|great|excellent|fantastic|powerful|"
    r"so\s+important|a\s+great\s+(?:post|point|read))",
    r"(?:very|really|super|such\s+an?)\s+(?:insightful|helpful|interesting|useful|valuable|"
    r"important)",
    r"(?:insightful|fantastic|excellent|brilliant|amazing|awesome|interesting|valuable|powerful)"
    r"\s+(?:post|point|read|share|insights?|take|perspective|article|thread)",
    r"absolutely(?=\s*(?:[.!,]|$))",
    r"100\s?%",
    r"this(?=\s*[.!]+(?:\s|$))",
)
_OPENER_RE: Final = regex.compile(
    r"(?i)^[\s\W]{0,4}(?:" + "|".join(_GENERIC_OPENERS) + r")(?![\p{L}\p{N}])"
)

_SLOP_UNAVAILABLE: Final = Finding(
    code=SLOP_UNAVAILABLE_CODE,
    severity="warn",
    message="The style checks could not run. Read it over once more for filler.",
)


def _position(finding: Finding) -> tuple[int, int, str]:
    start = finding.start if finding.start is not None else -1
    end = finding.end if finding.end is not None else -1
    return start, end, finding.code


def _sorted_unique(findings: Iterable[Finding]) -> list[Finding]:
    """One finding per ``(code, start, end)`` (the first one wins), sorted by position."""
    seen: set[tuple[int, int, str]] = set()
    out: list[Finding] = []
    for finding in findings:
        key = _position(finding)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return sorted(out, key=_position)


# --------------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------------


def link_findings(text: str) -> list[Finding]:
    """URLs, e-mail addresses and domain-like words (``block``), one finding per place."""
    covered: list[tuple[int, int]] = []
    findings: list[Finding] = []
    for pattern, code, message in _LINK_RULES:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if any(start < e and s < end for s, e in covered):
                continue
            covered.append((start, end))
            findings.append(
                Finding(code=code, severity="block", message=message, start=start, end=end)
            )
    return sorted(findings, key=_position)


def length_findings(text: str) -> list[Finding]:
    """``too-long`` (``warn``) over :data:`MAX_COMMENT_CHARS` characters; the span is the
    overflow."""
    if len(text) <= MAX_COMMENT_CHARS:
        return []
    return [
        Finding(
            code=TOO_LONG_CODE,
            severity="warn",
            message=(
                f"{len(text):,} characters. Keep a comment under {MAX_COMMENT_CHARS:,}: one "
                "point, made well."
            ),
            start=MAX_COMMENT_CHARS,
            end=len(text),
        )
    ]


def _ngrams(tokens: Sequence[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def copied_runs(
    draft: str, sources: Sequence[str], *, min_words: int = COPIED_RUN_WARN_WORDS
) -> list[tuple[int, int, int]]:
    """``(start, end, words)`` of every stretch of ``draft`` made of runs of at least
    ``min_words`` consecutive words that also occur, in order, in one of ``sources`` (case and
    punctuation are ignored). Spans index ``draft``."""
    if min_words < 1:
        raise ValueError("min_words must be at least 1")
    grams: set[tuple[str, ...]] = set()
    for source in sources:
        grams |= _ngrams(words(source), min_words)
    spans = word_spans(draft)
    tokens = [word for word, _, _ in spans]
    if not grams or len(tokens) < min_words:
        return []
    covered = [False] * len(tokens)
    for index in range(len(tokens) - min_words + 1):
        if tuple(tokens[index : index + min_words]) in grams:
            for position in range(index, index + min_words):
                covered[position] = True
    runs: list[tuple[int, int, int]] = []
    index = 0
    while index < len(tokens):
        if not covered[index]:
            index += 1
            continue
        last = index
        while last + 1 < len(tokens) and covered[last + 1]:
            last += 1
        runs.append((spans[index][1], spans[last][2], last - index + 1))
        index = last + 1
    return runs


def overlap_share(draft: str, sources: Sequence[str]) -> float | None:
    """Share of the draft's word trigrams that also occur in ``sources`` (``None`` when the draft
    has fewer than :data:`MIN_OVERLAP_TRIGRAMS` trigrams)."""
    tokens = words(draft)
    grams = [tuple(tokens[index : index + 3]) for index in range(len(tokens) - 2)]
    if len(grams) < MIN_OVERLAP_TRIGRAMS:
        return None
    known: set[tuple[str, ...]] = set()
    for source in sources:
        known |= _ngrams(words(source), 3)
    return sum(gram in known for gram in grams) / len(grams)


def restating_findings(draft: str, sources: Sequence[str]) -> list[Finding]:
    """``restates-post``: copied runs (``block`` from :data:`COPIED_RUN_BLOCK_WORDS` words,
    ``warn`` from :data:`COPIED_RUN_WARN_WORDS`), else one document-level ``warn`` when most
    of the draft repeats ``sources`` (the text being answered, the owner's own post)."""
    texts = [source for source in sources if source.strip()]
    if not texts:
        return []
    findings: list[Finding] = []
    for start, end, count in copied_runs(draft, texts):
        findings.append(
            Finding(
                code=RESTATES_CODE,
                severity="block" if count >= COPIED_RUN_BLOCK_WORDS else "warn",
                message=(
                    f"Repeats {count} words of the text you are answering. Say what you add "
                    "instead."
                ),
                start=start,
                end=end,
            )
        )
    if findings:
        return findings
    share = overlap_share(draft, texts)
    if share is not None and share >= OVERLAP_SHARE:
        findings.append(
            Finding(
                code=RESTATES_CODE,
                severity="warn",
                message=(
                    "Mostly restates the text you are answering. Add your own detail: a "
                    "decision, a number, a trade-off."
                ),
            )
        )
    return findings


def opener_findings(text: str) -> list[Finding]:
    """``generic-opener`` (``warn``): the draft opens with praise or agreement."""
    match = _OPENER_RE.match(text.translate(_APOSTROPHES))
    if match is None:
        return []
    return [
        Finding(
            code=OPENER_CODE,
            severity="warn",
            message="A generic opener. Start with the detail you add.",
            start=0,
            end=match.end(),
        )
    ]


def bait_findings(text: str) -> list[Finding]:
    """``engagement-bait`` (``block``), the patterns of ``linkedin.lint.ENGAGEMENT_BAIT``."""
    normalized = text.translate(_APOSTROPHES)  # same length: offsets stay valid
    findings: list[Finding] = []
    for pattern in _BAIT_RE:
        for match in pattern.finditer(normalized):
            findings.append(
                Finding(
                    code=BAIT_CODE,
                    severity="block",
                    message="Engagement bait: LinkedIn shows it to fewer people. Cut it.",
                    start=match.start(),
                    end=match.end(),
                )
            )
    return sorted(findings, key=_position)


def ai_tell_findings(text: str) -> list[Finding]:
    """The ``ai-tell`` findings ``linkedin.lint.lint_post`` reports for ``text`` (same spans,
    codes and messages), as domain findings sorted by position
    (:func:`blogwriter.linkedin.lint.ai_tell_findings`)."""
    return [from_linkedin_finding(finding) for finding in linkedin_ai_tell_findings(text)]


def slop_findings(text: str, slop: SlopChecker | None) -> list[Finding]:
    """The slop checker's findings; ``slop-unavailable`` when there is none or it fails (a
    check that cannot run is reported, never passed)."""
    if slop is None:
        return [_SLOP_UNAVAILABLE.model_copy()]
    try:
        return list(slop.check(text))
    except Exception as exc:  # a detector bug must not break the lint; report it instead
        _logger.warning("engagement_slop_failed", extra=describe_exception(exc))
        return [_SLOP_UNAVAILABLE.model_copy()]


# --------------------------------------------------------------------------------------------
# The lint
# --------------------------------------------------------------------------------------------


def lint_reply(
    draft: str,
    *,
    sources: Sequence[str],
    blocklist: Blocklist,
    slop: SlopChecker | None,
    policy: EmployerPolicy | None = None,
) -> list[Finding]:
    """Every finding for a reply or comment draft, sorted by position (see the module docs).

    ``sources`` are the texts the draft must not restate: the pasted post or comment, and the
    owner's own post for a reply. ``policy`` adds the employer's forbidden topics (block).
    CPU-bound on long texts: call it through ``asyncio.to_thread`` from async code.
    """
    findings: list[Finding] = [
        *blocklist_findings(draft, blocklist),
        *(forbidden_topic_findings(draft, policy) if policy is not None else []),
        *link_findings(draft),
        *bait_findings(draft),
        *restating_findings(draft, sources),
        *length_findings(draft),
        *opener_findings(draft),
        *ai_tell_findings(draft),
        *slop_findings(draft, slop),
    ]
    return _sorted_unique(findings)


def blocking_findings(
    text: str,
    *,
    sources: Sequence[str],
    blocklist: Blocklist,
    policy: EmployerPolicy | None = None,
) -> list[Finding]:
    """The ``block`` rules only (blocklist, forbidden topics, links, bait, long copied runs):
    what makes a model-written angle unusable as it stands."""
    findings = [
        *blocklist_findings(text, blocklist),
        *(forbidden_topic_findings(text, policy) if policy is not None else []),
        *link_findings(text),
        *bait_findings(text),
        *restating_findings(text, sources),
    ]
    return _sorted_unique(blocking(findings))


def is_substantive(text: str, *, sources: Sequence[str]) -> bool:
    """Whether a final reply or comment counts as substantive on the weekly scorecard: at least
    :data:`SUBSTANTIVE_MIN_WORDS` words, no long copied run and not mostly a restatement."""
    if len(words(text)) < SUBSTANTIVE_MIN_WORDS:
        return False
    texts = [source for source in sources if source.strip()]
    if not texts:
        return True
    if any(count >= COPIED_RUN_BLOCK_WORDS for _, _, count in copied_runs(text, texts)):
        return False
    share = overlap_share(text, texts)
    return share is None or share < OVERLAP_SHARE
