"""Gate 3: the blocklist on the exact bytes that ship (never echoing a term), forbidden topics
and the classifier (mosaic check)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from blogwriter.ai.ledger import Origin
from blogwriter.domain.articles import Article
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ClassifierCheck, Finding, MediaRef
from blogwriter.domain.confidentiality import AiUseNotConfirmed
from blogwriter.domain.ports import ServiceUnavailable
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.testing import SAMPLE_NOW, FakeClassifier, sample_document
from blogwriter.gates.confidentiality import (
    blocklist_gate_findings,
    classifier_draft,
    forbidden_topic_findings,
)
from blogwriter.gates.document import prepare_text
from blogwriter.gates.results import finalize
from blogwriter.gates.testing import BLOCKED_TERM, SAMPLE_META

BLOCKLIST = Blocklist(
    id=Blocklist.SINGLETON_ID,
    created_at=SAMPLE_NOW,
    updated_at=SAMPLE_NOW,
    configured=True,
    terms=[
        BlocklistTerm(id="client-1", term=BLOCKED_TERM, category="client"),
        BlocklistTerm(id="project-1", term="Bluebird", category="project"),
    ],
)


def article(body: str, **fields: object) -> Article:
    values: dict[str, object] = {"meta": SAMPLE_META, "markdown": body}
    values.update(fields)
    return sample_document(Article, **values)


def assert_never_echoes(findings: Sequence[Finding]) -> None:
    for finding in findings:
        dumped = finding.model_dump_json()
        assert BLOCKED_TERM.lower() not in dumped.lower()
        assert "bluebird" not in dumped.lower()


def test_blocklist_findings_point_at_the_body_and_never_echo_the_term() -> None:
    body = f"We helped {BLOCKED_TERM} move 2,000 apps.\n\nProject bluebird took 14 months.\n"
    doc = article(body)
    findings = blocklist_gate_findings(prepare_text(doc.meta, body), doc, BLOCKLIST)
    assert [(f.code, f.ref, f.severity, f.overridable) for f in findings] == [
        ("blocklisted-term", "client-1", "block", False),
        ("blocklisted-term", "project-1", "block", False),
    ]
    assert [body[f.start or 0 : f.end or 0] for f in findings] == [BLOCKED_TERM, "bluebird"]
    assert_never_echoes(findings)


def test_the_details_and_media_are_checked_too() -> None:
    meta = SAMPLE_META.model_copy(update={"title": f"How {BLOCKED_TERM} moved to Entra"})
    media = MediaRef(
        role="image",
        name="articles/art-1/abc.png",
        sha256="a" * 64,
        content_type="image/png",
        size_bytes=10,
        alt_text=f"The {BLOCKED_TERM} tenant",
        filename="bluebird-diagram.png",
    )
    doc = article("Clean body.\n", meta=meta, media=[media])
    findings = blocklist_gate_findings(prepare_text(meta, doc.markdown), doc, BLOCKLIST)
    assert len(findings) == 3
    assert all(f.start is None and f.end is None for f in findings)
    assert findings[0].message.startswith("In the article details")
    assert all(f.message.startswith("In a media file") for f in findings[1:])
    assert_never_echoes(findings)


def test_incomplete_meta_is_still_checked() -> None:
    meta = SAMPLE_META.model_copy(update={"slug": None, "excerpt": f"About {BLOCKED_TERM}."})
    doc = article(f"{BLOCKED_TERM} again.\n", meta=meta)
    findings = blocklist_gate_findings(prepare_text(meta, doc.markdown), doc, BLOCKLIST)
    assert [f.start for f in findings] == [None, 0]
    assert_never_echoes(findings)


def test_an_unconfigured_blocklist_is_a_hard_block() -> None:
    doc = article("Nothing confidential here.\n")
    unsaved = Blocklist.default(SAMPLE_NOW)
    findings = blocklist_gate_findings(prepare_text(doc.meta, doc.markdown), doc, unsaved)
    assert [(f.code, f.severity, f.overridable) for f in findings] == [
        ("blocklist-not-configured", "block", False)
    ]
    saved_empty = unsaved.model_copy(update={"configured": True})
    assert blocklist_gate_findings(prepare_text(doc.meta, doc.markdown), doc, saved_empty) == []


def test_forbidden_topics_block_overridably_by_number() -> None:
    policy = EmployerPolicy.default(SAMPLE_NOW).model_copy(
        update={"forbidden_topics": ["quarterly earnings", "layoffs"]}
    )
    body = "Our Quarterly\nEarnings call is not a topic here.\n"
    findings = forbidden_topic_findings(prepare_text(SAMPLE_META, body), policy)
    assert [(f.code, f.ref, f.overridable) for f in findings] == [
        ("forbidden-topic", "topic-1", True)
    ]
    assert "earnings" not in findings[0].message.lower()
    assert forbidden_topic_findings(prepare_text(SAMPLE_META, "Layoff math."), policy) == []


ORIGIN: Origin = "owner"


async def test_a_passing_classifier_adds_nothing() -> None:
    classifier = FakeClassifier()
    text = prepare_text(SAMPLE_META, "A clean body.\n")
    draft = await classifier_draft(text, classifier=classifier, origin=ORIGIN, job_id="job-1")
    assert draft.findings == []
    assert classifier.calls == [((text.checked,), "owner")]  # the exact bytes that ship


async def test_a_flagged_mosaic_blocks_overridably() -> None:
    body = "A bank in Ohio with 2,000 apps on Okta, migrated in Q3.\n"
    text = prepare_text(SAMPLE_META, body)
    draft = await classifier_draft(
        text, classifier=FakeClassifier(flag="bank in Ohio"), origin=ORIGIN, job_id=None
    )
    result = finalize(draft, ran_at=SAMPLE_NOW)
    assert result.verdict.value == "fail"
    assert [(f.code, f.overridable) for f in result.findings] == [("confidential-detail", True)]
    assert result.overridable  # only blocklist hits are hard in gate 3 (manual publish only)


class FlaggingClassifier:
    """Fails a text and returns the given findings (spans index the checked text)."""

    def __init__(self, findings: list[Finding], *, passed: bool = False) -> None:
        self.findings = findings
        self.passed = passed

    async def classify(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[ClassifierCheck]:
        return [
            ClassifierCheck(
                passed=self.passed,
                checked_at=SAMPLE_NOW,
                findings=self.findings,
                content_sha256=sha256_hex(texts[0]),
            )
            for _ in texts
        ]


async def test_classifier_spans_move_to_the_body_and_blocklist_codes_stay_hard() -> None:
    body = "We moved 2,000 apps.\n"
    text = prepare_text(SAMPLE_META, body)
    start = text.body_start + body.index("2,000")
    findings = [
        Finding(
            code="mosaic", severity="block", message="Too specific.", start=start, end=start + 5
        ),
        Finding(code="blocklisted-term", severity="block", message="Matches a term.", ref="t-1"),
    ]
    draft = await classifier_draft(
        text, classifier=FlaggingClassifier(findings), origin=ORIGIN, job_id=None
    )
    by_code = {finding.code: finding for finding in draft.findings}
    mosaic, blocked = by_code["mosaic"], by_code["blocklisted-term"]
    assert body[mosaic.start or 0 : mosaic.end or 0] == "2,000"
    assert mosaic.overridable
    assert not blocked.overridable
    empty = await classifier_draft(
        text, classifier=FlaggingClassifier([]), origin=ORIGIN, job_id=None
    )
    assert [f.code for f in empty.findings] == ["classifier-flagged"]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (AiUseNotConfirmed(), "ai_use_not_confirmed"),
        (ServiceUnavailable("ai"), "ai_unavailable"),
        (ServiceUnavailable("classifier"), "classifier_unavailable"),
    ],
)
async def test_a_classifier_that_cannot_run_skips_never_passes(
    error: Exception, reason: str
) -> None:
    text = prepare_text(SAMPLE_META, "Body.\n")
    draft = await classifier_draft(
        text, classifier=FakeClassifier(error=error), origin=ORIGIN, job_id=None
    )
    assert draft.unavailable == [reason]
    result = finalize(draft, ran_at=SAMPLE_NOW)
    assert result.verdict.value == "skipped"
    assert not result.passed
    assert not result.overridable


async def test_no_classifier_or_a_verdict_about_other_text_cannot_pass() -> None:
    text = prepare_text(SAMPLE_META, "Body.\n")
    missing = await classifier_draft(text, classifier=None, origin=ORIGIN, job_id=None)
    assert missing.unavailable == ["classifier_unavailable"]

    class WrongText(FlaggingClassifier):
        async def classify(
            self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
        ) -> list[ClassifierCheck]:
            return [ClassifierCheck(passed=True, content_sha256="0" * 64)]

    other = await classifier_draft(text, classifier=WrongText([]), origin=ORIGIN, job_id=None)
    assert other.unavailable == ["classifier_unavailable"]
