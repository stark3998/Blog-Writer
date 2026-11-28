"""Gate 2, fact check: claims map to the brief or the owner's material, and cited sources say it.

1. **Citations exist** (deterministic): every ``[^src-id]`` is a citation of the brief
   (``citation-not-in-brief``).
2. **Claims map** (FACT_CHECK, anonymized, tool-free): the model lists the article's checkable
   claims and maps each to a brief claim, stat or finding, or to the owner's material (story
   metrics, POVs, confirmed interview answers). Unmapped claims are ``claim-unsupported``;
   mapped but uncited brief claims warn (``claim-uncited``).
3. **Sources re-fetched and claims verified**: each cited source is fetched again through
   ``services.fetcher`` (``allowed_urls`` = the brief's citation URLs) and its cited claims are
   verified against the passage (FACT_CHECK, anonymized, tool-free). **The passage lives in
   memory only**: it is never stored, logged or emitted, and the verifier returns verdicts only
   (no free text that could carry it). Only the count of characters the body shares with it
   (runs of :data:`PASSAGE_RUN_CHARS`) leaves this module, for gate 4's verbatim budget.

All findings block overridably; ``claim-partly-supported`` and ``claim-uncited`` warn. No
model, no fetcher or the AI-use policy not confirmed: ``skipped``. The mapping request (brief,
owner material, article) and each verification's claim list pass the current blocklist first
(:func:`~blogwriter.gates.modelcalls.run_structured`): a hit in the mapping request sends
nothing and the gate is ``skipped`` (``material_blocklisted``); the fetched passage is public
text from the cited page and is not screened.

=============================  ===============================================================
code                           when
=============================  ===============================================================
``citation-not-in-brief``      ``[^id]`` that the brief does not have
``claim-unsupported``          a claim with no brief or owner material behind it
``claim-contradicted``         the cited source says otherwise
``claim-unverified``           the cited source does not show it (or could not be checked)
``claim-partly-supported``     (warn) the source supports part of it
``claim-uncited``              (warn) a brief-backed claim without its citation
``source-unreachable``         the source could not be fetched again
``source-unchecked``           more cited sources than :data:`MAX_SOURCES_CHECKED`
=============================  ===============================================================
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from blogwriter.ai.ledger import BrakeEngaged, Origin
from blogwriter.ai.types import Task
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.domain.articles import Article, GateName
from blogwriter.domain.common import Finding, ServedByRecord
from blogwriter.domain.confidentiality import AiUseNotConfirmed, require_ai_use
from blogwriter.domain.jobflow import JobDeferred
from blogwriter.domain.ports import FetchRefused, ModelRunner, PageFetcher
from blogwriter.domain.research import Brief, verbatim_spans
from blogwriter.domain.settings import Blocklist, EmployerPolicy
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry
from blogwriter.gates.markdown import citation_refs
from blogwriter.gates.modelcalls import fence, load_prompt, run_structured
from blogwriter.gates.results import GateDraft, safe_ref

__all__ = [
    "MAP_PROMPT",
    "MAX_SOURCES_CHECKED",
    "PASSAGE_RUN_CHARS",
    "VERIFY_PROMPT",
    "ClaimMap",
    "ClaimVerdict",
    "FactCheckOutcome",
    "MappedClaim",
    "SourceCheck",
    "citation_findings",
    "fact_check_draft",
    "locate",
    "locate_all",
]

MAP_PROMPT: Final = ("factcheck-map", 1)
VERIFY_PROMPT: Final = ("factcheck-verify", 1)
MAX_SOURCES_CHECKED: Final = 15
PASSAGE_RUN_CHARS: Final = 60
"""Runs this long shared with a fetched passage count against the verbatim budget (titles and
names stay below it)."""
MAX_OWNER_TEXT_CHARS: Final = 1500

_logger: Final = logging.getLogger("blogwriter.gates.factcheck")
register_log_fields("citation_id", "claims")


class MappedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^c[0-9]{1,3}$")
    quote: str = Field(min_length=1, max_length=400)
    """The claim's sentence, copied exactly from the article."""
    citations: list[str] = Field(default_factory=list[str], max_length=6)
    """The ``[^id]`` labels attached to it in the article."""
    support: Literal["brief_claim", "brief_stat", "brief_finding", "owner_material", "none"]
    support_ref: str | None = Field(default=None, max_length=80)


class ClaimMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[MappedClaim] = Field(default_factory=list[MappedClaim], max_length=60)


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^c[0-9]{1,3}$")
    verdict: Literal["supported", "partly_supported", "not_found", "contradicted"]


