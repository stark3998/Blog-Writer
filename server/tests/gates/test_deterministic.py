"""The deterministic step and the live editor lint."""

from __future__ import annotations

from blogwriter.domain.articles import GateName, OwnerSignalRef
from blogwriter.domain.settings import PillarSettings, StandardsCatalog
from blogwriter.domain.testing import SAMPLE_NOW, FakeSlopChecker, make_services
from blogwriter.gates import SlopDetectors, lint_text
from blogwriter.gates.deterministic import deterministic_checks
from blogwriter.gates.inputs import load_inputs
from blogwriter.gates.testing import (
    BLOCKED_TERM,
    SAMPLE_BODY,
    SAMPLE_META,
    gate_world,
    sample_brief,
    stored_article,
)
from blogwriter.jobs.testing import FakeClock

PILLARS = PillarSettings.default(SAMPLE_NOW)
CATALOG = StandardsCatalog.default(SAMPLE_NOW)


def test_lint_text_on_a_clean_article_is_quiet() -> None:
    findings = lint_text(
        SAMPLE_META,
        SAMPLE_BODY,
        brief=sample_brief(),
        catalog=CATALOG,
        pillars=PILLARS,
        slop=SlopDetectors(),
    )
    assert findings == []


def test_lint_text_reports_each_gate_family_sorted_by_position() -> None:
    body = (
        "Security isn't a tool, it's a mindset. OWASP LLM11 covers it.[^src-ghost]\n\n"
        "See [the guide](https://random.example.net/x?utm_source=feed).\n\n"
        "[^src-ghost]: Somebody. *Something*.\n"
    )
    findings = lint_text(
        SAMPLE_META,
        body,
        brief=sample_brief(),
        catalog=CATALOG,
        pillars=PILLARS,
        slop=SlopDetectors(),
    )
    codes = [finding.code for finding in findings]
    for code in (
        "isnt-x-its-y",
        "standard-id-unknown",
        "citation-not-in-brief",
        "citation-unknown-source",
        "outbound-domain",
        "tracking-parameter",
    ):
        assert code in codes, code
    starts = [finding.start if finding.start is not None else -1 for finding in findings]
    assert starts == sorted(starts)


def test_lint_text_without_a_brief_does_not_judge_sources() -> None:
    body = "A claim.[^src-ghost]\n\n[^src-ghost]: Somebody. *Something*.\n"
    findings = lint_text(
        SAMPLE_META, body, brief=None, catalog=CATALOG, pillars=PILLARS, slop=FakeSlopChecker()
    )
    codes = [finding.code for finding in findings]
    assert "citation-unknown-source" not in codes
    assert "citation-not-in-brief" not in codes


async def test_the_deterministic_step_fills_six_gates_and_flags_the_blocklist() -> None:
    w = await gate_world()
    inputs = await load_inputs(w.services.store, w.article, now=SAMPLE_NOW)
    clean = deterministic_checks(
        w.article,
        inputs,
        slop=SlopDetectors(),
        site_host=None,
        content_sha256=w.article.compute_sha256(),
        now=FakeClock().now(),
    )
    assert set(clean.drafts) == set(GateName) - {GateName.RUBRIC}
    assert not clean.blocklist_hit
    assert all(not draft.findings for draft in clean.drafts.values())
    assert clean.measure.words > 100
    leaked = stored_article(
        markdown=SAMPLE_BODY.replace("Last March", f"Last March, at {BLOCKED_TERM},"),
        owner_signals=[OwnerSignalRef(kind="story", id=w.story.id)],
    )
    hit = deterministic_checks(
        leaked,
        inputs,
        slop=SlopDetectors(),
        site_host=None,
        content_sha256=leaked.compute_sha256(),
        now=FakeClock().now(),
    )
    assert hit.blocklist_hit
    assert [f.code for f in hit.drafts[GateName.CONFIDENTIALITY].findings] == ["blocklisted-term"]


async def test_inputs_skip_unreadable_owner_material() -> None:
    services = make_services()
    article = stored_article(
        owner_signals=[OwnerSignalRef(kind="story", id="story-missing")], brief_id="brief-missing"
    )
    inputs = await load_inputs(services.store, article, now=SAMPLE_NOW)
    assert inputs.stories == ()
    assert inputs.brief is None
    assert inputs.citation_ids == frozenset()
    assert inputs.catalog.families["owasp_llm"]  # the seeds, even before the first refresh
