"""Gate 1: the rubric thresholds and the CRITIQUE call."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from blogwriter.ai.schema import compile_schema
from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.articles import Article, GateName
from blogwriter.domain.settings import Blocklist, EmployerPolicy, FormatPacks, PillarSettings
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    ScriptedModelRunner,
    ScriptedReply,
    sample_document,
)
from blogwriter.domain.voice import StyleGuide
from blogwriter.gates import (
    RUBRIC_DIMENSION_MIN,
    RUBRIC_DIMENSIONS,
    RUBRIC_OVERALL_MIN,
    RUBRIC_SPECIFICITY_MIN,
)
from blogwriter.gates.factcheck import ClaimMap, SourceCheck
from blogwriter.gates.results import GateDraft, finalize
from blogwriter.gates.rubric import RubricNote, RubricScores, rubric_draft, rubric_findings
from blogwriter.gates.testing import SAMPLE_BODY, SAMPLE_META, passing_scores, saved_blocklist
from blogwriter.jobs.testing import FakeClock

POLICY = EmployerPolicy.default(SAMPLE_NOW).model_copy(update={"ai_use_confirmed": True})
ARTICLE = sample_document(Article, meta=SAMPLE_META, markdown=SAMPLE_BODY)


def test_the_thresholds_are_the_contract() -> None:
    assert RUBRIC_DIMENSIONS == (
        "clarity",
        "structure",
        "accuracy",
        "depth",
        "first_hand_specificity",
        "voice",
        "originality",
        "actionability",
    )
    assert (RUBRIC_OVERALL_MIN, RUBRIC_DIMENSION_MIN, RUBRIC_SPECIFICITY_MIN) == (8, 6, 7)


def test_golden_thresholds() -> None:
    assert rubric_findings(passing_scores()) == []
    edge = passing_scores(overall=8, clarity=6, first_hand_specificity=7)
    assert rubric_findings(edge) == []
    cases = {
        "rubric-overall": passing_scores(overall=7),
        "rubric-dimension": passing_scores(voice=5),
        "rubric-specificity": passing_scores(first_hand_specificity=6),
    }
    for code, scores in cases.items():
        findings = rubric_findings(scores)
        assert [(f.code, f.severity, f.overridable) for f in findings] == [(code, "block", True)]
    voice = rubric_findings(passing_scores(voice=5))[0]
    assert voice.ref == "voice"
    assert voice.message.startswith("Voice 5/10 (needs 6).")


def test_the_critic_note_explains_a_low_dimension() -> None:
    scores = passing_scores(depth=4).model_copy(
        update={
            "notes": [
                RubricNote(dimension="depth", note="The SCIM section never shows the mapping.")
            ]
        }
    )
    (finding,) = rubric_findings(scores)
    assert finding.message.endswith("The SCIM section never shows the mapping.")


async def draft_with(
    runner: ScriptedModelRunner | None,
    *,
    policy: EmployerPolicy = POLICY,
    guide: StyleGuide | None = None,
    blocklist: Blocklist | None = None,
) -> GateDraft:
    return await rubric_draft(
        ARTICLE,
        runner=runner,
        policy=policy,
        blocklist=blocklist if blocklist is not None else saved_blocklist(),
        pillars=PillarSettings.default(SAMPLE_NOW),
        packs=FormatPacks.default(SAMPLE_NOW),
        guide=guide,
        words=140,
        origin="owner",
        job_id="job-1",
        now=FakeClock().now(),
    )


async def test_a_passing_critique_records_scores_and_what_served_it() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.CRITIQUE, passing_scores())
    guide = sample_document(StyleGuide, body="# Voice\n\nShort, specific, first-hand.")
    draft = await draft_with(runner, guide=guide)
    result = finalize(draft, ran_at=SAMPLE_NOW)
    assert result.gate is GateName.RUBRIC
    assert result.passed
    assert result.score == 9.0
    assert result.dimensions["first_hand_specificity"] == 9.0
    (record,) = result.served_by
    assert (record.step, record.prompt_version, record.task) == (
        "gates.rubric",
        "rubric.v1",
        "critique",
    )
    (call,) = runner.calls_for(Task.CRITIQUE)
    assert call.request.sensitivity is Sensitivity.ANONYMIZED
    assert call.request.tools == ()
    user = call.request.messages[0].text
    assert "<article>" in user
    assert "<style_guide>" in user
    assert "Format: Deep explainer, 1800-3000 words." in user
    assert "rubric judge" in call.request.system


async def test_a_fallback_or_a_refusal_in_the_chain_cannot_pass_on_its_own() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.CRITIQUE, ScriptedReply(value=passing_scores(), fallback_reason="refusal"))
    result = finalize(await draft_with(runner), ran_at=SAMPLE_NOW)
    assert not result.passed
    assert [(f.code, f.overridable) for f in result.findings] == [("rubric-review-only", True)]
    assert result.overridable


async def test_a_refused_critique_is_an_error_and_no_model_is_skipped() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.CRITIQUE, ScriptedReply(refused=True))
    refused = finalize(await draft_with(runner), ran_at=SAMPLE_NOW)
    assert refused.verdict.value == "error"
    assert "rubric-refused" in [f.code for f in refused.findings]
    skipped = finalize(await draft_with(None), ran_at=SAMPLE_NOW)
    assert skipped.verdict.value == "skipped"
    assert [f.code for f in skipped.findings] == ["ai-unavailable"]


async def test_no_ai_use_confirmation_means_no_critique() -> None:
    runner = ScriptedModelRunner()
    draft = await draft_with(runner, policy=EmployerPolicy.default(SAMPLE_NOW))
    assert draft.unavailable == ["ai_use_not_confirmed"]
    assert runner.calls == []


async def test_a_failing_model_call_is_an_error_never_a_pass() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.CRITIQUE, ScriptedReply(error=RuntimeError("provider down")))
    result = finalize(await draft_with(runner), ran_at=SAMPLE_NOW)
    assert result.verdict.value == "error"
    assert not result.passed


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("model", [RubricScores, ClaimMap, SourceCheck])
def test_output_schemas_compile_for_both_providers(
    provider: Literal["openai", "anthropic"], model: type[BaseModel]
) -> None:
    compiled = compile_schema(provider, model)
    assert compiled.model is model
    assert "additionalProperties" in compiled.schema_text


def test_a_model_reply_round_trips_through_the_compiled_schema() -> None:
    scores = passing_scores()
    compiled = compile_schema("openai", RubricScores)
    assert compiled.validate_json(scores.model_dump_json()) == scores


async def test_a_blocklisted_style_guide_never_reaches_the_critic() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.CRITIQUE, passing_scores())
    guide = sample_document(StyleGuide, body="# Voice\n\nLike my Contoso rollout notes.")
    draft = await draft_with(runner, guide=guide)
    assert runner.calls_for(Task.CRITIQUE) == []
    result = finalize(draft, ran_at=SAMPLE_NOW)
    assert not result.passed
    assert draft.unavailable == ["material_blocklisted"]


async def test_an_unsaved_blocklist_means_no_critique() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.CRITIQUE, passing_scores())
    draft = await draft_with(runner, blocklist=Blocklist.default(SAMPLE_NOW))
    assert runner.calls_for(Task.CRITIQUE) == []
    assert draft.unavailable == ["material_blocklisted"]
