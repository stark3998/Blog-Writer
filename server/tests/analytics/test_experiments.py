"""Pre-registered experiments: one running at a time (strict check), at least 8 measured posts
per arm to conclude, the definition locked while running."""

from __future__ import annotations

from typing import Any

import httpx

from blogwriter.analytics.experiments import ArmStats, conclusion_summary, metric_value
from blogwriter.analytics.rates import PostMetrics
from blogwriter.analytics.testing import posted_post, snapshot
from blogwriter.domain.analytics import Experiment
from blogwriter.domain.testing import FakeServices, sample_document

BASE = "/api/analytics/experiments"


def definition(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "question": "links_vs_none",
        "hypothesis": "Posts with no link get more saves per 1K impressions.",
        "metric": "saves",
        "arms": [{"id": "none", "label": "No link"}, {"id": "link", "label": "One link"}],
    }
    return {**body, **overrides}


async def create(client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any) -> Any:
    response = await client.post(BASE, json=definition(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def move(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    created: dict[str, Any],
    to: str,
    **extra: Any,
) -> httpx.Response:
    return await client.post(
        f"{BASE}/{created['item']['id']}/transition",
        json={"to": to, **extra},
        headers={**headers, "If-Match": created["etag"]},
    )


async def measured(services: FakeServices, saves: int, **fields: Any) -> str:
    post = await posted_post(
        services,
        posted_at="2026-11-02T15:00:00.000000Z",
        snapshots=[snapshot(taken_at="2026-11-09T15:00:00.000000Z", impressions=1000, saves=saves)],
        **fields,
    )
    return post.id


async def test_create_validates_the_registration(
    client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    created = await create(client, write_headers)
    item = created["item"]
    assert (item["status"], item["minPostsPerArm"], item["startedAt"]) == ("draft", 8, None)
    for bad in (
        definition(arms=[{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]),
        definition(
            arms=[
                {"id": "a", "label": "A", "postIds": ["post-1"]},
                {"id": "b", "label": "B", "postIds": ["post-1"]},
            ]
        ),
        definition(arms=[{"id": "a", "label": "A"}]),
        definition(hypothesis="   "),
        definition(minPostsPerArm=7),
        definition(metric="likes"),
    ):
        response = await client.post(BASE, json=bad, headers=write_headers)
        assert response.status_code == 422, bad


async def test_one_experiment_runs_at_a_time(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    first = await create(client, write_headers)
    second = await create(client, write_headers, question="slot_timing", metric="impressions")
    started = await move(client, write_headers, first, "running")
    assert started.status_code == 200
    assert started.json()["item"]["startedAt"] == "2026-11-26T17:00:00.000000Z"
    blocked = await move(client, write_headers, second, "running")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "experiment_running"
    assert blocked.json()["runningId"] == first["item"]["id"]
    abandoned = await move(client, write_headers, started.json(), "abandoned", conclusion="Stop.")
    assert abandoned.json()["item"]["status"] == "abandoned"
    now_free = await move(client, write_headers, second, "running")
    assert now_free.status_code == 200
    running = await client.get(BASE, params={"status": "running"}, headers=read_headers)
    assert [entry["item"]["id"] for entry in running.json()["items"]] == [second["item"]["id"]]
    wrong = await move(client, write_headers, second, "concluded")
    assert wrong.status_code == 412  # the etag moved on
    one = await client.get(f"{BASE}/{second['item']['id']}", headers=read_headers)
    invalid = await move(client, write_headers, one.json(), "running")
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "invalid_transition"


async def test_an_unreadable_running_experiment_fails_the_check(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    broken = sample_document(Experiment, status="running").to_document()
    del broken["hypothesis"]
    await services.store.upsert("settings", broken)
    created = await create(client, write_headers)
    response = await move(client, write_headers, created, "running")
    assert response.status_code == 500
    assert response.json()["code"] == "invalid_document"


async def test_concluding_needs_eight_measured_posts_per_arm(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    listed = [await measured(services, saves=2) for _ in range(8)]
    created = await create(
        client,
        write_headers,
        arms=[
            {"id": "none", "label": "No link", "postIds": listed},
            {"id": "link", "label": "One link"},
        ],
    )
    running = (await move(client, write_headers, created, "running")).json()
    experiment_id = created["item"]["id"]
    for saves in range(7):  # tagged by the posts themselves
        await measured(services, saves=saves, experiment_id=experiment_id, experiment_arm="link")
    await posted_post(  # not measured: no snapshot
        services,
        posted_at="2026-11-02T15:00:00.000000Z",
        experiment_id=experiment_id,
        experiment_arm="link",
    )
    short = await move(client, write_headers, running, "concluded")
    assert short.status_code == 409
    problem = short.json()
    assert problem["code"] == "too_few_posts"
    assert problem["required"] == 8
    assert problem["arms"] == [{"id": "none", "posts": 8}, {"id": "link", "posts": 7}]
    await measured(services, saves=9, experiment_id=experiment_id, experiment_arm="link")
    done = await move(client, write_headers, running, "concluded")
    assert done.status_code == 200, done.text
    item = done.json()["item"]
    assert item["status"] == "concluded"
    assert item["concludedAt"] == "2026-11-26T17:00:00.000000Z"
    assert item["conclusion"] == (
        "No link: median 2.0 saves per 1K impressions over 8 posts. "
        "One link: median 3.5 saves per 1K impressions over 8 posts."
    )


async def test_the_registration_is_locked_while_running(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    created = await create(client, write_headers)
    url = f"{BASE}/{created['item']['id']}"
    edited = await client.put(
        url,
        json=definition(hypothesis="Fewer links, more saves."),
        headers={**write_headers, "If-Match": created["etag"]},
    )
    assert edited.status_code == 200
    running = (await move(client, write_headers, edited.json(), "running")).json()
    locked = await client.put(
        url,
        json=definition(hypothesis="Changed my mind."),
        headers={**write_headers, "If-Match": running["etag"]},
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "experiment_locked"
    post_id = await measured(services, saves=1)
    assigned = await client.put(
        url,
        json=definition(
            hypothesis="Fewer links, more saves.",
            arms=[
                {"id": "none", "label": "No link", "postIds": [post_id]},
                {"id": "link", "label": "One link"},
            ],
        ),
        headers={**write_headers, "If-Match": running["etag"]},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["item"]["arms"][0]["postIds"] == [post_id]
    abandoned = await move(client, write_headers, assigned.json(), "abandoned")
    frozen = await client.put(
        url,
        json=definition(hypothesis="Fewer links, more saves."),
        headers={**write_headers, "If-Match": abandoned.json()["etag"]},
    )
    assert frozen.status_code == 409
    assert frozen.json()["code"] == "experiment_locked"


def test_metric_values_and_the_summary() -> None:
    metrics = PostMetrics(
        post_id="p",
        posted_at="2026-11-02T15:00:00.000000Z",
        impressions=1000,
        rates={"reactions": 12.0, "engagement": 15.0},
        values={},
    )
    assert metric_value(metrics, "impressions") == 1000.0
    assert metric_value(metrics, "engagement_rate") == 15.0
    assert metric_value(metrics, "reactions") == 12.0
    assert metric_value(metrics, "sends") is None
    experiment = sample_document(Experiment, metric="impressions")
    summary = conclusion_summary(
        experiment,
        [ArmStats("none", "No links", 9, 1234.5), ArmStats("link", "One link", 0, None)],
    )
    assert summary == (
        "No links: median 1234.5 impressions over 9 posts. One link: no measured posts."
    )
