"""The weekly plan end to end: the Sunday ``planner.plan-week`` schedule item (the real
scheduler) proposes next week (PLAN), the owner approves it, the flagship article is created
and its pipeline delayed to Monday's research start, the native posts are drafted from the
story and POV bank, and Monday's planned start runs research and asks the interview."""

from __future__ import annotations

from datetime import timedelta

from pydantic import JsonValue

from blogwriter.ai.types import Task
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.jobtypes import PIPELINE_ARTICLE, POSTS_GENERATE
from blogwriter.domain.posts import Post
from blogwriter.domain.states import ArticleStatus, PostStatus
from blogwriter.linkedin.posts.testing import claims_reply
from blogwriter.planner.proposal import FlagshipChoice, PlanOut, PostChoice

from .conftest import (
    KIT_BODY,
    T0,
    dig,
    draft_out,
    prepare_owner,
    script_flagship,
    seed_schedules,
    start_system,
)

WEEK = "2026-12-07"
PLAN_WEEK = "sched-planner-plan-week"
PLAN_READY = T0 + timedelta(days=6, hours=8)
"""Sunday 17:00 in New York: the plan job runs an hour before the owner's planning time."""
RESEARCH_START = T0 + timedelta(days=7, hours=-1)
"""Monday 08:00 in New York: the default research start."""


async def test_an_approved_week_plan_starts_the_flagship_and_drafts_natives() -> None:
    async with start_system() as system:
        owner = await prepare_owner(system)  # a story and a POV, both on the agents pillar
        new_idea: JsonValue = {"title": "Agent identities in Entra", "pillar": "agents"}
        idea = await system.ok("POST", "/api/ideas", new_idea, status=201)
        idea_id = str(dig(idea, "item", "id"))
        await system.ok(
            "POST", f"/api/ideas/{idea_id}/transition", {"to": "accepted"}, etag=str(idea["etag"])
        )
        assert await seed_schedules(system, PLAN_WEEK) == [PLAN_WEEK]
        system.model.add(
            Task.PLAN,
            PlanOut(
                flagship=FlagshipChoice(
                    title="Scope agent tokens per tool call",
                    pillar="agents",
                    format="deep-explainer",
                    idea_id=idea_id,
                ),
                posts=[
                    PostChoice(
                        lane="native",
                        pillar="agents",
                        format="text",
                        title="One borrowed token",
                        story_id=owner.story_id,
                    )
                ],
                rationale="One flagship from the inbox, one native post from the bank.",
            ),
        )

        # Sunday: the schedule proposes the week; nothing is created until the owner approves.
        await system.run_until(PLAN_READY + timedelta(minutes=5))
        plan, etag = await system.item(f"/api/plan/weeks/{WEEK}")
        assert plan["status"] == "proposed"
        assert dig(plan, "flagship", "ideaId") == idea_id
        slots = plan["slots"]
        assert isinstance(slots, list)
        assert len(slots) == 3
        # The bank has material on the flagship's pillar only: every native stays where it can
        # be drafted (a warning), each with a story or POV the first two times.
        assert [dig(slot, "pillar") for slot in slots] == ["agents"] * 3
        assert dig(slots[0], "storyIds") == [owner.story_id]
        assert dig(slots[1], "povIds") == [owner.pov_id]
        assert system.pushes() == ["Your plan for next week is ready"]
        today = dig(await system.ok("GET", "/api/today"), "items")
        assert isinstance(today, list)
        assert "approve_plan" in [dig(item, "kind") for item in today]
        assert await system.store.find("articles") == []
        assert await system.store.find("posts") == []

        system.model.add(Task.DRAFT, *(draft_out(body=KIT_BODY, fmt="text") for _ in range(3)))
        system.model.add(Task.FACT_CHECK, *(claims_reply() for _ in range(3)))
        approved = await system.ok("POST", f"/api/plan/weeks/{WEEK}/approve", etag=etag)
        article_id = approved["articleId"]
        assert isinstance(article_id, str)
        job_ids = approved["jobIds"]
        assert isinstance(job_ids, list)
        await system.idle()
        jobs = [await system.job(str(job_id)) for job_id in job_ids]
        assert [job.type for job in jobs] == [PIPELINE_ARTICLE, *([POSTS_GENERATE] * 3)]
        assert jobs[0].status == "queued"
        assert jobs[0].run_after == to_iso(RESEARCH_START)
        for job in jobs[1:]:
            assert job.status == "succeeded", job.error
            post_id = dig(job.input, "postId")
            assert isinstance(post_id, str)
            post = (await read_doc(system.store, Post, post_id)).doc
            assert post.status is PostStatus.DRAFT
            assert post.lane == "native"
            assert post.week_plan_id == f"plan-{WEEK}"
            assert post.story_ids or post.pov_ids
            assert post.lint is not None
            assert post.lint.passed
        flagship = (await read_doc(system.store, Article, article_id)).doc
        assert flagship.status is ArticleStatus.PLANNED
        assert flagship.idea_id == idea_id
        assert flagship.pipeline_job_id == jobs[0].id

        # Monday's research start: the planned pipeline runs (as the owner's) and asks.
        script_flagship(system, owner)
        await system.run_until(RESEARCH_START)
        waiting = await system.wait_job(jobs[0].id, {"waiting_input"}, key="interview")
        assert waiting.input is not None
        assert dig(waiting.input, "origin") == "owner"  # plan approval is an owner action
        started = (await read_doc(system.store, Article, article_id)).doc
        assert started.status is ArticleStatus.INTERVIEWING
        assert started.research_run_id is not None
        assert system.pushes()[-1] == "2 quick questions"
