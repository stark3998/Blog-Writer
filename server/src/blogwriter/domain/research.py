"""Research runs, briefs and quick searches (container ``research``). Owner: research.

**No passages are ever stored** (Web IQ terms; plan: "Research engine"). A document here holds
claims (model-written summaries), quotes of at most 200 characters per source in total, and the
source URL, title, publisher and date. Fetched pages live in memory only, inside one atomic
fetch-and-extract step. An invariant test checks that nothing passage-sized reaches these
documents, job events or logs.

A model can still copy a passage into a "claim". The extract step therefore runs
:func:`strip_verbatim` on every claim against the in-memory passage before anything is
checkpointed: any run of :data:`VERBATIM_RUN_CHARS` or more characters shared with the passage
is cut (a quote the step chose to keep goes through the 200-character budget instead).
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import Field, model_validator

from blogwriter.domain.base import (
    Code,
    DocId,
    Document,
    DomainModel,
    HostName,
    HttpsUrl,
    Slug,
    Timestamp,
    derived_id,
)
from blogwriter.domain.common import Citation, Quote, ServedByRecord, check_quote_budget
from blogwriter.domain.states import ResearchStatus

__all__ = [
    "MAX_CLAIM_CHARS",
    "VERBATIM_RUN_CHARS",
    "Brief",
    "BriefClaim",
    "BriefFinding",
    "BriefStat",
    "QuickSearch",
    "ResearchRun",
    "SearchQuery",
    "SourceRef",
    "strip_verbatim",
    "verbatim_spans",
]

MAX_CLAIM_CHARS: Final = 600
"""A claim is a short summary sentence, never a passage."""

type CitationId = Annotated[Slug, Field(max_length=64)]


class SearchQuery(DomainModel):
    """A ``web_search`` query the run issued (audited against the blocklist)."""

    text: str = Field(min_length=1, max_length=300)
    phase: Literal["allowlist", "open", "quick", "scout"]
    """``allowlist``: ``allowed_domains`` = the pillar allowlist; ``open``: the open-web pass;
    ``quick``: a quick search; ``scout``: weekly scouting."""


class SourceRef(DomainModel):
    """A URL a search returned. Only these (or owner-typed URLs) may be fetched."""

    url: HttpsUrl
    title: str | None = Field(default=None, max_length=500)
    domain: HostName
    phase: Literal["allowlist", "open", "owner"]
    allowlisted: bool = False
    fetched: bool = False
    via: Literal["webiq", "egress"] | None = None
    citation_id: CitationId | None = None
    """Set when the source made it into the brief."""


class ResearchRun(Document):
    """One standard research run (deep mode is deferred)."""

    CONTAINER: ClassVar[str] = "research"
    KIND: ClassVar[str] = "research_run"
    ID_PREFIX: ClassVar[str] = "run"
    OWNER: ClassVar[str] = "research"

    status: ResearchStatus = ResearchStatus.QUEUED
    status_changed_at: Timestamp
    topic: str = Field(min_length=3, max_length=300)
    pillar: Annotated[Slug, Field(max_length=40)]
    audience: str | None = Field(default=None, max_length=300)
    recency_days: int = Field(default=365, ge=1, le=3650)
    article_id: DocId | None = None
    idea_id: DocId | None = None
    owner_urls: list[HttpsUrl] = Field(default_factory=list[str], max_length=10)
    """URLs the owner typed; they may be fetched like search sources."""
    sub_questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=12
    )
    queries: list[SearchQuery] = Field(default_factory=list[SearchQuery], max_length=60)
    sources: list[SourceRef] = Field(default_factory=list[SourceRef], max_length=80)
    brief_id: DocId | None = None
    job_id: DocId | None = None
    webiq_fallback: bool = False
    """Web IQ Browse failed and the run fell back to ``web_search`` (or the egress fetcher)."""
    blocklist_configured: bool = False
    quarantine_reason: Code | None = None
    error_code: Code | None = None
    cost_usd: float = Field(default=0, ge=0)
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=50)
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None

    @staticmethod
    def id_for_pipeline(article_id: str, pipeline_run: int) -> str:
        """The research run of one pipeline run (``pipeline.article`` step ``research``): a
        resumed step finds the run it created instead of starting a second one."""
        return derived_id("run", "pipeline", article_id, pipeline_run)

    @staticmethod
    def id_for_eval(topic_id: str, eval_job_id: str) -> str:
        """The research run that makes an eval topic's brief (``analytics.eval``)."""
        return derived_id("run", "eval", topic_id, eval_job_id)


class BriefFinding(DomainModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS)
    citation_ids: list[CitationId] = Field(default_factory=list[str], max_length=10)


class BriefStat(DomainModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    value: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=300)
    key: bool = False
    """A key statistic needs two independent sources."""
    citation_ids: list[CitationId] = Field(default_factory=list[str], max_length=10)
    as_of: str | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def _key_stats_need_two_sources(self) -> Self:
        if self.key and len(set(self.citation_ids)) < 2:
            raise ValueError("a key statistic needs at least two sources")
        return self


class BriefClaim(DomainModel):
    """A checkable claim with its citation (used by the drafter, gate 2 and derived-claim
    checks)."""

    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS)
    citation_id: CitationId
    quote_ids: list[str] = Field(default_factory=list[str], max_length=5)
    status: Literal["verified", "unverified", "conflict"] = "unverified"
    conflict_note: str | None = Field(default=None, max_length=500)


