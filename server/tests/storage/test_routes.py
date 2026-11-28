"""``POST /api/media`` and ``GET /api/media/{name}`` through the real app (auth, CSRF, problems)."""

from __future__ import annotations

import hashlib
import io
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as csrf_headers
from blogwriter.domain.testing import FakeServices, MemoryBlobStore, make_app, make_services
from blogwriter.storage import get_router, upload_limits
from blogwriter.storage.routes import is_media_name
from blogwriter.storage.settings import StorageSettings

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)


def write(content_type: str | None = "image/png") -> dict[str, str]:
    headers = {**READ, **csrf_headers(SETTINGS)}
    if content_type is not None:
        headers["Content-Type"] = content_type
    return headers


def png(width: int = 6, height: int = 4) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "#0E2A24").save(out, format="PNG")
    return out.getvalue()


def app_for(services: FakeServices, limits: StorageSettings | None = None) -> FastAPI:
    app = make_app(services, routers=[get_router()], settings=SETTINGS)
    if limits is not None:
        app.dependency_overrides[upload_limits] = lambda: limits
    return app


@pytest.fixture
def services() -> FakeServices:
    return make_services()


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    async with asgi_client(app_for(services)) as http:
        yield http


def blobs_of(services: FakeServices) -> MemoryBlobStore:
    assert services.blobs is not None
    return services.blobs


# --------------------------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------------------------


