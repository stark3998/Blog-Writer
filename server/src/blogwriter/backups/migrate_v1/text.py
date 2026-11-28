"""Field helpers for converting v1 documents: times, plain text, tags, pillar and format guesses.

v1 wrote ``datetime.now(timezone.utc).isoformat()`` (``2026-03-01T12:34:56.789012+00:00``), free
text tags (``cloud security``) and no pillar or format. These helpers turn those values into what
v2 accepts, deterministically (the same v1 document always converts to the same v2 document, so
re-runs are idempotent).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from blogwriter.domain.base import SLUG_PATTERN

__all__ = [
    "FALLBACK_TIME",
    "PILLAR_KEYWORDS",
    "contract_stamp",
    "first_time",
    "guess_format",
    "guess_pillar",
    "item_bool",
    "item_str",
    "item_strings",
    "parse_v1_time",
    "plain_line",
    "ref_for",
    "slug_or_none",
    "tag_slug",
    "text_line",
    "unique_tags",
]

FALLBACK_TIME: Final = datetime(2026, 1, 1, tzinfo=UTC)
"""Used only when a v1 document has no readable time at all (not even ``_ts``)."""
_SLUG_RE: Final = re.compile(SLUG_PATTERN)
_NOT_PLAIN: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\ud800-\udfff]")
_WHITESPACE: Final = re.compile(r"\s+")
_NON_ALNUM: Final = re.compile(r"[^a-z0-9]+")
_SAFE_REF: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

PILLAR_KEYWORDS: Final[Mapping[str, tuple[str, ...]]] = {
    "identity": (
        "identity",
        "entra",
        "okta",
        "azure ad",
        "active directory",
        "sso",
        "single sign-on",
        "saml",
        "oidc",
        "openid",
        "oauth",
        "ciam",
        "mfa",
        "passwordless",
        "conditional access",
        "iam",
        "authentication",
    ),
    "governance": (
        "governance",
        "nist",
        "rmf",
        "compliance",
        "regulation",
        "regulatory",
        "eu ai act",
        "iso 42001",
        "risk management",
        "audit",
        "responsible ai",
        "policy",
    ),
    "agents": (
        "agent",
        "agents",
        "agentic",
        "mcp",
        "model context protocol",
        "llm",
        "prompt injection",
        "copilot",
        "genai",
        "generative ai",
        "ai",
        "openai",
        "machine learning",
    ),
}
"""Keyword lists for the default pillar ids (whole words, case-insensitive). Ties go to the
pillar listed first in the owner's settings; no hit means "no guess"."""
_KEYWORD_RES: Final[Mapping[str, tuple[re.Pattern[str], ...]]] = {
    pillar: tuple(re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])") for word in words)
    for pillar, words in PILLAR_KEYWORDS.items()
}


# --------------------------------------------------------------------------------------------
# Reading v1 values
# --------------------------------------------------------------------------------------------


def item_str(item: Mapping[str, Any], key: str) -> str | None:
    """A non-empty string field (stripped), else ``None``."""
    value: object = item.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def item_bool(item: Mapping[str, Any], key: str) -> bool:
    return item.get(key) is True


def item_strings(item: Mapping[str, Any], key: str) -> list[str]:
    """The non-empty strings of a list field (anything else is ignored)."""
    value: object = item.get(key)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
    return out


def parse_v1_time(value: object) -> datetime | None:
    """A v1 timestamp (ISO 8601, with or without offset; naive means UTC) or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def first_time(item: Mapping[str, Any], *keys: str) -> datetime:
    """The first readable time among ``keys``, else Cosmos ``_ts``, else :data:`FALLBACK_TIME`."""
    for key in keys:
        moment = parse_v1_time(item.get(key))
        if moment is not None:
            return moment
    stamp: object = item.get("_ts")
    if isinstance(stamp, int) and not isinstance(stamp, bool) and stamp > 0:
        return datetime.fromtimestamp(stamp, UTC)
    return FALLBACK_TIME


def contract_stamp(moment: datetime) -> str:
    """The published-blogs format: ``YYYY-MM-DDTHH:MM:SSZ`` (second precision, floored)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------------------------


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    cut = text[: max_length - 1]
    if " " in cut[max_length // 2 :]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + "\u2026"


def text_line(value: object, max_length: int) -> tuple[str | None, bool]:
    """One line of text: whitespace collapsed, control characters dropped, cut at a word
    boundary with an ellipsis (U+2026). Returns ``(text or None, adjusted)``."""
    if not isinstance(value, str):
        return None, False
    cleaned = _WHITESPACE.sub(" ", _NOT_PLAIN.sub(" ", value)).strip()
    if not cleaned:
        return None, bool(value.strip())
    short = _truncate(cleaned, max_length)
    return short, short != value


def plain_line(value: object, max_length: int) -> tuple[str | None, bool]:
    """:func:`text_line` without ``<`` (published-blogs plain text): ``<`` becomes U+2039."""
    if not isinstance(value, str):
        return None, False
    text, adjusted = text_line(value.replace("<", "\u2039"), max_length)
    return text, adjusted or "<" in value


def tag_slug(value: str, max_length: int = 40) -> str | None:
    """``"Cloud Security"`` -> ``"cloud-security"``; ``None`` when nothing is left."""
    folded = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = _NON_ALNUM.sub("-", ascii_text.lower()).strip("-")
    if len(slug) > max_length:
        cut = slug[:max_length]
        if slug[max_length] != "-" and "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        slug = cut.strip("-")
    return slug or None


def unique_tags(values: Iterable[str], *, limit: int = 10) -> list[str]:
    """Tag slugs in first-seen order, without duplicates, at most ``limit``."""
    tags: list[str] = []
    for value in values:
        slug = tag_slug(value)
        if slug is not None and slug not in tags:
            tags.append(slug)
        if len(tags) == limit:
            break
    return tags


def slug_or_none(value: object, max_length: int = 80) -> str | None:
    """``value`` if it already is a valid slug, else ``None``."""
    if isinstance(value, str) and len(value) <= max_length and _SLUG_RE.fullmatch(value):
        return value
    return None


def ref_for(container: str, v1_id: object) -> str:
    """A reference to a v1 document that is safe to print: ids and slugs as they are, anything
    that could be text (a keyword topic) as a short hash."""
    if container != "keywords" and isinstance(v1_id, str) and _SAFE_REF.fullmatch(v1_id):
        return v1_id
    material = v1_id if isinstance(v1_id, str) else repr(v1_id)
    return "sha256:" + hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()[:12]


# --------------------------------------------------------------------------------------------
# Pillar and format
# --------------------------------------------------------------------------------------------


def guess_pillar(texts: Iterable[str], pillars: Sequence[str]) -> str | None:
    """The configured pillar whose keywords occur most often in ``texts`` (``None``: no hit)."""
    haystack = " ".join(texts).lower()
    best: str | None = None
    best_hits = 0
    for pillar in pillars:
        patterns = _KEYWORD_RES.get(pillar, ())
        hits = sum(len(pattern.findall(haystack)) for pattern in patterns)
        if hits > best_hits:
            best, best_hits = pillar, hits
    return best


def guess_format(source_type: str | None, formats: Sequence[str]) -> str | None:
    """``github`` sources were repository explainers; everything else was news analysis."""
    wanted = "deep-explainer" if (source_type or "").lower() == "github" else "news-analysis"
    if wanted in formats:
        return wanted
    return formats[0] if formats else None
