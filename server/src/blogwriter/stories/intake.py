"""Input-side confidentiality for owner text: local findings, local masking and the anonymizer
proposal (contract W-A3; plan "Owner signal: interviews, story and POV banks, confidentiality").

**Private interview text is never persisted.** The raw text lives in the browser, in the
request that redacts it and in the one tool-free ``Task.ANONYMIZE`` call :func:`propose` makes
(``Sensitivity.PRIVATE``, ``origin="owner"``). Nothing here stores, logs, emits or caches it:
findings carry spans into the text, a code and a generic message, never the matched text.

**Blocklisted terms never reach a model, the anonymizer included.** Before the call, every
blocking span (blocklist hits, e-mail addresses, phone numbers, links, IP addresses, handles) is
replaced by a bracketed placeholder (:func:`mask_text`), and the proposal is checked and masked
again on the way back. The owner confirms or edits the proposal; only that confirmed wording
is ever saved (and checked again by :func:`local_findings` at save time).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Final, Literal

from blogwriter.ai import NoRouteError, PolicyViolation
from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.core.logging import describe_exception
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import Finding
from blogwriter.domain.confidentiality import (
    BLOCKLIST_CODE,
    BLOCKLIST_NOT_CONFIGURED_CODE,
    BlocklistNotConfigured,
    blocklist_findings,
)
from blogwriter.domain.docstore import read_singleton
from blogwriter.domain.ports import Services
from blogwriter.domain.settings import Blocklist, EmployerPolicy, PillarSettings
from blogwriter.stories import _detect
from blogwriter.stories._logging import logger
from blogwriter.stories._prompts import ANONYMIZE, fenced, load_prompt

__all__ = [
    "BLOCKING_CODES",
    "DOMAIN_CODE",
    "EMAIL_CODE",
    "FIGURE_CODE",
    "HANDLE_CODE",
    "IP_CODE",
    "MONEY_CODE",
    "NAME_CODE",
    "PHONE_CODE",
    "URL_CODE",
    "IntakePurpose",
    "ProposalReason",
    "RedactionProposal",
    "blocking_spans",
    "is_blocklist_finding",
    "local_findings",
    "mask_text",
    "propose",
    "public_terms",
]

type IntakePurpose = Literal[
    "story", "pov", "interview_answer", "voice_sample", "idea_note", "take"
]
type ProposalReason = Literal["strict_mode", "ai_unavailable", "ai_use_not_confirmed", "refused"]

EMAIL_CODE: Final = "email-address"
PHONE_CODE: Final = "phone-number"
URL_CODE: Final = "url"
IP_CODE: Final = "ip-address"
HANDLE_CODE: Final = "handle"
DOMAIN_CODE: Final = "domain-name"
MONEY_CODE: Final = "money"
FIGURE_CODE: Final = "exact-figure"
NAME_CODE: Final = "possible-name"

BLOCKING_CODES: Final = frozenset(
    {
        BLOCKLIST_CODE,
        BLOCKLIST_NOT_CONFIGURED_CODE,
        EMAIL_CODE,
        PHONE_CODE,
        URL_CODE,
        IP_CODE,
        HANDLE_CODE,
    }
)
"""Codes :func:`local_findings` reports as ``block`` (the rest warn)."""

_MESSAGES: Final[Mapping[str, str]] = {
    EMAIL_CODE: "Looks like an e-mail address. Remove it, or name the role instead.",
    PHONE_CODE: "Looks like a phone number. Remove it.",
    URL_CODE: "Contains a web link. Remove it, or keep only public sources.",
    IP_CODE: "Looks like an IP address. Remove it, or use a documentation range.",
    HANDLE_CODE: "Looks like a social or chat handle. Remove it, or name the role instead.",
    DOMAIN_CODE: "Looks like a web address. Keep it only if it is public.",
    MONEY_CODE: "A money amount. Use a range or a bucket unless it is public.",
    FIGURE_CODE: (
        'An exact figure. Round it into a bucket ("2,000+", "about 40%") unless it is public.'
    ),
    NAME_CODE: "Possibly the name of a person or an organization. Keep it only if it is public.",
}

_PLACEHOLDERS: Final[Mapping[str, str]] = {
    EMAIL_CODE: "[email address]",
    PHONE_CODE: "[phone number]",
    URL_CODE: "[link]",
    IP_CODE: "[IP address]",
    HANDLE_CODE: "[handle]",
}
_TERM_PLACEHOLDERS: Final[Mapping[str, str]] = {
    "client": "[client]",
    "project": "[internal project]",
    "person": "[person]",
    "internal": "[internal term]",
    "vendor": "[vendor]",
    "other": "[redacted]",
}
_REDACTED: Final = "[redacted]"
_QUOTES: Final = ('"', "'", "\u201c", "\u201d", "\u2018", "\u2019", "`")


class RedactionProposal(ApiModel):
    """What ``POST /api/stories/intake/redact`` answers (never stored).

    ``findings`` index the raw text the owner sent (highlighting); ``proposal`` is the
    anonymizer's public-safe wording for the owner to edit and confirm, or ``None`` with the
    ``reason`` no model proposed one.
    """

    findings: list[Finding]
    proposal: str | None
    reason: ProposalReason | None
    anonymizer_used: bool


def is_blocklist_finding(finding: Finding) -> bool:
    """A blocklist hit or the fail-closed ``blocklist-not-configured`` finding."""
    return finding.code in (BLOCKLIST_CODE, BLOCKLIST_NOT_CONFIGURED_CODE)


def _finding(code: str, start: int, end: int) -> Finding:
    blocking = code in BLOCKING_CODES
    return Finding(
        code=code,
        severity="block" if blocking else "warn",
        message=_MESSAGES[code],
        start=start,
        end=end,
        overridable=not blocking,
    )


class _Taken:
    """Which code points a finding already covers (one byte each; spans never overlap)."""

    def __init__(self, size: int) -> None:
        self._mask = bytearray(size)

    def free(self, start: int, end: int) -> bool:
        return end > start and self._mask.count(1, start, end) == 0

    def take(self, start: int, end: int) -> bool:
        if not self.free(start, end):
            return False
        self._mask[start:end] = b"\x01" * (end - start)
        return True

    def mark(self, start: int, end: int) -> None:
        self._mask[start:end] = b"\x01" * (end - start)


def _outside(spans: Iterable[tuple[int, int]], code: bytearray | None) -> Iterator[tuple[int, int]]:
    for start, end in spans:
        if code is None or code.count(1, start, end) == 0:
            yield start, end


def local_findings(
    text: str,
    blocklist: Blocklist,
    *,
    public_terms: Iterable[str] = (),
    markdown: bool = False,
) -> list[Finding]:
    """Deterministic findings for ``text``, with code-point spans into it. No I/O.

    ``block``: blocklist hits (``domain.confidentiality.blocklist_findings``; an unconfigured
    blocklist gives the document-level ``blocklist-not-configured`` block), e-mail addresses,
    phone numbers, links, IP addresses and ``@handles``. ``warn``: bare domain names, money
    amounts and exact figures (bucket them), and runs of capitalised words that may name a
    person or an organization (``possible-name``; words of ``public_terms``, such as the
    pillar vocabulary, and well-known vendor and product names never count).

    ``markdown``: the text is Markdown, so ``@...`` inside code (decorators, package scopes)
    is not a handle and the warnings skip code; the blocking detectors still read code.
    """
    findings = blocklist_findings(text, blocklist)
    taken = _Taken(len(text))
    code = _detect.code_mask(text) if markdown and ("`" in text or "~~~" in text) else None
    for detector_code, spans in (
        (URL_CODE, _detect.url_spans(text)),
        (EMAIL_CODE, _detect.email_spans(text)),
        (IP_CODE, _detect.ip_spans(text)),
        (PHONE_CODE, _detect.phone_spans(text)),
        (HANDLE_CODE, _outside(_detect.handle_spans(text), code)),
    ):
        for start, end in spans:
            if taken.take(start, end):
                findings.append(_finding(detector_code, start, end))
    for finding in findings:
        if finding.start is not None and finding.end is not None:
            taken.mark(finding.start, finding.end)
    public = frozenset(
        word.casefold() for term in public_terms for word in term.replace("-", " ").split()
    )
    for detector_code, spans in (
        (DOMAIN_CODE, _outside(_detect.domain_spans(text), code)),
        (MONEY_CODE, _outside(_detect.money_spans(text), code)),
        (FIGURE_CODE, _outside(_detect.figure_spans(text), code)),
        (NAME_CODE, _outside(_detect.name_spans(text, public), code)),
    ):
        for start, end in spans:
            if taken.take(start, end):
                findings.append(_finding(detector_code, start, end))
    findings.sort(
        key=lambda f: (
            -1 if f.start is None else f.start,
            -1 if f.end is None else f.end,
            f.code,
        )
    )
    return findings


def blocking_spans(findings: Iterable[Finding]) -> list[Finding]:
    """The ``block`` findings that have a span (what :func:`mask_text` replaces)."""
    return [
        finding
        for finding in findings
        if finding.severity == "block" and finding.start is not None and finding.end is not None
    ]


def _placeholder(finding: Finding, categories: Mapping[str, str]) -> str:
    if finding.code == BLOCKLIST_CODE:
        category = categories.get(finding.ref or "", "other")
        return _TERM_PLACEHOLDERS.get(category, _REDACTED)
    return _PLACEHOLDERS.get(finding.code, _REDACTED)


def mask_text(text: str, findings: Iterable[Finding], blocklist: Blocklist | None = None) -> str:
    """``text`` with the span of every blocking finding replaced by a bracketed placeholder.

    Blocklist hits become ``[client]``, ``[internal project]``, ``[person]``... by the term's
    category (looked up by ``ref`` in ``blocklist``); contact details and links become
    ``[email address]``, ``[phone number]``, ``[link]``, ``[IP address]``, ``[handle]``.
    Overlapping spans merge (a blocklist placeholder wins). Spans must index ``text``.
    """
    categories = {term.id: term.category for term in blocklist.terms} if blocklist else {}
    spans: list[tuple[int, int, str, bool]] = []
    for finding in sorted(blocking_spans(findings), key=lambda f: (f.start or 0, f.end or 0)):
        start, end = finding.start or 0, finding.end or 0
        label = _placeholder(finding, categories)
        is_term = finding.code == BLOCKLIST_CODE
        if spans and start < spans[-1][1]:
            prev_start, prev_end, prev_label, prev_term = spans[-1]
            keep_new = is_term and not prev_term
            spans[-1] = (
                prev_start,
                max(prev_end, end),
                label if keep_new else prev_label,
                prev_term or is_term,
            )
            continue
        spans.append((start, end, label, is_term))
    parts: list[str] = []
    position = 0
    for start, end, label, _ in spans:
        parts.append(text[position:start])
        parts.append(label)
        position = end
    parts.append(text[position:])
    return "".join(parts)


def public_terms(pillars: PillarSettings, blocklist: Blocklist, pillar: str | None) -> list[str]:
    """Public vocabulary of one pillar (or of every pillar): names, canonical terms and the
    current names of renamed products. Terms that hit the blocklist are left out, so this list
    can go to a model (it is owner-editable text)."""
    if not blocklist.configured:
        return []
    chosen = [p for p in pillars.pillars if pillar is None or p.id == pillar] or pillars.pillars
    terms: list[str] = []
    for item in chosen:
        terms.extend([item.name, *item.vocabulary, *item.renames.values()])
    unique = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    return [term for term in unique if not blocklist_findings(term, blocklist)]


def _clean_proposal(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in _QUOTES and cleaned[-1] in _QUOTES:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _user_message(masked: str, purpose: IntakePurpose, terms: Sequence[str]) -> str:
    lines = [f"Purpose: {purpose}"]
    if terms:
        lines.append(
            "Public terms for this topic (never confidential; keep them): " + "; ".join(terms)
        )
    lines.append("")
    lines.append(fenced("owner_text", masked))
    return "\n".join(lines)


def _proposal(findings: list[Finding], reason: ProposalReason, *, used: bool) -> RedactionProposal:
    return RedactionProposal(findings=findings, proposal=None, reason=reason, anonymizer_used=used)


async def propose(
    text: str,
    *,
    purpose: IntakePurpose,
    services: Services,
    pillar: str | None = None,
) -> RedactionProposal:
    """Local findings for ``text`` plus the anonymizer's public-safe wording.

    The call is ``Task.ANONYMIZE`` with ``Sensitivity.PRIVATE``, ``origin="owner"``, tool-free,
    on the locally masked text (no blocklisted term, contact detail or link reaches the model).
    No model call without a configured model (``ai_unavailable``), without the owner's AI-use
    confirmation (``ai_use_not_confirmed``) or in strict mode (``strict_mode``: the router's
    policy refuses private data before anything is sent). Raises
    :class:`~blogwriter.domain.confidentiality.BlocklistNotConfigured` until the owner saved the
    blocklist once. Nothing is stored or logged.
    """
    store = services.store
    stamp = to_iso(services.clock.now())
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    if not blocklist.configured:
        raise BlocklistNotConfigured()
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    terms = public_terms(pillars, blocklist, pillar)
    findings = local_findings(text, blocklist, public_terms=terms)
    runner = services.ai
    if runner is None:
        return _proposal(findings, "ai_unavailable", used=False)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    if not policy.ai_use_confirmed:
        return _proposal(findings, "ai_use_not_confirmed", used=False)
    masked = mask_text(text, findings, blocklist)
    prompt = load_prompt(ANONYMIZE)
    request = Request(
        system=prompt.text,
        messages=(Message.user(_user_message(masked, purpose, terms)),),
        sensitivity=Sensitivity.PRIVATE,
    )
    try:
        result = await runner.run(Task.ANONYMIZE, request, origin="owner")
    except PolicyViolation as exc:
        if exc.rule == "private_disabled":
            return _proposal(findings, "strict_mode", used=False)
        logger.warning(
            "stories_anonymizer_refused_by_policy",
            extra={"policy_rule": exc.rule, "intake_purpose": purpose},
        )
        return _proposal(findings, "ai_unavailable", used=False)
    except NoRouteError:
        return _proposal(findings, "ai_unavailable", used=False)
    except Exception as exc:
        # Provider errors after the router's retries: the owner still gets the local findings
        # and redacts by hand. Only the error type is logged (its message may carry content).
        logger.warning(
            "stories_anonymizer_failed",
            extra={"intake_purpose": purpose, **describe_exception(exc)},
        )
        return _proposal(findings, "ai_unavailable", used=False)
    proposal = _clean_proposal(result.text) if not result.refused else ""
    if not proposal:
        return _proposal(findings, "refused", used=True)
    leftovers = blocking_spans(local_findings(proposal, blocklist, public_terms=terms))
    if leftovers:
        proposal = mask_text(proposal, leftovers, blocklist)
    return RedactionProposal(
        findings=findings, proposal=proposal, reason=None, anonymizer_used=True
    )
