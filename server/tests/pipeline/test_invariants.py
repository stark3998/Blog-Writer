"""Invariants of the pipeline package: job types and schedule, settings, the rubric thresholds,
the banned openers in every writing prompt, and what the package never imports."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from blogwriter.domain.jobtypes import (
    PIPELINE_ARTICLE,
    PIPELINE_INTERVIEW_EXPIRE,
    PIPELINE_REVISE,
    SCHEDULED_JOB_TYPES,
    generates_content,
)
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.gates import (
    RUBRIC_DIMENSION_MIN,
    RUBRIC_DIMENSIONS,
    RUBRIC_OVERALL_MIN,
    RUBRIC_SPECIFICITY_MIN,
)
from blogwriter.jobs import JobRegistry
from blogwriter.pipeline import (
    PipelineSettings,
    register_jobs,
    rubric_passes,
    run_number,
    schedule_entries,
)
from blogwriter.pipeline.models import DIMENSIONS
from blogwriter.pipeline.prompting import DRAFT, OUTLINE, REVISE, load_prompt
from blogwriter.pipeline.testing import critique

PACKAGE = Path(__file__).parents[2] / "src" / "blogwriter" / "pipeline"
BANNED_OPENERS = (
    "Unpopular opinion",
    "Here's the thing",
    "Let that sink in",
    "X is dead",
    "Most people get X wrong",
    "Nobody talks about",
)


def test_job_types_and_no_schedule() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    assert registry.names() == tuple(
        sorted((PIPELINE_ARTICLE, PIPELINE_INTERVIEW_EXPIRE, PIPELINE_REVISE))
    )
    article = registry.get(PIPELINE_ARTICLE)
    assert article is not None
    assert article.step_names == (
        "research",
        "interview_questions",
        "interview",
        "outline",
        "draft",
        "normalize",
        "critique_revise",
        "gates",
        "assets",
        "finalize",
    )
    revise = registry.get(PIPELINE_REVISE)
    assert revise is not None
    assert revise.step_names == ("revise", "normalize", "gates", "finalize")
    for name in registry.names():
        assert name not in SCHEDULED_JOB_TYPES
        assert generates_content(name)  # content work starts from owner actions only
    assert schedule_entries(CadenceSettings.default(SAMPLE_NOW)) == []


def test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert PipelineSettings().max_revise_loops == 2
    monkeypatch.setenv("BW_PIPELINE_MAX_REVISE_LOOPS", "1")
    assert PipelineSettings().max_revise_loops == 1
    monkeypatch.setenv("BW_PIPELINE_MAX_REVISE_LOOPS", "3")
    with pytest.raises(ValidationError):
        PipelineSettings()  # the plan caps it at two loops


def test_run_numbers_from_job_ids() -> None:
    article = "art-0123456789abcdef0123456789ab"
    assert run_number(f"pipeline-{article}-3", article) == 3
    assert run_number(f"pipeline-{article}-12-r2", article) == 12
    assert run_number("revise-xyz", article) is None
    assert run_number(f"pipeline-{article}-x", article) is None


def test_the_critique_uses_gate_ones_rubric() -> None:
    assert DIMENSIONS == RUBRIC_DIMENSIONS
    assert rubric_passes(critique(passing=True))
    assert not rubric_passes(critique(passing=False))
    edge = critique().model_copy(
        update={
            "overall": RUBRIC_OVERALL_MIN,
            "depth": RUBRIC_DIMENSION_MIN,
            "first_hand_specificity": RUBRIC_SPECIFICITY_MIN,
        }
    )
    assert rubric_passes(edge)
    assert not rubric_passes(edge.model_copy(update={"first_hand_specificity": 6}))
    assert not rubric_passes(edge.model_copy(update={"overall": 7}))


@pytest.mark.parametrize("name", [OUTLINE, DRAFT, REVISE])
def test_writing_prompts_ban_the_formula_openers(name: str) -> None:
    text = " ".join(load_prompt(name).text.split())
    for opener in BANNED_OPENERS:
        assert opener in text, opener


def test_draft_and_revise_prompts_keep_the_truth_rules() -> None:
    for name in (DRAFT, REVISE):
        text = " ".join(load_prompt(name).text.split())
        assert "Never invent experience" in text
        assert "200 characters" in text
        assert "[^src-id]" in text


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_the_package_never_reaches_publishers_or_providers() -> None:
    """No LinkedIn code, no AI provider SDK, no other wave-B package: models through the
    router only, other packages through documents, job types and their public APIs."""
    forbidden = (
        "blogwriter.linkedin",
        "blogwriter.ai.providers",
        "openai",
        "anthropic",
        "blogwriter.planner",
        "blogwriter.analytics",
        "blogwriter.engagement",
        "blogwriter.publish.jobs",
        "blogwriter.publish.routes",
    )
    for path in PACKAGE.glob("*.py"):
        for module in _imports(path):
            assert not module.startswith(forbidden), f"{path.name} imports {module}"