class Brief(Document):
    """The research brief. The drafter only ever receives the brief (structural guarantee)."""

    CONTAINER: ClassVar[str] = "research"
    KIND: ClassVar[str] = "brief"
    ID_PREFIX: ClassVar[str] = "brief"
    OWNER: ClassVar[str] = "research"

    run_id: DocId | None = None
    article_id: DocId | None = None
    topic: str = Field(min_length=3, max_length=300)
    pillar: Annotated[Slug, Field(max_length=40)]
    audience: str | None = Field(default=None, max_length=300)
    executive_summary: str = Field(min_length=1, max_length=4000)
    findings: list[BriefFinding] = Field(default_factory=list[BriefFinding], max_length=40)
    stats: list[BriefStat] = Field(default_factory=list[BriefStat], max_length=30)
    counterpoints: list[Annotated[str, Field(min_length=1, max_length=600)]] = Field(
        default_factory=list[str], max_length=20
    )
    open_questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=20
    )
    angles: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=20
    )
    hook_seeds: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=20
    )
    claims: list[BriefClaim] = Field(default_factory=list[BriefClaim], max_length=100)
    quotes: list[Quote] = Field(default_factory=list[Quote], max_length=100)
    citations: list[Citation] = Field(default_factory=list[Citation], max_length=60)
    webiq_fallback: bool = False
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=50)
    edited_by_owner: bool = False

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        citation_ids = {citation.id for citation in self.citations}
        if len(citation_ids) != len(self.citations):
            raise ValueError("citation ids must be unique")
        quote_ids = {quote.id for quote in self.quotes}
        referenced = {quote.citation_id for quote in self.quotes}
        referenced |= {claim.citation_id for claim in self.claims}
        for item in (*self.findings, *self.stats):
            referenced |= set(item.citation_ids)
        if not referenced <= citation_ids:
            raise ValueError("a claim, finding, stat or quote cites an unknown citation")
        if any(not set(claim.quote_ids) <= quote_ids for claim in self.claims):
            raise ValueError("a claim names an unknown quote")
        check_quote_budget(self.quotes)
        return self

    def citation(self, citation_id: str) -> Citation | None:
        return next((c for c in self.citations if c.id == citation_id), None)

    @staticmethod
    def id_for(run_id: str) -> str:
        """``brief-<runId>``: one brief per run (``Conflict`` = already written)."""
        return f"brief-{run_id}"


class QuickSearch(Document):
    """A quick search from the Studio sidebar (persisted so quick takes can cite it)."""

    CONTAINER: ClassVar[str] = "research"
    KIND: ClassVar[str] = "quick_search"
    ID_PREFIX: ClassVar[str] = "qs"
    OWNER: ClassVar[str] = "research"

    query: str = Field(min_length=3, max_length=300)
    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    answer: str = Field(default="", max_length=2000)
    """A short model-written summary with inline ``[^src-id]`` markers; never a passage."""
    claims: list[BriefClaim] = Field(default_factory=list[BriefClaim], max_length=30)
    citations: list[Citation] = Field(default_factory=list[Citation], max_length=20)
    queries: list[SearchQuery] = Field(default_factory=list[SearchQuery], max_length=10)
    article_id: DocId | None = None
    post_id: DocId | None = None
    idea_id: DocId | None = None
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=5)

    @model_validator(mode="after")
    def _claims_cite_known_sources(self) -> Self:
        known = {citation.id for citation in self.citations}
        if any(claim.citation_id not in known for claim in self.claims):
            raise ValueError("a claim cites an unknown citation")
        return self


VERBATIM_RUN_CHARS: Final = 40
"""A claim may share no run this long with the passage it summarizes (case and whitespace
insensitive); longer shared runs are passages, not claims."""


def _folded(text: str) -> tuple[str, list[int]]:
    """``text`` lowercased with whitespace runs collapsed, and the source offset of each char."""
    out: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if in_space:
                continue
            out.append(" ")
            in_space = True
        else:
            lowered = char.lower()
            out.append(lowered if len(lowered) == 1 else char)
            in_space = False
        offsets.append(index)
    return "".join(out), offsets


def verbatim_spans(
    text: str, passage: str, *, min_chars: int = VERBATIM_RUN_CHARS
) -> list[tuple[int, int]]:
    """Spans of ``text`` (code-point offsets, merged, in order) that repeat a run of at least
    ``min_chars`` characters of ``passage``, ignoring case and whitespace differences.

    In-memory only: pass the fetched passage, never store it. O(len(text) + len(passage)).
    """
    if min_chars < 1:
        raise ValueError("min_chars must be at least 1")
    folded_text, offsets = _folded(text)
    folded_passage, _ = _folded(passage)
    if len(folded_text) < min_chars or len(folded_passage) < min_chars:
        return []
    grams = {
        folded_passage[index : index + min_chars]
        for index in range(len(folded_passage) - min_chars + 1)
    }
    spans: list[tuple[int, int]] = []
    for index in range(len(folded_text) - min_chars + 1):
        if folded_text[index : index + min_chars] not in grams:
            continue
        start, end = offsets[index], offsets[index + min_chars - 1] + 1
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def strip_verbatim(
    text: str, passage: str, *, min_chars: int = VERBATIM_RUN_CHARS, marker: str = " [...] "
) -> str:
    """``text`` with every :func:`verbatim_spans` run replaced by ``marker`` (and trimmed).

    The extract step stores the result as the claim; an empty result drops the claim.
    """
    spans = verbatim_spans(text, passage, min_chars=min_chars)
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(text[cursor:start])
        parts.append(marker)
        cursor = end
    parts.append(text[cursor:])
    stripped = " ".join("".join(parts).split())
    return "" if stripped.replace("[...]", "").strip() == "" else stripped
