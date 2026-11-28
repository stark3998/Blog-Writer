"""Building gate results: drafts that steps fill in, verdicts, the 50-finding cap.

A :class:`GateDraft` collects what one gate found so far (a job step checkpoints it; the next
step that works on the same gate adds to it). :func:`finalize` turns it into the stored
:class:`~blogwriter.domain.articles.GateResult`:

- any blocking finding: ``fail``;
- else a part that could not run (``unavailable``): ``skipped`` (never a pass);
- else an unexpected error (``error``): ``error``;
- else warnings: ``warn``; else ``pass``.

Findings are cut to :data:`~blogwriter.domain.common.MAX_STORED_FINDINGS` with
:func:`~blogwriter.domain.common.cap_findings` (blockers first, non-overridable before
overridable), the rest counted in ``truncated``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final, Literal

from pydantic import Field

from blogwriter.domain.articles import GateName, GateResult, GateVerdict
from blogwriter.domain.base import Code, DomainModel
from blogwriter.domain.common import (
    MAX_STORED_FINDINGS,
    Finding,
    ServedByRecord,
    cap_findings,
)

__all__ = [
    "MAX_SERVED_BY_PER_GATE",
    "GateDraft",
    "Unavailable",
    "finalize",
    "info",
    "safe_ref",
]

MAX_SERVED_BY_PER_GATE: Final = 20

type Unavailable = Literal[
    "ai_unavailable",
    "ai_use_not_confirmed",
    "fetcher_unavailable",
    "classifier_unavailable",
    "blocklist_hit",
    "material_blocklisted",
    "stale",
]

_UNAVAILABLE_TEXT: Final[dict[str, str]] = {
    "ai_unavailable": "The AI service is not configured, so this check did not run.",
    "ai_use_not_confirmed": (
        "Confirm the employer AI-use policy in Settings > Policy; until then this check cannot "
        "send the article to a model."
    ),
    "fetcher_unavailable": "Sources cannot be re-fetched (the fetcher is not configured).",
    "classifier_unavailable": "The confidentiality classifier is not available.",
    "blocklist_hit": (
        "Not run: the article matches the blocklist, so nothing was sent to a model. Fix the "
        "confidentiality findings and run the gates again."
    ),
    "material_blocklisted": (
        "Not run: the brief, owner material, style guide or settings this check sends to a "
        "model match the blocklist (or it is not set up), so nothing was sent. Fix them and run "
        "the gates again."
    ),
    "stale": "Not run: the article changed while the gates were running.",
}


class GateDraft(DomainModel):
    """One gate's findings so far (checkpointed between job steps; never stored on articles)."""

    gate: GateName
    findings: list[Finding] = Field(default_factory=list[Finding], max_length=MAX_STORED_FINDINGS)
    truncated: int = Field(default=0, ge=0)
    unavailable: list[Code] = Field(default_factory=list[str], max_length=10)
    error: Code | None = None
    score: float | None = None
    dimensions: dict[str, float] = Field(default_factory=dict[str, float])
    served_by: list[ServedByRecord] = Field(
        default_factory=list[ServedByRecord], max_length=MAX_SERVED_BY_PER_GATE
    )

    @classmethod
    def of(cls, gate: GateName, findings: Iterable[Finding] = ()) -> GateDraft:
        return cls(gate=gate).with_findings(findings)

    def with_findings(self, findings: Iterable[Finding]) -> GateDraft:
        """A copy with ``findings`` added (cut to the cap, the rest counted)."""
        kept, cut = cap_findings([*self.findings, *findings])
        return self.model_copy(update={"findings": kept, "truncated": self.truncated + cut})

    def with_unavailable(self, reason: Unavailable) -> GateDraft:
        if reason in self.unavailable:
            return self
        return self.model_copy(update={"unavailable": [*self.unavailable, reason]})

    def with_error(self, code: str) -> GateDraft:
        return self.model_copy(update={"error": code})

    def with_served_by(self, records: Sequence[ServedByRecord]) -> GateDraft:
        merged = [*self.served_by, *records][:MAX_SERVED_BY_PER_GATE]
        return self.model_copy(update={"served_by": merged})

    def merge(self, other: GateDraft) -> GateDraft:
        """Both drafts' findings and markers (same gate)."""
        if other.gate is not self.gate:
            raise ValueError("drafts of different gates")
        kept, cut = cap_findings([*self.findings, *other.findings])
        merged = self.model_copy(
            update={
                "findings": kept,
                "truncated": self.truncated + other.truncated + cut,
                "unavailable": list(dict.fromkeys([*self.unavailable, *other.unavailable])),
                "error": self.error or other.error,
                "score": other.score if other.score is not None else self.score,
                "dimensions": {**self.dimensions, **other.dimensions},
            }
        )
        return merged.with_served_by(other.served_by)

    @property
    def blocked(self) -> bool:
        return any(finding.severity == "block" for finding in self.findings)


def safe_ref(text: str) -> str | None:
    """``text`` as a ``Finding.ref`` (printable ASCII without ``"`` and ``\\``, 1-200
    characters), or ``None`` when it cannot be one."""
    if not 1 <= len(text) <= 200:
        return None
    if all("!" <= char <= "~" and char not in '"\\' for char in text):
        return text
    return None


def info(code: str, message: str) -> Finding:
    """A document-level ``info`` finding (why a part did not run)."""
    return Finding(code=code, severity="info", message=message)


def _reason_findings(reasons: Sequence[str]) -> list[Finding]:
    return [
        info(reason.replace("_", "-"), _UNAVAILABLE_TEXT.get(reason, "This check did not run."))
        for reason in reasons
    ]


def finalize(draft: GateDraft, *, ran_at: str) -> GateResult:
    """The stored result of a draft (verdict rules in the module docs)."""
    if draft.blocked:
        verdict = GateVerdict.FAIL
    elif draft.unavailable:
        verdict = GateVerdict.SKIPPED
    elif draft.error is not None:
        verdict = GateVerdict.ERROR
    elif any(finding.severity == "warn" for finding in draft.findings):
        verdict = GateVerdict.WARN
    else:
        verdict = GateVerdict.PASS
    extra = _reason_findings(draft.unavailable)
    if draft.error is not None:
        extra.append(
            info(
                "gate-error",
                "This check failed unexpectedly. Run the gates again; if it keeps failing, "
                "see System.",
            )
        )
    findings, cut = cap_findings([*draft.findings, *extra])
    return GateResult(
        gate=draft.gate,
        verdict=verdict,
        score=draft.score,
        dimensions=dict(draft.dimensions),
        findings=findings,
        truncated=draft.truncated + cut,
        ran_at=ran_at,
        served_by=list(draft.served_by),
    )
