"""The model requests of the pipeline: INTERVIEW, OUTLINE, DRAFT, CRITIQUE and REVISE.

Every request is ``Sensitivity.ANONYMIZED`` and tool-free: it carries the brief (public research
already reduced to claims, short quotes and citations) and the owner's anonymized material
(approved stories and POVs, confirmed interview answers, the approved voice guide). No request
here ever carries private text, and none carries a research passage: **the drafter only ever
receives the brief** (:func:`drafting_request` takes a :class:`~blogwriter.domain.research.Brief`
and the owner's documents, nothing a fetcher returned).

Drafting and revising share a cached prefix (``cache_breakpoint`` on its last part): the voice
guide, the brief and the selected stories, rendered deterministically so revise loops and owner
revisions hit Anthropic's 1 hour prompt cache. Everything that varies (the plan, the interview,
the draft, the notes) comes after the breakpoint.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from blogwriter.ai.types import Message, Request, Sensitivity, TextPart
from blogwriter.domain.common import Finding
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import FormatPack, Pillar
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry
from blogwriter.domain.voice import StyleGuide
from blogwriter.pipeline.models import CritiqueOut, InterviewOut, OutlineOut
from blogwriter.pipeline.prompting import (
    CRITIQUE,
    DRAFT,
    INTERVIEW,
    OUTLINE,
    REVISE,
    SELECTION,
    fenced,
    load_prompt,
)
from blogwriter.pipeline.text import LintIssue, clip
from blogwriter.stories import StoryBank
from blogwriter.voice import guide_prompt

__all__ = [
    "GUIDE_EXCERPT_CHARS",
    "Outline",
    "SlopNote",
    "clip_lines",
    "critique_request",
    "drafting_prefix",
    "drafting_request",
    "interview_request",
    "outline_request",
    "owner_revise_request",
    "render_bank",
    "render_brief",
    "render_interview",
    "render_outline",
    "revise_request",
    "selection_request",
    "slop_notes",
]

type Outline = OutlineOut
"""The plan the drafter follows (the OUTLINE step's structured output)."""

GUIDE_EXCERPT_CHARS: Final = 6000
"""The critic sees the head of the guide only (voice is judged, not imitated, there)."""
_MAX_BANK_STORIES: Final = 25
_MAX_BANK_POVS: Final = 25


# --------------------------------------------------------------------------------------------
# Rendering (deterministic: the same documents always give the same bytes)
# --------------------------------------------------------------------------------------------


def _bullets(items: Iterable[str]) -> list[str]:
    return [f"- {item}" for item in items if item.strip()]


def render_brief(brief: Brief | None) -> str:
    """The research brief as the models read it: claims, statistics, short quotes and the
    sources to cite by id. No URLs (the pipeline writes the footnotes itself). ``None`` (an
    article the owner wrote without research) says so."""
    if brief is None:
        return fenced(
            "brief",
            "No research brief for this article. Keep the existing citations as they are and "
            "add no new ones.",
        )
    lines: list[str] = [f"Topic: {brief.topic}"]
    if brief.audience:
        lines.append(f"Audience: {brief.audience}")
    lines += ["", "Executive summary:", brief.executive_summary.strip()]
    if brief.findings:
        lines += ["", "Findings:"]
        lines += [
            f"- [{item.id}] {item.text} (cites: {', '.join(item.citation_ids) or 'none'})"
            for item in brief.findings
        ]
    if brief.stats:
        lines += ["", "Statistics:"]
        for stat in brief.stats:
            when = f", as of {stat.as_of}" if stat.as_of else ""
            key = ", key statistic" if stat.key else ""
            lines.append(
                f"- [{stat.id}] {stat.value}: {stat.label} "
                f"(cites: {', '.join(stat.citation_ids) or 'none'}{when}{key})"
            )
    if brief.claims:
        lines += ["", "Claims, each with the source that supports it:"]
        for claim in brief.claims:
            note = f"; conflict: {claim.conflict_note}" if claim.conflict_note else ""
            lines.append(
                f"- [{claim.id}] {claim.text} -> [^{claim.citation_id}] ({claim.status}{note})"
            )
    if brief.quotes:
        lines += ["", "Verbatim quotes (at most 200 characters per source across the article):"]
        lines += [
            f'- [{quote.id}] "{quote.text}" -> [^{quote.citation_id}]' for quote in brief.quotes
        ]
    for heading, items in (
        ("Counterpoints:", brief.counterpoints),
        ("Open questions:", brief.open_questions),
        ("Angles:", brief.angles),
        ("Hook seeds:", brief.hook_seeds),
    ):
        if items:
            lines += ["", heading, *_bullets(items)]
    lines += ["", "Sources you may cite (write the label exactly, as [^id]):"]
    if brief.citations:
        for citation in brief.citations:
            details = ", ".join(
                part for part in (citation.publisher, citation.date, citation.domain) if part
            )
            lines.append(f"- [^{citation.id}] {citation.title} ({details})")
    else:
        lines.append("- none: write without citations")
    return fenced("brief", "\n".join(lines))


def _story_lines(story: StoryEntry) -> list[str]:
    lines = [f"[{story.id}] {story.title}"]
    for label, value in (
        ("Problem", story.problem),
        ("Actions", story.actions),
        ("Decisions and trade-offs", story.decisions),
        ("Lessons", story.lessons),
    ):
        if value.strip():
            lines.append(f"{label}: {value.strip()}")
    if story.metrics:
        metrics = "; ".join(f"{metric.label}: {metric.value}" for metric in story.metrics)
        lines.append(f"Metrics: {metrics}")
    lines.append(
        "Confidentiality: anonymized (keep it that way)"
        if story.confidentiality == "anonymized"
        else "Confidentiality: public"
    )
    return lines


def _pov_lines(pov: PovEntry) -> list[str]:
    lines = [f"[{pov.id}] ({pov.pillar}) {pov.statement.strip()}"]
    if pov.context:
        lines.append(f"Context: {pov.context.strip()}")
    return lines


def render_bank(bank: StoryBank) -> str:
    """The owner's stories and positions (the only first-hand material besides the interview)."""
    stories = bank.stories[:_MAX_BANK_STORIES]
    povs = bank.povs[:_MAX_BANK_POVS]
    if not stories and not povs:
        body = (
            "No stories or positions were selected. Nothing in the article may claim first-hand "
            "experience except what the interview answers say."
        )
        return fenced("author_material", body)
    lines: list[str] = []
    if stories:
        lines.append("Stories (the author's own experience):")
        for story in sorted(stories, key=lambda entry: entry.id):
            lines += ["", *_story_lines(story)]
    if povs:
        if lines:
            lines.append("")
        lines.append("Positions (the author's views, in their words):")
        for pov in sorted(povs, key=lambda entry: entry.id):
            lines += ["", *_pov_lines(pov)]
    return fenced("author_material", "\n".join(lines))


def render_interview(interview: Interview | None) -> str | None:
    """The confirmed interview answers (anonymized wording), or ``None`` without answers."""
    if interview is None or not interview.answers:
        return None
    lines: list[str] = []
    for question in interview.questions:
        answer = interview.answer(question.id)
        if answer is None:
            continue
        lines += [
            f"[{question.id}] Question: {question.text}",
            f"Answer: {answer.text.strip()}",
            "",
        ]
    if not lines:
        return None
    return fenced("interview", "\n".join(lines).strip())


def render_outline(outline: Outline) -> str:
    """The plan as the drafter and the reviser read it."""
    lines = [
        f"Thesis: {outline.thesis}",
        f"Opening: {outline.opening}",
        "",
        "Sections:",
    ]
    for index, section in enumerate(outline.sections, 1):
        refs = [
            *(f"[^{cid}]" for cid in section.citation_ids),
            *section.story_ids,
            *section.pov_ids,
            *(f"interview {qid}" for qid in section.interview_question_ids),
        ]
        lines.append(f"{index}. {section.heading} - {section.purpose}")
        lines += [f"   - {point}" for point in section.points]
        if refs:
            lines.append(f"   Uses: {', '.join(refs)}")
    if outline.diagram:
        lines += ["", f"Diagram: {outline.diagram}"]
    lines += ["", f"Closing (the reader's first step on Monday): {outline.closing}"]
    return fenced("plan", "\n".join(lines))


def _assignment(
    pack: FormatPack, pillar: Pillar, *, title: str | None, citation_ids: Sequence[str]
) -> str:
    lines: list[str] = []
    if title:
        lines.append(f"Title (set separately; do not repeat it as a heading): {title}")
    lines.append(f"Format: {pack.name}, {pack.min_words}-{pack.max_words} words of prose.")
    if pack.must_contain:
        lines.append(f"Must contain: {'; '.join(pack.must_contain)}.")
    if pack.banned:
        lines.append(f"Never: {'; '.join(pack.banned)}.")
    lines.append(
        f"Pillar: {pillar.name}." + (f" Topics: {pillar.topics}." if pillar.topics else "")
    )
    if pillar.audience:
        lines.append(f"Audience: {pillar.audience}")
    if pillar.vocabulary:
        lines.append(f"Use these terms as written: {', '.join(pillar.vocabulary)}.")
    if pillar.renames:
        renames = "; ".join(f"{old} -> {new}" for old, new in sorted(pillar.renames.items()))
        lines.append(f"Current names (never the old ones): {renames}.")
    cites = ", ".join(f"[^{cid}]" for cid in citation_ids) or "none"
    lines.append(f"Citation labels you may use: {cites}.")
    return fenced("assignment", "\n".join(lines))


def _user(*parts: TextPart) -> tuple[Message, ...]:
    return (Message("user", parts),)


# --------------------------------------------------------------------------------------------
# INTERVIEW and OUTLINE
# --------------------------------------------------------------------------------------------


def interview_request(
    *,
    topic: str,
    pack: FormatPack,
    pillar: Pillar,
    brief: Brief | None,
    gaps: Sequence[str],
    positions: Sequence[str],
    count: int,
    mode: str,
) -> Request:
    """INTERVIEW: ``count`` questions built from the brief and the story bank's gaps (never
    from private text: the gaps and positions are approved, anonymized material)."""
    lines = [
        f"Interview: {mode} ({count} questions).",
        f"Article topic: {topic}",
        f"Format: {pack.name}. Must contain: {'; '.join(pack.must_contain) or '-'}.",
        f"Pillar: {pillar.name}." + (f" Topics: {pillar.topics}." if pillar.topics else ""),
    ]
    if pillar.audience:
        lines.append(f"Audience: {pillar.audience}")
    parts = [TextPart("\n".join(lines))]
    if brief is not None:
        brief_lines = [brief.executive_summary.strip()]
        if brief.findings:
            brief_lines += ["", "Findings:", *_bullets(item.text for item in brief.findings[:12])]
        for heading, items in (
            ("Angles:", brief.angles),
            ("Counterpoints:", brief.counterpoints),
            ("Open questions for the author:", brief.open_questions),
        ):
            if items:
                brief_lines += ["", heading, *_bullets(items[:8])]
        parts.append(TextPart(fenced("brief", "\n".join(brief_lines))))
    gap_text = "\n".join(_bullets(gaps)) or "- none"
    parts.append(TextPart(fenced("story_gaps", gap_text)))
    known = "\n".join(_bullets(positions[:12])) or "- none yet"
    parts.append(TextPart(fenced("known_positions", known)))
    parts.append(
        TextPart(
            f"Write exactly {count} questions for this {mode} interview. Return only the JSON."
        )
    )
    return Request(
        system=load_prompt(INTERVIEW).text,
        messages=_user(*parts),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=InterviewOut,
    )


def outline_request(
    *,
    brief: Brief,
    bank: StoryBank,
    interview: Interview | None,
    pack: FormatPack,
    pillar: Pillar,
    title: str | None,
) -> Request:
    """OUTLINE: the plan, from the brief, the usable stories and POVs and the answers."""
    parts = [
        TextPart(
            _assignment(pack, pillar, title=title, citation_ids=[c.id for c in brief.citations])
        ),
        TextPart(render_brief(brief)),
        TextPart(render_bank(bank)),
    ]
    answers = render_interview(interview)
    if answers is not None:
        parts.append(TextPart(answers))
    parts.append(TextPart("Plan the article. Return only the JSON."))
    return Request(
        system=load_prompt(OUTLINE).text,
        messages=_user(*parts),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=OutlineOut,
    )


# --------------------------------------------------------------------------------------------
# DRAFT and REVISE (the cached prefix)
# --------------------------------------------------------------------------------------------


def drafting_prefix(
    guide: StyleGuide, brief: Brief | None, bank: StoryBank
) -> tuple[TextPart, ...]:
    """The cached prefix: voice guide, brief, stories (breakpoint on the last part)."""
    return (
        TextPart(guide_prompt(guide)),
        TextPart(render_brief(brief)),
        TextPart(render_bank(bank), cache_breakpoint=True),
    )


def drafting_request(
    *,
    guide: StyleGuide,
    brief: Brief,
    bank: StoryBank,
    interview: Interview | None,
    outline: Outline,
    pack: FormatPack,
    pillar: Pillar,
) -> Request:
    """DRAFT: the cached prefix (style guide + brief + stories), then the assignment, the plan
    and the interview answers. ``Sensitivity.ANONYMIZED``; the drafter only ever receives the
    brief (never a source passage)."""
    tail = [
        TextPart(
            _assignment(
                pack, pillar, title=outline.title, citation_ids=[c.id for c in brief.citations]
            )
        ),
        TextPart(render_outline(outline)),
    ]
    answers = render_interview(interview)
    if answers is not None:
        tail.append(TextPart(answers))
    tail.append(TextPart("Write the article body now, following the plan."))
    return Request(
        system=load_prompt(DRAFT).text,
        messages=_user(*drafting_prefix(guide, brief, bank), *tail),
        sensitivity=Sensitivity.ANONYMIZED,
    )


@dataclass(frozen=True, slots=True)
class SlopNote:
    """A structural slop finding with the text it points at (the draft is the model's own)."""

    code: str
    message: str
    excerpt: str


def _checks_text(
    *, words: int, pack: FormatPack, errors: Sequence[LintIssue], slop: Sequence[SlopNote]
) -> str | None:
    lines: list[str] = []
    if words < pack.min_words:
        lines.append(
            f"- Length: {words} words, below the format's {pack.min_words}. "
            "Add substance, not filler."
        )
    elif words > pack.max_words:
        lines.append(
            f"- Length: {words} words, above the format's {pack.max_words}. Cut repetition."
        )
    for error in errors:
        where = f" (line {error.line})" if error.line is not None else ""
        lines.append(f"- Markdown dialect error {error.code}{where}: fix the construct.")
    for note in slop:
        lines.append(f'- {note.code}: {note.message} At: "{note.excerpt}"')
    return fenced("checks", "\n".join(lines)) if lines else None


def _critique_text(critique: CritiqueOut) -> str:
    scores = ", ".join(f"{name} {value}" for name, value in critique.scores().items())
    lines = [f"Scores: {scores}; overall {critique.overall}.", "", "Notes:"]
    lines += [
        f"- ({note.dimension}) at {note.where}: {note.problem} Fix: {note.fix}"
        for note in critique.notes
    ] or ["- none"]
    if critique.keep:
        lines += ["", "Keep:", *_bullets(critique.keep)]
    return fenced("critique", "\n".join(lines))


def revise_request(
    *,
    guide: StyleGuide,
    brief: Brief,
    bank: StoryBank,
    interview: Interview | None,
    outline: Outline | None,
    pack: FormatPack,
    pillar: Pillar,
    title: str | None,
    draft: str,
    critique: CritiqueOut | None,
    words: int,
    errors: Sequence[LintIssue] = (),
    slop: Sequence[SlopNote] = (),
) -> Request:
    """REVISE in the critique loop: the cached prefix, then the plan, the draft, the critic's
    notes and the automatic checks."""
    tail = [
        TextPart(
            _assignment(pack, pillar, title=title, citation_ids=[c.id for c in brief.citations])
        )
    ]
    if outline is not None:
        tail.append(TextPart(render_outline(outline)))
    answers = render_interview(interview)
    if answers is not None:
        tail.append(TextPart(answers))
    tail.append(TextPart(fenced("draft", draft)))
    if critique is not None:
        tail.append(TextPart(_critique_text(critique)))
    checks = _checks_text(words=words, pack=pack, errors=errors, slop=slop)
    if checks is not None:
        tail.append(TextPart(checks))
    tail.append(
        TextPart(
            "Revise the draft: fix what the critique and the checks name, keep the rest. "
            "Return the complete revised body."
        )
    )
    return Request(
        system=load_prompt(REVISE).text,
        messages=_user(*drafting_prefix(guide, brief, bank), *tail),
        sensitivity=Sensitivity.ANONYMIZED,
    )


def owner_revise_request(
    *,
    guide: StyleGuide,
    brief: Brief | None,
    bank: StoryBank,
    interview: Interview | None,
    pack: FormatPack,
    pillar: Pillar,
    title: str | None,
    draft: str,
    instruction: str,
) -> Request:
    """REVISE for the owner's instruction (``pipeline.revise``): the cached prefix, the draft
    and the instruction (already cleared by the blocklist in the route)."""
    cited = [c.id for c in brief.citations] if brief is not None else []
    tail = [TextPart(_assignment(pack, pillar, title=title, citation_ids=cited))]
    answers = render_interview(interview)
    if answers is not None:
        tail.append(TextPart(answers))
    tail.append(TextPart(fenced("draft", draft)))
    tail.append(TextPart(fenced("author_instruction", instruction.strip())))
    tail.append(
        TextPart(
            "Revise the draft as the author's instruction asks, keep everything else. "
            "Return the complete revised body."
        )
    )
    return Request(
        system=load_prompt(REVISE).text,
        messages=_user(*drafting_prefix(guide, brief, bank), *tail),
        sensitivity=Sensitivity.ANONYMIZED,
    )


# --------------------------------------------------------------------------------------------
# CRITIQUE and the selection proposal
# --------------------------------------------------------------------------------------------


def critique_request(
    *,
    guide: StyleGuide,
    brief: Brief,
    bank: StoryBank,
    interview: Interview | None,
    pack: FormatPack,
    pillar: Pillar,
    title: str | None,
    excerpt: str | None,
    body: str,
    words: int,
) -> Request:
    """CRITIQUE: gate 1's rubric over the draft, with the material it may draw on (so invented
    first-hand claims are visible to the critic) and revision notes."""
    meta = [
        f"Title: {title or '(none)'}",
        f"Excerpt: {excerpt or '(none)'}",
        f"Words: {words} (format range {pack.min_words}-{pack.max_words})",
        f"Pillar: {pillar.name}.",
        f"Format: {pack.name}. Must contain: {'; '.join(pack.must_contain) or '-'}.",
    ]
    if pack.banned:
        meta.append(f"Banned: {'; '.join(pack.banned)}.")
    parts = [
        TextPart("\n".join(meta)),
        TextPart(fenced("style_guide", clip_lines(guide.body, GUIDE_EXCERPT_CHARS))),
        TextPart(render_brief(brief)),
        TextPart(render_bank(bank)),
    ]
    answers = render_interview(interview)
    if answers is not None:
        parts.append(TextPart(answers))
    parts.append(TextPart(fenced("article", body)))
    parts.append(
        TextPart("Score the article in <article> and write the notes. Return only the JSON.")
    )
    return Request(
        system=load_prompt(CRITIQUE).text,
        messages=_user(*parts),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=CritiqueOut,
    )


def selection_request(
    *, guide: StyleGuide | None, pillar: Pillar | None, text: str, instruction: str
) -> Request:
    """REVISE for one selected passage (the editor's selection AI)."""
    parts: list[TextPart] = []
    if guide is not None:
        parts.append(TextPart(guide_prompt(guide), cache_breakpoint=True))
    if pillar is not None and (pillar.vocabulary or pillar.renames):
        terms = ", ".join(pillar.vocabulary) or "-"
        renames = "; ".join(f"{old} -> {new}" for old, new in sorted(pillar.renames.items())) or "-"
        parts.append(TextPart(f"Terms to keep as written: {terms}. Current names: {renames}."))
    parts.append(TextPart(fenced("passage", text)))
    parts.append(TextPart(fenced("author_instruction", instruction.strip())))
    parts.append(
        TextPart("Rewrite the passage as the instruction asks. Return only the new passage.")
    )
    return Request(
        system=load_prompt(SELECTION).text,
        messages=_user(*parts),
        sensitivity=Sensitivity.ANONYMIZED,
    )


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def clip_lines(text: str, max_chars: int) -> str:
    """The head of ``text`` (whole lines) within ``max_chars``."""
    if len(text) <= max_chars:
        return text.strip()
    cut = text[:max_chars]
    newline = cut.rfind("\n")
    return (cut[:newline] if newline > max_chars // 2 else cut).strip()


def slop_notes(body: str, findings: Sequence[Finding], *, limit: int = 12) -> list[SlopNote]:
    """Slop findings with a short excerpt of the text they point at (for the reviser)."""
    notes: list[SlopNote] = []
    for finding in findings:
        if finding.start is None or finding.end is None:
            excerpt = "(whole article)"
        else:
            excerpt = clip(body[finding.start : finding.end], 160)
        notes.append(SlopNote(code=finding.code, message=finding.message, excerpt=excerpt))
        if len(notes) >= limit:
            break
    return notes
