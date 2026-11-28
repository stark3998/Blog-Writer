"""Gate 3, confidentiality, on the exact bytes that ship.

1. **Blocklist** (deterministic, local): ``domain.confidentiality.blocklist_findings`` over
   ``compose_document(meta, body)``, the media alt texts and file names. Findings name the
   term by id (``ref``), never the term. A blocklist the owner never saved is the hard
   ``blocklist-not-configured`` finding. **Any blocklist finding ends the model work of the
   run**: nothing is sent to CRITIQUE, FACT_CHECK, the classifier or the embedder.
2. **Employer forbidden topics** (deterministic): ``forbidden-topic`` (overridable), by topic
   number, never quoting it.
3. **Classifier and mosaic check**: ``services.classifier`` (implemented by stories: industry,
   counts, vendor stack, geography, timeline in combination). Its findings block, overridably,
   except blocklist codes. Could not run (not configured, AI-use not confirmed): ``skipped``.

Only blocklist hits are hard (``overridable: false``); gate 3 is also a hard gate as a whole: a
skipped run can never be overridden (``GateResult.overridable``).
"""

from __future__ import annotations

from collections.abc import Iterable

from blogwriter.ai.ledger import Origin
from blogwriter.domain.articles import Article, GateName
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import Finding
from blogwriter.domain.confidentiality import (
    BLOCKLIST_CODE,
    BLOCKLIST_NOT_CONFIGURED_CODE,
    AiUseNotConfirmed,
    blocklist_findings,
)
from blogwriter.domain.confidentiality import (
    forbidden_topic_findings as domain_forbidden_topic_findings,
)
from blogwriter.domain.ports import ConfidentialityClassifier, ServiceUnavailable
from blogwriter.domain.settings import Blocklist, EmployerPolicy
from blogwriter.gates.document import ArticleText
from blogwriter.gates.results import GateDraft

__all__ = [
    "BLOCKLIST_CODES",
    "blocklist_gate_findings",
    "blocklist_text_findings",
    "classifier_draft",
    "forbidden_topic_findings",
    "has_blocklist_finding",
]

BLOCKLIST_CODES = frozenset({BLOCKLIST_CODE, BLOCKLIST_NOT_CONFIGURED_CODE})


def has_blocklist_finding(findings: Iterable[Finding]) -> bool:
    return any(finding.code in BLOCKLIST_CODES for finding in findings)


def _relocate(finding: Finding, text: ArticleText, where: str | None = None) -> Finding:
    """A finding on :attr:`ArticleText.checked` with its span moved onto the body (findings in
    the frontmatter, or on media, lose the span and say where they are)."""
    start, end = finding.start, finding.end
    if where is None and text.document is not None and start is not None and end is not None:
        if start >= text.body_start:
            return finding.model_copy(
                update={"start": start - text.body_start, "end": end - text.body_start}
            )
        where = "In the article details (title, excerpt, slug, tags or series)"
    if where is None:
        return finding
    message = f"{where}: {finding.message}"[:500]
    return finding.model_copy(update={"start": None, "end": None, "message": message})


def blocklist_text_findings(text: ArticleText, blocklist: Blocklist) -> list[Finding]:
    """Blocklist findings on the composed document, or on the details and the body when it
    cannot be composed yet (spans index the body)."""
    if not blocklist.configured:
        return blocklist_findings("", blocklist)
    if text.document is not None:
        return [_relocate(f, text) for f in blocklist_findings(text.document, blocklist)]
    findings: list[Finding] = []
    meta = text.meta
    details = [meta.title, meta.excerpt, meta.slug, " ".join(meta.tags)]
    if meta.series is not None:
        details.extend([meta.series.id, meta.series.title])
    for detail in details:
        if detail:
            findings.extend(
                _relocate(f, text, "In the article details")
                for f in blocklist_findings(detail, blocklist)
            )
    findings.extend(blocklist_findings(text.body, blocklist))
    return findings


def blocklist_gate_findings(
    text: ArticleText, article: Article, blocklist: Blocklist
) -> list[Finding]:
    """Blocklist findings on the bytes that ship: the document, media alt texts and names."""
    findings = blocklist_text_findings(text, blocklist)
    if not blocklist.configured:
        return findings
    for media in article.media:
        for value in (media.alt_text, media.filename, media.name):
            if value:
                findings.extend(
                    _relocate(f, text, "In a media file's alt text or name")
                    for f in blocklist_findings(value, blocklist)
                )
    return findings


def forbidden_topic_findings(text: ArticleText, policy: EmployerPolicy) -> list[Finding]:
    """Mentions of the employer policy's forbidden topics (by topic number, never the topic):
    ``domain.confidentiality.forbidden_topic_findings`` over the bytes that ship."""
    found = domain_forbidden_topic_findings(text.checked, policy)
    if text.document is None:
        return found
    return [_relocate(finding, text) for finding in found]


async def classifier_draft(
    text: ArticleText,
    *,
    classifier: ConfidentialityClassifier | None,
    origin: Origin,
    job_id: str | None,
) -> GateDraft:
    """The classifier and mosaic check (callers wrap it in ``defer_on_brake``)."""
    draft = GateDraft.of(GateName.CONFIDENTIALITY)
    if classifier is None:
        return draft.with_unavailable("classifier_unavailable")
    checked = text.checked
    try:
        checks = await classifier.classify([checked], origin=origin, job_id=job_id)
    except AiUseNotConfirmed:
        return draft.with_unavailable("ai_use_not_confirmed")
    except ServiceUnavailable as exc:
        reason = "ai_unavailable" if exc.name == "ai" else "classifier_unavailable"
        return draft.with_unavailable(reason)
    check = checks[0] if checks else None
    if (
        check is None
        or check.passed is None
        or (check.content_sha256 is not None and check.content_sha256 != sha256_hex(checked))
    ):
        return draft.with_unavailable("classifier_unavailable")
    findings = [_classifier_finding(f, text) for f in check.findings]
    if check.passed is False and not any(f.severity == "block" for f in findings):
        findings.append(
            Finding(
                code="classifier-flagged",
                severity="block",
                message=(
                    "The confidentiality classifier flagged this article (a combination of "
                    "details could identify a client). Generalize the details."
                ),
                overridable=True,
            )
        )
    return draft.with_findings(findings)


def _classifier_finding(finding: Finding, text: ArticleText) -> Finding:
    hard = finding.code in BLOCKLIST_CODES
    relocated = _relocate(finding, text) if text.document is not None else finding
    return relocated.model_copy(update={"overridable": not hard})
