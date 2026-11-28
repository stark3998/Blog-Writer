"""Cadence (one reseed per save, failures logged not fatal, the calendar rules) and the models
view (routing and prices from a running ``AIRouter``, else prices from ``BW_AI_*``,
preferences)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest

from blogwriter.ai import DEFAULT_PRICES, AIRouter, AISettings, MemoryLedger, ModelPrice
from blogwriter.ai.config import RouteSettings, RouteTarget
from blogwriter.ai.types import (
    Completion,
    ModelSpec,
    ProviderName,
    Request,
    Sensitivity,
    StreamEvent,
    Task,
)
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.testing import FakeServices, RecordingSeeder

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CREATE = {**WRITE, "If-None-Match": "*"}
SERVER_FIELDS = {"id", "schemaVersion", "createdAt", "updatedAt"}


async def cadence_body(client: httpx.AsyncClient) -> dict[str, Any]:
    item = (await client.get("/api/kb/cadence", headers=READ)).json()["item"]
    return {key: value for key, value in item.items() if key not in SERVER_FIELDS}


class FailingSeeder:
    async def reseed(self) -> None:
        raise RuntimeError("scheduler unavailable: secret detail")


async def test_a_cadence_save_reseeds_once(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    seeder = services.schedules
    assert isinstance(seeder, RecordingSeeder)
    body = await cadence_body(client)
    body["timezone"] = "Europe/London"
    saved = await client.put("/api/kb/cadence", headers=CREATE, json=body)
    assert saved.status_code == 200
    assert saved.json()["item"]["timezone"] == "Europe/London"
    assert seeder.reseeds == 1

    refused = await client.put("/api/kb/cadence", headers=CREATE, json=body)  # 412
    invalid = await client.put(
        "/api/kb/cadence",
        headers={**WRITE, "If-Match": saved.headers["etag"]},
        json={**body, "timezone": "Mars/Olympus"},
    )
    assert (refused.status_code, invalid.status_code) == (412, 422)
    assert seeder.reseeds == 1


async def test_a_failed_reseed_is_logged_not_fatal(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    services.schedules = FailingSeeder()  # pyright: ignore[reportAttributeAccessIssue]
    response = await client.put("/api/kb/cadence", headers=CREATE, json=await cadence_body(client))
    assert response.status_code == 200
    records = [r for r in caplog.records if r.getMessage() == "kb_reseed_failed"]
    assert [getattr(r, "error_type", None) for r in records] == ["RuntimeError"]
    assert "secret detail" not in caplog.text


async def test_no_seeder_is_logged(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    services.schedules = None
    response = await client.put("/api/kb/cadence", headers=CREATE, json=await cadence_body(client))
    assert response.status_code == 200
    assert any(r.getMessage() == "kb_reseed_skipped" for r in caplog.records)


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"businessHoursStart": "17:00", "businessHoursEnd": "09:00"}, ["businessHoursEnd"]),
        ({"vetoNotice": {"day": "fri", "time": "19:00"}}, ["vetoNotice", "time"]),
        ({"vetoNotice": {"day": "sat", "time": "12:00"}}, ["vetoNotice"]),
        ({"minVetoLeadHours": 72}, ["vetoNotice"]),
        (
            {"postSlots": [{"day": "tue", "time": "10:00"}, {"day": "tue", "time": "10:00"}]},
            ["postSlots", 1],
        ),
        (
            {"postSlots": [{"day": "tue", "time": "10:00"}, {"day": "tue", "time": "15:00"}]},
            ["postSlots", 1, "day"],
        ),
        ({"postsPerWeek": {"normal": 2, "light": 3, "away": 0, "max": 5}}, ["postsPerWeek"]),
        ({"postsPerWeek": {"normal": 4, "light": 2, "away": 0, "max": 3}}, ["postsPerWeek"]),
        (
            {"reminders": {"replyMinutes": [15, 15], "snapshotHours": [48]}},
            ["reminders", "replyMinutes", 1],
        ),
        (
            {"reminders": {"replyMinutes": [0], "snapshotHours": [48]}},
            ["reminders", "replyMinutes", 0],
        ),
        (
            {"reminders": {"replyMinutes": [15], "snapshotHours": [9999]}},
            ["reminders", "snapshotHours", 0],
        ),
        ({"timezone": "Mars/Olympus"}, ["timezone"]),
    ],
)
async def test_calendar_rules(
    client: httpx.AsyncClient, change: dict[str, Any], loc: list[Any]
) -> None:
    body = {**await cadence_body(client), **change}
    response = await client.put("/api/kb/cadence", headers=CREATE, json=body)
    assert response.status_code == 422, response.text
    assert ["body", *loc] in [error["loc"] for error in response.json()["errors"]]


async def test_two_slots_a_day_are_fine_when_not_blocked(client: httpx.AsyncClient) -> None:
    slots = [{"day": "tue", "time": "10:00"}, {"day": "tue", "time": "15:00"}]
    body = {**await cadence_body(client), "postSlots": slots, "sameDayBlock": False}
    assert (await client.put("/api/kb/cadence", headers=CREATE, json=body)).status_code == 200


# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------


class Provider:
    """Satisfies ``blogwriter.ai`` providers; kb never calls a model."""

    def __init__(self, name: ProviderName) -> None:
        self._name: ProviderName = name

    @property
    def name(self) -> ProviderName:
        return self._name

    def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        raise AssertionError("kb never calls a model")

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        raise AssertionError("kb never calls a model")

    async def aclose(self) -> None:
        return None


def router(settings: AISettings | None = None) -> AIRouter:
    return AIRouter.from_settings(
        settings or AISettings(),
        {"openai": Provider("openai"), "anthropic": Provider("anthropic")},
        ledger=MemoryLedger(),
    )


async def test_models_view_without_a_router(client: httpx.AsyncClient) -> None:
    view = (await client.get("/api/kb/models", headers=READ)).json()
    assert view["routes"] == []  # the fake runner is not an AIRouter
    assert [price["model"] for price in view["prices"]] == sorted(DEFAULT_PRICES)
    opus = next(p for p in view["prices"] if p["model"] == "claude-opus-5-5")
    assert opus == {
        "model": "claude-opus-5-5",
        "input": 4.0,
        "output": 20.0,
        "cachedInput": 0.2,
        "cacheWrite": 8.0,
    }
    assert view["webSearchUsdPer1k"] == 14.0
    assert view["preferences"]["etag"] is None


async def test_models_view_lists_the_running_routes(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    services.ai = router()  # pyright: ignore[reportAttributeAccessIssue]
    view = (await client.get("/api/kb/models", headers=READ)).json()
    routes = {route["task"]: route for route in view["routes"]}
    assert list(routes) == [task.value for task in Task if task is not Task.IMAGE]
    assert routes["draft"] == {
        "task": "draft",
        "maxClass": "anonymized",
        "tools": False,
        "chain": [
            {
                "model": "claude-opus-5-5",
                "deployment": "claude-opus-5-5",
                "provider": "anthropic",
                "effort": "high",
            },
            {
                "model": "gpt-6.2-sol",
                "deployment": "gpt-6.2-sol",
                "provider": "openai",
                "effort": "high",
            },
        ],
    }
    assert routes["quick_search"]["tools"] is True
    assert routes["anonymize"]["maxClass"] == "private"
    assert routes["triage"]["chain"][0]["model"] == "gpt-6-luna"


async def test_prices_follow_the_environment(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BW_AI_WEB_SEARCH_USD_PER_1K", "10")
    view = (await client.get("/api/kb/models", headers=READ)).json()
    assert view["webSearchUsdPer1k"] == 10.0
    monkeypatch.setenv("BW_AI_WEB_SEARCH_USD_PER_1K", "-1")
    broken = await client.get("/api/kb/models", headers=READ)
    assert broken.status_code == 200
    assert (broken.json()["prices"], broken.json()["webSearchUsdPer1k"]) == ([], None)
    assert any(r.getMessage() == "kb_prices_unavailable" for r in caplog.records)


async def test_prices_come_from_the_running_router(
    client: httpx.AsyncClient,
    services: FakeServices,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contract I-11: the prices every call is costed with (``AIRouter.prices``), not a fresh
    read of the environment."""
    opus = ModelPrice(input=Decimal("3"), output=Decimal("15"), cache_write=Decimal("6"))
    running = router(
        AISettings(price_overrides={"claude-opus-5-5": opus}, web_search_usd_per_1k=Decimal(11))
    )
    services.ai = running  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setenv("BW_AI_WEB_SEARCH_USD_PER_1K", "-1")  # unreadable, and never read now
    view = (await client.get("/api/kb/models", headers=READ)).json()
    assert view["webSearchUsdPer1k"] == 11.0
    prices = {price["model"]: price for price in view["prices"]}
    assert list(prices) == sorted(running.prices.models)
    assert prices["claude-opus-5-5"] == {
        "model": "claude-opus-5-5",
        "input": 3.0,
        "output": 15.0,
        "cachedInput": None,
        "cacheWrite": 6.0,
    }
    assert prices["gpt-6-luna"]["input"] == float(DEFAULT_PRICES["gpt-6-luna"].input)
    assert not any(record.getMessage() == "kb_prices_unavailable" for record in caplog.records)


async def test_model_preferences(client: httpx.AsyncClient, services: FakeServices) -> None:
    body = {"imagesEnabled": False, "noveltyMaxSimilarity": 0.9, "nearDuplicateMaxSimilarity": 0.6}
    saved = await client.put("/api/kb/models", headers=CREATE, json=body)
    assert saved.status_code == 200
    assert saved.json()["item"]["noveltyMaxSimilarity"] == 0.9
    etag = saved.headers["etag"]

    images = {**body, "imagesEnabled": True}
    refused = await client.put("/api/kb/models", headers={**WRITE, "If-Match": etag}, json=images)
    assert refused.status_code == 409
    assert refused.json()["code"] == "images_unavailable"

    image_route = RouteSettings(
        primary=RouteTarget(model="gpt-6.2-sol"), max_sensitivity=Sensitivity.PUBLIC
    )
    services.ai = router(AISettings(route_overrides={Task.IMAGE: image_route}))  # pyright: ignore[reportAttributeAccessIssue]
    allowed = await client.put("/api/kb/models", headers={**WRITE, "If-Match": etag}, json=images)
    assert allowed.status_code == 200
    assert allowed.json()["item"]["imagesEnabled"] is True
