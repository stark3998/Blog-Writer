"""Every status machine: the golden edge tables, every allowed and every refused transition."""

from __future__ import annotations

from collections import deque
from enum import StrEnum

import pytest

from blogwriter.domain.states import (
    ARTICLE_MACHINE,
    ARTICLE_WRITER,
    CONNECTION_MACHINE,
    ENGAGEMENT_MACHINE,
    EXPERIMENT_MACHINE,
    GUIDE_MACHINE,
    IDEA_MACHINE,
    INTERVIEW_MACHINE,
    KIT_MACHINE,
    MACHINES,
    OWNER_EDGES,
    OWNER_WRITES,
    PLAN_MACHINE,
    POST_MACHINE,
    REMINDER_MACHINE,
    RESEARCH_MACHINE,
    REVIEW_MACHINE,
    SAMPLE_MACHINE,
    SOURCE_MACHINE,
    SUGGESTION_MACHINE,
    ArticleStatus,
    StateMachine,
    TransitionError,
    WriterConflict,
    check_article_writer,
    transition,
)

# The contract's edge tables (docs/v2/build/contract.md, "State machines"). A change here is a
# contract change: update the work orders that rely on it.
GOLDEN: dict[str, dict[str, list[str]]] = {
    "article": {
        "planned": ["archived", "drafting", "in_review", "interviewing", "researching"],
        "researching": ["archived", "drafting", "interviewing", "needs_attention"],
        "interviewing": ["archived", "drafting", "needs_attention"],
        "drafting": ["archived", "gating", "in_review", "needs_attention"],
        "gating": ["approved", "in_review", "needs_attention"],
        "in_review": [
            "approved",
            "archived",
            "drafting",
            "gating",
            "interviewing",
            "researching",
        ],
        "approved": ["in_review", "publishing"],
        "publishing": ["publish_failed", "published"],
        "published": ["in_review", "publishing", "unpublishing"],
        "publish_failed": ["in_review", "publishing", "unpublishing"],
        "unpublishing": ["publish_failed", "unpublished"],
        "unpublished": ["archived", "in_review"],
        "needs_attention": ["archived", "drafting", "in_review", "interviewing", "researching"],
        "archived": ["planned"],
    },
    "post": {
        "generating": ["archived", "draft", "needs_attention"],
        "draft": ["approved", "archived", "expired", "generating"],
        "approved": ["archived", "draft", "expired", "kit", "missed", "publishing", "staged"],
        "staged": ["approved", "draft", "expired", "kit", "missed", "publishing"],
        "publishing": ["approved", "needs_attention", "posted"],
        "posted": ["deleted"],
        "kit": ["approved", "archived", "expired", "missed", "posted"],
        "needs_attention": ["approved", "archived", "draft", "kit", "posted"],
        "missed": ["approved", "archived", "expired", "kit"],
        "expired": ["archived", "draft"],
        "deleted": [],
        "archived": ["draft"],
    },
    "kit": {
        "prepared": ["native_draft_saved", "natively_scheduled", "posted"],
        "native_draft_saved": ["natively_scheduled", "posted"],
        "natively_scheduled": ["native_draft_saved", "posted"],
        "posted": [],
    },
    "idea": {
        "new": ["accepted", "dismissed", "expired", "snoozed", "used"],
        "accepted": ["dismissed", "new", "used"],
        "snoozed": ["dismissed", "expired", "new"],
        "used": ["accepted"],
        "dismissed": ["new"],
        "expired": ["new"],
    },
    "research_run": {
        "queued": ["cancelled", "running"],
        "running": ["cancelled", "failed", "quarantined", "succeeded"],
        "succeeded": [],
        "failed": ["queued"],
        "quarantined": [],
        "cancelled": [],
    },
    "review": {
        "proposed": ["approved", "rejected"],
        "approved": ["archived"],
        "rejected": ["proposed"],
        "archived": ["approved"],
    },
    "voice_sample": {"active": ["excluded"], "excluded": ["active"]},
    "style_guide": {
        "proposed": ["active", "rejected"],
        "active": ["superseded"],
        "superseded": ["active"],
        "rejected": [],
    },
    "suggestion": {"proposed": ["accepted", "dismissed"], "accepted": [], "dismissed": []},
    "interview": {
        "pending": ["answered", "expired", "skipped"],
        "answered": [],
        "skipped": [],
        "expired": ["answered", "skipped"],
    },
    "week_plan": {
        "generating": ["proposed"],
        "proposed": ["approved", "generating"],
        "approved": ["closed", "proposed"],
        "closed": [],
    },
    "reminder": {
        "pending": ["cancelled", "expired", "sent"],
        "sent": [],
        "cancelled": [],
        "expired": [],
    },
    "engagement_item": {
        "open": ["dismissed", "expired", "finalized"],
        "finalized": [],
        "dismissed": [],
        "expired": [],
    },
    "experiment": {
        "draft": ["abandoned", "running"],
        "running": ["abandoned", "concluded"],
        "concluded": [],
        "abandoned": [],
    },
    "source": {
        "active": ["failing", "paused"],
        "paused": ["active"],
        "failing": ["active", "paused"],
    },
    "linkedin_connection": {
        "disconnected": ["connected"],
        "connected": ["disconnected", "needs_reconnect"],
        "needs_reconnect": ["connected", "disconnected"],
    },
}

