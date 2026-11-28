"""Standard research runs: ``create_run``, ``start_run`` and the ``research.run`` steps.

Steps (contract W-A5), each re-entrant:

``plan``
    The blocklist checks topic, audience and owner URLs again (a hit quarantines before any
    model call). The run goes to ``running``. PLAN writes sub-questions, audience and the
    recency window onto the run (the run is the checkpoint: a planned run is not planned again).
``search``
    RESEARCH with ``web_search``: an allowlist pass (``allowed_domains`` = the pillar's research
    domains) and an open pass. Queries and sources (URLs, titles, domains) go onto the run;
    the finding lines of each pass are kept as search claims (the "web_search alone" material).
    The queries, URLs and titles are audited right away: a hit quarantines before anything is
    fetched.
``extract``
    One atomic fetch-and-extract per source (owner URLs first, then allowlisted, at most
    ``MAX_SOURCES``): ``services.fetcher`` reads the page into memory, RESEARCH (tool-free)
    extracts claims and quotes, every claim loses any run it shares with the passage
    (``strip_verbatim``) and quotes are kept only verbatim and within 200 characters per source.
    The checkpoint holds claims and quotes, never the passage; a resumed step re-fetches what is
    not done. A source that cannot be fetched keeps its search claims only.
``verify``
    SYNTHESIS: deduplication, conflicts, dates against the recency window, statistics (a key
    statistic needs two independent sources). Citations (``src-<slug>``), claims and quotes are
    assembled in code, so every reference resolves.
``brief``
    SYNTHESIS writes the summary, findings, counterpoints, open questions, angles and hook
    seeds; the ``Brief`` (id ``Brief.id_for(runId)``; ``Conflict`` means it is written) and
    ``run.briefId``.
``audit``
    Every query, source URL and title and all brief text against the blocklist. A hit:
    quarantine (brief deleted, ``emit_alert("quarantined", runId)``,
    ``JobFailed("blocklist_hit")``); otherwise the run succeeds. The run is marked
    ``succeeded`` here, not in ``brief``: ``succeeded -> quarantined`` is not an edge of the
    machine.

Every outcome ends with ``jobflow.finish_child`` (the result is recorded on the job even without
a parent); the last step returns the ``ChildResult``. All model calls are public, tool-free
except the two ``web_search`` passes, and ``store=false`` (no background responses exist to
delete). Background-origin calls run inside ``defer_on_brake``.

Nothing here logs or emits text: ids, counts and codes only.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal, NoReturn

from pydantic import Field, JsonValue, ValidationError

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import RouterResult
from blogwriter.ai.types import Message, Request, Sensitivity, Task, Tool, WebSearchTool
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Conflict, NotFound, Store
from blogwriter.domain.base import DomainModel, to_iso
from blogwriter.domain.common import MAX_SERVED_BY, Citation, Quote, ServedByRecord
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_findings,
    check_texts,
)
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    delete_doc,
    read_doc,
    read_singleton,
    update_doc,
)
from blogwriter.domain.jobflow import (
    CHILD_DELIVERY_INTERVAL,
    CHILD_DELIVERY_WINDOW,
    defer_on_brake,
    enqueue_once,
    finish_child,
    require_service,
    step_services,
)
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    RESEARCH_RUN,
    ChildResult,
    EntityName,
    JsonPayload,
    ParentRef,
    ResearchRunInput,
    entity_changed,
    research_job_id,
)
from blogwriter.domain.ports import FetchRefused, PageFetcher, Services
from blogwriter.domain.research import (
    MAX_CLAIM_CHARS,
    Brief,
    BriefClaim,
    BriefFinding,
    BriefStat,
    ResearchRun,
    SearchQuery,
    SourceRef,
)
from blogwriter.domain.settings import Blocklist, Pillar, PillarSettings
from blogwriter.domain.states import RESEARCH_MACHINE, ResearchStatus
from blogwriter.jobs import Job, JobFailed, StepContext
from blogwriter.notify import emit_alert
from blogwriter.research.audit import audit_texts, brief_texts, run_texts, scrub_run
from blogwriter.research.claims import (
    citation_id_for,
    clean_claim,
    clean_source_url,
    clip,
    clip_list,
    cut_quotes,
    defang,
    is_allowlisted,
    parse_findings,
    partial_date,
    source_domain,
    styled,
)
from blogwriter.research.fetch import fetch_page
from blogwriter.research.models import BriefOutput, PlanOutput, SourceExtraction, VerifyOutput
from blogwriter.research.prompting import Prompt, load_prompt
from blogwriter.research.settings import research_settings

__all__ = [
    "CHILD_DELIVERY",
    "STEP_NAMES",
    "ChildDelivery",
    "ExtractState",
    "SearchState",
    "SourceRecord",
    "VerifiedState",
    "audit_step",
    "brief_step",
    "create_run",
    "extract_step",
    "plan_step",
    "search_step",
    "start_run",
    "verify_step",
]

_logger: Final = logging.getLogger("blogwriter.research.run")
register_log_fields(
    "run_id",
    "research_step",
    "source_count",
    "fetched_count",
    "failed_count",
    "claim_count",
    "quote_count",
    "citation_count",
)

STEP_NAMES: Final = ("plan", "search", "extract", "verify", "brief", "audit")

MAX_ALLOWED_DOMAINS: Final = 20
"""``web_search`` ``allowed_domains`` entries sent on the allowlist pass."""
MAX_SOURCES_PER_PASS: Final = 40
MAX_RUN_SOURCES: Final = 80
MAX_RUN_QUERIES: Final = 60
MAX_SUB_QUESTIONS: Final = 6
MAX_PAGE_CLAIMS: Final = 8
MAX_VERIFY_INPUTS: Final = 150
MAX_CITATIONS: Final = 60
MAX_BRIEF_CLAIMS: Final = 100
MAX_BRIEF_QUOTES: Final = 100
MAX_FINDINGS: Final = 40
MAX_STATS: Final = 30
FALLBACK_FINDINGS: Final = 12

_R: Final = ResearchStatus


@dataclass(frozen=True, slots=True)
class ChildDelivery:
    """How long a finished run keeps telling a parent that has not suspended yet."""

    window: timedelta = CHILD_DELIVERY_WINDOW
    interval: timedelta = CHILD_DELIVERY_INTERVAL


CHILD_DELIVERY = ChildDelivery()
"""Read at call time (tests shorten it)."""


# --------------------------------------------------------------------------------------------
# Creating and starting runs
# --------------------------------------------------------------------------------------------


def _owner_fields(
    topic: str, audience: str | None, owner_urls: Sequence[str]
) -> dict[str, str | None]:
    fields: dict[str, str | None] = {"topic": topic, "audience": audience}
    fields.update({f"ownerUrls[{index}]": url for index, url in enumerate(owner_urls)})
    return fields


async def create_run(
    store: Store,
    *,
    topic: str,
    pillar: str,
    now: datetime,
    audience: str | None = None,
    recency_days: int = 365,
    article_id: str | None = None,
    idea_id: str | None = None,
    owner_urls: Sequence[str] = (),
    run_id: str | None = None,
) -> Stored[ResearchRun]:
    """Create a queued research run after the blocklist cleared its topic, audience and owner
    URLs (``ConfidentialText`` / ``BlocklistNotConfigured`` otherwise; nothing is written).

    ``run_id``: a derived id (``ResearchRun.id_for_pipeline`` / ``id_for_eval``); when that run
    exists already, it is returned (a resumed step never makes two).
    """
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    urls = list(dict.fromkeys(owner_urls))
    check_texts(_owner_fields(topic, audience, urls), blocklist)
    doc_id = run_id if run_id is not None else ResearchRun.new_id(now=now)
    run = ResearchRun(
        id=doc_id,
        created_at=stamp,
        updated_at=stamp,
        status=_R.QUEUED,
        status_changed_at=stamp,
        topic=topic,
        pillar=pillar,
        audience=audience,
        recency_days=recency_days,
        article_id=article_id,
        idea_id=idea_id,
        owner_urls=urls,
        job_id=research_job_id(doc_id),
        blocklist_configured=True,
    )
    try:
        return await create_doc(store, run)
    except Conflict:
        if run_id is None:
            raise
        return await read_doc(store, ResearchRun, run_id)


async def start_run(
    services: Services, run: ResearchRun, *, origin: Origin, parent: ParentRef | None = None
) -> Job:
    """Enqueue ``research.run`` for ``run`` (id ``research_job_id(run.id)``, ``reuse``).

    Chained runs pass the parent's origin and ``ParentRef``; the run ends with
    ``finish_child`` either way.
    """
    payload = ResearchRunInput(run_id=run.id, parent=parent, origin=origin)
    return await enqueue_once(
        services.jobs, RESEARCH_RUN, payload.to_json(), job_id=research_job_id(run.id)
    )


# --------------------------------------------------------------------------------------------
# Step state (checkpoints and step outputs: claims, quotes, ids; never a passage)
# --------------------------------------------------------------------------------------------


class SearchClaim(DomainModel):
    text: str
    url: str


class SearchState(JsonPayload):
    passes: list[str] = Field(default_factory=list[str])
    claims: list[SearchClaim] = Field(default_factory=list[SearchClaim])


class SourceRecord(DomainModel):
    """What the extract step kept of one source (claims and quotes only)."""

    url: str
    status: Literal["fetched", "failed", "skipped"]
    reason: str | None = None
    via: Literal["webiq", "egress"] | None = None
    title: str | None = None
    publisher: str | None = None
    published: str | None = None
    fetched_at: str | None = None
    page_claims: list[str] = Field(default_factory=list[str])
    search_claims: list[str] = Field(default_factory=list[str])
    quotes: list[str] = Field(default_factory=list[str])


class ExtractState(JsonPayload):
    sources: list[SourceRecord] = Field(default_factory=list[SourceRecord])
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord])


class VerifiedState(JsonPayload):
    citations: list[Citation] = Field(default_factory=list[Citation])
    claims: list[BriefClaim] = Field(default_factory=list[BriefClaim])
    stats: list[BriefStat] = Field(default_factory=list[BriefStat])
    quotes: list[Quote] = Field(default_factory=list[Quote])
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord])


def _state[S: JsonPayload](cls: type[S], value: JsonValue) -> S:
    if value is None:
        return cls()
    try:
        return cls.from_json(value)
    except (ValueError, ValidationError):
        return cls()


def _accounting(run: ResearchRun, records: Sequence[ServedByRecord]) -> dict[str, object]:
    """``servedBy`` (newest 50) and ``costUsd`` after adding ``records``.

    Records the run already holds are not added again (a step re-applying its update after a
    crash), counted as a multiset so two identical calls in one batch both count.
    """
    held = Counter(record.model_dump_json() for record in run.served_by)
    added: list[ServedByRecord] = []
    for record in records:
        key = record.model_dump_json()
        if held[key] > 0:
            held[key] -= 1
            continue
        added.append(record)
    cost = run.cost_usd + sum(record.cost_usd or 0.0 for record in added)
    return {
        "served_by": [*run.served_by, *added][-MAX_SERVED_BY:],
        "cost_usd": round(cost, 6),
    }


# --------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------


def _request(
    prompt: Prompt,
    text: str,
    *,
    schema: type[PlanOutput | SourceExtraction | VerifyOutput | BriefOutput] | None = None,
    tools: tuple[Tool, ...] = (),
    max_tool_calls: int | None = None,
) -> Request:
    return Request(
        system=prompt.text,
        messages=(Message.user(text),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=schema,
        tools=tools,
        max_tool_calls=max_tool_calls,
    )


def _clean_texts(texts: Sequence[str], blocklist: Blocklist) -> list[str]:
    """Owner-editable context (pillar name and topics) without anything the blocklist flags."""
    return [
        text
        for text in texts
        if text and blocklist.configured and not blocklist_findings(text, blocklist)
    ]


def _pillar_line(pillar: Pillar | None, blocklist: Blocklist) -> str | None:
    if pillar is None:
        return None
    parts = _clean_texts([pillar.name, pillar.topics], blocklist)
    if not parts:
        return None
    return f"Pillar: {defang(parts[0])}" + (f" ({defang(parts[1])})" if len(parts) > 1 else "")


def _cutoff(now: datetime, recency_days: int) -> str:
    return (now - timedelta(days=recency_days)).date().isoformat()


def _plan_request(
    run: ResearchRun, pillar: Pillar | None, blocklist: Blocklist, prompt: Prompt
) -> Request:
    lines = [f"Topic: {defang(run.topic)}"]
    pillar_line = _pillar_line(pillar, blocklist)
    if pillar_line:
        lines.append(pillar_line)
    lines.append(f"Audience: {defang(run.audience)}" if run.audience else "Audience: not given")
    lines.append(f"Recency window: at most {run.recency_days} days.")
    if run.owner_urls:
        lines.append(
            f"The owner also supplied {len(run.owner_urls)} source URL(s); they are read "
            "separately."
        )
    return _request(prompt, "\n".join(lines), schema=PlanOutput)


def _search_request(
    run: ResearchRun,
    phase: Literal["allowlist", "open"],
    domains: tuple[str, ...],
    prompt: Prompt,
    *,
    cutoff: str,
    max_tool_calls: int,
) -> Request:
    lines = [f"Topic: {defang(run.topic)}"]
    if run.audience:
        lines.append(f"Audience: {defang(run.audience)}")
    lines.append("Sub-questions:")
    questions = run.sub_questions or [run.topic]
    lines.extend(f"{index}. {defang(question)}" for index, question in enumerate(questions, 1))
    if phase == "allowlist":
        lines.append(
            "Pass: allowlist. Only these trusted domains are searched: " + ", ".join(domains) + "."
        )
    else:
        lines.append("Pass: open web. Search anywhere; prefer primary sources.")
    lines.append(f"Recency window: prefer sources published on or after {cutoff}.")
    return _request(
        prompt,
        "\n".join(lines),
        tools=(WebSearchTool(allowed_domains=domains),),
        max_tool_calls=max_tool_calls,
    )


def _extract_request(
    run: ResearchRun, url: str, title: str | None, passage: str, prompt: Prompt
) -> Request:
    lines = [f"Topic: {defang(run.topic)}"]
    if run.sub_questions:
        lines.append("Sub-questions:")
        lines.extend(f"- {defang(question)}" for question in run.sub_questions)
    lines.append(f"Source URL: {url}")
    if title:
        lines.append(f"Source title: {defang(title)}")
    lines.append("<<<PAGE")
    lines.append(defang(passage))
    lines.append("PAGE>>>")
    return _request(prompt, "\n".join(lines), schema=SourceExtraction)


# --------------------------------------------------------------------------------------------
# Search results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SearchOutcome:
    phase: Literal["allowlist", "open"]
    queries: tuple[str, ...]
    sources: tuple[tuple[str, str | None], ...]
    claims: tuple[SearchClaim, ...]

    def audited(self) -> list[str]:
        texts = list(self.queries)
        for url, title in self.sources:
            texts.append(url)
            if title:
                texts.append(title)
        return texts


def _search_outcome(result: RouterResult, phase: Literal["allowlist", "open"]) -> _SearchOutcome:
    completion = result.completion
    titles: dict[str, str | None] = {}
    for citation in completion.citations:
        url = clean_source_url(citation.url)
        if url is not None and url not in titles:
            titles[url] = clip(citation.title, 500) or None
    for raw in completion.search_sources:
        url = clean_source_url(raw)
        if url is not None and url not in titles:
            titles[url] = None
    claims: list[SearchClaim] = []
    for sentence, raw_url in parse_findings(completion.text):
        url = clean_source_url(raw_url) if raw_url else None
        if url is None or url not in titles:
            continue  # a URL the search did not return: never trusted, never fetched
        text = clean_claim(sentence, None)
        if text is not None:
            claims.append(SearchClaim(text=text, url=url))
    queries = [clip(query, 300) for query in completion.search_queries]
    sources = list(titles.items())[:MAX_SOURCES_PER_PASS]
    kept = {url for url, _ in sources}
    return _SearchOutcome(
        phase=phase,
        queries=tuple(dict.fromkeys(query for query in queries if query)),
        sources=tuple(sources),
        claims=tuple(claim for claim in claims if claim.url in kept),
    )


def _merge_search(
    current: ResearchRun,
    *,
    found: _SearchOutcome,
    allowlist: tuple[str, ...],
    record: ServedByRecord,
) -> ResearchRun:
    queries = list(current.queries)
    seen = {(query.text, query.phase) for query in queries}
    for text in found.queries:
        if (text, found.phase) not in seen and len(queries) < MAX_RUN_QUERIES:
            seen.add((text, found.phase))
            queries.append(SearchQuery(text=text, phase=found.phase))
    sources = list(current.sources)
    known = {source.url for source in sources}
    for url, title in found.sources:
        domain = source_domain(url)
        if url in known or domain is None or len(sources) >= MAX_RUN_SOURCES:
            continue
        known.add(url)
        sources.append(
            SourceRef(
                url=url,
                title=title,
                domain=domain,
                phase=found.phase,
                allowlisted=is_allowlisted(domain, allowlist),
            )
        )
    return current.model_copy(
        update={"queries": queries, "sources": sources, **_accounting(current, [record])}
    )


def _with_owner_sources(current: ResearchRun, *, allowlist: tuple[str, ...]) -> ResearchRun | None:
    known = {source.url for source in current.sources}
    owner: list[SourceRef] = []
    for url in current.owner_urls:
        domain = source_domain(url)
        if url in known or domain is None:
            continue
        known.add(url)
        owner.append(
            SourceRef(
                url=url,
                domain=domain,
                phase="owner",
                allowlisted=is_allowlisted(domain, allowlist),
            )
        )
    if not owner:
        return None
    return current.model_copy(update={"sources": [*owner, *current.sources][:MAX_RUN_SOURCES]})


def _candidates(run: ResearchRun, limit: int) -> list[SourceRef]:
    """Sources to read: owner URLs first, then allowlisted, then the open pass."""
    owner = [source for source in run.sources if source.phase == "owner"]
    trusted = [source for source in run.sources if source.phase != "owner" and source.allowlisted]
    rest = [source for source in run.sources if source.phase != "owner" and not source.allowlisted]
    return [*owner, *trusted, *rest][:limit]


def _record_fetches(
    current: ResearchRun,
    *,
    records: Sequence[SourceRecord],
    fallback: bool,
    served: Sequence[ServedByRecord],
) -> ResearchRun:
    fetched = {record.url: record for record in records if record.status == "fetched"}
    sources = [
        source.model_copy(update={"fetched": True, "via": fetched[source.url].via})
        if source.url in fetched
        else source
        for source in current.sources
    ]
    return current.model_copy(
        update={"sources": sources, "webiq_fallback": fallback, **_accounting(current, served)}
    )


# --------------------------------------------------------------------------------------------
# Verification material
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClaimInput:
    id: str
    text: str
    citation_id: str
    from_page: bool
    date: str | None
    allowlisted: bool


@dataclass(frozen=True, slots=True)
class _Material:
    citations: tuple[Citation, ...]
    claims: tuple[_ClaimInput, ...]
    quotes: tuple[Quote, ...]

    @classmethod
    def build(cls, run: ResearchRun, extract: ExtractState, *, accessed_at: str) -> _Material:
        by_url = {source.url: source for source in run.sources}
        citations: list[Citation] = []
        claims: list[_ClaimInput] = []
        quotes: list[Quote] = []
        used_ids: set[str] = set()
        for record in extract.sources:
            texts = [*record.page_claims, *record.search_claims]
            if not texts and not record.quotes:
                continue
            if len(citations) >= MAX_CITATIONS:
                break
            source = by_url.get(record.url)
            domain = source.domain if source is not None else source_domain(record.url)
            if domain is None:
                continue
            citation_id = citation_id_for(record.url)
            if citation_id in used_ids:
                citation_id = f"{citation_id}-{len(citations)}"
            used_ids.add(citation_id)
            allowlisted = source.allowlisted if source is not None else False
            title = record.title or (source.title if source is not None else None) or domain
            citations.append(
                Citation(
                    id=citation_id,
                    url=record.url,
                    title=clip(title, 500) or domain,
                    publisher=clip(record.publisher, 200) or None,
                    date=partial_date(record.published),
                    domain=domain,
                    allowlisted=allowlisted,
                    accessed_at=record.fetched_at or accessed_at,
                )
            )
            for text, from_page in (
                *((text, True) for text in record.page_claims),
                *((text, False) for text in record.search_claims),
            ):
                if len(claims) < MAX_VERIFY_INPUTS:
                    claims.append(
                        _ClaimInput(
                            id=f"c{len(claims) + 1}",
                            text=text,
                            citation_id=citation_id,
                            from_page=from_page,
                            date=partial_date(record.published),
                            allowlisted=allowlisted,
                        )
                    )
            for text in record.quotes:
                if len(quotes) < MAX_BRIEF_QUOTES:
                    quote = Quote(id=f"q-{len(quotes) + 1}", citation_id=citation_id, text=text)
                    quotes.append(quote)
        return cls(citations=tuple(citations), claims=tuple(claims), quotes=tuple(quotes))

    def prompt_json(self) -> str:
        return json.dumps(
            [
                {
                    "id": claim.id,
                    "text": defang(claim.text),
                    "citationId": claim.citation_id,
                    "sourceDate": claim.date,
                    "allowlisted": claim.allowlisted,
                    "fromPage": claim.from_page,
                }
                for claim in self.claims
            ],
            ensure_ascii=False,
        )

    def verified(self, output: VerifyOutput | None, *, cutoff: str) -> VerifiedState:
        """Claims and statistics with every reference checked in code."""
        by_id = {claim.id: claim for claim in self.claims}
        domains = {citation.id: citation.domain for citation in self.citations}
        claims: list[BriefClaim] = []
        if output is None:
            seen: set[str] = set()
            for item in self.claims:
                key = item.text.casefold()
                if key in seen or len(claims) >= MAX_BRIEF_CLAIMS:
                    continue
                seen.add(key)
                claims.append(
                    BriefClaim(
                        id=f"cl-{len(claims) + 1}",
                        text=clip(item.text, MAX_CLAIM_CHARS),
                        citation_id=item.citation_id,
                        status="unverified",
                    )
                )
        else:
            for out in output.claims:
                text = clip(out.text, MAX_CLAIM_CHARS)
                inputs = [by_id[source] for source in out.sources if source in by_id]
                if not text or not inputs or len(claims) >= MAX_BRIEF_CLAIMS:
                    continue
                primary = max(
                    inputs, key=lambda item: (item.from_page, item.allowlisted, item.date or "")
                )
                status = out.status
                if status == "verified" and not any(
                    item.from_page and _in_window(item.date, cutoff) for item in inputs
                ):
                    status = "unverified"
                note = clip(out.conflict_note, 500) if status == "conflict" else ""
                claims.append(
                    BriefClaim(
                        id=f"cl-{len(claims) + 1}",
                        text=text,
                        citation_id=primary.citation_id,
                        status=status,
                        conflict_note=note or None,
                    )
                )
        stats: list[BriefStat] = []
        for out in output.stats if output is not None else []:
            value = " ".join(out.value.split())
            label = clip(out.label, 300)
            ids = [
                by_id[ref].citation_id if ref in by_id else ref
                for ref in out.citation_ids
                if ref in by_id or ref in domains
            ]
            cited = list(dict.fromkeys(ids))[:10]
            if not value or len(value) > 40 or not label or not cited:
                continue
            if len(stats) >= MAX_STATS:
                break
            independent = len({domains[ref] for ref in cited}) >= 2
            stats.append(
                BriefStat(
                    id=f"st-{len(stats) + 1}",
                    value=value,
                    label=label,
                    key=out.key and independent,
                    citation_ids=cited,
                    as_of=partial_date(out.as_of),
                )
            )
        return VerifiedState(
            citations=list(self.citations), claims=claims, stats=stats, quotes=list(self.quotes)
        )


def _in_window(date: str | None, cutoff: str) -> bool:
    """Undated sources count as in the window; a partial date compares at its precision."""
    return date is None or date >= cutoff[: len(date)]


def _verify_request(run: ResearchRun, material: _Material, prompt: Prompt, cutoff: str) -> Request:
    text = "\n".join(
        [
            f"Topic: {defang(run.topic)}",
            f"Recency window: sources dated on or after {cutoff}.",
            "<<<CLAIMS",
            material.prompt_json(),
            "CLAIMS>>>",
        ]
    )
    return _request(prompt, text, schema=VerifyOutput)


def _brief_request(
    run: ResearchRun,
    verified: VerifiedState,
    pillar: Pillar | None,
    blocklist: Blocklist,
    prompt: Prompt,
) -> Request:
    research = {
        "claims": [
            {
                "id": claim.id,
                "text": defang(claim.text),
                "citationId": claim.citation_id,
                "status": claim.status,
                "conflictNote": defang(claim.conflict_note) if claim.conflict_note else None,
            }
            for claim in verified.claims
        ],
        "stats": [
            {
                "value": defang(stat.value),
                "label": defang(stat.label),
                "citationIds": stat.citation_ids,
                "key": stat.key,
                "asOf": stat.as_of,
            }
            for stat in verified.stats
        ],
        "sources": [
            {
                "citationId": citation.id,
                "title": defang(citation.title),
                "publisher": defang(citation.publisher) if citation.publisher else None,
                "date": citation.date,
                "allowlisted": citation.allowlisted,
            }
            for citation in verified.citations
        ],
        "quotes": [
            {"citationId": quote.citation_id, "text": defang(quote.text)}
            for quote in verified.quotes
        ],
    }
    lines = [f"Topic: {defang(run.topic)}"]
    pillar_line = _pillar_line(pillar, blocklist)
    if pillar_line:
        lines.append(pillar_line)
    if run.audience:
        lines.append(f"Audience: {defang(run.audience)}")
    lines.extend(["<<<RESEARCH", json.dumps(research, ensure_ascii=False), "RESEARCH>>>"])
    return _request(prompt, "\n".join(lines), schema=BriefOutput)


def build_brief(
    run: ResearchRun,
    verified: VerifiedState,
    output: BriefOutput | None,
    *,
    served_by: Sequence[ServedByRecord],
    stamp: str,
) -> Brief:
    """The brief: written parts from ``output`` (references checked and repaired), claims,
    quotes, statistics and citations from ``verified``. Without usable model output the
    findings are the verified claims."""
    known = {citation.id for citation in verified.citations}
    claim_citation = {claim.id: claim.citation_id for claim in verified.claims}

    def cited(refs: Sequence[str]) -> list[str]:
        ids = [claim_citation.get(ref, ref) for ref in refs]
        return list(dict.fromkeys(ref for ref in ids if ref in known))[:10]

    findings: list[BriefFinding] = []
    if output is not None:
        for item in output.findings:
            text = clip(item.text, MAX_CLAIM_CHARS)
            refs = cited(item.citation_ids)
            if text and refs and len(findings) < MAX_FINDINGS:
                findings.append(
                    BriefFinding(id=f"f-{len(findings) + 1}", text=text, citation_ids=refs)
                )
    if not findings:
        ordered = sorted(verified.claims, key=lambda claim: claim.status != "verified")
        findings = [
            BriefFinding(id=f"f-{index}", text=claim.text, citation_ids=[claim.citation_id])
            for index, claim in enumerate(ordered[:FALLBACK_FINDINGS], 1)
        ]
    summary = clip(output.executive_summary, 4000) if output is not None else ""
    if not summary:
        summary = (
            "Automatic summary unavailable. The findings below come from "
            f"{len(verified.citations)} source(s); review them before drafting."
        )
    return Brief(
        id=Brief.id_for(run.id),
        created_at=stamp,
        updated_at=stamp,
        run_id=run.id,
        article_id=run.article_id,
        topic=run.topic,
        pillar=run.pillar,
        audience=run.audience,
        executive_summary=summary,
        findings=findings,
        stats=verified.stats,
        counterpoints=clip_list(output.counterpoints if output else (), 600, 20),
        open_questions=clip_list(output.open_questions if output else (), 300, 20),
        angles=styled(clip_list(output.angles if output else (), 300, 20), ai_tells=False),
        hook_seeds=styled(clip_list(output.hook_seeds if output else (), 300, 20), ai_tells=True),
        claims=verified.claims,
        quotes=verified.quotes,
        citations=verified.citations,
        webiq_fallback=run.webiq_fallback,
        served_by=list(served_by)[-MAX_SERVED_BY:],
    )


def _link_brief(
    current: ResearchRun,
    *,
    brief_id: str,
    cited: Mapping[str, str],
    records: Sequence[ServedByRecord],
) -> ResearchRun:
    sources = [
        source.model_copy(update={"citation_id": cited[source.url]})
        if source.url in cited
        else source
        for source in current.sources
    ]
    return current.model_copy(
        update={"brief_id": brief_id, "sources": sources, **_accounting(current, records)}
    )


# --------------------------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------------------------


class _Settled(JobFailed):
    """A failure already recorded on the run and the job (quarantine, cancellation)."""


@dataclass(frozen=True, slots=True)
class _Plan:
    sub_questions: list[str]
    audience: str | None
    recency_days: int


def _plan_from(result: RouterResult, run: ResearchRun) -> _Plan:
    value = result.value
    if result.refused or not isinstance(value, PlanOutput):
        return _Plan(sub_questions=[run.topic], audience=None, recency_days=run.recency_days)
    questions = clip_list(value.sub_questions, 300, MAX_SUB_QUESTIONS) or [run.topic]
    audience = clip(value.audience, 300) or None
    recency = max(1, min(value.recency_days, 3650))
    return _Plan(sub_questions=questions, audience=audience, recency_days=recency)


class _RunJob:
    """One step's view of a ``research.run`` job: services, input, the run, the outcomes."""

    def __init__(self, ctx: StepContext) -> None:
        self.ctx = ctx
        self.services = step_services()
        try:
            self.input = ResearchRunInput.from_json(ctx.input)
        except (ValueError, ValidationError):
            raise JobFailed("invalid_input") from None
        self.run_id = self.input.run_id
        self.settings = research_settings()

    @property
    def store(self) -> Store:
        return self.services.store

    def now(self) -> datetime:
        return self.services.clock.now()

    def stamp(self) -> str:
        return to_iso(self.now())

    # -- reading ------------------------------------------------------------------------------

    async def load(self) -> ResearchRun:
        try:
            return (await read_doc(self.store, ResearchRun, self.run_id)).doc
        except NotFound:
            raise JobFailed("run_missing", "The research run no longer exists.") from None

    async def blocklist(self) -> Blocklist:
        blocklist, _ = await read_singleton(self.store, Blocklist, now=self.stamp())
        return blocklist

    async def pillar(self, pillar_id: str) -> Pillar | None:
        pillars, _ = await read_singleton(self.store, PillarSettings, now=self.stamp())
        return pillars.get(pillar_id)

    # -- outcomes -----------------------------------------------------------------------------

    async def finish(self, result: ChildResult) -> None:
        delivery = CHILD_DELIVERY
        await finish_child(
            self.ctx,
            self.services.jobs,
            self.input.parent,
            result,
            window=delivery.window,
            interval=delivery.interval,
        )

    async def changed(self, entity: EntityName, doc_id: str) -> None:
        await self.ctx.emit(ENTITY_CHANGED, entity_changed(entity, doc_id))

    async def cancelled(self) -> NoReturn:
        await self.finish(ChildResult(ok=False, error_code="cancelled"))
        raise _Settled("cancelled", "The research run was cancelled.")

    async def enter(self) -> ResearchRun | None:
        """The run, or ``None`` when it already succeeded (the step has nothing to do).
        A quarantined or cancelled run settles the job."""
        run = await self.load()
        match run.status:
            case _R.SUCCEEDED:
                return None
            case _R.QUARANTINED:
                await self.settle_quarantine(run.quarantine_reason or "blocklist_hit")
            case _R.CANCELLED:
                await self.cancelled()
            case _:
                return run

    async def mark_running(self) -> ResearchRun:
        stamp = self.stamp()
        job_id = self.ctx.job_id

        def mutate(run: ResearchRun) -> ResearchRun | None:
            status = run.status
            if status == _R.FAILED:
                status = RESEARCH_MACHINE.transition(status, _R.QUEUED)
            if status == _R.QUEUED:
                status = RESEARCH_MACHINE.transition(status, _R.RUNNING)
            if status != _R.RUNNING:
                return None
            if run.status == _R.RUNNING and run.job_id == job_id:
                return None
            changed = run.status != _R.RUNNING
            return run.model_copy(
                update={
                    "status": _R.RUNNING,
                    "status_changed_at": stamp if changed else run.status_changed_at,
                    "updated_at": stamp,
                    "started_at": run.started_at or stamp,
                    "finished_at": None,
                    "error_code": None,
                    "job_id": job_id,
                }
            )

        stored = await update_doc(self.store, ResearchRun, self.run_id, mutate)
        if stored.doc.status == _R.CANCELLED:
            await self.cancelled()
        if stored.doc.status != _R.RUNNING:
            raise JobFailed("run_state", "The research run is not running.")
        return stored.doc

    async def update(self, change: Callable[[ResearchRun], ResearchRun | None]) -> ResearchRun:
        """Apply ``change`` to the running run (CAS). A run the owner cancelled meanwhile
        settles the job instead."""
        stamp = self.stamp()

        def mutate(run: ResearchRun) -> ResearchRun | None:
            if run.status != _R.RUNNING:
                return None
            changed = change(run)
            return None if changed is None else changed.model_copy(update={"updated_at": stamp})

        stored = await update_doc(self.store, ResearchRun, self.run_id, mutate)
        if stored.doc.status == _R.CANCELLED:
            await self.cancelled()
        if stored.doc.status != _R.RUNNING:
            raise JobFailed("run_state", "The research run is not running.")
        return stored.doc

    async def fail(self, code: str) -> None:
        """Record a permanent failure on the run (when it can still fail) and the job."""
        stamp = self.stamp()

        def mutate(run: ResearchRun) -> ResearchRun | None:
            status = run.status
            if status == _R.QUEUED:
                status = RESEARCH_MACHINE.transition(status, _R.RUNNING)
            if status != _R.RUNNING:
                return None
            return run.model_copy(
                update={
                    "status": RESEARCH_MACHINE.transition(status, _R.FAILED),
                    "status_changed_at": stamp,
                    "updated_at": stamp,
                    "finished_at": stamp,
                    "error_code": code,
                }
            )

        try:
            await update_doc(self.store, ResearchRun, self.run_id, mutate)
        except NotFound:
            pass
        else:
            await self.changed("research_run", self.run_id)
        _logger.warning("research_run_failed", extra={"run_id": self.run_id, "reason": code})
        await self.finish(ChildResult(ok=False, error_code=code))

    async def quarantine(self, blocklist: Blocklist, *, reason: str = "blocklist_hit") -> NoReturn:
        """Stop the run for good: ``quarantined`` (the matched queries and sources scrubbed),
        the brief deleted, an out-of-band alert, ``JobFailed(reason)``."""
        stamp = self.stamp()

        def mutate(run: ResearchRun) -> ResearchRun | None:
            status = run.status
            if status == _R.FAILED:
                status = RESEARCH_MACHINE.transition(status, _R.QUEUED)
            if status == _R.QUEUED:
                status = RESEARCH_MACHINE.transition(status, _R.RUNNING)
            if status != _R.RUNNING:
                return None
            return scrub_run(run, blocklist).model_copy(
                update={
                    "status": RESEARCH_MACHINE.transition(status, _R.QUARANTINED),
                    "status_changed_at": stamp,
                    "updated_at": stamp,
                    "finished_at": stamp,
                    "quarantine_reason": reason,
                    "error_code": reason,
                    "brief_id": None,
                }
            )

        stored = await update_doc(self.store, ResearchRun, self.run_id, mutate)
        if stored.doc.status == _R.CANCELLED:
            await self.cancelled()
        if stored.doc.status != _R.QUARANTINED:
            raise JobFailed("run_state", "The research run is not running.")
        await self.settle_quarantine(reason)

    async def settle_quarantine(self, reason: str) -> NoReturn:
        """The idempotent rest of a quarantine (also on a resumed step)."""
        with contextlib.suppress(NotFound):
            await delete_doc(self.store, Brief, Brief.id_for(self.run_id))
        emit_alert("quarantined", self.run_id)
        _logger.warning("research_run_quarantined", extra={"run_id": self.run_id, "reason": reason})
        await self.changed("research_run", self.run_id)
        await self.finish(ChildResult(ok=False, error_code=reason))
        raise _Settled(reason, "A blocklisted term appeared in the research; the run was stopped.")

    # -- model calls --------------------------------------------------------------------------

    async def call(self, task: Task, request: Request) -> RouterResult:
        ai = require_service(self.services.ai, "ai")
        with defer_on_brake(self.now()):
            return await ai.run(task, request, origin=self.input.origin, job_id=self.ctx.job_id)

    def served(self, result: RouterResult, *, step: str, prompt: Prompt) -> ServedByRecord:
        return ServedByRecord.from_served_by(
            result.served_by,
            step=step,
            at=self.stamp(),
            refused=result.refused or bool(result.refusals),
            prompt_version=prompt.version,
            cost_usd=round(float(result.cost_usd), 6),
        )

    def log_step(self, step: str, **counts: int) -> None:
        _logger.info(
            "research_step_done", extra={"run_id": self.run_id, "research_step": step, **counts}
        )

    # -- steps --------------------------------------------------------------------------------

    async def plan(self) -> JsonValue:
        run = await self.enter()
        if run is None:
            return {"skipped": "succeeded"}
        blocklist = await self.blocklist()
        try:
            check_texts(_owner_fields(run.topic, run.audience, run.owner_urls), blocklist)
        except BlocklistNotConfigured:
            raise JobFailed(
                "blocklist_not_configured", "Save the blocklist once in Settings > Policy."
            ) from None
        except ConfidentialText:
            await self.quarantine(blocklist)
        run = await self.mark_running()
        if run.sub_questions:  # planned before a crash or a retry: the run is the checkpoint
            return {"subQuestions": len(run.sub_questions)}
        pillar = await self.pillar(run.pillar)
        prompt = load_prompt("plan")
        result = await self.call(Task.PLAN, _plan_request(run, pillar, blocklist, prompt))
        plan = _plan_from(result, run)
        if audit_texts([*plan.sub_questions, plan.audience or ""], blocklist):
            await self.quarantine(blocklist)
        record = self.served(result, step="plan", prompt=prompt)

        def change(current: ResearchRun) -> ResearchRun:
            return current.model_copy(
                update={
                    "sub_questions": plan.sub_questions,
                    "audience": current.audience or plan.audience,
                    "recency_days": min(current.recency_days, plan.recency_days),
                    **_accounting(current, [record]),
                }
            )

        run = await self.update(change)
        await self.changed("research_run", run.id)
        self.log_step("plan", count=len(plan.sub_questions))
        return {"subQuestions": len(plan.sub_questions)}

    async def search(self) -> JsonValue:
        run = await self.enter()
        if run is None:
            return SearchState().to_json()
        run = await self.mark_running()
        blocklist = await self.blocklist()
        pillar = await self.pillar(run.pillar)
        allowlist = tuple(pillar.research_domains) if pillar is not None else ()
        state = _state(SearchState, self.ctx.output)
        prompt = load_prompt("search")
        cutoff = _cutoff(self.now(), run.recency_days)
        passes: list[tuple[Literal["allowlist", "open"], tuple[str, ...]]] = []
        if allowlist:
            passes.append(("allowlist", allowlist[:MAX_ALLOWED_DOMAINS]))
        passes.append(("open", ()))
        for phase, domains in passes:
            if phase in state.passes:
                continue
            request = _search_request(
                run,
                phase,
                domains,
                prompt,
                cutoff=cutoff,
                max_tool_calls=self.settings.max_tool_calls,
            )
            result = await self.call(Task.RESEARCH, request)
            found = _search_outcome(result, phase)
            if audit_texts(found.audited(), blocklist):
                await self.quarantine(blocklist)
            record = self.served(result, step=f"search_{phase}", prompt=prompt)
            run = await self.update(
                functools.partial(_merge_search, found=found, allowlist=allowlist, record=record)
            )
            state.passes.append(phase)
            state.claims.extend(found.claims)
            await self.ctx.checkpoint(state.to_json())
        run = await self.update(functools.partial(_with_owner_sources, allowlist=allowlist))
        await self.changed("research_run", run.id)
        self.log_step(
            "search",
            source_count=len(run.sources),
            claim_count=len(state.claims),
            count=len(run.queries),
        )
        return state.to_json()

    async def extract(self) -> JsonValue:
        run = await self.enter()
        if run is None:
            return ExtractState().to_json()
        run = await self.mark_running()
        search = _state(SearchState, self.ctx.outputs.get("search"))
        by_url: dict[str, list[str]] = {}
        for claim in search.claims:
            by_url.setdefault(claim.url, []).append(claim.text)
        candidates = _candidates(run, self.settings.max_sources)
        allowed = frozenset({source.url for source in run.sources} | set(run.owner_urls))
        state = _state(ExtractState, self.ctx.output)
        done = {record.url for record in state.sources}
        prompt = load_prompt("extract")
        for index, source in enumerate(candidates, 1):
            if source.url in done:
                continue
            record, served = await self._extract_one(
                run, source, allowed, by_url.get(source.url, []), prompt
            )
            state.sources.append(record)
            if served is not None:
                state.served_by.append(served)
            await self.ctx.checkpoint(state.to_json())
            await self.ctx.emit(
                "progress", {"done": index, "total": len(candidates), "label": "Reading sources"}
            )
        read = {record.url for record in state.sources}
        titles = {source.url: source.title for source in run.sources}
        for url, texts in by_url.items():
            if url not in read:
                state.sources.append(
                    SourceRecord(
                        url=url, status="skipped", title=titles.get(url), search_claims=texts
                    )
                )
        attempted = [record for record in state.sources if record.status != "skipped"]
        fallback = any(record.status == "failed" or record.via == "egress" for record in attempted)
        run = await self.update(
            functools.partial(
                _record_fetches, records=state.sources, fallback=fallback, served=state.served_by
            )
        )
        await self.changed("research_run", run.id)
        self.log_step(
            "extract",
            source_count=len(candidates),
            fetched_count=sum(record.status == "fetched" for record in state.sources),
            failed_count=sum(record.status == "failed" for record in state.sources),
            claim_count=sum(len(record.page_claims) for record in state.sources),
            quote_count=sum(len(record.quotes) for record in state.sources),
        )
        return state.to_json()

    async def _extract_one(
        self,
        run: ResearchRun,
        source: SourceRef,
        allowed: frozenset[str],
        search_claims: list[str],
        prompt: Prompt,
    ) -> tuple[SourceRecord, ServedByRecord | None]:
        """Fetch one source and extract from it. The passage never leaves this function."""
        fetcher: PageFetcher | None = self.services.fetcher
        if fetcher is None:
            return SourceRecord(
                url=source.url,
                status="failed",
                reason="unavailable",
                title=source.title,
                search_claims=search_claims,
            ), None
        try:
            page = await fetch_page(fetcher, source.url, allowed_urls=allowed, query=run.topic)
        except FetchRefused as exc:
            return SourceRecord(
                url=source.url,
                status="failed",
                reason=exc.reason,
                title=source.title,
                search_claims=search_claims,
            ), None
        except Exception as exc:  # a fetcher bug must not fail the run: the source is skipped
            _logger.warning(
                "research_fetch_error", extra={"run_id": self.run_id, **describe_exception(exc)}
            )
            return SourceRecord(
                url=source.url,
                status="failed",
                reason="unavailable",
                title=source.title,
                search_claims=search_claims,
            ), None
        passage = page.text
        result = await self.call(
            Task.RESEARCH, _extract_request(run, source.url, page.title, passage, prompt)
        )
        served = self.served(result, step="extract", prompt=prompt)
        value = result.value
        extraction = value if isinstance(value, SourceExtraction) and not result.refused else None
        page_claims: list[str] = []
        quotes: list[str] = []
        if extraction is not None and extraction.relevant:
            cleaned = (clean_claim(text, passage) for text in extraction.claims[:MAX_PAGE_CLAIMS])
            page_claims = list(dict.fromkeys(text for text in cleaned if text))
            quotes = cut_quotes(extraction.quotes[:4], passage)
        kept_search = (clean_claim(text, passage) for text in search_claims)
        record = SourceRecord(
            url=source.url,
            status="fetched",
            via=page.via,
            title=clip(page.title, 500) or source.title,
            publisher=(clip(extraction.publisher, 200) or None) if extraction else None,
            published=partial_date(extraction.published) if extraction else None,
            fetched_at=to_iso(page.fetched_at),
            page_claims=page_claims,
            search_claims=list(dict.fromkeys(text for text in kept_search if text)),
            quotes=quotes,
        )
        return record, served

    async def verify(self) -> JsonValue:
        run = await self.enter()
        if run is None:
            return VerifiedState().to_json()
        run = await self.mark_running()
        extract = _state(ExtractState, self.ctx.outputs.get("extract"))
        material = _Material.build(run, extract, accessed_at=self.stamp())
        if not material.claims:
            raise JobFailed("no_sources", "The research found no usable sources for this topic.")
        prompt = load_prompt("verify")
        cutoff = _cutoff(self.now(), run.recency_days)
        result = await self.call(Task.SYNTHESIS, _verify_request(run, material, prompt, cutoff))
        served = self.served(result, step="verify", prompt=prompt)
        value = result.value
        output = value if isinstance(value, VerifyOutput) and not result.refused else None
        verified = material.verified(output, cutoff=cutoff)
        verified.served_by = [served]
        await self.update(functools.partial(_with_records, records=[served]))
        self.log_step(
            "verify",
            claim_count=len(verified.claims),
            citation_count=len(verified.citations),
            count=len(verified.stats),
        )
        return verified.to_json()

    async def brief(self) -> JsonValue:
        brief_id = Brief.id_for(self.run_id)
        run = await self.enter()
        if run is None:
            return {"briefId": brief_id}
        run = await self.mark_running()
        records: list[ServedByRecord] = []
        try:
            stored = await read_doc(self.store, Brief, brief_id)
        except NotFound:
            verified = _state(VerifiedState, self.ctx.outputs.get("verify"))
            pillar = await self.pillar(run.pillar)
            blocklist = await self.blocklist()
            prompt = load_prompt("brief")
            result = await self.call(
                Task.SYNTHESIS, _brief_request(run, verified, pillar, blocklist, prompt)
            )
            served = self.served(result, step="brief", prompt=prompt)
            value = result.value
            output = value if isinstance(value, BriefOutput) and not result.refused else None
            brief = build_brief(
                run, verified, output, served_by=[*verified.served_by, served], stamp=self.stamp()
            )
            try:
                stored = await create_doc(self.store, brief)
            except Conflict:
                stored = await read_doc(self.store, Brief, brief_id)
            records.append(served)
        cited = {citation.url: citation.id for citation in stored.doc.citations}
        run = await self.update(
            functools.partial(_link_brief, brief_id=brief_id, cited=cited, records=records)
        )
        await self.changed("brief", brief_id)
        await self.changed("research_run", run.id)
        self.log_step("brief", claim_count=len(stored.doc.claims))
        return {"briefId": brief_id}

    async def audit(self) -> JsonValue:
        run = await self.enter()
        if run is None:
            done = ChildResult(ok=True, ref=Brief.id_for(self.run_id))
            await self.finish(done)
            return done.to_json()
        run = await self.mark_running()
        blocklist = await self.blocklist()
        try:
            brief = (await read_doc(self.store, Brief, run.brief_id or Brief.id_for(run.id))).doc
        except NotFound:
            raise JobFailed("brief_missing", "The research brief is missing.") from None
        if not blocklist.configured:
            await self.quarantine(blocklist, reason="blocklist_not_configured")
        if audit_texts([*run_texts(run), *brief_texts(brief)], blocklist):
            await self.quarantine(blocklist)
        stamp = self.stamp()

        def succeed(current: ResearchRun) -> ResearchRun | None:
            if current.status != _R.RUNNING:
                return None
            return current.model_copy(
                update={
                    "status": RESEARCH_MACHINE.transition(current.status, _R.SUCCEEDED),
                    "status_changed_at": stamp,
                    "updated_at": stamp,
                    "finished_at": stamp,
                    "brief_id": brief.id,
                    "error_code": None,
                }
            )

        stored = await update_doc(self.store, ResearchRun, self.run_id, succeed)
        if stored.doc.status == _R.CANCELLED:
            await self.cancelled()
        if stored.doc.status != _R.SUCCEEDED:
            raise JobFailed("run_state", "The research run is not running.")
        await self.changed("research_run", self.run_id)
        result = ChildResult(ok=True, ref=brief.id)
        await self.finish(result)
        self.log_step("audit", citation_count=len(brief.citations))
        return result.to_json()


def _with_records(current: ResearchRun, *, records: Sequence[ServedByRecord]) -> ResearchRun:
    return current.model_copy(update=_accounting(current, records))


async def _guarded(ctx: StepContext, body: Callable[[_RunJob], Awaitable[JsonValue]]) -> JsonValue:
    """Run one step; a permanent failure is recorded on the run and the job first."""
    job = _RunJob(ctx)
    try:
        return await body(job)
    except _Settled:
        raise
    except JobFailed as exc:
        await job.fail(exc.code)
        raise


async def plan_step(ctx: StepContext) -> JsonValue:
    return await _guarded(ctx, _RunJob.plan)


async def search_step(ctx: StepContext) -> JsonValue:
    return await _guarded(ctx, _RunJob.search)


async def extract_step(ctx: StepContext) -> JsonValue:
    return await _guarded(ctx, _RunJob.extract)


async def verify_step(ctx: StepContext) -> JsonValue:
    return await _guarded(ctx, _RunJob.verify)


async def brief_step(ctx: StepContext) -> JsonValue:
    return await _guarded(ctx, _RunJob.brief)


async def audit_step(ctx: StepContext) -> JsonValue:
    return await _guarded(ctx, _RunJob.audit)
