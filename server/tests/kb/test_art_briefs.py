"""Art briefs for the opt-in illustrations: per pillar, editable through ``PUT /api/kb/brand``;
a pillar without one uses the default (editorial, abstract, in the brand palette, no text),
which the never-saved brand kit shows. A brief names no colour value (the current palette
travels next to it), so a default saved with the kit never goes stale. ``imagesEnabled`` is the
owner's switch."""

from __future__ import annotations

import re
from typing import Any

import httpx

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.settings import BrandColors, BrandKit, Pillar
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.kb import (
    PLAN_PILLARS,
    art_brief_for,
    default_art_brief,
    default_art_briefs,
    kb_default,
)

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
CREATE = {**WRITE, "If-None-Match": "*"}


def test_the_default_brief_is_editorial_on_brand_and_text_free() -> None:
    colors = BrandColors()
    for pillar in PLAN_PILLARS:
        brief = default_art_brief(pillar)
        assert len(brief) <= 500
        assert brief.startswith("Editorial illustration, abstract")
        assert pillar.name in brief
        assert "Colours from the brand palette only" in brief
        assert "No text, letters, numbers, logos, brand marks or recognisable people." in brief
        # No colour value and no colour name: the current palette goes with it (by hex value).
        assert re.search(r"#[0-9A-Fa-f]{3,8}", brief) is None
        for token in (colors.ink, colors.paper, colors.muted_on_ink, colors.signal, "teal"):
            assert token not in brief
    long_name = Pillar(id="long", name="A" * 80)
    assert len(default_art_brief(long_name)) <= 500


def test_the_owner_brief_wins_and_an_empty_one_falls_back() -> None:
    pillar = PLAN_PILLARS[0]
    brand = BrandKit.default(SAMPLE_NOW).model_copy(
        update={"art_briefs": {pillar.id: "  Ink drawings of locks and keys.  "}}
    )
    assert art_brief_for(brand, pillar) == "Ink drawings of locks and keys."
    other = PLAN_PILLARS[1]
    assert art_brief_for(brand, other) == default_art_brief(other)
    blank = brand.model_copy(update={"art_briefs": {pillar.id: "   "}})
    assert art_brief_for(blank, pillar) == default_art_brief(pillar)


def test_the_never_saved_brand_kit_carries_the_default_briefs() -> None:
    brand = kb_default(BrandKit, SAMPLE_NOW)
    assert brand.art_briefs == default_art_briefs(PLAN_PILLARS)
    assert set(brand.art_briefs) == {pillar.id for pillar in PLAN_PILLARS}
    assert BrandKit.model_validate(brand.model_dump()) == brand  # every brief fits the model


async def test_briefs_and_the_images_switch_are_edited_through_kb(
    client: httpx.AsyncClient,
) -> None:
    shown = (await client.get("/api/kb/brand", headers=READ)).json()
    assert shown["etag"] is None
    defaults: dict[str, str] = shown["item"]["artBriefs"]
    assert set(defaults) == {"agents", "identity", "governance"}
    keys = ("authorName", "authorHeadline", "siteName", "colors", "font", "headshot", "nameMark")
    body: dict[str, Any] = {key: shown["item"][key] for key in keys}
    body["authorName"] = "A. Author"
    body["artBriefs"] = {**defaults, "agents": "Ink drawings of locks and keys.", "identity": " "}
    saved = await client.put("/api/kb/brand", headers=CREATE, json=body)
    assert saved.status_code == 200, saved.text
    briefs = saved.json()["item"]["artBriefs"]
    assert briefs == {
        "agents": "Ink drawings of locks and keys.",
        "governance": defaults["governance"],
    }
    stored = BrandKit.model_validate(saved.json()["item"])
    identity = next(pillar for pillar in PLAN_PILLARS if pillar.id == "identity")
    assert art_brief_for(stored, identity) == default_art_brief(identity)

    models = (await client.get("/api/kb/models", headers=READ)).json()
    assert models["preferences"]["item"]["imagesEnabled"] is False


async def test_the_models_view_lists_per_image_prices(client: httpx.AsyncClient) -> None:
    view = (await client.get("/api/kb/models", headers=READ)).json()
    prices = {price["model"]: price for price in view["prices"]}
    assert prices["gpt-image-2"]["imageUsd"] == {"1536x1024": 0.25, "1024x1024": 0.17}
    assert "imageUsd" not in prices["claude-opus-5-5"]  # text models list token prices only
