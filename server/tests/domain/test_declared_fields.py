"""Fields the web reads, declared on the models instead of kept as extra members: a finding's
``field``, a check's ``truncated``, a post's read-only ``approvalCurrent`` and its plan slot, a
planned slot's references, an eval result's ``errorCode`` and an insight suggestion's ``value``.

Documents written before (with the camelCase extras) read into the declared fields, and a
computed view is never stored.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blogwriter.db import MemoryStore
from blogwriter.domain.analytics import EvalResult, InsightSuggestion
from blogwriter.domain.common import Finding
from blogwriter.domain.docstore import create_doc, read_doc, replace_doc
from blogwriter.domain.golive import (
    PlanSlotRefused,
    link_plan_slot,
    plan_slot_for,
    slot_post_gone,
)
from blogwriter.domain.planner import PlannedSlot, WeekPlan
from blogwriter.domain.posts import Approval, CheckState, Post, Slot
from blogwriter.domain.states import PlanStatus, PostStatus
from blogwriter.domain.testing import SAMPLE_NOW, sample_document

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SLOT = Slot.at("2026-12-01", "10:00", "America/New_York", "primary")


def test_a_finding_names_its_field_only_when_it_has_one() -> None:
    plain = Finding(code="ai-tell", severity="warn", message="An AI tell.", start=0, end=4)
    located = plain.model_copy(update={"field": "slides[2]"})
    assert "field" not in plain.model_dump(by_alias=True)
    assert plain.model_dump(mode="json")["code"] == "ai-tell"
    assert located.model_dump(by_alias=True)["field"] == "slides[2]"
    stored = Finding.model_validate({**plain.model_dump(by_alias=True), "field": "displayText"})
    assert stored.field == "displayText"
    assert stored.model_extra == {}
    schema = Finding.model_json_schema(mode="serialization")
    assert "field" in schema["properties"]
    assert "field" not in schema.get("required", [])
    with pytest.raises(ValueError, match="field"):
        Finding(code="x", severity="warn", message="Too long.", field="f" * 201)


def test_a_check_counts_what_it_left_out() -> None:
    state = CheckState(ran_at=SAMPLE_NOW, content_sha256="0" * 64, passed=True)
    assert state.truncated == 0
    old = CheckState.model_validate(
        {"ranAt": SAMPLE_NOW, "contentSha256": "0" * 64, "passed": False, "truncated": 7}
    )
    assert old.truncated == 7
    assert old.model_extra == {}


def test_approval_current_is_served_but_never_stored_or_taken_from_input() -> None:
    post = sample_document(Post, display_text="Hello", lane="native", pillar="agents")
    approved = post.model_copy(
        update={"approval": Approval(sha256=post.compute_approval_hash(), approved_at=SAMPLE_NOW)}
    )
    assert approved.approval_current
    assert approved.model_dump(by_alias=True)["approvalCurrent"] is True  # the API's view
    assert "approvalCurrent" not in approved.to_document()  # never stored
    forged = Post.from_document({**post.to_document(), "approvalCurrent": True})
    assert forged.approval_current is False
    assert "approvalCurrent" not in (forged.model_extra or {})
    edited = Post.model_validate(
        approved.model_copy(update={"display_text": "Changed"}).model_dump(by_alias=True)
    )
    assert edited.approval_current is False  # recomputed from the content
    schema = Post.model_json_schema(mode="serialization")
    assert schema["properties"]["approvalCurrent"]["readOnly"] is True
    assert "approvalCurrent" not in Post.model_json_schema(mode="validation")["properties"]


def test_planned_slots_declare_their_references() -> None:
    old = WeekPlan.from_document(
        {
            **sample_document(WeekPlan).to_document(),
            "slots": [
                {
                    "id": "s1",
                    "slot": SLOT.model_dump(by_alias=True),
                    "lane": "quick_take",
                    "storyIds": ["story-1"],
                    "povIds": ["pov-1"],
                    "ideaId": "idea-1",
                    "articleId": "art-1",
                }
            ],
        }
    )
    (slot,) = old.slots
    assert (slot.story_ids, slot.pov_ids, slot.idea_id, slot.article_id) == (
        ["story-1"],
        ["pov-1"],
        "idea-1",
        "art-1",
    )
    assert slot.model_extra == {}
    with pytest.raises(ValueError, match=r"story_ids|storyIds"):
        PlannedSlot(id="s2", slot=SLOT, story_ids=[f"story-{n}" for n in range(6)])


def test_eval_errors_and_suggestion_values_are_fields() -> None:
    result = EvalResult.from_document(
        {
            **sample_document(EvalResult).to_document(),
            "judgePassed": None,
            "errorCode": "not_drafted",
        }
    )
    assert result.error_code == "not_drafted"
    assert result.model_extra == {}
    suggestion = InsightSuggestion.from_document(
        {**sample_document(InsightSuggestion).to_document(), "value": "carousel"}
    )
    assert suggestion.value == "carousel"
    assert suggestion.model_extra == {}


# --------------------------------------------------------------------------------------------
# A post written for a planned slot
# --------------------------------------------------------------------------------------------


async def stored_plan(store: MemoryStore, *slots: PlannedSlot, status: PlanStatus) -> WeekPlan:
    plan = sample_document(WeekPlan, status=status, approved_at=SAMPLE_NOW, slots=list(slots))
    return (await create_doc(store, plan)).doc


def take(slot_id: str, **fields: object) -> PlannedSlot:
    return PlannedSlot.model_validate({"id": slot_id, "slot": SLOT, "lane": "quick_take", **fields})


async def test_linking_a_post_names_it_on_the_slot_and_changes_nothing_else() -> None:
    store = MemoryStore()
    plan = await stored_plan(store, take("s1"), take("s2"), status=PlanStatus.APPROVED)
    found, slot = await plan_slot_for(store, plan.week_start, "s1", lane="quick_take")
    assert (found.id, slot.id) == (plan.id, "s1")
    linked = await link_plan_slot(store, plan.id, "s1", "post-1", now=NOW)
    assert linked is not None
    assert [s.post_id for s in linked.doc.slots] == ["post-1", None]
    assert linked.doc.status is PlanStatus.APPROVED
    assert linked.doc.approved_at == plan.approved_at
    again = await link_plan_slot(store, plan.id, "s1", "post-1", now=NOW)  # idempotent
    assert again is not None
    assert again.etag == linked.etag
    with pytest.raises(PlanSlotRefused) as taken:
        await plan_slot_for(store, plan.week_start, "s1", lane="quick_take")
    assert (taken.value.code, taken.value.status) == ("plan_slot_taken", 409)


async def test_a_slot_filled_meanwhile_keeps_its_post() -> None:
    store = MemoryStore()
    plan = await stored_plan(store, take("s1"), status=PlanStatus.APPROVED)
    assert await link_plan_slot(store, plan.id, "s1", "post-1", now=NOW) is not None
    assert await link_plan_slot(store, plan.id, "s1", "post-2", now=NOW) is None
    assert await link_plan_slot(store, plan.id, "gone", "post-2", now=NOW) is None
    assert await link_plan_slot(store, "plan-2026-12-07", "s1", "post-2", now=NOW) is None
    current = (await read_doc(store, WeekPlan, plan.id)).doc
    assert [s.post_id for s in current.slots] == ["post-1"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(PlanStatus.CLOSED, "plan_closed"), (PlanStatus.GENERATING, "plan_generating")],
)
async def test_a_closed_or_generating_plan_takes_no_post(status: PlanStatus, code: str) -> None:
    store = MemoryStore()
    plan = await stored_plan(store, take("s1"), status=status)
    with pytest.raises(PlanSlotRefused) as refused:
        await plan_slot_for(store, plan.week_start, "s1", lane="quick_take")
    assert refused.value.code == code
    assert await link_plan_slot(store, plan.id, "s1", "post-1", now=NOW) is None


async def test_a_slot_of_another_lane_is_refused() -> None:
    store = MemoryStore()
    plan = await stored_plan(store, take("s1", lane="native"), status=PlanStatus.PROPOSED)
    with pytest.raises(PlanSlotRefused) as refused:
        await plan_slot_for(store, plan.week_start, "s1", lane="quick_take")
    assert (refused.value.code, refused.value.status) == ("plan_slot_lane", 409)
    current = await read_doc(store, WeekPlan, plan.id)
    open_lane = current.doc.model_copy(update={"slots": [take("s1", lane=None)]})
    stored = await replace_doc(store, open_lane, if_match=current.etag)
    found, _ = await plan_slot_for(store, stored.doc.week_start, "s1", lane="quick_take")
    assert found.status is PlanStatus.PROPOSED


@pytest.mark.parametrize("gone", [PostStatus.ARCHIVED, PostStatus.EXPIRED, PostStatus.DELETED])
async def test_a_slot_whose_post_is_gone_takes_a_new_one(gone: PostStatus) -> None:
    """Nothing clears ``postId`` when a post is archived or expires (quick takes expire by
    design): the slot is open again, and the new post replaces exactly the gone one."""
    store = MemoryStore()
    plan = await stored_plan(store, take("s1"), status=PlanStatus.APPROVED)
    old = sample_document(Post, status=gone, lane="quick_take", pillar="agents")
    await create_doc(store, old)
    assert await link_plan_slot(store, plan.id, "s1", old.id, now=NOW) is not None
    assert await slot_post_gone(store, old.id)

    _found, slot = await plan_slot_for(store, plan.week_start, "s1", lane="quick_take")
    assert slot.post_id == old.id  # still named: the new post takes over this id
    # Without ``replaces`` a link never overwrites a slot's post.
    assert await link_plan_slot(store, plan.id, "s1", "post-2", now=NOW) is None
    linked = await link_plan_slot(store, plan.id, "s1", "post-2", now=NOW, replaces=old.id)
    assert linked is not None
    assert [s.post_id for s in linked.doc.slots] == ["post-2"]
    assert (linked.doc.status, linked.doc.approved_at) == (PlanStatus.APPROVED, plan.approved_at)
    # A third post naming the old id cannot push the new one out.
    assert await link_plan_slot(store, plan.id, "s1", "post-3", now=NOW, replaces=old.id) is None
    current = (await read_doc(store, WeekPlan, plan.id)).doc
    assert [s.post_id for s in current.slots] == ["post-2"]


@pytest.mark.parametrize(
    "status", [PostStatus.GENERATING, PostStatus.DRAFT, PostStatus.APPROVED, PostStatus.POSTED]
)
async def test_a_live_or_unknown_post_keeps_its_slot(status: PostStatus) -> None:
    store = MemoryStore()
    live = sample_document(Post, status=status, lane="quick_take", pillar="agents")
    await create_doc(store, live)
    plan = await stored_plan(
        store,
        take("s1", post_id=live.id),
        take("s2", post_id="post-unknown"),
        status=PlanStatus.APPROVED,
    )
    for slot_id in ("s1", "s2"):  # a post that cannot be found is never taken for a gone one
        with pytest.raises(PlanSlotRefused) as taken:
            await plan_slot_for(store, plan.week_start, slot_id, lane="quick_take")
        assert (taken.value.code, taken.value.status) == ("plan_slot_taken", 409)
    assert not await slot_post_gone(store, live.id)
    assert not await slot_post_gone(store, "post-unknown")
