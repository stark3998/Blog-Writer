"""Gate 6, owner signal: the article carries something only the owner could have written.

Passes when ``ownerSignals`` names at least one *usable* story or POV (approved by the owner and
passed by the classifier), or when the body quotes a confirmed interview answer verbatim: a run
of at least :data:`MIN_INTERVIEW_QUOTE_CHARS` characters shared with an answer of an interview
the article references (case and whitespace insensitive, ``domain.research.verbatim_spans``).
Otherwise ``owner-signal-missing`` blocks (overridable: manual publish only).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from blogwriter.domain.articles import Article
from blogwriter.domain.common import Finding
from blogwriter.domain.research import verbatim_spans
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry

__all__ = ["MIN_INTERVIEW_QUOTE_CHARS", "owner_signal_findings", "owner_signal_present"]

MIN_INTERVIEW_QUOTE_CHARS: Final = 40


def owner_signal_present(
    article: Article,
    *,
    body: str,
    stories: Iterable[StoryEntry],
    povs: Iterable[PovEntry],
    interviews: Iterable[Interview],
) -> bool:
    used = {(ref.kind, ref.id) for ref in article.owner_signals}
    if any(("story", story.id) in used and story.usable for story in stories):
        return True
    if any(("pov", pov.id) in used and pov.usable for pov in povs):
        return True
    for interview in interviews:
        for answer in interview.answers:
            if len(answer.text) >= MIN_INTERVIEW_QUOTE_CHARS and verbatim_spans(
                body, answer.text, min_chars=MIN_INTERVIEW_QUOTE_CHARS
            ):
                return True
    return False


def owner_signal_findings(
    article: Article,
    *,
    body: str,
    stories: Iterable[StoryEntry],
    povs: Iterable[PovEntry],
    interviews: Iterable[Interview],
) -> list[Finding]:
    if owner_signal_present(article, body=body, stories=stories, povs=povs, interviews=interviews):
        return []
    return [
        Finding(
            code="owner-signal-missing",
            severity="block",
            message=(
                "No approved story or POV, and no confirmed interview answer quoted in the text. "
                "Add something only you could have written."
            ),
            overridable=True,
        )
    ]
