"""Brief assembly: references always resolve, verification rules, and a golden brief."""

from __future__ import annotations

import json
import os
from pathlib import Path

from blogwriter.ai.types import Task
from blogwriter.domain.common import Citation, Quote
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.research import Brief, BriefClaim, ResearchRun
from blogwriter.domain.testing import SAMPLE_NOW, ScriptedModelRunner, sample_document
from blogwriter.research.claims import citation_id_for
from blogwriter.research.models import BriefOutput, FindingOut, StatOut
from blogwriter.research.run import VerifiedState, build_brief
from blogwriter.research.testing import (
    ALLOW_URL,
    BLOCKED_TERM,
    OPEN_URL,
    brief_reply,
    extraction,
    new_run,
    page_fetcher,
    research_services,
    run_research,
    save_blocklist,
    script_happy_path,
    verify_reply,
)

GOLDENS = Path(__file__).parent / "goldens"
ALLOW_CITE = citation_id_for(ALLOW_URL)
OPEN_CITE = citation_id_for(OPEN_URL)
RUN_ID = ResearchRun.id_for_pipeline("art-0000000000000000000000000abc", 1)


def verified_state() -> VerifiedState:
    return VerifiedState(
        citations=[
            Citation(id="src-a-000001", url="https://nist.gov/a", title="A", domain="nist.gov"),
            Citation(id="src-b-000002", url="https://owasp.org/b", title="B", domain="owasp.org"),
        ],
        claims=[
            BriefClaim(id="cl-1", text="Claim one.", citation_id="src-a-000001", status="verified"),
            BriefClaim(id="cl-2", text="Claim two.", citation_id="src-b-000002"),
        ],
        quotes=[Quote(id="q-1", citation_id="src-a-000001", text="exact words from A")],
    )


def test_build_brief_repairs_references_and_filters_style() -> None:
    run = sample_document(ResearchRun, id=RUN_ID, audience="architects")
    output = BriefOutput(
        executive_summary="  A summary   with  spaces. ",
        findings=[
            FindingOut(text="Cites a claim id.", citation_ids=["cl-2"]),
            FindingOut(
                text="Cites a real and a fake source.", citation_ids=["src-a-000001", "src-x"]
            ),
            FindingOut(text="Cites nothing known.", citation_ids=["src-x"]),
            FindingOut(text="", citation_ids=["src-a-000001"]),
        ],
        counterpoints=["Counter.", "counter."],
        open_questions=["Question?"],
        angles=["Here's the thing: angles.", "A specific angle."],
        hook_seeds=["A concrete seed with 3 numbers.", "It's a game-changer for teams."],
    )
    brief = build_brief(run, verified_state(), output, served_by=[], stamp=SAMPLE_NOW)
    assert brief.id == Brief.id_for(RUN_ID)
    assert brief.run_id == RUN_ID
    assert brief.executive_summary == "A summary with spaces."
    assert [(f.text, f.citation_ids) for f in brief.findings] == [
        ("Cites a claim id.", ["src-b-000002"]),
        ("Cites a real and a fake source.", ["src-a-000001"]),
    ]
    assert brief.counterpoints == ["Counter."]
    assert brief.angles == ["A specific angle."]
    assert brief.hook_seeds == ["A concrete seed with 3 numbers."]
    assert brief.audience == "architects"
    Brief.from_document(brief.to_document())  # the model validator: every reference resolves


def test_without_model_output_the_claims_become_findings() -> None:
    run = sample_document(ResearchRun, id=RUN_ID)
    brief = build_brief(run, verified_state(), None, served_by=[], stamp=SAMPLE_NOW)
    assert brief.executive_summary.startswith("Automatic summary unavailable")
    assert [f.text for f in brief.findings] == ["Claim one.", "Claim two."]
    assert brief.hook_seeds == []
    assert brief.angles == []
    Brief.from_document(brief.to_document())


async def test_verification_rules_are_enforced_in_code() -> None:
    ai = ScriptedModelRunner()
    services = research_services(fetcher=page_fetcher(), ai=ai)
    await save_blocklist(services.store, BLOCKED_TERM)
    script_happy_path(
        ai,
        extracted=[
            extraction(
                ["Each agent identity is created from a blueprint that holds shared credentials."]
            ),
            extraction(
                ["Most surveyed teams run their agents under one shared service principal."],
                published="2019-05",
            ),
        ],
        verify=verify_reply(
            (
                "Each agent identity is created from a blueprint that holds shared credentials.",
                ["c1"],
                "verified",
            ),
            ("Only the search said this, so it cannot be verified yet.", ["c2"], "verified"),
            ("The survey is older than the recency window allows.", ["c3"], "verified"),
            ("A claim with only unknown sources is dropped.", ["c99"], "verified"),
            ("Sources disagree on the token lifetime.", ["c1", "c3"], "conflict"),
            stats=[
                StatOut(
                    value="1,000",
                    label="Agent identities per blueprint",
                    citation_ids=[ALLOW_CITE, OPEN_CITE],
                    key=True,
                ),
                StatOut(
                    value="62%",
                    label="Teams sharing one principal",
                    citation_ids=["c3", "c4"],
                    key=True,
                ),
                StatOut(
                    value="x" * 60,
                    label="Too long to be a value",
                    citation_ids=[ALLOW_CITE],
                    key=False,
                ),
                StatOut(value="7", label="Unknown source", citation_ids=["src-nowhere"], key=False),
            ],
        ),
    )
    run = (await new_run(services)).doc

    job = await run_research(services, run)

    assert job.status == "succeeded", job.error
    brief = (await read_doc(services.store, Brief, Brief.id_for(run.id))).doc
    assert [(claim.text, claim.status) for claim in brief.claims] == [
        (
            "Each agent identity is created from a blueprint that holds shared credentials.",
            "verified",
        ),
        ("Only the search said this, so it cannot be verified yet.", "unverified"),
        ("The survey is older than the recency window allows.", "unverified"),
        ("Sources disagree on the token lifetime.", "conflict"),
    ]
    assert brief.claims[3].citation_id == ALLOW_CITE  # the page-read, allowlisted source leads
    assert [(stat.value, stat.key, stat.citation_ids) for stat in brief.stats] == [
        ("1,000", True, [ALLOW_CITE, OPEN_CITE]),  # two independent domains
        ("62%", False, [OPEN_CITE]),  # one source only: not a key statistic
    ]
    open_citation = brief.citation(OPEN_CITE)
    assert open_citation is not None
    assert open_citation.date == "2019-05"


async def test_golden_brief() -> None:
    ai = ScriptedModelRunner()
    services = research_services(fetcher=page_fetcher(), ai=ai)
    await save_blocklist(services.store, BLOCKED_TERM)
    script_happy_path(
        ai,
        brief=brief_reply(
            ("Entra keeps agent identities apart from app registrations.", [ALLOW_CITE]),
            ("Most teams still share one service principal across agents.", ["cl-2"]),
        ),
    )
    run = (await new_run(services, run_id=RUN_ID)).doc
    job = await run_research(services, run)
    assert job.status == "succeeded", job.error

    stored = await read_doc(services.store, Brief, Brief.id_for(RUN_ID))
    actual = stored.doc.to_document()
    path = GOLDENS / "brief.json"
    if os.environ.get("BW_UPDATE_GOLDENS") == "1":  # pragma: no cover - deliberate updates only
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert actual == json.loads(path.read_text(encoding="utf-8"))
    assert ai.pending(Task.SYNTHESIS) == 0
