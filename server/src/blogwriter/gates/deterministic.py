"""The deterministic checks of every gate (no model, no network) and the live editor lint.

:func:`deterministic_checks` is the ``deterministic`` step of ``gates.run`` (and the first
thing :func:`blogwriter.gates.run.run_gates` does): dialect lint and standards ids (gate 5),
links, tracking parameters, raw HTML, clearance and policy (gate 7), owner signal (gate 6),
the blocklist and forbidden topics (gate 3), the verbatim budget, slop and specifics (gate 4)
and the citations the brief must have (gate 2). Its ``blocklist_hit`` ends the model work of
the run.

:func:`lint_text` is the same set minus what needs documents beyond the article (blocklist,
owner signal, clearance, policy), for ``POST /api/gates/lint``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blogwriter.domain.articles import Article, ArticleMeta, GateName
from blogwriter.domain.base import utc_now
from blogwriter.domain.common import Finding
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import PillarSettings, StandardsCatalog
from blogwriter.gates.confidentiality import (
    blocklist_gate_findings,
    forbidden_topic_findings,
    has_blocklist_finding,
)
from blogwriter.gates.document import ArticleText, prepare_text
from blogwriter.gates.factcheck import citation_findings
from blogwriter.gates.inputs import GateInputs
from blogwriter.gates.lint import dialect_findings, standards_findings
from blogwriter.gates.originality import slop_budget_findings, slop_findings, verbatim_findings
from blogwriter.gates.outbound import clearance_findings, link_findings, policy_findings
from blogwriter.gates.ownersignal import owner_signal_findings
from blogwriter.gates.results import GateDraft
from blogwriter.gates.settings import gates_settings
from blogwriter.gates.slop import TextMeasure

__all__ = ["DeterministicChecks", "deterministic_checks", "lint_text"]


@dataclass(frozen=True, slots=True)
class DeterministicChecks:
    drafts: dict[GateName, GateDraft]
    blocklist_hit: bool
    measure: TextMeasure


def deterministic_checks(
    article: Article,
    inputs: GateInputs,
    *,
    slop: SlopChecker,
    site_host: str | None,
    content_sha256: str,
    now: datetime,
) -> DeterministicChecks:
    """Every gate's deterministic part for ``article`` (CPU-bound: call it in a thread)."""
    text = prepare_text(article.meta, article.markdown)
    body = article.markdown
    citation_ids = inputs.citation_ids
    lint = [
        *dialect_findings(text, source_ids=citation_ids),
        *standards_findings(text, catalog=inputs.catalog, pillars=inputs.pillars, now=now),
    ]
    outbound = [
        *link_findings(text, pillars=inputs.pillars, brief=inputs.brief, site_host=site_host),
        *clearance_findings(article, stories=inputs.stories, content_sha256=content_sha256),
        *policy_findings(inputs.policy),
    ]
    owner = owner_signal_findings(
        article,
        body=body,
        stories=inputs.stories,
        povs=inputs.povs,
        interviews=inputs.interviews,
    )
    blocklist = blocklist_gate_findings(text, article, inputs.blocklist)
    confidentiality = [*blocklist, *forbidden_topic_findings(text, inputs.policy)]
    found_slop, counts = slop_findings(text.view, slop)
    originality = [
        *verbatim_findings(body, inputs.brief),
        *found_slop,
        *slop_budget_findings(found_slop, counts),
    ]
    drafts = {
        GateName.FACT_CHECK: GateDraft.of(
            GateName.FACT_CHECK, citation_findings(body, citation_ids)
        ),
        GateName.CONFIDENTIALITY: GateDraft.of(GateName.CONFIDENTIALITY, confidentiality),
        GateName.ORIGINALITY: GateDraft.of(GateName.ORIGINALITY, originality),
        GateName.LINT: GateDraft.of(GateName.LINT, lint),
        GateName.OWNER_SIGNAL: GateDraft.of(GateName.OWNER_SIGNAL, owner),
        GateName.OUTBOUND: GateDraft.of(GateName.OUTBOUND, outbound),
    }
    return DeterministicChecks(
        drafts=drafts, blocklist_hit=has_blocklist_finding(blocklist), measure=counts
    )


def lint_text(
    meta: ArticleMeta,
    body: str,
    *,
    brief: Brief | None,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    slop: SlopChecker,
) -> list[Finding]:
    """The live editor lint (deterministic, no I/O): dialect lint over the composed document,
    standards ids, outbound links, verbatim budget, slop and specifics, and, with a brief,
    citations the brief does not have. Spans index ``body``. CPU-bound: use a thread."""
    text: ArticleText = prepare_text(meta, body)
    source_ids = None if brief is None else {citation.id for citation in brief.citations}
    found_slop, counts = slop_findings(text.view, slop)
    findings = [
        *dialect_findings(text, source_ids=source_ids),
        *standards_findings(text, catalog=catalog, pillars=pillars, now=utc_now()),
        *link_findings(text, pillars=pillars, brief=brief, site_host=gates_settings().site_host),
        *verbatim_findings(body, brief),
        *found_slop,
        *slop_budget_findings(found_slop, counts),
    ]
    if source_ids is not None:
        findings.extend(citation_findings(body, source_ids))
    return sorted(findings, key=_order)


def _order(finding: Finding) -> tuple[int, int, str]:
    start = finding.start if finding.start is not None else -1
    end = finding.end if finding.end is not None else -1
    return start, end, finding.code
