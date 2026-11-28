"""Invariants: the planner meets other wave B packages only through documents, job types and
ports; job documents carry references, never content; every route is owner-only and every
write passes CSRF; the registered types are exactly the planner's."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

import blogwriter.planner
from blogwriter.ai.types import Task
from blogwriter.domain import jobtypes
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner
from blogwriter.jobs import JobRegistry
from blogwriter.planner import register_jobs
from blogwriter.planner.proposal import PlanOut, PostChoice
from blogwriter.planner.testing import idea, pov, run_job, seed_policy, story

PACKAGE = Path(blogwriter.planner.__file__).parent
FORBIDDEN = (
    "blogwriter.pipeline",
    "blogwriter.linkedin.posts",
    "blogwriter.linkedin.transport",
    "blogwriter.engagement",
    "blogwriter.analytics",
    "blogwriter.publish",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_no_other_wave_b_package_or_the_publisher_is_imported() -> None:
    modules = sorted(PACKAGE.glob("*.py"))
    assert len(modules) > 10
    for path in modules:
        for name in imported_modules(path):
            assert not name.startswith(FORBIDDEN), f"{path.name} imports {name}"


def test_the_planner_registers_exactly_its_job_types() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    planner_types = {name for name in jobtypes.ALL_JOB_TYPES if name.startswith("planner.")}
    assert set(registry.names()) == planner_types
    assert len(planner_types) == 7


async def test_job_documents_carry_references_not_content(services: FakeServices) -> None:
    await seed_policy(services)
    secret_story = await story(
        services, "Cut-over night with the fourteen stragglers", ["identity"]
    )
    take = await pov(services, "Sponsors beat policies for agent inventories.", "governance")
    news = await idea(services, "Vendor patches a session-fixation flaw", time_sensitive=True)
    runner = services.ai
    assert isinstance(runner, ScriptedModelRunner)
    runner.add(
        Task.PLAN,
        PlanOut(
            posts=[
                PostChoice(
                    lane="native",
                    pillar="identity",
                    format="text",
                    title="Fourteen stragglers",
                    story_id=secret_story.id,
                ),
                PostChoice(
                    lane="native",
                    pillar="governance",
                    format="text",
                    title="Sponsors first",
                    pov_id=take.id,
                ),
                PostChoice(
                    lane="quick_take",
                    pillar="identity",
                    format="text",
                    title="Patch window",
                    idea_id=news.id,
                ),
            ],
            rationale="Identity and governance have material.",
        ),
    )
    job = await run_job(services, "planner.plan-week", {"weekStart": "2026-11-30"}, job_id="plan-x")
    assert job.status == "succeeded"
    body = json.dumps(job.to_body())
    for text in (
        secret_story.title,
        take.statement,
        news.title,
        "Fourteen stragglers",
        "Sponsors first",
        "Identity and governance have material.",
    ):
        assert text not in body
    assert secret_story.id in body  # references are fine


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/today"),
        ("GET", "/api/plan/weeks"),
        ("GET", "/api/plan/weeks/2026-11-30"),
        ("GET", "/api/plan/scorecard"),
        ("GET", "/api/reminders"),
        ("GET", "/api/articles/art-0000000000000000000000000000/veto"),
        ("POST", "/api/plan/weeks/2026-11-30/generate"),
        ("POST", "/api/plan/weeks/2026-11-30/approve"),
        ("PUT", "/api/plan/weeks/2026-11-30"),
        ("PUT", "/api/plan/weeks/2026-11-30/mode"),
        ("POST", "/api/posts/post-0000000000000000000000000000/reslot"),
        ("POST", "/api/reminders/rem-0000000000000000000000000000/dismiss"),
        ("POST", "/api/articles/art-0000000000000000000000000000/veto"),
    ],
)
async def test_every_route_is_owner_only_and_writes_need_csrf(
    client: httpx.AsyncClient, read_headers: dict[str, str], method: str, path: str
) -> None:
    anonymous = await client.request(method, path)
    assert anonymous.status_code == 401
    if method != "GET":
        no_csrf = await client.request(method, path, headers=read_headers, json={})
        assert no_csrf.status_code == 403
        assert no_csrf.json()["code"] == "csrf_failed"
