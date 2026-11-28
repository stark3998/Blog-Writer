"""run_gates end to end, the blocklist stop, readiness rules, capping and save_report."""

from __future__ import annotations

from decimal import Decimal

import pytest

from blogwriter.ai.ledger import BrakeEngaged, BrakeStatus
from blogwriter.ai.types import Task, TextPart
from blogwriter.domain.articles import (
    GATE_ORDER,
    Article,
    GateName,
    GateOverride,
    GateReport,
    GateResult,
    GateVerdict,
)
from blogwriter.domain.common import Finding
from blogwriter.domain.docstore import create_doc, read_doc, save_singleton, upsert_doc
from blogwriter.domain.jobflow import JobDeferred
from blogwriter.domain.settings import VoicePreferences
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeClassifier,
    FakeSlopChecker,
    make_services,
    sample_document,
)
from blogwriter.domain.voice import StyleGuide
from blogwriter.gates import run_gates, run_gates_detailed, save_report
from blogwriter.gates.run import gates_view
from blogwriter.gates.testing import BLOCKED_TERM, gate_world, passing_scores, script_passing_ai


def verdicts(report: GateReport) -> dict[str, str]:
    return {result.gate.value: result.verdict.value for result in report.results}


async def test_a_clean_article_passes_all_seven_gates() -> None:
    w = await gate_world()
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    outcome = await run_gates_detailed(w.article, services=w.services, origin="owner", job_id="j1")
    report = outcome.report
    assert verdicts(report) == {gate.value: "pass" for gate in GATE_ORDER}
    assert report.auto_publish_ready
    assert report.manual_publish_ready
    assert report.blocking() == []
    assert report.content_sha256 == w.article.compute_sha256()
    assert report.version == w.article.version
    assert report.job_id == "j1"
    assert outcome.embedding is not None


async def test_a_blocklist_hit_makes_no_model_call_at_all() -> None:
    w = await gate_world()
    body = w.article.markdown.replace("Last March", f"Last March, for {BLOCKED_TERM},")
    article = w.article.model_copy(update={"markdown": body})
    report = await run_gates(article, services=w.services, origin="owner")
    services = w.services
    assert services.ai is not None
    assert services.ai.calls == []
    assert services.classifier is not None
    assert services.classifier.calls == []
    assert services.embedder is not None
    assert services.embedder.calls == []
    assert services.fetcher is not None
    assert services.fetcher.fetched == []
    assert services.egress.requested == []
    assert verdicts(report) == {
        "rubric": "skipped",
        "fact_check": "skipped",
        "confidentiality": "fail",
        "originality": "skipped",
        "lint": "skipped",
        "owner_signal": "pass",
        "outbound": "pass",
    }
    gate3 = report.result(GateName.CONFIDENTIALITY)
    assert gate3 is not None
    assert [(f.code, f.ref, f.overridable) for f in gate3.findings] == [
        ("blocklisted-term", "client-1", False)
    ]
    assert BLOCKED_TERM not in report.model_dump_json()
    assert not report.manual_publish_ready


async def test_an_unconfigured_blocklist_stops_the_model_work_too() -> None:
    w = await gate_world(blocklist=False)
    report = await run_gates(w.article, services=w.services, origin="owner")
    assert w.services.ai is not None
    assert w.services.ai.calls == []
    gate3 = report.result(GateName.CONFIDENTIALITY)
    assert gate3 is not None
    assert gate3.verdict is GateVerdict.FAIL
    assert [f.code for f in gate3.findings] == ["blocklist-not-configured"]
    skipped = report.result(GateName.RUBRIC)
    assert skipped is not None
    assert [f.code for f in skipped.findings] == ["blocklist-hit"]


async def test_the_eval_judge_runs_the_rubric_only() -> None:
    w = await gate_world()
    assert w.services.ai is not None
    w.services.ai.add(Task.CRITIQUE, passing_scores())
    report = await run_gates(
        w.article, services=w.services, origin="owner", gates={GateName.RUBRIC}
    )
    assert [result.gate for result in report.results] == [GateName.RUBRIC]
    assert report.results[0].passed
    assert not report.complete
    assert not report.auto_publish_ready
    assert [call.task for call in w.services.ai.calls] == [Task.CRITIQUE]
    assert w.services.classifier is not None
    assert w.services.classifier.calls == []


async def test_the_judge_never_reads_a_blocklisted_article() -> None:
    w = await gate_world()
    article = w.article.model_copy(update={"markdown": f"{BLOCKED_TERM} {w.article.markdown}"})
    report = await run_gates(article, services=w.services, origin="owner", gates={GateName.RUBRIC})
    assert verdicts(report) == {"rubric": "skipped"}
    assert w.services.ai is not None
    assert w.services.ai.calls == []


