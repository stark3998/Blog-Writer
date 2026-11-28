"""Pure helpers for research output: source URLs, finding lines, claims, quotes, dates, style.

Everything here is deterministic and in memory. The rules that keep passages out of storage
live here: :func:`clean_claim` strips every run the claim shares with the passage
(``domain.research.strip_verbatim``) and :func:`cut_quotes` keeps only verbatim quotes within the
200-character budget per source.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from blogwriter.domain.base import (
    HOST_PATTERN,
    PARTIAL_DATE_PATTERN,
    check_https_url,
    sha256_hex,
)
from blogwriter.domain.common import MAX_QUOTE_CHARS
from blogwriter.domain.hashing import TRACKING_PARAMETERS
from blogwriter.domain.research import MAX_CLAIM_CHARS, strip_verbatim
from blogwriter.linkedin.lint import lint_post

__all__ = [
    "MIN_CLAIM_CHARS",
    "MIN_QUOTE_CHARS",
    "citation_id_for",
    "clean_claim",
    "clean_source_url",
    "clip",
    "clip_list",
    "cut_quotes",
    "defang",
    "is_allowlisted",
    "parse_findings",
    "parse_quick_answer",
    "partial_date",
    "source_domain",
    "strip_links",
    "styled",
]

MIN_CLAIM_CHARS: Final = 30
"""A claim left shorter than this after stripping verbatim runs says nothing: it is dropped."""
MIN_QUOTE_CHARS: Final = 12
MIN_CUT_QUOTE_CHARS: Final = 40
"""A quote cut to fit the budget keeps at least this much, or is dropped."""

_HOST_RE: Final = re.compile(HOST_PATTERN)
_PARTIAL_DATE_RE: Final = re.compile(PARTIAL_DATE_PATTERN)
_TRAILING_PUNCTUATION: Final = ".,;:!?)]}'\"\u201d\u2019>"
_BULLET_RE: Final = re.compile(r"^\s*(?:[-*\u2022\u2013]|\d{1,2}[.)])\s+(?P<body>.+?)\s*$")
_MD_LINK_RE: Final = re.compile(r"\[(?P<label>[^\]\n]{0,300})\]\((?P<url>https?://[^\s)]+)\)")
_URL_RE: Final = re.compile(r"https?://[^\s<>\"'\]\[()]+")
_DOMAINISH_RE: Final = re.compile(r"^[\w.-]+\.[a-z]{2,}(?:/\S*)?$", re.IGNORECASE)
_EMPTY_BRACKETS_RE: Final = re.compile(
    r"[\[(]\s*(?:(?:see|via|from|source|sources)\s*:?\s*)?[\])]", re.IGNORECASE
)
_SPACE_BEFORE_PUNCT_RE: Final = re.compile(r"\s+([.,;:!?])")
_SLUG_CHARS_RE: Final = re.compile(r"[^a-z0-9]+")
_STYLE_BLOCKERS: Final = frozenset({"formula-opener", "engagement-bait"})


# --------------------------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------------------------


def clean_source_url(raw: str) -> str | None:
    """A search result URL as we store and fetch it, or ``None`` when it is not usable.

    ``https`` only, with a lowercase DNS host (no IP literals), no fragment and no tracking
    parameters (``utm_*`` and ``domain.hashing.TRACKING_PARAMETERS``). Everything else is kept
    as it came: nothing is ever added.
    """
    text = raw.strip().rstrip(_TRAILING_PUNCTUATION)
    try:
        check_https_url(text)
    except ValueError:
        return None
    parts = urlsplit(text)
    host = (parts.hostname or "").rstrip(".").lower()
    if source_domain(f"https://{host}/") is None:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port in (None, 443) else f"{host}:{port}"
    query = parts.query
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (key, value)
        for key, value in pairs
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    if len(kept) != len(pairs):
        query = urlencode(kept)
    return urlunsplit(("https", netloc, parts.path or "/", query, ""))


def source_domain(url: str) -> str | None:
    """The lowercase host of ``url`` if it is a DNS name with two or more labels (an IP
    address, whose last label is numeric, is not)."""
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if not _HOST_RE.fullmatch(host) or host.rsplit(".", 1)[-1].isdigit():
        return None
    return host


def is_allowlisted(domain: str, allowlist: Iterable[str]) -> bool:
    """Whether ``domain`` is one of ``allowlist`` or a subdomain of one."""
    return any(domain == item or domain.endswith(f".{item}") for item in allowlist)


def citation_id_for(url: str) -> str:
    """``src-<host slug>-<6 hex>``: stable for a URL, unique enough within a brief."""
    host = (urlsplit(url).hostname or "").lower()
    slug = _SLUG_CHARS_RE.sub("-", host).strip("-")[:40].strip("-") or "source"
    return f"src-{slug}-{sha256_hex(url)[:6]}"


# --------------------------------------------------------------------------------------------
# Model text
# --------------------------------------------------------------------------------------------


def clip(text: str | None, max_chars: int) -> str:
    """Whitespace collapsed and cut at ``max_chars`` (at a word boundary when one is near)."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    cut = collapsed[:max_chars]
    boundary = cut.rfind(" ")
    if boundary >= max_chars * 0.5:
        cut = cut[:boundary]
    return cut.rstrip(" ,;:-")