async def test_upload_re_encodes_and_returns_the_media_ref(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    response = await client.post(
        "/api/media?role=headshot&filename=me.png", content=png(), headers=write()
    )
    assert response.status_code == 201, response.text
    body = response.json()
    stored = blobs_of(services).blobs
    assert len(stored) == 1
    (container, name), (data, content_type) = next(iter(stored.items()))
    assert container == "media"
    assert content_type == "image/png"
    assert name == f"uploads/2026-11/{hashlib.sha256(data).hexdigest()}.png"
    assert body == {
        "role": "headshot",
        "container": "media",
        "name": name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "contentType": "image/png",
        "sizeBytes": len(data),
        "width": 6,
        "height": 4,
        "pageCount": None,
        "altText": None,
        "filename": "me.png",
        "linkedinUrn": None,
        "linkedinStatus": None,
        "uploadedAt": "2026-11-26T12:00:00.000000Z",
    }


async def test_the_same_upload_twice_is_one_blob(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    first = await client.post("/api/media", content=png(), headers=write())
    second = await client.post("/api/media", content=png(), headers=write())
    assert first.status_code == second.status_code == 201
    assert first.json()["name"] == second.json()["name"]
    assert len(blobs_of(services).blobs) == 1


@pytest.mark.parametrize("content_type", ["image/svg+xml", "text/html", "image/gif", None])
async def test_unsupported_content_types_are_415(
    client: httpx.AsyncClient, services: FakeServices, content_type: str | None
) -> None:
    response = await client.post("/api/media", content=png(), headers=write(content_type))
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"
    assert blobs_of(services).blobs == {}


async def test_a_body_that_is_not_its_declared_type_is_415(client: httpx.AsyncClient) -> None:
    out = io.BytesIO()
    Image.new("RGB", (4, 4)).save(out, format="JPEG")
    response = await client.post("/api/media", content=out.getvalue(), headers=write("image/png"))
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


async def test_svg_sent_as_png_is_refused(client: httpx.AsyncClient) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    response = await client.post("/api/media", content=svg, headers=write("image/png"))
    assert response.status_code == 415
    assert "alert" not in response.text


async def test_garbage_is_422_invalid_image(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/media", content=b"\x89PNG\r\n\x1a\nnot really", headers=write()
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_image"
    empty = await client.post("/api/media", content=b"", headers=write())
    assert empty.status_code == 422


async def test_bodies_over_the_cap_are_413(services: FakeServices) -> None:
    limits = StorageSettings(max_upload_bytes=2048)
    async with asgi_client(app_for(services, limits)) as http:
        declared = await http.post("/api/media", content=b"x" * 4096, headers=write())
        assert declared.status_code == 413
        assert declared.json()["code"] == "too_large"

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(8):
                yield b"y" * 1024

        streamed = await http.post("/api/media", content=chunks(), headers=write())
        assert streamed.status_code == 413
    assert blobs_of(services).blobs == {}


async def test_images_over_the_pixel_cap_are_413(services: FakeServices) -> None:
    limits = StorageSettings(max_image_pixels=100)
    async with asgi_client(app_for(services, limits)) as http:
        response = await http.post("/api/media", content=png(20, 20), headers=write())
    assert response.status_code == 413
    assert response.json()["code"] == "too_large"


@pytest.mark.parametrize(
    "query",
    ["role=carousel_pdf", "role=anything", "filename=../etc/passwd", "filename=a%2Fb.png"],
)
async def test_bad_query_values_are_422(client: httpx.AsyncClient, query: str) -> None:
    response = await client.post(f"/api/media?{query}", content=png(), headers=write())
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_uploads_need_csrf(client: httpx.AsyncClient, services: FakeServices) -> None:
    response = await client.post(
        "/api/media", content=png(), headers={**READ, "Content-Type": "image/png"}
    )
    assert response.status_code == 403
    assert blobs_of(services).blobs == {}


async def test_uploads_need_the_owner(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/media", content=png(), headers={"Content-Type": "image/png"})
    assert response.status_code == 401


# --------------------------------------------------------------------------------------------
# Serving
# --------------------------------------------------------------------------------------------


async def test_media_are_served_with_cache_and_sandbox_headers(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post("/api/media", content=png(), headers=write())
    name = created.json()["name"]
    response = await client.get(f"/api/media/{name}", headers=READ)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert hashlib.sha256(response.content).hexdigest() == created.json()["sha256"]


async def test_names_with_folders_resolve_with_or_without_encoded_slashes(
    client: httpx.AsyncClient,
) -> None:
    """A media name has folders (``uploads/2026-11/<sha>.png``): the path parameter takes them
    as they are, and a client that encodes the slashes (``%2F``) reaches the same file."""
    created = await client.post("/api/media", content=png(), headers=write())
    name = created.json()["name"]
    assert name.count("/") == 2
    plain = await client.get(f"/api/media/{name}", headers=READ)
    encoded = await client.get(f"/api/media/{name.replace('/', '%2F')}", headers=READ)
    for response in (plain, encoded):
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert hashlib.sha256(response.content).hexdigest() == created.json()["sha256"]


def test_the_openapi_document_describes_raw_bodies_and_media_answers() -> None:
    """The web client is generated from the OpenAPI document: the upload's raw body and the
    media types it serves are declared (OpenAPI 3.1: no schema, any bytes)."""
    document = app_for(make_services()).openapi()
    upload = document["paths"]["/api/media"]["post"]
    body = upload["requestBody"]
    assert body["required"] is True
    assert body["content"] == {"image/jpeg": {}, "image/png": {}, "image/webp": {}}
    read = document["paths"]["/api/media/{name}"]["get"]
    (parameter,) = read["parameters"]
    assert (parameter["name"], parameter["in"]) == ("name", "path")
    assert "uploads/2026-11/" in parameter["description"]
    served = read["responses"]["200"]["content"]
    assert set(served) == {
        "application/octet-stream",
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
    }


async def test_unknown_stored_types_are_served_as_attachments(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await blobs_of(services).put(
        "media", "brand/page.html", b"<script>alert(1)</script>", content_type="text/html"
    )
    response = await client.get("/api/media/brand/page.html", headers=READ)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"


@pytest.mark.parametrize(
    "path",
    [
        "uploads/2026-11/missing.png",
        "secrets/a.png",
        "uploads",
        "Uploads/a.png",
        "uploads/%2e%2e/brand/a.png",
        "uploads//a.png",
        "brand/a.png%00",
        "brand/" + "a" * 300 + ".png",
    ],
)
async def test_names_outside_the_media_areas_or_with_dots_are_404(
    client: httpx.AsyncClient, services: FakeServices, path: str
) -> None:
    await blobs_of(services).put("media", "brand/a.png", png(), content_type="image/png")
    response = await client.get(f"/api/media/{path}", headers=READ)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def raw_get(app: FastAPI, path: str) -> int:
    """GET ``path`` exactly as written (httpx would normalize dot segments away)."""
    status = 0

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])

    headers = [(key.lower().encode(), value.encode()) for key, value in READ.items()]
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"writer.test"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("writer.test", 443),
    }
    await app(scope, receive, send)
    return status


@pytest.mark.parametrize(
    "name",
    ["uploads/../brand/a.png", "uploads/./a.png", "brand/../../a.png", "../brand/a.png"],
)
async def test_dot_segments_are_404_even_when_sent_verbatim(
    services: FakeServices, name: str
) -> None:
    await blobs_of(services).put("media", "brand/a.png", png(), content_type="image/png")
    app = app_for(services)
    assert await raw_get(app, "/api/media/brand/a.png") == 200
    assert await raw_get(app, f"/api/media/{name}") == 404


def test_is_media_name() -> None:
    assert is_media_name("uploads/2026-11/" + "a" * 64 + ".png")
    assert is_media_name("posts/post-1/x.pdf")
    for name in ("uploads/../x.png", "brand/./x", "brand//x", "/brand/x", "brand", "other/x"):
        assert not is_media_name(name)


async def test_reading_media_needs_the_owner(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/media/brand/a.png")
    assert response.status_code == 401


# --------------------------------------------------------------------------------------------
# Storage off
# --------------------------------------------------------------------------------------------


async def test_without_storage_both_routes_answer_503() -> None:
    services = make_services(blobs=None)
    async with asgi_client(app_for(services)) as http:
        upload = await http.post("/api/media", content=png(), headers=write())
        read = await http.get("/api/media/brand/a.png", headers=READ)
    for response in (upload, read):
        assert response.status_code == 503
        assert response.json()["code"] == "storage_unavailable"
        assert response.headers["retry-after"] == "5"