async def test_no_embedder_fails_originality_and_only_an_override_lets_it_through() -> None:
    w = await gate_world(services=make_services(embedder=None))
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    report = await run_gates(w.article, services=w.services, origin="owner")
    originality = report.result(GateName.ORIGINALITY)
    assert originality is not None
    assert originality.verdict is GateVerdict.FAIL
    assert [f.code for f in originality.findings] == ["novelty-unavailable"]
    assert not report.auto_publish_ready
    assert not report.manual_publish_ready
    assert report.blocking() == [GateName.ORIGINALITY]
    overridden = _override(report, GateName.ORIGINALITY)
    assert overridden.manual_publish_ready
    assert not overridden.auto_publish_ready  # overrides never count for auto-publish


def _override(report: GateReport, gate: GateName) -> GateReport:
    results = [
        result.model_copy(
            update={"override": GateOverride(reason="Checked by hand.", at=SAMPLE_NOW)}
        )
        if result.gate is gate
        else result
        for result in report.results
    ]
    return report.model_copy(update={"results": results})


def _result(gate: GateName, verdict: GateVerdict, *findings: Finding) -> GateResult:
    return GateResult(gate=gate, verdict=verdict, findings=list(findings), ran_at=SAMPLE_NOW)


def _report(*results: GateResult) -> GateReport:
    by_gate = {result.gate: result for result in results}
    full = [by_gate.get(gate, _result(gate, GateVerdict.PASS)) for gate in GATE_ORDER]
    return GateReport(content_sha256="a" * 64, version=1, ran_at=SAMPLE_NOW, results=full)


def test_readiness_auto_needs_seven_passes_manual_accepts_allowed_overrides_only() -> None:
    soft = Finding(code="slop-over-threshold", severity="block", message="x", overridable=True)
    hard = Finding(code="verbatim-budget", severity="block", message="x", overridable=False)
    warned = _report(_result(GateName.ORIGINALITY, GateVerdict.WARN))
    assert warned.auto_publish_ready
    failing_soft = _report(_result(GateName.ORIGINALITY, GateVerdict.FAIL, soft))
    assert not failing_soft.auto_publish_ready
    assert not failing_soft.manual_publish_ready
    assert _override(failing_soft, GateName.ORIGINALITY).manual_publish_ready
    assert not _override(failing_soft, GateName.ORIGINALITY).auto_publish_ready
    failing_hard = _report(_result(GateName.ORIGINALITY, GateVerdict.FAIL, soft, hard))
    assert not _override(failing_hard, GateName.ORIGINALITY).manual_publish_ready
    skipped_hard = _report(_result(GateName.LINT, GateVerdict.SKIPPED))
    assert not _override(skipped_hard, GateName.LINT).manual_publish_ready
    skipped_soft = _report(_result(GateName.RUBRIC, GateVerdict.SKIPPED))
    assert _override(skipped_soft, GateName.RUBRIC).manual_publish_ready
    incomplete = GateReport(
        content_sha256="a" * 64,
        version=1,
        ran_at=SAMPLE_NOW,
        results=[_result(GateName.RUBRIC, GateVerdict.PASS)],
    )
    assert not incomplete.auto_publish_ready
    assert not incomplete.manual_publish_ready


async def test_300_slop_findings_store_50_and_keep_every_hard_blocker() -> None:
    hard = [
        Finding(code="isnt-x-its-y", severity="block", message="hard", start=n, end=n + 1)
        for n in range(2)
    ]
    soft = [
        Finding(
            code="rule-of-three", severity="warn", message="x", start=n, end=n + 1, overridable=True
        )
        for n in range(297)
    ]
    w = await gate_world(services=make_services(slop=FakeSlopChecker([*soft, *hard])))
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    report = await run_gates(w.article, services=w.services, origin="owner")
    originality = report.result(GateName.ORIGINALITY)
    assert originality is not None
    assert len(originality.findings) == 50
    assert originality.truncated == 250  # 299 slop findings plus slop-over-threshold
    kept_hard = [f for f in originality.findings if f.severity == "block" and not f.overridable]
    assert len(kept_hard) == 2
    assert "slop-over-threshold" in [f.code for f in originality.findings]
    assert not originality.overridable  # a hard blocker survived the cut


async def test_missing_services_skip_their_gates_and_never_pass() -> None:
    w = await gate_world(services=make_services(ai=None, fetcher=None, classifier=None))
    report = await run_gates(w.article, services=w.services, origin="owner")
    assert verdicts(report)["rubric"] == "skipped"
    assert verdicts(report)["fact_check"] == "skipped"
    assert verdicts(report)["confidentiality"] == "skipped"
    assert not report.auto_publish_ready
    assert report.blocking() == [GateName.RUBRIC, GateName.FACT_CHECK, GateName.CONFIDENTIALITY]
    overridden = _override(_override(report, GateName.RUBRIC), GateName.FACT_CHECK)
    overridden = _override(overridden, GateName.CONFIDENTIALITY)
    assert overridden.blocking() == [GateName.CONFIDENTIALITY]  # a hard gate must run again


