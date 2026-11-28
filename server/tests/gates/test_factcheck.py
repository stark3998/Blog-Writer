"""Gate 2: citations exist, claims map, cited sources are re-fetched and verified (passages in
memory only)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.articles import Article
from blogwriter.domain.common import Finding
from blogwriter.domain.research import Brief, BriefClaim
from blogwriter.domain.settings import Blocklist, EmployerPolicy
from blogwriter.domain.stories import StoryEntry
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    ScriptedModelRunner,
    ScriptedReply,
    StaticPageFetcher,
    sample_document,
)
from blogwriter.gates.factcheck import (
    PASSAGE_RUN_CHARS,
    ClaimMap,
    ClaimVerdict,
    FactCheckOutcome,
    MappedClaim,
    SourceCheck,
    citation_findings,
    fact_check_draft,
    locate,
)
from blogwriter.gates.testing import (
    SAMPLE_BODY,
    SAMPLE_CITATION,
    SAMPLE_META,
    SAMPLE_PASSAGE,
    SAMPLE_URL,
    sample_brief,
    saved_blocklist,
    usable_story,
)
from blogwriter.jobs.testing import FakeClock

POLICY = EmployerPolicy.default(SAMPLE_NOW).model_copy(update={"ai_use_confirmed": True})
ORIGIN: Origin = "background"
CLAIM = "SCIM provisioning broke for 37 of them"
BRIEF = sample_brief(
    claims=[
        BriefClaim(
            id="claim-1",
            text="Attribute mapping is the first thing to plan.",
            citation_id=SAMPLE_CITATION,
        )
    ]
)
STORY = usable_story()
ARTICLE = sample_document(Article, meta=SAMPLE_META, markdown=SAMPLE_BODY, brief_id=BRIEF.id)
BLOCKLIST = saved_blocklist()


def mapped(*claims: MappedClaim) -> ClaimMap:
    return ClaimMap(claims=list(claims))


def claim(
    claim_id: str = "c1",
    *,
    quote: str = CLAIM,
    citations: Sequence[str] = (SAMPLE_CITATION,),
    support: str = "owner_material",
    support_ref: str | None = None,
) -> MappedClaim:
    return MappedClaim.model_validate(
        {
            "id": claim_id,
            "quote": quote,
            "citations": list(citations),
            "support": support,
            "support_ref": support_ref if support_ref is not None else STORY.id,
        }
    )


async def check(
    runner: ScriptedModelRunner | None,
    fetcher: StaticPageFetcher | None = None,
    *,
    policy: EmployerPolicy = POLICY,
    article: Article = ARTICLE,
    blocklist: Blocklist = BLOCKLIST,
    stories: Sequence[StoryEntry] = (STORY,),
    brief: Brief = BRIEF,
) -> FactCheckOutcome:
    return await fact_check_draft(
        article,
        brief=brief,
        stories=list(stories),
        povs=[],
        interviews=[],
        policy=policy,
        blocklist=blocklist,
        runner=runner,
        fetcher=fetcher if fetcher is not None else StaticPageFetcher({SAMPLE_URL: SAMPLE_PASSAGE}),
        origin=ORIGIN,
        job_id="job-1",
        now=FakeClock().now(),
    )


def codes(findings: Sequence[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def test_citations_must_exist_in_the_brief() -> None:
    body = "A claim.[^src-entra] Another one.[^src-ghost]\n\n[^src-entra]: A.\n\n[^src-ghost]: B.\n"
    findings = citation_findings(body, {SAMPLE_CITATION})
    assert [(f.code, f.ref, f.overridable) for f in findings] == [
        ("citation-not-in-brief", "src-ghost", True)
    ]
    assert body[findings[0].start or 0 : findings[0].end or 0] == "[^src-ghost]"
    assert citation_findings(body, {"src-entra", "src-ghost"}) == []


def test_locate_ignores_case_whitespace_and_emphasis() -> None:
    body = "We **moved** 2,000 apps\nfrom Okta. Then it broke."
    span = locate(body, "we moved 2,000 apps from okta")
    assert span is not None
    assert body[span[0] : span[1]] == "We **moved** 2,000 apps\nfrom Okta"
    assert locate(body, "nothing like this sentence at all") is None


async def test_a_supported_claim_passes_and_the_passage_stays_in_the_request() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    runner.add(Task.FACT_CHECK, SourceCheck(verdicts=[ClaimVerdict(id="c1", verdict="supported")]))
    outcome = await check(runner)
    assert outcome.draft.findings == []
    assert outcome.draft.error is None
    calls = runner.calls_for(Task.FACT_CHECK)
    assert len(calls) == 2
    for call in calls:
        assert call.request.sensitivity is Sensitivity.ANONYMIZED
        assert call.request.tools == ()
        assert call.origin == "background"
        assert call.job_id == "job-1"
    mapping = calls[0].request.messages[0].text
    assert "claim claim-1" in mapping
    assert "<article>" in mapping
    assert SAMPLE_PASSAGE not in mapping  # the mapper never sees a passage
    verify = calls[1].request.messages[0].text
    assert SAMPLE_PASSAGE in verify
    dumped = outcome.draft.model_dump_json()
    assert "attribute mapping before you migrate" not in dumped  # never stored
    assert [record.step for record in outcome.draft.served_by] == [
        "gates.fact_check.map",
        "gates.fact_check.verify",
    ]


@pytest.mark.parametrize(
    ("verdict", "code", "severity"),
    [
        ("contradicted", "claim-contradicted", "block"),
        ("not_found", "claim-unverified", "block"),
        ("partly_supported", "claim-partly-supported", "warn"),
    ],
)
async def test_verdicts_become_findings(verdict: str, code: str, severity: str) -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    runner.add(
        Task.FACT_CHECK,
        SourceCheck(verdicts=[ClaimVerdict.model_validate({"id": "c1", "verdict": verdict})]),
    )
    outcome = await check(runner)
    finding = outcome.draft.findings[0]
    assert (finding.code, finding.severity, finding.ref) == (code, severity, SAMPLE_CITATION)
    assert SAMPLE_BODY[finding.start or 0 : finding.end or 0] == CLAIM


async def test_a_missing_verdict_or_a_refused_verification_is_unverified() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    runner.add(Task.FACT_CHECK, SourceCheck(verdicts=[]))
    assert codes((await check(runner)).draft.findings) == ["claim-unverified"]
    refusing = ScriptedModelRunner()
    refusing.add(Task.FACT_CHECK, mapped(claim()), ScriptedReply(refused=True))
    outcome = await check(refusing)
    assert codes(outcome.draft.findings) == ["claim-unverified"]
    assert "could not be checked" in outcome.draft.findings[0].message


async def test_unsupported_and_uncited_claims() -> None:
    runner = ScriptedModelRunner()
    runner.add(
        Task.FACT_CHECK,
        mapped(
            claim("c1", citations=(), support="none"),
            claim("c2", citations=(), support="brief_claim", support_ref="claim-1"),
            claim("c3", citations=(), support="brief_stat", support_ref="no-such-stat"),
            claim("c4", citations=(), support="owner_material", support_ref="story-unknown"),
        ),
    )
    outcome = await check(runner)
    assert [(f.code, f.severity) for f in outcome.draft.findings] == [
        ("claim-unsupported", "block"),
        ("claim-unsupported", "block"),
        ("claim-unsupported", "block"),
        ("claim-uncited", "warn"),
    ]
    assert len(runner.calls_for(Task.FACT_CHECK)) == 1  # nothing cited: nothing re-fetched


async def test_an_unreachable_source_is_a_finding_without_a_verify_call() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    outcome = await check(runner, StaticPageFetcher({}))
    assert [(f.code, f.ref) for f in outcome.draft.findings] == [
        ("source-unreachable", SAMPLE_CITATION)
    ]
    assert "http_error" in outcome.draft.findings[0].message
    assert len(runner.calls_for(Task.FACT_CHECK)) == 1


async def test_text_shared_with_the_passage_is_counted_not_kept() -> None:
    copied = "Plan the attribute mapping before you migrate applications, and test provisioning"
    assert len(copied) >= PASSAGE_RUN_CHARS
    body = f"{SAMPLE_BODY}\n{copied} on a pilot group.\n"
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    runner.add(Task.FACT_CHECK, SourceCheck(verdicts=[ClaimVerdict(id="c1", verdict="supported")]))
    article = ARTICLE.model_copy(update={"markdown": body})
    outcome = await check(runner, article=article)
    assert outcome.passage_counts[SAMPLE_CITATION] >= len(copied)


async def test_the_gate_skips_without_its_services_or_the_ai_use_confirmation() -> None:
    assert (await check(None)).draft.unavailable == ["ai_unavailable"]
    runner = ScriptedModelRunner()
    no_fetcher = await fact_check_draft(
        ARTICLE,
        brief=BRIEF,
        stories=[],
        povs=[],
        interviews=[],
        policy=POLICY,
        blocklist=BLOCKLIST,
        runner=runner,
        fetcher=None,
        origin=ORIGIN,
        job_id=None,
        now=FakeClock().now(),
    )
    assert no_fetcher.draft.unavailable == ["fetcher_unavailable"]
    unconfirmed = await check(runner, policy=EmployerPolicy.default(SAMPLE_NOW))
    assert unconfirmed.draft.unavailable == ["ai_use_not_confirmed"]
    assert runner.calls == []


async def test_a_refused_mapping_is_an_error() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, ScriptedReply(refused=True))
    outcome = await check(runner)
    assert outcome.draft.error == "refused"
    assert outcome.draft.served_by[0].refused


async def test_sources_over_the_cap_are_findings_not_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("blogwriter.gates.factcheck.MAX_SOURCES_CHECKED", 0)
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    outcome = await check(runner)
    assert [(f.code, f.ref, f.overridable) for f in outcome.draft.findings] == [
        ("source-unchecked", SAMPLE_CITATION, True)
    ]
    assert len(runner.calls_for(Task.FACT_CHECK)) == 1


def test_odd_citation_labels_never_break_a_finding() -> None:
    body = 'A claim.[^src"odd\\label]\n\n[^src"odd\\label]: A.\n'
    (finding,) = citation_findings(body, set())
    assert finding.ref is None
    assert finding.code == "citation-not-in-brief"


# --------------------------------------------------------------------------------------------
# The blocklist at call time (the material, not only the article)
# --------------------------------------------------------------------------------------------


async def test_a_blocklisted_story_title_sends_nothing_and_skips() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    story = usable_story(title="Rebuilt the IAM stack at Contoso")
    outcome = await check(runner, stories=[story])
    assert runner.calls_for(Task.FACT_CHECK) == []  # no model call at all
    assert outcome.draft.unavailable == ["material_blocklisted"]
    assert not outcome.draft.findings


async def test_a_blocklisted_brief_claim_sends_nothing_and_skips() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    brief = sample_brief(
        claims=[
            BriefClaim(
                id="claim-1", text="Contoso cut tickets by 40%.", citation_id=SAMPLE_CITATION
            )
        ]
    )
    outcome = await check(runner, brief=brief)
    assert runner.calls_for(Task.FACT_CHECK) == []
    assert outcome.draft.unavailable == ["material_blocklisted"]


async def test_an_unsaved_blocklist_sends_nothing() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim()))
    outcome = await check(runner, blocklist=Blocklist.default(SAMPLE_NOW))
    assert runner.calls_for(Task.FACT_CHECK) == []
    assert outcome.draft.unavailable == ["material_blocklisted"]


async def test_a_blocklisted_claim_list_is_never_verified() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.FACT_CHECK, mapped(claim(quote=f"{CLAIM} at the Fabrikam office")))
    runner.add(Task.FACT_CHECK, SourceCheck(verdicts=[ClaimVerdict(id="c1", verdict="supported")]))
    outcome = await check(runner, blocklist=saved_blocklist("Fabrikam"))
    calls = runner.calls_for(Task.FACT_CHECK)
    assert len(calls) == 1  # the mapping only: the model-written claim list was screened out
    assert "claim-unverified" in codes(outcome.draft.findings)
