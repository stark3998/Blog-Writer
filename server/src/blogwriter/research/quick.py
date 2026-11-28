"""Quick search: one ``web_search`` call with the pillar allowlist, persisted for quick takes.

The query passes the blocklist first (``ConfidentialText`` / ``BlocklistNotConfigured``). The
stored ``QuickSearch`` holds a short model-written answer with ``[^src-id]`` markers, the claims
the answer's finding lines carry, the citations (URL, title, domain) and the queries: no page
text ever (quick search reads no page; it keeps what the search cited). Public web content can
still mention a blocklisted name: such claims and citations are dropped before anything is
stored, and a model-issued query that hit the blocklist raises an out-of-band alert (it already
went to the search engine).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Final

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import RouterResult
from blogwriter.ai.types import Message, Request, Sensitivity, Task, WebSearchTool
from blogwriter.core.logging import register_log_fields
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import Citation, ServedByRecord
from blogwriter.domain.confidentiality import blocklist_findings, check_texts
from blogwriter.domain.docstore import Stored, create_doc, read_singleton
from blogwriter.domain.ports import Services, ServiceUnavailable
from blogwriter.domain.research import MAX_CLAIM_CHARS, BriefClaim, QuickSearch, SearchQuery
from blogwriter.domain.settings import Blocklist, PillarSettings
from blogwriter.notify import emit_alert
from blogwriter.research.claims import (
    citation_id_for,
    clean_claim,
    clean_source_url,
    clip,
    defang,
    is_allowlisted,
    parse_quick_answer,
    source_domain,
)
from blogwriter.research.prompting import load_prompt
from blogwriter.research.settings import research_settings

__all__ = ["QUICK_MAX_TOOL_CALLS", "QuickSearchTimeout", "quick_search"]

QUICK_MAX_TOOL_CALLS: Final = 4
MAX_QUICK_CITATIONS: Final = 20
MAX_QUICK_CLAIMS: Final = 30
MAX_QUICK_QUERIES: Final = 10
MAX_ANSWER_MARKERS: Final = 3

_logger: Final = logging.getLogger("blogwriter.research.quick")
register_log_fields("quick_id", "dropped_count")


class QuickSearchTimeout(TimeoutError):
    """The quick search did not finish within ``BW_RESEARCH_QUICK_TIMEOUT_SECONDS`` (504)."""


def _hits(text: str | None, blocklist: Blocklist) -> bool:
    return bool(text) and bool(blocklist_findings(text or "", blocklist))


async def quick_search(
    services: Services,
    query: str,
    *,
    pillar: str | None,
    origin: Origin,
    article_id: str | None = None,
    post_id: str | None = None,
    idea_id: str | None = None,
) -> Stored[QuickSearch]:
    """Run a quick search and store it.

    Raises ``ConfidentialText`` / ``BlocklistNotConfigured`` (the query), ``ServiceUnavailable``
    (``ai``), :class:`QuickSearchTimeout`, and lets ``BrakeEngaged`` through for background
    callers (owner calls are never braked).
    """
    settings = research_settings()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    check_texts({"query": query}, blocklist)
    ai = services.ai
    if ai is None:
        raise ServiceUnavailable("ai")
    allowlist: tuple[str, ...] = ()
    if pillar is not None:
        pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
        found = pillars.get(pillar)
        if found is not None:
            allowlist = tuple(found.research_domains)
    prompt = load_prompt("quick")
    lines = [f"Question: {defang(query)}"]
    if allowlist:
        lines.append("Search only these trusted domains: " + ", ".join(allowlist[:20]) + ".")
    request = Request(
        system=prompt.text,
        messages=(Message.user("\n".join(lines)),),
        sensitivity=Sensitivity.PUBLIC,
        tools=(WebSearchTool(allowed_domains=allowlist[:20]),),
        max_tool_calls=min(settings.max_tool_calls, QUICK_MAX_TOOL_CALLS),
    )
    try:
        async with asyncio.timeout(settings.quick_timeout_seconds):
            result = await ai.run(Task.QUICK_SEARCH, request, origin=origin)
    except TimeoutError:
        raise QuickSearchTimeout("the quick search timed out") from None
    doc_id = QuickSearch.new_id(now=now)
    record = ServedByRecord.from_served_by(
        result.served_by,
        step="quick_search",
        at=stamp,
        refused=result.refused or bool(result.refusals),
        prompt_version=prompt.version,
        cost_usd=round(float(result.cost_usd), 6),
    )
    answer, claims, citations, queries, dropped = _assemble(result, allowlist, blocklist, stamp)
    if dropped.queries:
        emit_alert("quarantined", doc_id)
    if dropped.total:
        _logger.warning(
            "research_quick_blocklist_hit",
            extra={"quick_id": doc_id, "dropped_count": dropped.total},
        )
    search = QuickSearch(
        id=doc_id,
        created_at=stamp,
        updated_at=stamp,
        query=query,
        pillar=pillar,
        answer=answer,
        claims=claims,
        citations=citations,
        queries=queries,
        article_id=article_id,
        post_id=post_id,
        idea_id=idea_id,
        served_by=[record],
    )
    return await create_doc(store, search)


class _Dropped:
    def __init__(self) -> None:
        self.queries = 0
        self.items = 0

    @property
    def total(self) -> int:
        return self.queries + self.items


def _assemble(
    result: RouterResult, allowlist: Sequence[str], blocklist: Blocklist, stamp: str
) -> tuple[str, list[BriefClaim], list[Citation], list[SearchQuery], _Dropped]:
    dropped = _Dropped()
    completion = result.completion
    titles: dict[str, str | None] = {}
    for item in completion.citations:
        url = clean_source_url(item.url)
        if url is not None and url not in titles:
            titles[url] = clip(item.title, 500) or None
    known_sources = {url for url in map(clean_source_url, completion.search_sources) if url}
    summary, lines = parse_quick_answer(completion.text) if not result.refused else ("", [])
    citations: dict[str, Citation] = {}

    def cite(url: str) -> Citation | None:
        if url in citations:
            return citations[url]
        domain = source_domain(url)
        title = titles.get(url) or domain
        if domain is None or title is None or len(citations) >= MAX_QUICK_CITATIONS:
            return None
        if _hits(url, blocklist) or _hits(title, blocklist):
            dropped.items += 1
            return None
        citation = Citation(
            id=citation_id_for(url),
            url=url,
            title=title,
            domain=domain,
            allowlisted=is_allowlisted(domain, allowlist),
            accessed_at=stamp,
        )
        citations[url] = citation
        return citation

    claims: list[BriefClaim] = []
    for sentence, raw_url in lines:
        url = clean_source_url(raw_url) if raw_url else None
        if url is None or (url not in titles and url not in known_sources):
            continue
        text = clean_claim(sentence, None)
        if text is None or len(claims) >= MAX_QUICK_CLAIMS:
            continue
        if _hits(text, blocklist):
            dropped.items += 1
            continue
        citation = cite(url)
        if citation is None:
            continue
        claims.append(
            BriefClaim(
                id=f"cl-{len(claims) + 1}",
                text=clip(text, MAX_CLAIM_CHARS),
                citation_id=citation.id,
                status="unverified",
            )
        )
    for url in titles:
        cite(url)
    answer = clip(summary, 1800)
    if _hits(answer, blocklist):
        dropped.items += 1
        answer = ""
    if answer:
        markers = list(dict.fromkeys(claim.citation_id for claim in claims))[:MAX_ANSWER_MARKERS]
        answer = (answer + " " + "".join(f"[^{marker}]" for marker in markers)).strip()
    queries: list[SearchQuery] = []
    for text in dict.fromkeys(clip(query, 300) for query in completion.search_queries):
        if not text or len(queries) >= MAX_QUICK_QUERIES:
            continue
        if _hits(text, blocklist):
            dropped.queries += 1
            continue
        queries.append(SearchQuery(text=text, phase="quick"))
    return answer, claims, list(citations.values()), queries, dropped