async def test_an_unexpected_gate_error_is_an_error_verdict() -> None:
    w = await gate_world(services=make_services(classifier=FakeClassifier(error=RuntimeError())))
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    report = await run_gates(w.article, services=w.services, origin="owner")
    assert verdicts(report)["confidentiality"] == "error"
    assert verdicts(report)["rubric"] == "pass"


async def test_the_brake_defers_background_work() -> None:
    w = await gate_world()
    status = BrakeStatus(w.services.clock.now().date(), Decimal(30), Decimal(5), Decimal(15))
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    w.services.classifier = FakeClassifier(error=BrakeEngaged(status))
    with pytest.raises(JobDeferred) as deferred:
        await run_gates(w.article, services=w.services, origin="background")
    assert deferred.value.reason == "brake_over_budget"


async def test_save_report_writes_only_gate_fields_on_unchanged_content() -> None:
    w = await gate_world()
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    outcome = await run_gates_detailed(w.article, services=w.services, origin="owner")
    before = await read_doc(w.services.store, Article, w.article.id)
    stored = await save_report(
        w.services.store,
        w.article.id,
        outcome.report,
        now=w.services.clock.now(),
        embedding=outcome.embedding,
    )
    assert stored is not None
    doc = stored.doc
    assert doc.gates == outcome.report
    assert doc.embedding == outcome.embedding
    assert doc.embedding_sha256 is not None
    assert (doc.version, doc.meta, doc.markdown) == (
        before.doc.version,
        before.doc.meta,
        before.doc.markdown,
    )
    assert doc.updated_by == "system"
    view = gates_view(doc)
    assert (view.stale, view.auto_publish_ready, view.blocking) == (False, True, [])


async def test_a_stale_report_is_discarded() -> None:
    w = await gate_world()
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    report = await run_gates(w.article, services=w.services, origin="owner")
    current = await read_doc(w.services.store, Article, w.article.id)
    edited = current.doc.model_copy(
        update={"markdown": current.doc.markdown + "\nOne more line.\n", "version": 4}
    )
    edited = edited.model_copy(update={"content_sha256": edited.compute_sha256()})
    await w.services.store.replace("articles", edited.to_document(), if_match=current.etag)
    assert (
        await save_report(w.services.store, w.article.id, report, now=w.services.clock.now())
        is None
    )
    after = await read_doc(w.services.store, Article, w.article.id)
    assert after.doc.gates is None
    view = gates_view(after.doc.model_copy(update={"gates": report}))
    assert view.stale
    assert not view.manual_publish_ready
    assert view.blocking == list(GATE_ORDER)


async def test_blocklisted_owner_material_never_reaches_a_model_and_never_passes() -> None:
    """The story behind a clean article names a blocklisted client (saved before the term
    joined the list): FACT_CHECK and CRITIQUE get nothing with it, and the report is not a
    pass."""
    w = await gate_world()
    services = w.services
    assert services.ai is not None
    script_passing_ai(services.ai, story_id=w.story.id)
    story = w.story.model_copy(update={"title": f"Rebuilt the IAM stack at {BLOCKED_TERM}"})
    await upsert_doc(services.store, story)
    report = await run_gates(w.article, services=services, origin="owner")
    for call in services.ai.calls:
        sent = [
            part.text
            for message in call.request.messages
            for part in message.parts
            if isinstance(part, TextPart)
        ]
        assert all(BLOCKED_TERM not in text for text in sent)
    assert verdicts(report)["fact_check"] == "skipped"
    assert not report.auto_publish_ready
    assert not report.manual_publish_ready


async def test_a_blocklisted_style_guide_never_reaches_the_critic() -> None:
    w = await gate_world()
    services = w.services
    assert services.ai is not None
    script_passing_ai(services.ai, story_id=w.story.id)
    guide = sample_document(StyleGuide, body=f"# Voice\n\nThe way I wrote for {BLOCKED_TERM}.")
    await create_doc(services.store, guide)
    stamp = SAMPLE_NOW
    await save_singleton(
        services.store,
        VoicePreferences.default(stamp).model_copy(update={"active_guide_id": guide.id}),
        if_match=None,
    )
    report = await run_gates(w.article, services=services, origin="owner")
    assert services.ai.calls_for(Task.CRITIQUE) == []
    assert verdicts(report)["rubric"] == "skipped"
    assert not report.auto_publish_ready