ALL: list[StateMachine[StrEnum]] = [
    ARTICLE_MACHINE,  # pyright: ignore[reportAssignmentType]
    POST_MACHINE,  # pyright: ignore[reportAssignmentType]
    KIT_MACHINE,  # pyright: ignore[reportAssignmentType]
    IDEA_MACHINE,  # pyright: ignore[reportAssignmentType]
    RESEARCH_MACHINE,  # pyright: ignore[reportAssignmentType]
    REVIEW_MACHINE,  # pyright: ignore[reportAssignmentType]
    SAMPLE_MACHINE,  # pyright: ignore[reportAssignmentType]
    GUIDE_MACHINE,  # pyright: ignore[reportAssignmentType]
    SUGGESTION_MACHINE,  # pyright: ignore[reportAssignmentType]
    INTERVIEW_MACHINE,  # pyright: ignore[reportAssignmentType]
    PLAN_MACHINE,  # pyright: ignore[reportAssignmentType]
    REMINDER_MACHINE,  # pyright: ignore[reportAssignmentType]
    ENGAGEMENT_MACHINE,  # pyright: ignore[reportAssignmentType]
    EXPERIMENT_MACHINE,  # pyright: ignore[reportAssignmentType]
    SOURCE_MACHINE,  # pyright: ignore[reportAssignmentType]
    CONNECTION_MACHINE,  # pyright: ignore[reportAssignmentType]
]
IDS = [machine.name for machine in ALL]


def test_registry_lists_every_machine() -> None:
    assert sorted(MACHINES) == sorted(GOLDEN) == sorted(IDS)


@pytest.mark.parametrize("machine", ALL, ids=IDS)
def test_edges_match_the_contract(machine: StateMachine[StrEnum]) -> None:
    assert machine.as_table() == GOLDEN[machine.name]
    assert MACHINES[machine.name].as_table() == GOLDEN[machine.name]


@pytest.mark.parametrize("machine", ALL, ids=IDS)
def test_every_pair_is_allowed_or_refused_as_specified(machine: StateMachine[StrEnum]) -> None:
    golden = GOLDEN[machine.name]
    for current in machine.states:
        for target in machine.states:
            allowed = target.value in golden[current.value]
            assert machine.can(current, target) is allowed
            if allowed:
                assert transition(machine, current, target) is target
                assert machine.transition(current, target) is target
            else:
                with pytest.raises(TransitionError) as caught:
                    transition(machine, current, target)
                error = caught.value
                assert error.machine == machine.name
                assert (error.current, error.target) == (current.value, target.value)
                assert list(error.allowed) == golden[current.value]
                assert TransitionError.code == "invalid_transition"


@pytest.mark.parametrize("machine", ALL, ids=IDS)
def test_every_state_is_reachable_from_the_initial_state(machine: StateMachine[StrEnum]) -> None:
    seen = {machine.initial}
    queue = deque([machine.initial])
    while queue:
        for target in machine.allowed(queue.popleft()):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    assert seen == set(machine.states)


@pytest.mark.parametrize("machine", ALL, ids=IDS)
def test_terminal_states_have_no_exits(machine: StateMachine[StrEnum]) -> None:
    expected = {state for state, targets in GOLDEN[machine.name].items() if not targets}
    assert {state.value for state in machine.terminal} == expected


