"""The blocklist audit of research output (plan: "After each run, search queries and fetched URLs
are checked against the blocklist. A hit quarantines the run and sends an alert.").

The audit is deterministic and local: blocklist terms never leave the process. It fails closed:
a blocklist the owner never saved counts as a hit (:func:`audit_texts` is
``domain.confidentiality.blocklist_hit``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from blogwriter.domain.confidentiality import blocklist_findings, blocklist_hit
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.settings import Blocklist

__all__ = ["audit_texts", "brief_texts", "run_texts", "scrub_run"]


def audit_texts(texts: Sequence[str], blocklist: Blocklist) -> bool:
    """Whether any text (a query, URL, title or brief text) contains a blocklisted term.

    True for an unconfigured blocklist: a check that cannot run never passes.
    """
    return blocklist_hit([text for text in texts if text], blocklist)


def run_texts(run: ResearchRun) -> list[str]:
    """Everything a run sent to a model or a search, or got back from one: topic, audience,
    sub-questions, owner URLs, search queries, and every source URL and title."""
    texts = [run.topic, *(run.sub_questions), *(run.owner_urls)]
    if run.audience:
        texts.append(run.audience)
    texts.extend(query.text for query in run.queries)
    for source in run.sources:
        texts.append(source.url)
        if source.title:
            texts.append(source.title)
    return texts


def brief_texts(brief: Brief) -> list[str]:
    """Every text of a brief (summary, findings, stats, lists, claims, quotes, citations)."""
    texts: list[str] = [brief.executive_summary, brief.topic]
    if brief.audience:
        texts.append(brief.audience)
    texts.extend(finding.text for finding in brief.findings)
    for stat in brief.stats:
        texts.extend((stat.value, stat.label))
    texts.extend(brief.counterpoints)
    texts.extend(brief.open_questions)
    texts.extend(brief.angles)
    texts.extend(brief.hook_seeds)
    for claim in brief.claims:
        texts.append(claim.text)
        if claim.conflict_note:
            texts.append(claim.conflict_note)
    texts.extend(quote.text for quote in brief.quotes)
    for citation in brief.citations:
        texts.extend((citation.url, citation.title))
        if citation.publisher:
            texts.append(citation.publisher)
    return texts


def _clean(texts: Iterable[str | None], blocklist: Blocklist) -> bool:
    return not any(text and blocklist_findings(text, blocklist) for text in texts)


def scrub_run(run: ResearchRun, blocklist: Blocklist) -> ResearchRun:
    """A copy of ``run`` without the queries, sources and sub-questions that hit the blocklist
    (applied when a run is quarantined, so its document does not keep the matched term).

    The topic stays (the owner typed it and sees it); an audience that hits is cleared. With an
    unconfigured blocklist nothing can be judged and nothing is removed.
    """
    if not blocklist.configured:
        return run
    return run.model_copy(
        update={
            "sub_questions": [q for q in run.sub_questions if _clean([q], blocklist)],
            "queries": [q for q in run.queries if _clean([q.text], blocklist)],
            "sources": [s for s in run.sources if _clean([s.url, s.title], blocklist)],
            "audience": run.audience if _clean([run.audience], blocklist) else None,
        }
    )
