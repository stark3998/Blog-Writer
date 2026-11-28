"""The brand kit: ``validate_brand`` through ``RenderBrand`` (WCAG AA pairs, plain text, media)
and ``PUT /api/kb/brand`` (422 ``brand_contrast`` naming the failing pairs, uploads checked)."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.common import MediaRef, blocking
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.settings import BrandColors, BrandKit, EmployerPolicy
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, png_bytes, sample_document
from blogwriter.kb import validate_brand
from blogwriter.render import BrandColors as RenderColors
from blogwriter.render import contrast_ratio

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CREATE = {**WRITE, "If-None-Match": "*"}
NAMED = BrandKit.default(SAMPLE_NOW).model_copy(update={"author_name": "A. Author"})


def brand_with(**fields: Any) -> BrandKit:
    return BrandKit.model_validate({**NAMED.model_dump(by_alias=False), **fields})


def test_contrast_failures_name_the_pairs() -> None:
    colors = BrandColors(muted="#AAB2AE", signal="#1C3A33")
    findings = validate_brand(brand_with(colors=colors), disclaimer=None)
    refs = {f.ref for f in findings if f.code == "brand-contrast"}
    assert refs == {"muted:paper", "signal:ink", "ink:signal"}
    for finding in findings:
        assert finding.severity == "block"
        assert "WCAG AA" in finding.message


def test_pairs_follow_the_render_package() -> None:
    """kb checks exactly the pairs the templates use, with render's own ratio."""
    defaults = BrandColors()
    pairs = RenderColors().text_pairs()
    assert len(pairs) == 7
    for foreground, background in pairs.values():
        assert contrast_ratio(foreground, background) >= 4.5
    assert validate_brand(brand_with(colors=defaults), disclaimer=None) == []


def test_text_must_render_as_plain_text() -> None:
    brand = brand_with(author_name="Name\x07", author_headline="Headline \ue000", site_name="ok")
    findings = validate_brand(brand, disclaimer="\u200b")
    assert {f.ref for f in findings} == {"authorName", "authorHeadline", "disclaimerShort"}
    assert {f.code for f in findings} == {"brand-text"}
    assert all("\x07" not in f.message and "\ue000" not in f.message for f in findings)


def test_incomplete_brand_is_a_warning() -> None:
    findings = validate_brand(BrandKit.default(SAMPLE_NOW), disclaimer=None)
    assert [(f.code, f.severity, f.ref) for f in findings] == [
        ("brand-incomplete", "warn", "authorName")
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"container": "backups"},
        {"name": "uploads/../backups/x.png"},
        {"name": "posts/post-1/x.png"},
        {"contentType": "application/pdf"},
        {"role": "carousel_pdf"},
    ],
)
def test_media_must_be_an_uploaded_image(change: dict[str, str]) -> None:
    ref = MediaRef.model_validate(
        {
            "role": "headshot",
            "container": "media",
            "name": "uploads/2026-11/" + "a" * 64 + ".png",
            "sha256": "a" * 64,
            "contentType": "image/png",
            "sizeBytes": 10,
            **change,
        }
    )
    findings = validate_brand(brand_with(headshot=ref), disclaimer=None)
    assert [(f.code, f.ref) for f in blocking(findings)] == [("brand-media", "headshot")]


# --------------------------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------------------------


async def brand_body(client: httpx.AsyncClient) -> tuple[dict[str, Any], str | None]:
    body = (await client.get("/api/kb/brand", headers=READ)).json()
    item = body["item"]
    keys = ("authorName", "authorHeadline", "siteName", "colors", "font", "headshot", "nameMark")
    return {**{key: item[key] for key in keys}, "artBriefs": item["artBriefs"]}, body["etag"]


async def upload(services: FakeServices) -> dict[str, Any]:
    assert services.blobs is not None
    data = png_bytes(64, 64)
    sha = hashlib.sha256(data).hexdigest()
    name = f"uploads/2026-11/{sha}.png"
    await services.blobs.put("media", name, data, content_type="image/png")
    return {
        "role": "upload",
        "container": "media",
        "name": name,
        "sha256": sha,
        "contentType": "image/png",
        "sizeBytes": len(data),
        "width": 64,
        "height": 64,
    }


async def test_low_contrast_is_refused_with_findings(client: httpx.AsyncClient) -> None:
    body, _ = await brand_body(client)
    body["authorName"] = "A. Author"
    body["colors"] = {**body["colors"], "muted": "#AAB2AE"}
    response = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "brand_contrast"
    assert [f["ref"] for f in problem["findings"]] == ["muted:paper"]
    assert (await client.get("/api/kb/brand", headers=READ)).json()["etag"] is None


async def test_unrenderable_text_is_refused(client: httpx.AsyncClient) -> None:
    body, _ = await brand_body(client)
    body["authorName"] = "Name\x07"
    response = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "brand_invalid"
    assert [f["ref"] for f in response.json()["findings"]] == ["authorName"]


async def test_art_briefs_are_per_pillar(client: httpx.AsyncClient) -> None:
    body, _ = await brand_body(client)
    body["artBriefs"] = {"agents": "Circuit lines, muted green.", "unknown": "x"}
    response = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "artBriefs"]
    body["artBriefs"] = {"agents": "Circuit lines, muted green.", "identity": "  "}
    saved = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert saved.status_code == 200
    assert saved.json()["item"]["artBriefs"] == {"agents": "Circuit lines, muted green."}


async def test_uploaded_headshot_is_accepted(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    body, _ = await brand_body(client)
    body.update(authorName="  A. Author  ", authorHeadline=" ", headshot=await upload(services))
    response = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["authorName"] == "A. Author"
    assert item["authorHeadline"] is None
    assert item["headshot"]["role"] == "headshot"

    # Unchanged references are not checked again (storage may be down meanwhile).
    services.blobs = None
    again, etag = await brand_body(client)
    resaved = await client.put(
        "/api/kb/brand", headers={**WRITE, "If-Match": str(etag)}, json=again
    )
    assert resaved.status_code == 200


async def test_headshot_must_exist_in_storage(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    body, _ = await brand_body(client)
    ref = await upload(services)
    body["headshot"] = {**ref, "sha256": "b" * 64}
    response = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "brand_invalid"
    assert response.json()["findings"][0]["ref"] == "headshot"

    services.blobs = None
    body["headshot"] = ref
    unavailable = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "storage_unavailable"


async def test_a_bad_stored_disclaimer_does_not_block_the_brand(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    # Singletons take camelCase overrides (``sample_document`` dumps the default by alias).
    policy = sample_document(EmployerPolicy, disclaimerShort="Views\x07", configured=True)
    assert policy.disclaimer_short == "Views\x07"
    await create_doc(services.store, policy)
    body, _ = await brand_body(client)
    body["authorName"] = "A. Author"
    assert (await client.put("/api/kb/brand", headers=CREATE, json=body)).status_code == 200


async def test_only_known_colour_tokens_are_stored(client: httpx.AsyncClient) -> None:
    body, _ = await brand_body(client)
    body["colors"] = {**body["colors"], "glow": "#FFFFFF"}
    saved = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert saved.status_code == 200
    assert "glow" not in saved.json()["item"]["colors"]
    tokens = {field.alias for field in BrandColors.model_fields.values()}
    assert set(saved.json()["item"]["colors"]) == tokens
    assert len(tokens) == len(RenderColors.model_fields)