class SourceCheck(BaseModel):
    """Verdicts only: no free text, so nothing from the passage can come back."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[ClaimVerdict] = Field(default_factory=list[ClaimVerdict], max_length=60)


@dataclass(frozen=True, slots=True)
class FactCheckOutcome:
    draft: GateDraft
    passage_counts: dict[str, int] = field(default_factory=dict[str, int])
    """Characters the body shares with each re-fetched source (counts only)."""


def _block(code: str, message: str, span: tuple[int, int] | None, ref: str | None) -> Finding:
    return Finding(
        code=code,
        severity="block",
        message=message[:500],
        start=None if span is None else span[0],
        end=None if span is None else span[1],
        ref=ref,
        overridable=True,
    )


def citation_findings(body: str, citation_ids: Collection[str]) -> list[Finding]:
    """``[^id]`` citations the brief does not have."""
    return [
        _block(
            "citation-not-in-brief",
            f"[^{ref.label}] is not a source in the brief.",
            (ref.start, ref.end),
            safe_ref(ref.label),
        )
        for ref in citation_refs(body)
        if ref.label not in citation_ids
    ]


# --------------------------------------------------------------------------------------------
# Locating claims
# --------------------------------------------------------------------------------------------


def _folded(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace() or char in "*_`":
            if in_space:
                continue
            out.append(" ")
            in_space = True
        else:
            lowered = char.lower().replace("\u2019", "'")
            out.append(lowered if len(lowered) == 1 else char)
            in_space = False
        offsets.append(index)
    return "".join(out), offsets


def locate(body: str, quote: str) -> tuple[int, int] | None:
    """Where ``quote`` (copied by a model) is in ``body``, ignoring case, whitespace and
    emphasis markers; ``None`` when it is not there."""
    return locate_all(body, {"q": quote})["q"]


def locate_all(body: str, quotes: Mapping[str, str]) -> dict[str, tuple[int, int] | None]:
    """:func:`locate` for many quotes, folding the body once (CPU-bound: use a thread)."""
    folded_body, offsets = _folded(body)
    found: dict[str, tuple[int, int] | None] = {}
    for key, quote in quotes.items():
        found[key] = None
        folded_quote = _folded(quote)[0].strip()
        for probe in (folded_quote, folded_quote[:80]):
            if len(probe) < 12:
                continue
            index = folded_body.find(probe)
            if index != -1:
                end = index + len(probe) - 1
                found[key] = (offsets[index], offsets[end] + 1)
                break
    return found


# --------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------


def _brief_section(brief: Brief | None) -> str:
    if brief is None:
        return fence("brief", "(no brief)")
    lines: list[str] = []
    for claim in brief.claims:
        lines.append(f"claim {claim.id} [^{claim.citation_id}]: {claim.text}")
    for stat in brief.stats:
        cited = " ".join(f"[^{c}]" for c in stat.citation_ids)
        lines.append(f"stat {stat.id} {cited}: {stat.value} {stat.label}")
    for finding in brief.findings:
        cited = " ".join(f"[^{c}]" for c in finding.citation_ids)
        lines.append(f"finding {finding.id} {cited}: {finding.text}")
    for citation in brief.citations:
        lines.append(f"source [^{citation.id}]: {citation.title} ({citation.domain})")
    return fence("brief", "\n".join(lines) or "(empty brief)")


def _owner_section(
    stories: Sequence[StoryEntry], povs: Sequence[PovEntry], interviews: Sequence[Interview]
) -> tuple[str, frozenset[str]]:
    lines: list[str] = []
    refs: set[str] = set()
    for story in stories:
        refs.add(story.id)
        metrics = "; ".join(f"{m.label}: {m.value}" for m in story.metrics)
        lines.append(f"story {story.id}: {story.title}. Metrics: {metrics or '-'}")
    for pov in povs:
        refs.add(pov.id)
        lines.append(f"pov {pov.id}: {pov.statement[:MAX_OWNER_TEXT_CHARS]}")
    for interview in interviews:
        for answer in interview.answers:
            ref = f"{interview.id}:{answer.question_id}"
            refs.add(ref)
            lines.append(f"answer {ref}: {answer.text[:MAX_OWNER_TEXT_CHARS]}")
    return fence("owner_material", "\n".join(lines) or "(none)"), frozenset(refs)


def _brief_refs(brief: Brief | None) -> dict[str, set[str]]:
    if brief is None:
        return {"brief_claim": set(), "brief_stat": set(), "brief_finding": set()}
    return {
        "brief_claim": {claim.id for claim in brief.claims},
        "brief_stat": {stat.id for stat in brief.stats},
        "brief_finding": {finding.id for finding in brief.findings},
    }


# --------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------


async def fact_check_draft(
    article: Article,
    *,
    brief: Brief | None,
    stories: Sequence[StoryEntry],
    povs: Sequence[PovEntry],
    interviews: Sequence[Interview],
    policy: EmployerPolicy,
    blocklist: Blocklist,
    runner: ModelRunner | None,
    fetcher: PageFetcher | None,
    origin: Origin,
    job_id: str | None,
    now: datetime,
) -> FactCheckOutcome:
    """Gate 2's model part (callers add :func:`citation_findings` and have checked the
    article against the blocklist; this screens the material it sends with it)."""
    draft = GateDraft.of(GateName.FACT_CHECK)
    if runner is None:
        return FactCheckOutcome(draft.with_unavailable("ai_unavailable"))
    if fetcher is None:
        return FactCheckOutcome(draft.with_unavailable("fetcher_unavailable"))
    try:
        require_ai_use(policy)
    except AiUseNotConfirmed:
        return FactCheckOutcome(draft.with_unavailable("ai_use_not_confirmed"))
    body = article.markdown
    owner, owner_refs = _owner_section(stories, povs, interviews)
    user = "\n\n".join(
        [
            _brief_section(brief),
            owner,
            fence("article", body),
            "List and map the checkable claims of the article in <article>. Return only the JSON.",
        ]
    )
    mapped = await run_structured(
        runner,
        Task.FACT_CHECK,
        prompt=load_prompt(*MAP_PROMPT),
        user=user,
        output=ClaimMap,
        origin=origin,
        job_id=job_id,
        step="gates.fact_check.map",
        now=now,
        blocklist=blocklist,
    )
    if mapped.screened_out:
        return FactCheckOutcome(draft.with_unavailable("material_blocklisted"))
    records: list[ServedByRecord] = [mapped.record] if mapped.record is not None else []
    if mapped.value is None:
        error = "refused" if mapped.refused else (mapped.error or "model_failed")
        return FactCheckOutcome(draft.with_served_by(records).with_error(error))
    claims = mapped.value.claims
    spans = await asyncio.to_thread(locate_all, body, {claim.id: claim.quote for claim in claims})
    findings = list(_mapping_findings(claims, spans, brief=brief, owner_refs=owner_refs))
    citation_ids: set[str] = (
        {citation.id for citation in brief.citations} if brief is not None else set[str]()
    )
    by_source: dict[str, list[MappedClaim]] = {}
    for claim in claims:
        for label in dict.fromkeys(claim.citations):
            if label in citation_ids:
                by_source.setdefault(label, []).append(claim)
    sources = sorted(by_source)
    for label in sources[MAX_SOURCES_CHECKED:]:
        findings.append(
            _block(
                "source-unchecked",
                f"Source [^{label}] was not re-checked (more than {MAX_SOURCES_CHECKED} sources).",
                None,
                label,
            )
        )
    passage_counts: dict[str, int] = {}
    if brief is not None and sources:
        allowed = [citation.url for citation in brief.citations]
        for label in sources[:MAX_SOURCES_CHECKED]:
            result = await _verify_source(
                brief,
                label,
                by_source[label],
                spans=spans,
                body=body,
                runner=runner,
                fetcher=fetcher,
                allowed_urls=allowed,
                origin=origin,
                job_id=job_id,
                now=now,
                blocklist=blocklist,
            )
            findings.extend(result.findings)
            records.extend(result.records)
            if result.shared_chars is not None:
                passage_counts[label] = result.shared_chars
    return FactCheckOutcome(
        draft.with_findings(findings).with_served_by(records), passage_counts=passage_counts
    )


def _mapping_findings(
    claims: Iterable[MappedClaim],
    spans: Mapping[str, tuple[int, int] | None],
    *,
    brief: Brief | None,
    owner_refs: Collection[str],
) -> Iterable[Finding]:
    brief_refs = _brief_refs(brief)
    for claim in claims:
        span = spans.get(claim.id)
        if claim.support == "none":
            yield _block(
                "claim-unsupported",
                "No brief source or owner material supports this claim. Cite it or cut it.",
                span,
                None,
            )
        elif claim.support == "owner_material":
            if claim.support_ref not in owner_refs:
                yield _block(
                    "claim-unsupported",
                    "This claim names owner material the article does not reference.",
                    span,
                    None,
                )
        elif claim.support_ref not in brief_refs[claim.support]:
            yield _block(
                "claim-unsupported",
                "This claim points at nothing in the brief. Cite it or cut it.",
                span,
                None,
            )
        elif not claim.citations:
            yield Finding(
                code="claim-uncited",
                severity="warn",
                message="This claim comes from the brief: add its citation.",
                start=None if span is None else span[0],
                end=None if span is None else span[1],
                ref=claim.support_ref,
                overridable=True,
            )


@dataclass(frozen=True, slots=True)
class _SourceResult:
    findings: list[Finding]
    records: list[ServedByRecord]
    shared_chars: int | None


def _shared_chars(body: str, passage: str) -> int:
    """Characters of distinct runs (``PASSAGE_RUN_CHARS``+) the body shares with a passage."""
    distinct: dict[str, int] = {}
    for start, end in verbatim_spans(body, passage, min_chars=PASSAGE_RUN_CHARS):
        key = " ".join(body[start:end].lower().split())
        distinct[key] = max(distinct.get(key, 0), end - start)
    return sum(distinct.values())


async def _verify_source(
    brief: Brief,
    label: str,
    claims: Sequence[MappedClaim],
    *,
    spans: Mapping[str, tuple[int, int] | None],
    body: str,
    runner: ModelRunner,
    fetcher: PageFetcher,
    allowed_urls: Sequence[str],
    origin: Origin,
    job_id: str | None,
    now: datetime,
    blocklist: Blocklist,
) -> _SourceResult:
    citation = brief.citation(label)
    if citation is None:  # pragma: no cover - only labels of the brief get here
        return _SourceResult([], [], None)
    try:
        page = await fetcher.fetch(citation.url, allowed_urls=allowed_urls)
    except (JobDeferred, BrakeEngaged):
        raise
    except FetchRefused as exc:
        return _SourceResult([_unreachable(label, exc.reason)], [], None)
    except Exception as exc:  # any failure to fetch is an unreachable source
        _logger.warning(
            "gates_fetch_failed", extra={"citation_id": label, **describe_exception(exc)}
        )
        return _SourceResult([_unreachable(label, "error")], [], None)
    shared = await asyncio.to_thread(_shared_chars, body, page.text)
    listing = "\n".join(f"{claim.id}: {claim.quote}" for claim in claims)
    user = "\n\n".join(
        [
            fence("source", page.text),
            fence("claims", listing),
            "Give a verdict for every claim in <claims> against <source>. Return only the JSON.",
        ]
    )
    del page  # the passage goes no further than this request
    outcome = await run_structured(
        runner,
        Task.FACT_CHECK,
        prompt=load_prompt(*VERIFY_PROMPT),
        user=user,
        output=SourceCheck,
        origin=origin,
        job_id=job_id,
        step="gates.fact_check.verify",
        now=now,
        blocklist=blocklist,
        screened=[listing],
    )
    del user
    records = [outcome.record] if outcome.record is not None else []
    verdicts = {v.id: v.verdict for v in outcome.value.verdicts} if outcome.value else {}
    findings: list[Finding] = []
    for claim in claims:
        span = spans.get(claim.id)
        verdict = verdicts.get(claim.id)
        if verdict == "supported":
            continue
        if verdict == "partly_supported":
            findings.append(
                Finding(
                    code="claim-partly-supported",
                    severity="warn",
                    message=f"Source [^{label}] supports only part of this claim.",
                    start=None if span is None else span[0],
                    end=None if span is None else span[1],
                    ref=label,
                    overridable=True,
                )
            )
        elif verdict == "contradicted":
            findings.append(
                _block(
                    "claim-contradicted",
                    f"Source [^{label}] says something different. Fix the claim.",
                    span,
                    label,
                )
            )
        else:
            reason = (
                "could not be checked against it"
                if outcome.value is None
                else "does not show this claim"
            )
            findings.append(_block("claim-unverified", f"Source [^{label}] {reason}.", span, label))
    _logger.info("gates_source_checked", extra={"citation_id": label, "claims": len(claims)})
    return _SourceResult(findings, records, shared)


def _unreachable(label: str, reason: str) -> Finding:
    code = reason if reason.replace("_", "").isalpha() and len(reason) <= 30 else "error"
    return _block(
        "source-unreachable",
        f"Source [^{label}] could not be fetched again ({code}).",
        None,
        label,
    )