def clip_list(items: Iterable[str], max_chars: int, max_items: int) -> list[str]:
    """Non-empty, clipped, de-duplicated items (case-insensitive), at most ``max_items``."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = clip(item, max_chars)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def strip_links(text: str) -> str:
    """``text`` without Markdown links and URLs (a link label that is not a domain is kept)."""

    def label(match: re.Match[str]) -> str:
        value = match.group("label").strip()
        return "" if not value or _DOMAINISH_RE.match(value) else value

    without = _MD_LINK_RE.sub(label, text)
    without = _URL_RE.sub("", without)
    without = _EMPTY_BRACKETS_RE.sub("", without)
    without = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", without)
    return " ".join(without.split()).strip(" -\u2013\u2014:;,")


def _first_url(text: str) -> str | None:
    for match in _MD_LINK_RE.finditer(text):
        return match.group("url")
    found = _URL_RE.search(text)
    return found.group(0) if found else None


def parse_findings(text: str) -> list[tuple[str, str | None]]:
    """Finding lines (``- <sentence> [<url>]``) as ``(sentence, url)``; other lines are ignored.

    The URL is the first one on the line (a Markdown link or a bare URL), unvalidated; the
    sentence has every link removed.
    """
    findings: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if match is None:
            continue
        body = match.group("body")
        sentence = strip_links(body)
        if len(sentence) < 10:
            continue
        findings.append((sentence, _first_url(body)))
    return findings


def parse_quick_answer(text: str) -> tuple[str, list[tuple[str, str | None]]]:
    """A quick-search reply: the ``SUMMARY:`` line (else the first prose line) and its
    finding lines."""
    summary = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("SUMMARY:"):
            summary = stripped[len("SUMMARY:") :]
            break
    if not summary:
        summary = next(
            (line for line in text.splitlines() if line.strip() and _BULLET_RE.match(line) is None),
            "",
        )
    return strip_links(summary), parse_findings(text)


def clean_claim(text: str, passage: str | None) -> str | None:
    """A claim fit to store: links removed, every run shared with ``passage`` (40+ characters,
    ``domain.research.strip_verbatim``) cut, clipped to ``MAX_CLAIM_CHARS``; ``None`` when what is
    left says nothing."""
    claim = " ".join(text.split())
    if passage is not None:
        claim = strip_verbatim(claim, passage)  # first: links removed later cannot split a run
    claim = strip_links(claim)
    meaningful = claim.replace("[...]", "").strip()
    if len(meaningful) < MIN_CLAIM_CHARS:
        return None
    return clip(claim, MAX_CLAIM_CHARS) or None


def cut_quotes(quotes: Sequence[str], passage: str, *, budget: int = MAX_QUOTE_CHARS) -> list[str]:
    """The quotes to keep for one source: verbatim in ``passage`` (whitespace-insensitive),
    together at most ``budget`` characters. A quote that does not fit is cut at a word boundary
    (kept only if at least 40 characters remain); quotes not found in the passage are dropped.
    """
    folded = " ".join(passage.split())
    kept: list[str] = []
    remaining = budget
    for raw in quotes:
        text = " ".join(raw.split())
        if text not in folded:
            text = text.strip("\"'\u201c\u201d\u2018\u2019 ")
            if text not in folded:
                continue
        if len(text) < MIN_QUOTE_CHARS or text in kept:
            continue
        if len(text) > remaining:
            cut = text[:remaining]
            boundary = cut.rfind(" ")
            cut = (cut[:boundary] if boundary > 0 else cut).rstrip(" ,;:-")
            if len(cut) < MIN_CUT_QUOTE_CHARS:
                continue
            text = cut
        kept.append(text)
        remaining -= len(text)
        if remaining < MIN_QUOTE_CHARS:
            break
    return kept


def partial_date(value: str | None) -> str | None:
    """``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` (a longer ISO timestamp is cut to its date)."""
    if not value:
        return None
    text = value.strip()
    if len(text) > 10 and text[10:11] in ("T", " "):
        text = text[:10]
    return text if _PARTIAL_DATE_RE.fullmatch(text) else None


def defang(text: str) -> str:
    """Untrusted text made safe to place between ``<<<NAME`` / ``NAME>>>`` delimiters."""
    return text.replace("<<<", "<< <").replace(">>>", "> >>")


def styled(items: Iterable[str], *, ai_tells: bool) -> list[str]:
    """Items without formula openers or engagement bait (``linkedin.lint``), and without AI
    tells when ``ai_tells`` (hook seeds become post openings)."""
    kept: list[str] = []
    for item in items:
        codes = {finding.code for finding in lint_post(item, approved_hashtags=())}
        if codes & _STYLE_BLOCKERS or (ai_tells and "ai-tell" in codes):
            continue
        kept.append(item)
    return kept
