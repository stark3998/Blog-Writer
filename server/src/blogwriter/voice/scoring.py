"""AI-tell and slop scoring of voice samples.

Plan, "Voice": every sample is scored with the AI-tell lint and the structural detectors; flagged
passages inform topics and terminology only and are never used as exemplars.

- **AI tells** are :func:`blogwriter.linkedin.lint.ai_tell_findings` (contract I-9): the
  :data:`~blogwriter.linkedin.lint.AI_TELLS` phrases with that module's phrase boundaries and
  apostrophe normalization (never plain substring matching), plus its "starts with TL;DR" check,
  converted with :func:`~blogwriter.domain.common.from_linkedin_finding`. They equal the
  ``ai-tell`` findings of ``lint_post`` on the same text.
- **Structural detectors** are the ``SlopChecker`` port (``services.slop``, implemented by gates).
  Without one, or when it fails, a sample is never exemplar-eligible (fail closed).

The score is flags per 100 words. Any flag makes a sample ineligible as an exemplar.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from blogwriter.core.logging import describe_exception
from blogwriter.domain.common import Finding, from_linkedin_finding
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.voice import VoiceSourceType
from blogwriter.linkedin.lint import ai_tell_findings as linkedin_ai_tell_findings
from blogwriter.voice.text import word_count

__all__ = [
    "MAX_FLAGS",
    "SOURCE_WEIGHTS",
    "TextScore",
    "ai_tell_findings",
    "sample_weight",
    "score_text",
]

MAX_FLAGS: Final = 500
"""``VoiceSample.flags`` keeps at most this many findings (the score counts all of them)."""

SOURCE_WEIGHTS: Final[dict[VoiceSourceType, float]] = {
    "linkedin_post": 2.0,
    "linkedin_comment": 1.5,
    "interview_answer": 2.0,
    "portfolio_post": 1.0,
    "portfolio_about": 1.0,
    "final_post": 0.5,
}
"""Base weight per source. The owner's unassisted words count double (plan: "the owner's
unassisted words carry the highest weight"); AI drafts the owner edited count least."""

_logger: Final = logging.getLogger("blogwriter.voice")


@dataclass(frozen=True, slots=True)
class TextScore:
    """The scoring of one text.

    ``ai_tell_score`` counts every flag per 100 words; ``flags`` keeps at most
    :data:`MAX_FLAGS` of them (by position). ``exemplar_eligible`` is true only when the
    structural detectors ran and nothing was flagged. ``word_count`` and ``checked`` (the
    detectors ran) are extensions of the contract's three fields.
    """

    ai_tell_score: float
    flags: list[Finding]
    exemplar_eligible: bool
    word_count: int = 0
    checked: bool = False


def ai_tell_findings(text: str) -> list[Finding]:
    """The ``ai-tell`` findings ``linkedin.lint.lint_post`` reports for ``text`` (same spans,
    codes and messages), as domain findings sorted by position
    (:func:`blogwriter.linkedin.lint.ai_tell_findings`)."""
    return [from_linkedin_finding(finding) for finding in linkedin_ai_tell_findings(text)]


def _position(finding: Finding) -> tuple[int, int, str]:
    start = finding.start if finding.start is not None else -1
    end = finding.end if finding.end is not None else -1
    return start, end, finding.code


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """One finding per ``(code, start, end)``: the slop checker reports AI tells too."""
    seen: set[tuple[int, int, str]] = set()
    out: list[Finding] = []
    for finding in sorted(findings, key=_position):
        key = _position(finding)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def score_text(text: str, *, slop: SlopChecker | None) -> TextScore:
    """AI tells plus the structural detectors' findings, per 100 words.

    CPU-bound: call it through ``asyncio.to_thread`` for long texts. A failing slop checker is
    logged (error type only) and treated like a missing one: the text is not exemplar-eligible.
    """
    findings = ai_tell_findings(text)
    checked = False
    if slop is not None:
        try:
            findings.extend(slop.check(text))
            checked = True
        except Exception as exc:  # a detector bug must not break intake; fail closed below
            _logger.warning("voice_slop_failed", extra=describe_exception(exc))
    flags = _dedupe(findings)
    words = word_count(text)
    score = round(len(flags) * 100 / max(words, 1), 2)
    return TextScore(
        ai_tell_score=score,
        flags=flags[:MAX_FLAGS],
        exemplar_eligible=checked and not flags,
        word_count=words,
        checked=checked,
    )


def sample_weight(source_type: VoiceSourceType, owner_unassisted: bool) -> float:
    """The weight a sample carries in the guide synthesis (0-5)."""
    base = SOURCE_WEIGHTS[source_type]
    return min(5.0, base * 2 if owner_unassisted else base)
