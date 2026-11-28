"""Pre-registered experiments: one running at a time, at least 8 measured posts per arm.

An experiment is written down before it runs (question, hypothesis, metric, arms) and its
definition is locked while it runs: only the arms' post lists may change. Starting one checks,
with a strict query, that no other experiment is running (409 ``experiment_running``);
concluding needs ``minPostsPerArm`` measured posts in every arm (409 ``too_few_posts``).

An arm's posts are the ids the owner assigned (``arms[].postIds``) plus posts that name the
experiment and arm themselves (``Post.experimentId`` / ``experimentArm``). A post counts once it
is live and has a snapshot with impressions; its value is the experiment metric: impressions,
or reactions, comments, saves, sends or all engagement per 1K impressions
(:mod:`blogwriter.analytics.rates`). Concluding without a note records a numbers-only summary.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from blogwriter.analytics.rates import PostMetrics, post_metrics
from blogwriter.core.errors import JsonValue
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.analytics import Experiment, ExperimentArm
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import DocId, to_iso
from blogwriter.domain.docstore import InvalidStoredDocument, Stored, find_docs, read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.states import EXPERIMENT_MACHINE, ExperimentStatus, PostStatus

__all__ = [
    "MAX_ARM_POSTS",
    "ArmStats",
    "ExperimentArmIn",
    "ExperimentIn",
    "ExperimentRefused",
    "ExperimentTransitionIn",
    "arm_stats",
    "conclusion_summary",
    "edited_experiment",
    "metric_value",
    "new_experiment",
    "running_experiments",
    "transitioned_experiment",
]

MAX_ARM_POSTS: Final = 100

type ExperimentQuestion = Literal[
    "links_vs_none", "slot_timing", "carousel_vs_image_card", "custom"
]
type ExperimentMetric = Literal[
    "impressions", "reactions", "comments", "saves", "sends", "engagement_rate"
]

_METRIC_LABELS: Final[dict[str, str]] = {
    "impressions": "impressions",
    "reactions": "reactions per 1K impressions",
    "comments": "comments per 1K impressions",
    "saves": "saves per 1K impressions",
    "sends": "sends per 1K impressions",
    "engagement_rate": "engagements per 1K impressions",
}


class ExperimentRefused(ValueError):
    """A 409 the experiment rules give (``code``, owner-facing ``detail``, extensions)."""

    def __init__(
        self, code: str, detail: str, extensions: dict[str, JsonValue] | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.extensions = extensions


class ExperimentArmIn(ApiModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    label: str = Field(min_length=1, max_length=120)
    post_ids: list[DocId] = Field(default_factory=list[str], max_length=MAX_ARM_POSTS)


class ExperimentIn(ApiModel):
    """``POST``/``PUT /api/analytics/experiments``: the pre-registration."""

    question: ExperimentQuestion
    hypothesis: str = Field(min_length=1, max_length=500)
    metric: ExperimentMetric
    arms: list[ExperimentArmIn] = Field(min_length=2, max_length=4)
    min_posts_per_arm: int = Field(default=8, ge=8, le=100)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if not self.hypothesis.strip():
            raise ValueError("the hypothesis must not be blank")
        ids = [arm.id for arm in self.arms]
        if len(ids) != len(set(ids)):
            raise ValueError("arm ids must be unique")
        posts = [post_id for arm in self.arms for post_id in arm.post_ids]
        if len(posts) != len(set(posts)):
            raise ValueError("a post belongs to one arm at most")
        return self

    def arm_models(self) -> list[ExperimentArm]:
        return [
            ExperimentArm(id=arm.id, label=arm.label.strip(), post_ids=list(arm.post_ids))
            for arm in self.arms
        ]


class ExperimentTransitionIn(ApiModel):
    to: Literal["running", "concluded", "abandoned"]
    conclusion: str | None = Field(default=None, max_length=2000)


def new_experiment(body: ExperimentIn, *, now: datetime) -> Experiment:
    """A ``draft`` experiment."""
    stamp = to_iso(now)
    return Experiment(
        id=Experiment.new_id(now=now),
        created_at=stamp,
        updated_at=stamp,
        status=ExperimentStatus.DRAFT,
        status_changed_at=stamp,
        question=body.question,
        hypothesis=body.hypothesis.strip(),
        metric=body.metric,
        min_posts_per_arm=body.min_posts_per_arm,
        arms=body.arm_models(),
    )


def _definition(experiment: Experiment) -> tuple[object, ...]:
    return (
        experiment.question,
        experiment.hypothesis,
        experiment.metric,
        experiment.min_posts_per_arm,
        tuple((arm.id, arm.label) for arm in experiment.arms),
    )


def edited_experiment(current: Experiment, body: ExperimentIn, *, now: datetime) -> Experiment:
    """``current`` after a ``PUT``: anything while ``draft``; only the arms' posts while
    ``running`` (409 ``experiment_locked`` otherwise); nothing once concluded or abandoned."""
    changes: dict[str, object] = {
        "question": body.question,
        "hypothesis": body.hypothesis.strip(),
        "metric": body.metric,
        "min_posts_per_arm": body.min_posts_per_arm,
        "arms": body.arm_models(),
        "updated_at": to_iso(now),
    }
    edited = current.model_copy(update=changes)
    match current.status:
        case ExperimentStatus.DRAFT:
            return edited
        case ExperimentStatus.RUNNING:
            if _definition(edited) != _definition(current):
                raise ExperimentRefused(
                    "experiment_locked",
                    "A running experiment keeps its question, hypothesis, metric and arms. "
                    "Only the posts in each arm can change.",
                )
            return edited
        case _:
            raise ExperimentRefused(
                "experiment_locked", "A concluded or abandoned experiment cannot change."
            )


async def running_experiments(store: Store) -> list[Stored[Experiment]]:
    """Every running experiment (strict: an unreadable one fails the check)."""
    return await find_docs(
        store, Experiment, [Filter("status", "==", ExperimentStatus.RUNNING.value)], strict=True
    )


# --------------------------------------------------------------------------------------------
# Arm results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArmStats:
    arm_id: str
    label: str
    posts: int
    """Measured posts (live, with impressions and the metric)."""
    median: float | None


def metric_value(metrics: PostMetrics, metric: ExperimentMetric) -> float | None:
    """One post's value for the experiment metric."""
    if metric == "impressions":
        return float(metrics.impressions)
    if metric == "engagement_rate":
        return metrics.rates.get("engagement")
    return metrics.rates.get(metric)


