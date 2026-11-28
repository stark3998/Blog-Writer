"""Sub-models shared by several documents: findings, ``served_by`` records, media, citations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Self

from pydantic import Field, model_validator

from blogwriter.domain.base import (
    CODE_PATTERN,
    Code,
    DomainModel,
    HostName,
    HttpsUrl,
    PartialDate,
    Sha256Hex,
    Slug,
    Timestamp,
)

if TYPE_CHECKING:
    from blogwriter.ai.router import RouterResult, ServedBy
    from blogwriter.linkedin.lint import Finding as LinkedInFinding
    from blogwriter.publish.lint import Finding as PublishFinding

__all__ = [
    "FINDING_REF_PATTERN",
    "MAX_FINDING_FIELD",
    "MAX_FINDING_MESSAGE",
    "MAX_QUOTE_CHARS",
    "MAX_SERVED_BY",
    "MAX_STORED_FINDINGS",
    "BlobContainer",
    "Citation",
    "ClassifierCheck",
    "Finding",
    "MediaRef",
    "MediaRole",
    "Quote",
    "ServedByRecord",
    "Severity",
    "blocking",
    "cap_findings",
    "check_quote_budget",
    "from_linkedin_finding",
    "from_publish_finding",
    "review_only_reasons",
]

type Severity = Literal["block", "warn", "info"]
"""``block`` refuses the action (approval, publish) until fixed; ``warn`` is shown only."""

MAX_QUOTE_CHARS: Final = 200
"""Web IQ terms: at most 200 verbatim characters per source, across a whole piece."""
MAX_FINDING_MESSAGE: Final = 500
MAX_STORED_FINDINGS: Final = 50
"""Findings kept per stored check (a gate result): the rest are counted, not stored, so a
document never outgrows Cosmos's 2 MB (:func:`cap_findings`)."""
FINDING_REF_PATTERN: Final = r"^[!#-\[\]-~]{1,200}$"
MAX_FINDING_FIELD: Final = 200
"""Longest ``Finding.field`` (a document field path such as ``slides[2]``)."""
"""Printable ASCII without ``"`` and ``\\`` (ids, citation ids, URLs): never escaped in JSON."""
_CODE_RE: Final = re.compile(CODE_PATTERN)


def _is_none(value: object) -> bool:
    return value is None


class Finding(DomainModel):
    """One problem found in a text: by a gate, a lint, a check or input-side redaction.

    ``start``/``end`` are code-point offsets into the text that was checked (the Markdown body,
    the post display text, a slide's text...). ``field`` names that text when a document has
    several (``displayText``, ``slides[2]``, ``documentTitle``: the web highlights by field and
    span); it is left out when unset, so findings without one serialize as before. ``message``
    is owner-facing and must not quote confidential matches (say "matches a blocklisted name",
    never the name). ``ref`` points at a related document part (a citation id, a story id, a
    similar post id). ``overridable`` findings may be overridden by the owner with a recorded
    reason (manual publish only).
    """

    code: Code
    severity: Severity
    message: str = Field(min_length=1, max_length=MAX_FINDING_MESSAGE)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    ref: str | None = Field(default=None, pattern=FINDING_REF_PATTERN)
    overridable: bool = False
    field: str | None = Field(
        default=None, min_length=1, max_length=MAX_FINDING_FIELD, exclude_if=_is_none
    )

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end are set together")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must not be before start")
        return self


def blocking(findings: Sequence[Finding]) -> list[Finding]:
    """The findings with severity ``block``."""
    return [finding for finding in findings if finding.severity == "block"]


_SEVERITY_RANK: Final[Mapping[str, int]] = {"block": 0, "warn": 1, "info": 2}


