"""Autonomy: the owner's auto-publish switch (calibration and judge are read-only here), the
decision, and the one-tap pause/resume (idempotent, no If-Match)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import MemoryStore
from blogwriter.domain.docstore import update_singleton
from blogwriter.domain.settings import AutonomySettings, JudgeCalibration, record_review
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CREATE = {**WRITE, "If-None-Match": "*"}


async def view(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/api/kb/autonomy", headers=READ)
    assert response.status_code == 200
    return response.json()


async def calibrate(store: MemoryStore, clean: int, *, judge: bool) -> None:
    """Write calibration the way the publish and analytics packages do."""
    for index in range(clean):
        article = f"art-{index:028x}"
        await update_singleton(
            store,
            AutonomySettings,
            lambda doc, article=article: record_review(
                doc, article_id=article, outcome="clean", at=SAMPLE_NOW
            ),
            now=SAMPLE_NOW,
        )
    if judge:
        calibrated = JudgeCalibration(as_is_rate=0.95, sample_size=12, checked_at=SAMPLE_NOW)
        await update_singleton(
            store,
            AutonomySettings,
            lambda doc: doc.model_copy(update={"judge": calibrated}),
            now=SAMPLE_NOW,
        )


async def test_defaults_and_decision(client: httpx.AsyncClient) -> None:
    body = await view(client)
    assert body["settings"]["etag"] is None
    settings = body["settings"]["item"]
    assert (settings["autoPublishEnabled"], settings["paused"]) == (False, False)
    assert settings["calibration"]["cleanStreak"] == 0
    assert settings["calibration"]["required"] == 6
    assert body["decision"] == {
        "allowed": False,
        "reasons": ["not_enabled", "calibrating", "judge_uncalibrated"],
    }


async def test_the_switch_keeps_server_written_fields(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await calibrate(services.store, 6, judge=True)
    etag = (await view(client))["settings"]["etag"]
    saved = await client.put(
        "/api/kb/autonomy",
        headers={**WRITE, "If-Match": etag},
        json={"autoPublishEnabled": True},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["settings"]["item"]["calibration"]["cleanStreak"] == 6
    assert body["settings"]["item"]["judge"]["asIsRate"] == 0.95
    assert body["decision"] == {"allowed": True, "reasons": []}
    assert saved.headers["etag"] == body["settings"]["etag"]


@pytest.mark.parametrize(
    "extra",
    [
        {"calibration": {"cleanStreak": 99, "required": 1, "events": []}},
        {"judge": {"asIsRate": 1.0, "sampleSize": 12, "checkedAt": SAMPLE_NOW}},
        {"paused": False},
        {"lastCorrectionAt": None},
    ],
)
async def test_calibration_and_judge_cannot_be_written(
    client: httpx.AsyncClient, services: FakeServices, extra: dict[str, Any]
) -> None:
    await calibrate(services.store, 4, judge=False)
    etag = (await view(client))["settings"]["etag"]
    response = await client.put(
        "/api/kb/autonomy",
        headers={**WRITE, "If-Match": etag},
        json={"autoPublishEnabled": True, **extra},
    )
    assert response.status_code == 422
    after = await view(client)
    assert after["settings"]["etag"] == etag
    assert after["settings"]["item"]["calibration"]["cleanStreak"] == 4
    assert after["settings"]["item"]["autoPublishEnabled"] is False


async def test_pause_and_resume_are_idempotent(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    resumed = await client.post("/api/kb/autonomy/resume", headers=WRITE)
    assert resumed.status_code == 200
    assert resumed.json()["settings"]["etag"] is None  # nothing to resume: nothing written
    assert await services.store.find("settings") == []

    paused = await client.post("/api/kb/autonomy/pause", headers=WRITE)
    first = paused.json()
    assert first["settings"]["item"]["paused"] is True
    assert first["settings"]["item"]["pausedAt"] == SAMPLE_NOW
    assert "paused" in first["decision"]["reasons"]

    services.clock.advance(600)
    again = (await client.post("/api/kb/autonomy/pause", headers=WRITE)).json()
    assert again["settings"]["etag"] == first["settings"]["etag"]
    assert again["settings"]["item"]["pausedAt"] == SAMPLE_NOW

    stale = await client.put("/api/kb/autonomy", headers=CREATE, json={"autoPublishEnabled": True})
    assert stale.status_code == 412  # the pause saved it: the first save needs If-Match now

    back = (await client.post("/api/kb/autonomy/resume", headers=WRITE)).json()
    assert back["settings"]["item"]["paused"] is False
    assert back["settings"]["item"]["pausedAt"] is None
    assert back["settings"]["etag"] != first["settings"]["etag"]
    once_more = (await client.post("/api/kb/autonomy/resume", headers=WRITE)).json()
    assert once_more["settings"]["etag"] == back["settings"]["etag"]


async def test_pause_overrides_a_calibrated_switch(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await calibrate(services.store, 6, judge=True)
    etag = (await view(client))["settings"]["etag"]
    await client.put(
        "/api/kb/autonomy", headers={**WRITE, "If-Match": etag}, json={"autoPublishEnabled": True}
    )
    paused = (await client.post("/api/kb/autonomy/pause", headers=WRITE)).json()
    assert paused["decision"] == {"allowed": False, "reasons": ["paused"]}
    assert paused["settings"]["item"]["autoPublishEnabled"] is True