async def _read_post(store: Store, post_id: str) -> Post | None:
    try:
        return (await read_doc(store, Post, post_id)).doc
    except (NotFound, InvalidStoredDocument):
        return None


async def arm_stats(store: Store, experiment: Experiment) -> list[ArmStats]:
    """Measured posts and the median metric value of every arm."""
    tagged = await find_docs(store, Post, [Filter("experimentId", "==", experiment.id)])
    out: list[ArmStats] = []
    seen: set[str] = set()
    for arm in experiment.arms:
        members: dict[str, Post] = {
            stored.doc.id: stored.doc
            for stored in tagged
            if stored.doc.experiment_arm == arm.id and stored.doc.id not in seen
        }
        for post_id in arm.post_ids:
            if post_id in members or post_id in seen:
                continue
            post = await _read_post(store, post_id)
            if post is not None:
                members[post_id] = post
        seen.update(members)
        values: list[float] = []
        for post in members.values():
            if post.status is not PostStatus.POSTED:
                continue
            measured = post_metrics(post)
            value = metric_value(measured, experiment.metric) if measured is not None else None
            if value is not None:
                values.append(value)
        median = statistics.median(values) if values else None
        out.append(ArmStats(arm_id=arm.id, label=arm.label, posts=len(values), median=median))
    return out


def conclusion_summary(experiment: Experiment, stats: Sequence[ArmStats]) -> str:
    """A numbers-only conclusion (used when the owner concludes without a note)."""
    unit = _METRIC_LABELS[experiment.metric]
    parts: list[str] = []
    for arm in stats:
        if arm.median is None:
            parts.append(f"{arm.label}: no measured posts.")
        else:
            parts.append(f"{arm.label}: median {arm.median:.1f} {unit} over {arm.posts} posts.")
    return " ".join(parts)[:2000]


async def transitioned_experiment(
    store: Store,
    current: Experiment,
    body: ExperimentTransitionIn,
    *,
    now: datetime,
) -> Experiment:
    """``current`` moved to ``body.to`` (raises ``TransitionError`` or
    :class:`ExperimentRefused`)."""
    target = EXPERIMENT_MACHINE.transition(current.status, ExperimentStatus(body.to))
    stamp = to_iso(now)
    changes: dict[str, object] = {
        "status": target,
        "status_changed_at": stamp,
        "updated_at": stamp,
    }
    note = body.conclusion.strip() if body.conclusion is not None else None
    if target is ExperimentStatus.RUNNING:
        others = [
            stored for stored in await running_experiments(store) if stored.doc.id != current.id
        ]
        if others:
            raise ExperimentRefused(
                "experiment_running",
                "Another experiment is running. Conclude or abandon it first.",
                {"runningId": others[0].doc.id},
            )
        changes["started_at"] = stamp
    elif target is ExperimentStatus.CONCLUDED:
        stats = await arm_stats(store, current)
        short = [arm for arm in stats if arm.posts < current.min_posts_per_arm]
        if short:
            counts: list[JsonValue] = [{"id": arm.arm_id, "posts": arm.posts} for arm in stats]
            raise ExperimentRefused(
                "too_few_posts",
                f"Every arm needs {current.min_posts_per_arm} measured posts to conclude.",
                {"required": current.min_posts_per_arm, "arms": counts},
            )
        changes["concluded_at"] = stamp
        changes["conclusion"] = note or conclusion_summary(current, stats)
    elif note:
        changes["conclusion"] = note
    return current.model_copy(update=changes)