def cap_findings(
    findings: Sequence[Finding], limit: int = MAX_STORED_FINDINGS
) -> tuple[list[Finding], int]:
    """The findings to store (at most ``limit``) and how many were left out.

    Blocking findings come first, non-overridable ones before overridable ones, then by
    position, so a cut never drops a hard blocker while keeping a softer one (a gate result's
    ``overridable`` stays truthful).
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    ordered = sorted(
        findings,
        key=lambda f: (
            _SEVERITY_RANK[f.severity],
            f.overridable,
            f.start if f.start is not None else -1,
        ),
    )
    return ordered[:limit], max(0, len(ordered) - limit)


def _clip_message(message: str, fallback: str) -> str:
    text = message.strip() or fallback
    if len(text) <= MAX_FINDING_MESSAGE:
        return text
    return text[: MAX_FINDING_MESSAGE - 3] + "..."


def from_linkedin_finding(finding: LinkedInFinding) -> Finding:
    """A ``blogwriter.linkedin.lint`` finding as a domain finding (same code, severity, span).

    The span indexes the display text that was linted. Long messages are clipped.
    """
    severity: Severity = "block" if str(finding.severity) == "block" else "warn"
    code = finding.code if _CODE_RE.fullmatch(finding.code) else "post-lint"
    return Finding(
        code=code,
        severity=severity,
        message=_clip_message(finding.message, code),
        start=finding.start,
        end=max(finding.start, finding.end),
    )


def _line_span(text: str, line: int) -> tuple[int, int] | None:
    """Code-point offsets of 1-based ``line`` in ``text`` (without its newline)."""
    if line < 1:
        return None
    start = 0
    for _ in range(line - 1):
        newline = text.find("\n", start)
        if newline == -1:
            return None
        start = newline + 1
    end = text.find("\n", start)
    return start, len(text) if end == -1 else end


def from_publish_finding(finding: PublishFinding, text: str, *, body_start: int = 0) -> Finding:
    """A ``blogwriter.publish.lint`` finding (line numbers, ``error``/``warning``) as a domain
    finding: ``error`` blocks and is never overridable, ``warning`` warns.

    ``text`` is what was linted (gate 5 lints :func:`blogwriter.domain.compose.compose_document`)
    and ``body_start`` the offset where the article body starts in it, so spans index the body
    the editor shows; findings inside the frontmatter, or without a line, have no span. The
    publish linter's messages may quote the text: they are clipped to
    :data:`MAX_FINDING_MESSAGE`.
    """
    severity: Severity = "block" if finding.severity == "error" else "warn"
    start: int | None = None
    end: int | None = None
    if finding.line is not None:
        span = _line_span(text, finding.line)
        if span is not None and span[0] >= body_start:
            start, end = span[0] - body_start, span[1] - body_start
    code = finding.code if _CODE_RE.fullmatch(finding.code) else "dialect-lint"
    return Finding(
        code=code,
        severity=severity,
        message=_clip_message(finding.message, code),
        start=start,
        end=end,
    )


class ServedByRecord(DomainModel):
    """Which model served one AI step (``ai.router.ServedBy``), stored on the document it wrote.

    ``refused`` records a refusal anywhere in the chain; refusals and fallbacks make drafts
    review-only (plan: "Auto-publish calibration").
    """

    step: Code
    at: Timestamp
    task: Code
    model: str = Field(min_length=1, max_length=100)
    deployment: str = Field(min_length=1, max_length=100)
    provider: Literal["openai", "anthropic"]
    effort: str | None = Field(default=None, max_length=20)
    fallback_reason: str | None = Field(default=None, max_length=40)
    review_only: bool = False
    repaired: bool = False
    refused: bool = False
    prompt_version: str | None = Field(default=None, max_length=40)
    cost_usd: float | None = Field(default=None, ge=0)

    @classmethod
    def from_served_by(
        cls,
        served_by: ServedBy,
        *,
        step: str,
        at: str,
        refused: bool = False,
        prompt_version: str | None = None,
        cost_usd: float | None = None,
    ) -> ServedByRecord:
        """Build the record from the router's ``ServedBy`` (``result.served_by``)."""
        data: Mapping[str, object] = served_by.to_dict()
        return cls.model_validate(
            {
                **data,
                "step": step,
                "at": at,
                "refused": refused,
                "promptVersion": prompt_version,
                "costUsd": cost_usd,
            }
        )

    @property
    def fallback(self) -> bool:
        return self.fallback_reason is not None


def review_only_reasons(result: RouterResult) -> tuple[str, ...]:
    """Why an AI result makes a draft or post review-only (empty: it may count as clean).

    ``fallback_model`` when a fallback model served (``served_by.fallback_reason``),
    ``refusal`` when any model in the chain refused (``result.refusals``) or the result itself
    is a refusal, and ``review_only`` when the router marked it so for another reason.
    """
    reasons: list[str] = []
    if result.served_by.fallback_reason is not None:
        reasons.append("fallback_model")
    if result.refusals or result.refused:
        reasons.append("refusal")
    if result.served_by.review_only and not reasons:
        reasons.append("review_only")
    return tuple(reasons)


