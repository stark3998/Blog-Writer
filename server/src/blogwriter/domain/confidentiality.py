"""Blocklist matching and the AI-use gate, shared by every package that handles owner text.

:func:`blocklist_findings` is deterministic and local: the confidential terms never leave the
process. Findings carry the term's id in ``ref`` and a generic message, never the term itself,
so they are safe to return to the owner's browser, store on a gate report or put in a job event.

Matching is case-insensitive (Unicode case folding). ``word`` terms match only as whole words
(no letter, digit or underscore on either side); ``substring`` terms match anywhere.

**Fail closed.** A blocklist the owner never saved (``configured`` false) cannot clear anything:
:func:`blocklist_findings` returns one non-overridable ``blocklist-not-configured`` finding,
:func:`blocklist_hit` is true and :func:`check_texts` raises :class:`BlocklistNotConfigured`
(409 ``blocklist_not_configured``). An empty list the owner saved is a valid answer.

Rule: run :func:`check_texts` on every request that carries owner-editable or model-written text
to a model or a search (revise and selection instructions, regenerate instructions, research
topics, audiences and owner URLs, quick searches) and on every intake save; refuse on a hit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from blogwriter.domain.common import Finding
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy

__all__ = [
    "BLOCKLIST_CODE",
    "BLOCKLIST_NOT_CONFIGURED_CODE",
    "FORBIDDEN_TOPIC_CODE",
    "AiUseNotConfirmed",
    "BlocklistNotConfigured",
    "ConfidentialText",
    "blocklist_findings",
    "blocklist_hit",
    "check_texts",
    "forbidden_topic_findings",
    "require_ai_use",
]

BLOCKLIST_CODE: Final = "blocklisted-term"
BLOCKLIST_NOT_CONFIGURED_CODE: Final = "blocklist-not-configured"
FORBIDDEN_TOPIC_CODE: Final = "forbidden-topic"

_CATEGORY_TEXT: Final = {
    "client": "a blocklisted client name",
    "project": "a blocklisted internal project name",
    "person": "a blocklisted person's name",
    "internal": "a blocklisted internal term",
    "vendor": "a blocklisted vendor name",
    "other": "a blocklisted term",
}

_NOT_CONFIGURED: Final = Finding(
    code=BLOCKLIST_NOT_CONFIGURED_CODE,
    severity="block",
    message="The blocklist is not set up yet. Save it once in Settings > Policy.",
    overridable=False,
)


class BlocklistNotConfigured(Exception):
    """The owner never saved the blocklist, so no text can be cleared (409)."""

    code: Final = "blocklist_not_configured"

    def __init__(self) -> None:
        super().__init__("the blocklist is not configured")


class ConfidentialText(ValueError):
    """Text matched the blocklist (422 ``confidential_text``; jobs: ``JobFailed("blocklist_hit")``).

    ``findings`` maps each checked field name to its findings (term ids only, never terms).
    """

    code: Final = "confidential_text"

    def __init__(self, findings: Mapping[str, Sequence[Finding]]) -> None:
        self.findings: dict[str, list[Finding]] = {
            name: list(items) for name, items in findings.items() if items
        }
        super().__init__("text matches the blocklist in: " + ", ".join(sorted(self.findings)))


class AiUseNotConfirmed(Exception):
    """The owner has not confirmed the employer's AI-use policy, so no anonymized owner material
    may go to a model (409 ``ai_use_not_confirmed``; jobs: ``JobFailed("ai_use_not_confirmed")``).
    """

    code: Final = "ai_use_not_confirmed"

    def __init__(self) -> None:
        super().__init__("the employer AI-use policy is not confirmed")


def require_ai_use(policy: EmployerPolicy) -> None:
    """Raise :class:`AiUseNotConfirmed` unless ``policy.ai_use_confirmed`` (call it before any
    request with ``Sensitivity.ANONYMIZED`` owner material; public-only work skips it)."""
    if not policy.ai_use_confirmed:
        raise AiUseNotConfirmed()


def _pattern(terms: Sequence[BlocklistTerm], *, words: bool) -> re.Pattern[str] | None:
    chosen = sorted(
        (term for term in terms if (term.match == "word") == words),
        key=lambda term: len(term.term),
        reverse=True,
    )
    if not chosen:
        return None
    alternatives = "|".join(re.escape(term.term) for term in chosen)
    body = rf"(?<!\w)(?:{alternatives})(?!\w)" if words else rf"(?:{alternatives})"
    return re.compile(body, re.IGNORECASE)


def blocklist_findings(text: str, blocklist: Blocklist) -> list[Finding]:
    """Every blocklisted term in ``text`` as a ``block`` finding (span = the match).

    An unconfigured blocklist gives one document-level ``blocklist-not-configured`` finding
    (fail closed), whatever the text.
    """
    if not blocklist.configured:
        return [_NOT_CONFIGURED.model_copy()]
    by_folded = {term.term.casefold(): term for term in blocklist.terms}
    findings: list[Finding] = []
    for words in (True, False):
        pattern = _pattern(blocklist.terms, words=words)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            term = by_folded.get(match.group(0).casefold())
            if term is None:
                # Case folding can change the length (German sharp s); fall back to a scan.
                term = next(
                    (t for t in blocklist.terms if t.term.casefold() == match.group(0).casefold()),
                    None,
                )
            category = term.category if term is not None else "other"
            findings.append(
                Finding(
                    code=BLOCKLIST_CODE,
                    severity="block",
                    message=f"Matches {_CATEGORY_TEXT[category]}.",
                    start=match.start(),
                    end=match.end(),
                    ref=term.id if term is not None else None,
                    overridable=False,
                )
            )
    findings.sort(key=lambda finding: (finding.start or 0, finding.end or 0))
    return findings


def blocklist_hit(texts: Sequence[str], blocklist: Blocklist) -> bool:
    """Whether any of ``texts`` (queries, URLs, titles) contains a blocklisted term.

    True for an unconfigured blocklist (a check that cannot run never passes).
    """
    if not blocklist.configured:
        return True
    return any(blocklist_findings(text, blocklist) for text in texts)


def check_texts(fields: Mapping[str, str | None], blocklist: Blocklist) -> None:
    """Refuse text bound for a model, a search or storage unless the blocklist clears it.

    ``fields`` maps a field name (``instruction``, ``topic``, ``ownerUrls[0]``...) to its text
    (``None`` is skipped). Raises :class:`BlocklistNotConfigured` when the owner never saved the
    list, :class:`ConfidentialText` with the findings per field on a hit.
    """
    if not blocklist.configured:
        raise BlocklistNotConfigured()
    hits = {
        name: blocklist_findings(text, blocklist)
        for name, text in fields.items()
        if text is not None
    }
    if any(hits.values()):
        raise ConfidentialText(hits)


def forbidden_topic_findings(text: str, policy: EmployerPolicy) -> list[Finding]:
    """Mentions of the employer policy's forbidden topics in ``text`` (whole words, any
    whitespace between them, case-insensitive) as ``block`` findings named by topic number,
    never quoting the topic (``ref`` ``topic-<n>``; overridable where a gate allows overrides).
    Articles (gate 3), every published post text and engagement drafts use this one rule."""
    findings: list[Finding] = []
    for index, topic in enumerate(policy.forbidden_topics, start=1):
        words = topic.split()
        if not words:
            continue
        body = r"\s+".join(re.escape(word) for word in words)
        pattern = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
        findings.extend(
            Finding(
                code=FORBIDDEN_TOPIC_CODE,
                severity="block",
                message=f"Touches a topic your employer policy forbids (topic {index}).",
                start=match.start(),
                end=match.end(),
                ref=f"topic-{index}",
                overridable=True,
            )
            for match in pattern.finditer(text)
        )
    return findings
