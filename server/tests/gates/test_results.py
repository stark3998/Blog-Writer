"""Gate drafts and verdicts: block > could not run > error > warn > pass; the 50-finding cap."""

from __future__ import annotations

import pytest

from blogwriter.domain.articles import GateName, GateVerdict
from blogwriter.domain.common import Finding
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.gates.results import GateDraft, finalize


def finding(severity: str, *, overridable: bool = True, start: int = 0) -> Finding:
    return Finding.model_validate(
        {
            "code": "x-code",
            "severity": severity,
            "message": "m",
            "start": start,
            "end": start + 1,
            "overridable": overridable,
        }
    )


def verdict(draft: GateDraft) -> GateVerdict:
    return finalize(draft, ran_at=SAMPLE_NOW).verdict


def test_verdict_precedence() -> None:
    base = GateDraft.of(GateName.LINT)
    assert verdict(base) is GateVerdict.PASS
    assert verdict(base.with_findings([finding("info")])) is GateVerdict.PASS
    assert verdict(base.with_findings([finding("warn")])) is GateVerdict.WARN
    assert verdict(base.with_error("unexpected")) is GateVerdict.ERROR
    assert verdict(base.with_error("unexpected").with_unavailable("ai_unavailable")) is (
        GateVerdict.SKIPPED
    )
    blocked = base.with_unavailable("ai_unavailable").with_findings([finding("block")])
    assert verdict(blocked) is GateVerdict.FAIL


def test_reasons_become_info_findings() -> None:
    result = finalize(
        GateDraft.of(GateName.RUBRIC).with_unavailable("ai_use_not_confirmed"), ran_at=SAMPLE_NOW
    )
    assert [(f.code, f.severity) for f in result.findings] == [("ai-use-not-confirmed", "info")]
    errored = finalize(GateDraft.of(GateName.RUBRIC).with_error("unexpected"), ran_at=SAMPLE_NOW)
    assert [f.code for f in errored.findings] == ["gate-error"]


def test_merging_keeps_blockers_and_counts_what_was_cut() -> None:
    warns = GateDraft.of(GateName.ORIGINALITY, [finding("warn", start=n) for n in range(60)])
    assert (len(warns.findings), warns.truncated) == (50, 10)
    hard = GateDraft.of(GateName.ORIGINALITY, [finding("block", overridable=False)])
    merged = warns.merge(hard)
    assert (len(merged.findings), merged.truncated) == (50, 11)
    assert merged.findings[0].severity == "block"
    result = finalize(merged, ran_at=SAMPLE_NOW)
    assert (result.verdict, result.truncated, result.overridable) == (GateVerdict.FAIL, 11, False)


def test_merge_combines_markers_scores_and_served_by() -> None:
    first = GateDraft.of(GateName.ORIGINALITY).with_unavailable("blocklist_hit")
    second = GateDraft.of(GateName.ORIGINALITY).model_copy(update={"score": 0.42})
    merged = first.merge(second).merge(second)
    assert merged.unavailable == ["blocklist_hit"]
    assert merged.score == 0.42
    with pytest.raises(ValueError, match="different gates"):
        first.merge(GateDraft.of(GateName.LINT))
