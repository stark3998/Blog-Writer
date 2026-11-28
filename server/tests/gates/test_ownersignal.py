"""Gate 6: a usable story or POV, or a confirmed interview answer quoted verbatim."""

from __future__ import annotations

from blogwriter.domain.articles import Article, OwnerSignalRef
from blogwriter.domain.common import ClassifierCheck
from blogwriter.domain.stories import Interview, InterviewAnswer, PovEntry
from blogwriter.domain.testing import SAMPLE_NOW, sample_document
from blogwriter.gates.ownersignal import MIN_INTERVIEW_QUOTE_CHARS, owner_signal_findings
from blogwriter.gates.testing import SAMPLE_BODY, usable_story

ANSWER = "Scope every agent token to one tool call and expire it within five minutes."


def article(*refs: OwnerSignalRef, body: str = SAMPLE_BODY) -> Article:
    return sample_document(Article, markdown=body, owner_signals=list(refs))


def interview(text: str = ANSWER) -> Interview:
    return sample_document(
        Interview,
        status="answered",
        answers=[InterviewAnswer(question_id="q1", text=text, confirmed_at=SAMPLE_NOW)],
    )


def test_a_usable_story_passes() -> None:
    story = usable_story()
    doc = article(OwnerSignalRef(kind="story", id=story.id))
    assert (
        owner_signal_findings(doc, body=doc.markdown, stories=[story], povs=[], interviews=[]) == []
    )


def test_unapproved_unclassified_or_unreferenced_material_does_not_count() -> None:
    proposed = usable_story(status="proposed")
    flagged = usable_story(classifier=ClassifierCheck(passed=False))
    unreferenced = usable_story()
    doc = article(
        OwnerSignalRef(kind="story", id=proposed.id), OwnerSignalRef(kind="story", id=flagged.id)
    )
    findings = owner_signal_findings(
        doc, body=doc.markdown, stories=[proposed, flagged, unreferenced], povs=[], interviews=[]
    )
    assert [(f.code, f.severity, f.overridable) for f in findings] == [
        ("owner-signal-missing", "block", True)
    ]


def test_a_usable_pov_passes() -> None:
    pov = sample_document(PovEntry, status="approved", classifier=ClassifierCheck(passed=True))
    doc = article(OwnerSignalRef(kind="pov", id=pov.id))
    assert (
        owner_signal_findings(doc, body=doc.markdown, stories=[], povs=[pov], interviews=[]) == []
    )


def test_a_confirmed_answer_quoted_verbatim_passes() -> None:
    answers = interview()
    quoted = f"My rule is blunt.\n\n> {ANSWER.upper()}\n"
    doc = article(OwnerSignalRef(kind="interview", id=answers.id), body=quoted)
    assert owner_signal_findings(doc, body=quoted, stories=[], povs=[], interviews=[answers]) == []
    paraphrased = "My rule: tokens are scoped per tool call and expire quickly.\n"
    findings = owner_signal_findings(
        doc, body=paraphrased, stories=[], povs=[], interviews=[answers]
    )
    assert [f.code for f in findings] == ["owner-signal-missing"]


def test_short_answers_are_not_enough() -> None:
    short = "Per tool call, always."
    assert len(short) < MIN_INTERVIEW_QUOTE_CHARS
    answers = interview(short)
    doc = article(OwnerSignalRef(kind="interview", id=answers.id), body=f"{short}\n")
    findings = owner_signal_findings(
        doc, body=doc.markdown, stories=[], povs=[], interviews=[answers]
    )
    assert [f.code for f in findings] == ["owner-signal-missing"]