def test_machine_definitions_are_checked() -> None:
    with pytest.raises(ValueError, match="not a transition"):
        StateMachine(
            name="bad",
            states=tuple(ArticleStatus),
            initial=ArticleStatus.PLANNED,
            edges={ArticleStatus.PLANNED: frozenset({ArticleStatus.PLANNED})},
        )
    with pytest.raises(ValueError, match="unknown"):
        StateMachine(
            name="bad",
            states=(ArticleStatus.PLANNED,),
            initial=ArticleStatus.PLANNED,
            edges={ArticleStatus.PLANNED: frozenset({ArticleStatus.ARCHIVED})},
        )
    with pytest.raises(ValueError, match="initial"):
        StateMachine(
            name="bad",
            states=(ArticleStatus.PLANNED,),
            initial=ArticleStatus.ARCHIVED,
            edges={},
        )


def test_edges_are_read_only() -> None:
    with pytest.raises(TypeError):
        ARTICLE_MACHINE.edges[ArticleStatus.PLANNED] = frozenset()  # type: ignore[index]


# ---------------------------------------------------------------------------------------------
# Who writes an article
# ---------------------------------------------------------------------------------------------

PIPELINE_STATES = {"researching", "interviewing", "drafting", "gating"}
PUBLISH_STATES = {"publishing", "unpublishing"}
OWNER_STATES = {"planned", "in_review", "needs_attention"}


def test_writer_map_covers_every_status() -> None:
    assert set(ARTICLE_WRITER) == set(ArticleStatus)
    assert {s.value for s, w in ARTICLE_WRITER.items() if w == "pipeline"} == PIPELINE_STATES
    assert {s.value for s, w in ARTICLE_WRITER.items() if w == "publish"} == PUBLISH_STATES
    assert {s.value for s, w in ARTICLE_WRITER.items() if w == "owner"} == OWNER_STATES


def test_the_pipeline_cannot_touch_the_bytes_being_published() -> None:
    for status in (ArticleStatus.PUBLISHING, ArticleStatus.UNPUBLISHING):
        with pytest.raises(WriterConflict) as caught:
            check_article_writer(status, "pipeline")
        assert caught.value.writer == "publish"
        check_article_writer(status, "publish")


@pytest.mark.parametrize("status", list(ArticleStatus), ids=[s.value for s in ArticleStatus])
def test_check_article_writer(status: ArticleStatus) -> None:
    for actor in ("owner", "pipeline", "publish"):
        if ARTICLE_WRITER[status] == actor:
            check_article_writer(status, actor)
        else:
            with pytest.raises(WriterConflict) as caught:
                check_article_writer(status, actor)
            assert caught.value.code == "read_only_state"
            assert caught.value.writer == ARTICLE_WRITER[status]


# ---------------------------------------------------------------------------------------------
# The owner's part (served by GET /api/meta/machines)
# ---------------------------------------------------------------------------------------------


def test_every_machine_says_what_the_owner_does_in_it() -> None:
    assert set(OWNER_EDGES) == set(MACHINES)
    assert set(OWNER_WRITES) == set(MACHINES)
    for name, machine in MACHINES.items():
        table = machine.as_table()
        for source, targets in OWNER_EDGES[name].items():
            assert targets, (name, source)
            assert set(targets) <= set(table[source]), (name, source)  # only real edges
        assert set(OWNER_WRITES[name]) <= set(table), name


def test_the_owner_edits_articles_exactly_where_the_owner_writes() -> None:
    assert OWNER_WRITES["article"] == OWNER_STATES


def test_jobs_and_answers_keep_their_edges() -> None:
    """System-only edges: the owner never asks for them (the web shows no button)."""
    article = OWNER_EDGES["article"]
    assert "publishing" not in article
    assert "unpublishing" not in article
    assert "gating" not in article["in_review"]  # the pipeline checks a draft
    assert "approved" not in article.get("gating", frozenset())  # the auto path
    post = OWNER_EDGES["post"]
    assert "publishing" not in post  # LinkedIn's answer settles a tap
    assert "staged" not in post["approved"]  # the staging job
    assert all("missed" not in targets and "expired" not in targets for targets in post.values())
    assert "draft" not in post.get("archived", frozenset())  # no restore route yet
    assert "used" not in OWNER_EDGES["idea"]["new"]  # an article or post made from it
    assert "superseded" not in OWNER_EDGES["style_guide"].get("active", frozenset())


def test_the_owner_can_leave_a_failed_publish() -> None:
    assert OWNER_EDGES["article"]["publish_failed"] == frozenset({"in_review", "unpublishing"})
