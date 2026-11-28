"""Gate 1, rubric: CRITIQUE scores the draft (structured output, anonymized, tool-free).

Passes on its own when ``overall >= RUBRIC_OVERALL_MIN`` (8), every dimension is at least
``RUBRIC_DIMENSION_MIN`` (6) and ``first_hand_specificity >= RUBRIC_SPECIFICITY_MIN`` (7). The
eval judge is this gate (``run_gates(gates={GateName.RUBRIC})``), so it uses the same prompt
and thresholds.

A score from a fallback model or with a refusal in the chain cannot pass on its own
(``rubric-review-only``, overridable): auto-publish needs the calibrated judge. A refusal or a
failed call is ``error``; no model, or the AI-use policy not confirmed, is ``skipped``. The
request (article details, pillar and format-pack settings, the style guide, the article) passes
the current blocklist first: a hit sends nothing and the gate is ``skipped``
(``material_blocklisted``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Task
from blogwriter.domain.articles import Article, GateName
from blogwriter.domain.common import Finding
from blogwriter.domain.confidentiality import AiUseNotConfirmed, require_ai_use
from blogwriter.domain.ports import ModelRunner
from blogwriter.domain.settings import Blocklist, EmployerPolicy, FormatPacks, PillarSettings
from blogwriter.domain.voice import StyleGuide
from blogwriter.gates.modelcalls import fence, load_prompt, run_structured
from blogwriter.gates.results import GateDraft

__all__ = [
    "RUBRIC_DIMENSIONS",
    "RUBRIC_DIMENSION_MIN",
    "RUBRIC_OVERALL_MIN",
    "RUBRIC_PROMPT",
    "RUBRIC_SPECIFICITY_MIN",
    "RubricNote",
    "RubricScores",
    "rubric_draft",
    "rubric_findings",
]

RUBRIC_DIMENSIONS: Final = (
    "clarity",
    "structure",
    "accuracy",
    "depth",
    "first_hand_specificity",
    "voice",
    "originality",
    "actionability",
)
RUBRIC_OVERALL_MIN: Final = 8
RUBRIC_DIMENSION_MIN: Final = 6
RUBRIC_SPECIFICITY_MIN: Final = 7
RUBRIC_PROMPT: Final = ("rubric", 1)
GUIDE_EXCERPT_CHARS: Final = 6000

type Dimension = Literal[
    "clarity",
    "structure",
    "accuracy",
    "depth",
    "first_hand_specificity",
    "voice",
    "originality",
    "actionability",
]


class RubricNote(BaseModel):
    """One concrete problem the critic found (owner-facing; never generic advice)."""

    model_config = ConfigDict(extra="forbid")

    dimension: Dimension
    note: str = Field(min_length=1, max_length=300)


class RubricScores(BaseModel):
    """The CRITIQUE output schema: whole-number scores 0-10."""

    model_config = ConfigDict(extra="forbid")

    clarity: int = Field(ge=0, le=10)
    structure: int = Field(ge=0, le=10)
    accuracy: int = Field(ge=0, le=10)
    depth: int = Field(ge=0, le=10)
    first_hand_specificity: int = Field(ge=0, le=10)
    voice: int = Field(ge=0, le=10)
    originality: int = Field(ge=0, le=10)
    actionability: int = Field(ge=0, le=10)
    overall: int = Field(ge=0, le=10)
    notes: list[RubricNote] = Field(default_factory=list[RubricNote], max_length=8)

    def dimension_scores(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in RUBRIC_DIMENSIONS}


def _note(scores: RubricScores, dimension: str) -> str:
    notes = [note.note.strip() for note in scores.notes if note.dimension == dimension]
    return f" {notes[0]}" if notes else ""


def rubric_findings(scores: RubricScores) -> list[Finding]:
    """The blockers for scores under the thresholds (overridable: the rubric is soft)."""
    findings: list[Finding] = []
    if scores.overall < RUBRIC_OVERALL_MIN:
        findings.append(
            Finding(
                code="rubric-overall",
                severity="block",
                message=f"Overall {scores.overall}/10 (needs {RUBRIC_OVERALL_MIN}).",
                overridable=True,
            )
        )
    for name in RUBRIC_DIMENSIONS:
        value = int(getattr(scores, name))
        minimum = (
            RUBRIC_SPECIFICITY_MIN if name == "first_hand_specificity" else RUBRIC_DIMENSION_MIN
        )
        if value < minimum:
            code = "rubric-specificity" if name == "first_hand_specificity" else "rubric-dimension"
            label = name.replace("_", " ")
            findings.append(
                Finding(
                    code=code,
                    severity="block",
                    message=(
                        f"{label.capitalize()} {value}/10 (needs {minimum})." + _note(scores, name)
                    )[:500],
                    ref=name,
                    overridable=True,
                )
            )
    return findings


def _user_message(
    article: Article,
    *,
    pillars: PillarSettings,
    packs: FormatPacks,
    guide: StyleGuide | None,
    words: int,
) -> str:
    meta = article.meta
    pillar = pillars.get(meta.pillar) if meta.pillar else None
    pack = packs.get(meta.format) if meta.format else None
    lines = [
        f"Title: {meta.title or '(none)'}",
        f"Excerpt: {meta.excerpt or '(none)'}",
        f"Words: {words}",
    ]
    if pillar is not None:
        lines.append(f"Pillar: {pillar.name}. Topics: {pillar.topics or '-'}.")
        if pillar.audience:
            lines.append(f"Audience: {pillar.audience}")
    if pack is not None:
        lines.append(
            f"Format: {pack.name}, {pack.min_words}-{pack.max_words} words. "
            f"Must contain: {'; '.join(pack.must_contain) or '-'}. "
            f"Banned: {'; '.join(pack.banned) or '-'}."
        )
    parts = ["\n".join(lines)]
    if guide is not None:
        parts.append(fence("style_guide", guide.body[:GUIDE_EXCERPT_CHARS]))
    parts.append(fence("article", article.markdown))
    parts.append("Score the article in <article> against the rubric. Return only the JSON.")
    return "\n\n".join(parts)


async def rubric_draft(
    article: Article,
    *,
    runner: ModelRunner | None,
    policy: EmployerPolicy,
    blocklist: Blocklist,
    pillars: PillarSettings,
    packs: FormatPacks,
    guide: StyleGuide | None,
    words: int,
    origin: Origin,
    job_id: str | None,
    now: datetime,
) -> GateDraft:
    """Gate 1 (callers have checked the article against the blocklist; this screens the whole
    request, which also carries the style guide and settings)."""
    draft = GateDraft.of(GateName.RUBRIC)
    if runner is None:
        return draft.with_unavailable("ai_unavailable")
    try:
        require_ai_use(policy)
    except AiUseNotConfirmed:
        return draft.with_unavailable("ai_use_not_confirmed")
    prompt = load_prompt(*RUBRIC_PROMPT)
    outcome = await run_structured(
        runner,
        Task.CRITIQUE,
        prompt=prompt,
        user=_user_message(article, pillars=pillars, packs=packs, guide=guide, words=words),
        output=RubricScores,
        origin=origin,
        job_id=job_id,
        step="gates.rubric",
        now=now,
        blocklist=blocklist,
    )
    if outcome.screened_out:
        return draft.with_unavailable("material_blocklisted")
    if outcome.record is not None:
        draft = draft.with_served_by([outcome.record])
    if outcome.refused:
        return draft.with_findings(
            [
                Finding(
                    code="rubric-refused",
                    severity="info",
                    message="The critic model declined to score this article.",
                )
            ]
        ).with_error("refused")
    if outcome.value is None:
        return draft.with_error(outcome.error or "model_failed")
    scores = outcome.value
    draft = draft.model_copy(
        update={"score": float(scores.overall), "dimensions": scores.dimension_scores()}
    )
    findings = rubric_findings(scores)
    if outcome.review_only:
        findings.append(
            Finding(
                code="rubric-review-only",
                severity="block",
                message=(
                    "Scored by a fallback model or after a refusal: review it yourself "
                    "(it cannot pass on its own)."
                ),
                overridable=True,
            )
        )
    return draft.with_findings(findings)
