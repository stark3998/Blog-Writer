"""Tests for blogwriter.core.spa: deep-link fallback, cache headers and safe file lookup."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from blogwriter.core.app import create_app
from blogwriter.core.spa import ASSET_CACHE_CONTROL, DOCUMENT_CACHE_CONTROL, SpaFiles
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)

OWNER = principal_headers(OWNER_OID)
INDEX = "<!doctype html><title>Blog Writer</title><script type=module src=/assets/index-BXd1.js>"
SCRIPT = "console.log('app')"


@pytest.fixture
def spa_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "index-BXd1Fq7a.js").write_text(SCRIPT)
    (root / "assets" / "index-Ck2pQ9zW.css").write_text("body{margin:0}")
    (root / "manifest.webmanifest").write_text('{"name":"Blog Writer"}')
    (root / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (root / "data.bin").write_bytes(b"\x00\x01")
    (root / ".env").write_text("SECRET=dotfile")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("SECRET=dotdir")
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET outside the SPA directory")
    (root / "linked.txt").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path)
    return root


@pytest.fixture
def app(spa_dir: Path) -> FastAPI:
    return create_app(make_settings(spa_dir=spa_dir))


def test_index_contains_only_regular_visible_files(spa_dir: Path) -> None:
    assert SpaFiles(spa_dir).paths == {
        "index.html",
        "assets/index-BXd1Fq7a.js",
        "assets/index-Ck2pQ9zW.css",
        "manifest.webmanifest",
        "sw.js",
        "favicon.svg",
        "data.bin",
    }


async def test_root_serves_index_without_caching(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        response = await client.get("/", headers=OWNER)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == DOCUMENT_CACHE_CONTROL == "no-cache"
    assert response.headers["etag"].startswith('"')


@pytest.mark.parametrize("path", ["/studio/42", "/calendar", "/library/voice/edit", "/index.html"])
async def test_deep_links_fall_back_to_index(app: FastAPI, path: str) -> None:
    async with asgi_client(app) as client:
        response = await client.get(path, headers=OWNER)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


async def test_hashed_assets_are_immutable(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        script = await client.get("/assets/index-BXd1Fq7a.js", headers=OWNER)
        style = await client.get("/assets/index-Ck2pQ9zW.css", headers=OWNER)
    assert script.status_code == style.status_code == 200
    assert script.text == SCRIPT
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    assert style.headers["content-type"] == "text/css; charset=utf-8"
    for response in (script, style):
        assert response.headers["cache-control"] == ASSET_CACHE_CONTROL
        assert "immutable" in response.headers["cache-control"]


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/manifest.webmanifest", "application/manifest+json"),
        ("/sw.js", "text/javascript; charset=utf-8"),
        ("/favicon.svg", "image/svg+xml"),
        ("/data.bin", "application/octet-stream"),
    ],
)
async def test_other_files_revalidate(app: FastAPI, path: str, content_type: str) -> None:
    async with asgi_client(app) as client:
        response = await client.get(path, headers=OWNER)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.headers["cache-control"] == "no-cache"


async def test_missing_hashed_asset_is_404_not_index(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        response = await client.get("/assets/index-OLDHASH1.js", headers=OWNER)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("path", ["/api/unknown", "/api", "/api/me/extra"])
async def test_unknown_api_paths_are_404_not_index(app: FastAPI, path: str) -> None:
    async with asgi_client(app) as client:
        response = await client.get(path, headers=OWNER)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_etag_revalidation_returns_304(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        first = await client.get("/", headers=OWNER)
        etag = first.headers["etag"]
        again = await client.get("/", headers={**OWNER, "If-None-Match": etag})
        weak = await client.get("/studio/1", headers={**OWNER, "If-None-Match": f"W/{etag}"})
        other = await client.get("/", headers={**OWNER, "If-None-Match": '"nope"'})
    assert again.status_code == weak.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag
    assert again.headers["cache-control"] == "no-cache"
    assert other.status_code == 200


async def test_head_returns_headers_only(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        response = await client.head("/", headers=OWNER)
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(INDEX.encode()))


@pytest.mark.parametrize(
    "raw_path",
    [
        "/%2e%2e/outside.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
        "/linked.txt",
        "/linked-dir/outside.txt",
        "/.env",
        "/.git/config",
        "//etc/passwd",
    ],
)
async def test_files_outside_the_index_are_never_served(app: FastAPI, raw_path: str) -> None:
    async with asgi_client(app) as client:
        response = await client.get(raw_path, headers=OWNER)
    assert "SECRET" not in response.text
    assert response.status_code in (200, 404)


async def test_spa_requires_authentication(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        index = await client.get("/")
        asset = await client.get("/assets/index-BXd1Fq7a.js")
    assert index.status_code == asset.status_code == 401


async def test_spa_paths_only_allow_get_and_head(spa_dir: Path) -> None:
    settings = make_settings(spa_dir=spa_dir)
    app = create_app(settings)
    async with asgi_client(app) as client:
        response = await client.post("/studio/1", headers=write_headers(settings))
    assert response.status_code == 405


def test_spa_dir_must_exist_and_hold_index_html(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SpaFiles(tmp_path / "missing")
    with pytest.raises(FileNotFoundError, match=r"index\.html"):
        SpaFiles(tmp_path)
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(NotADirectoryError):
        SpaFiles(file_path)


def test_create_app_fails_fast_on_a_bad_spa_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        create_app(make_settings(spa_dir=tmp_path / "missing"))


async def test_without_spa_dir_non_api_paths_are_404() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/studio/1", headers=OWNER)
    assert response.status_code == 404