MAX_SERVED_BY: Final = 50
"""Keep at most this many ``served_by`` records per document (drop the oldest)."""


class ClassifierCheck(DomainModel):
    """The confidentiality classifier's verdict on a story, POV entry or exemplar.

    ``passed`` is ``None`` until the classifier has run (an unchecked entry is not used).
    """

    passed: bool | None = None
    checked_at: Timestamp | None = None
    findings: list[Finding] = Field(default_factory=list[Finding], max_length=50)
    served_by: ServedByRecord | None = None
    content_sha256: Sha256Hex | None = None
    """The text the verdict is about; an edit makes the verdict stale."""


type BlobContainer = Literal["media", "published", "backups"]
"""Logical Blob containers (env ``BW_STORAGE_CONTAINER_MEDIA/_PUBLISHED/_BACKUPS``)."""


type MediaRole = Literal[
    "upload",
    "carousel_pdf",
    "carousel_cover",
    "image",
    "image_card",
    "diagram_svg",
    "diagram_png",
    "og_image",
    "hero_image",
    "headshot",
    "name_mark",
]
"""What a :class:`MediaRef` is for (storage uploads and every package's rendered assets)."""


class MediaRef(DomainModel):
    """A binary in Blob storage, addressed by container and name, with its SHA-256.

    Names are content-addressed (``<area>/<owner id>/<sha256>.<ext>``) so a reference can never
    silently point at changed bytes. ``alt_text`` is required for anything shown to readers.
    """

    URL_PREFIX: ClassVar[str] = "/api/media/"

    role: MediaRole
    container: BlobContainer = "media"
    name: str = Field(min_length=1, max_length=300, pattern=r"^[a-z0-9][a-z0-9/._-]*$")
    sha256: Sha256Hex
    content_type: Literal[
        "image/png", "image/jpeg", "image/webp", "image/svg+xml", "application/pdf"
    ]
    size_bytes: int = Field(ge=1, le=100 * 1024 * 1024)
    width: int | None = Field(default=None, ge=1, le=16_384)
    height: int | None = Field(default=None, ge=1, le=16_384)
    page_count: int | None = Field(default=None, ge=1, le=300)
    alt_text: str | None = Field(default=None, max_length=1000)
    filename: str | None = Field(default=None, max_length=120)
    """Download name for the kit (plain, no path)."""
    linkedin_urn: str | None = Field(default=None, max_length=200)
    """``urn:li:document:...`` / ``urn:li:image:...`` once uploaded (recorded before polling)."""
    linkedin_status: Literal["uploading", "available", "failed"] | None = None
    uploaded_at: Timestamp | None = None

    @property
    def url(self) -> str:
        """Same-origin path the SPA loads it from (``media`` container only)."""
        if self.container != "media":
            raise ValueError("only media-container blobs are served at /api/media/")
        return self.URL_PREFIX + self.name


class Citation(DomainModel):
    """A source a brief, quick search or article cites. No passages, ever.

    ``id`` is the ``[^src-id]`` footnote label of the article dialect and the
    ``citations[].id`` of the published-blogs contract.
    """

    id: Annotated[Slug, Field(max_length=64)]
    url: HttpsUrl
    title: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=200)
    date: PartialDate | None = None
    domain: HostName
    allowlisted: bool = False
    """The domain is on the pillar's research allowlist (or owner-approved)."""
    accessed_at: Timestamp | None = None


class Quote(DomainModel):
    """A verbatim quote from one citation: at most :data:`MAX_QUOTE_CHARS` characters."""

    id: Annotated[Slug, Field(max_length=64)]
    citation_id: Annotated[Slug, Field(max_length=64)]
    text: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)


def check_quote_budget(quotes: Sequence[Quote], *, limit: int = MAX_QUOTE_CHARS) -> None:
    """Raise ``ValueError`` when the quotes of one citation total more than ``limit`` chars."""
    totals: dict[str, int] = {}
    for quote in quotes:
        totals[quote.citation_id] = totals.get(quote.citation_id, 0) + len(quote.text)
    over = sorted(citation for citation, total in totals.items() if total > limit)
    if over:
        raise ValueError(
            f"verbatim quotes exceed {limit} characters for citation(s): {', '.join(over)}"
        )
