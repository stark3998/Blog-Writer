"""``POST /api/render/mermaid``: the Draft tab's live diagram preview.

Plan: "Studio / Draft" (live Mermaid previews in the editor), contract section 1.9 (owner-only,
CSRF on every non-GET, nothing stored, no content in logs) and section 10 (server SVG is shown
only as an image). The render service is the fake adapter; the real renderer is tested in
``tests/render`` (Chromium).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Iterator

import httpx
import pytest

from blogwriter.api.render import (
    MAX_BODY_BYTES,
    MAX_SOURCE_CHARS,
    DiagramCache,
    clear_cache,
    get_router,
)
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import CONTAINERS
from blogwriter.domain.ports import RenderFailed
from blogwriter.domain.testing import FakeRenderService, FakeServices, make_app, make_services

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
URL = "/api/render/mermaid"
SOURCE = "flowchart LR\n  agent --> vault"


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def services() -> FakeServices:
    return make_services()


@pytest.fixture
async def client(services: FakeServices) -> AsyncGenerator[httpx.AsyncClient]:
    async with asgi_client(make_app(services, [get_router()], settings=SETTINGS)) as http:
        yield http


def render_of(services: FakeServices) -> FakeRenderService:
    assert isinstance(services.render, FakeRenderService)
    return services.render


async def test_a_diagram_renders_to_a_sanitised_svg_with_its_size(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    response = await client.post(URL, json={"source": SOURCE}, headers=WRITE)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "svg": FakeRenderService.SVG.decode(),
        "width": 10,
        "height": 10,
    }
    assert render_of(services).calls == ["render_mermaid_svg"]
    assert response.headers["cache-control"] == "no-store"


async def test_the_same_source_is_served_from_the_cache(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    first = await client.post(URL, json={"source": SOURCE}, headers=WRITE)
    second = await client.post(URL, json={"source": SOURCE}, headers=WRITE)
    other = await client.post(URL, json={"source": SOURCE + "\n  vault --> api"}, headers=WRITE)
    assert first.json() == second.json()
    assert other.status_code == 200
    assert render_of(services).calls == ["render_mermaid_svg", "render_mermaid_svg"]


async def test_it_is_owner_only_and_needs_csrf(client: httpx.AsyncClient) -> None:
    anonymous = await client.post(URL, json={"source": SOURCE})
    no_csrf = await client.post(URL, json={"source": SOURCE}, headers=READ)
    read = await client.get(URL, headers=READ)
    assert anonymous.status_code == 401
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_failed"
    assert read.status_code == 405  # no GET changes state, and none renders


async def test_without_a_renderer_it_is_503_render_unavailable() -> None:
    services = make_services(render=None)
    async with asgi_client(make_app(services, [get_router()], settings=SETTINGS)) as http:
        response = await http.post(URL, json={"source": SOURCE}, headers=WRITE)
    assert response.status_code == 503
    assert response.json()["code"] == "render_unavailable"
    assert response.headers["retry-after"] == "5"


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (RenderFailed("mermaid_error", "A diagram could not be drawn."), 422, "mermaid_error"),
        (RenderFailed("unsafe_svg", "Not safe."), 422, "unsafe_svg"),
        (RenderFailed("unavailable", "Rendering is not available."), 503, "render_unavailable"),
        (RenderFailed("timeout", "Too slow."), 504, "render_timeout"),
    ],
)
async def test_render_failures_map_to_problems_and_are_not_cached(
    client: httpx.AsyncClient, services: FakeServices, failure: RenderFailed, status: int, code: str
) -> None:
    render_of(services).fail_with = failure
    response = await client.post(URL, json={"source": SOURCE}, headers=WRITE)
    assert response.status_code == status
    assert response.json()["code"] == code
    assert SOURCE not in response.text
    render_of(services).fail_with = None
    again = await client.post(URL, json={"source": SOURCE}, headers=WRITE)
    assert again.status_code == 200
    assert render_of(services).calls == ["render_mermaid_svg", "render_mermaid_svg"]


async def test_the_body_is_capped_before_it_is_parsed(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    padded = json.dumps({"source": SOURCE}) + " " * MAX_BODY_BYTES
    too_large = await client.post(
        URL, content=padded, headers={**WRITE, "Content-Type": "application/json"}
    )
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "too_large"
    too_long = await client.post(URL, json={"source": "a" * (MAX_SOURCE_CHARS + 1)}, headers=WRITE)
    assert too_long.status_code == 422
    assert too_long.json()["code"] == "diagram_too_long"
    assert render_of(services).calls == []


@pytest.mark.parametrize(
    ("content", "content_type", "status", "code"),
    [
        ('{"source": ""}', "application/json", 422, "validation_failed"),
        ('{"source": "   "}', "application/json", 422, "mermaid_error"),
        ('{"source": "a", "brand": "x"}', "application/json", 422, "validation_failed"),
        ("not json", "application/json", 422, "validation_failed"),
        ("flowchart LR", "text/plain", 415, "unsupported_media_type"),
    ],
)
async def test_bad_bodies_are_refused_without_rendering(
    client: httpx.AsyncClient,
    services: FakeServices,
    content: str,
    content_type: str,
    status: int,
    code: str,
) -> None:
    response = await client.post(
        URL, content=content, headers={**WRITE, "Content-Type": content_type}
    )
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert render_of(services).calls == []


async def test_nothing_is_stored_and_the_source_is_never_logged(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    private = "flowchart LR\n  contoso_vault --> payroll_api"
    caplog.set_level(logging.DEBUG)
    render_of(services).fail_with = RenderFailed("mermaid_error", "A diagram could not be drawn.")
    await client.post(URL, json={"source": private}, headers=WRITE)
    render_of(services).fail_with = None
    await client.post(URL, json={"source": private}, headers=WRITE)
    assert "contoso_vault" not in caplog.text
    assert DiagramCache.key(private) not in caplog.text
    for name in CONTAINERS:
        assert await services.store.find(name, []) == []


def test_the_cache_drops_the_least_recently_used_entries() -> None:
    cache = DiagramCache(max_entries=2, max_bytes=100)
    cache.put("a", "x" * 10)
    cache.put("b", "y" * 10)
    assert cache.get("a") == "x" * 10  # a is now the most recent
    cache.put("c", "z" * 10)
    assert cache.get("b") is None
    assert len(cache) == 2
    cache.put("big", "w" * 101)  # larger than the whole budget: never kept
    assert cache.get("big") is None
    cache.put("d", "v" * 91)  # over the count, then over the byte budget: the oldest go
    assert cache.get("a") is None
    assert cache.get("c") is None
    assert cache.get("d") == "v" * 91
    assert DiagramCache.key("x") == DiagramCache.key("x") != DiagramCache.key("y")


def test_the_openapi_document_types_the_route() -> None:
    document = make_app(make_services(), [get_router()], settings=SETTINGS).openapi()
    operation = document["paths"][URL]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["source"]
    assert schema["additionalProperties"] is False
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response == {"$ref": "#/components/schemas/MermaidOut"}
