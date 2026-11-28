"""Live check (``BW_LIVE_AI=1`` plus the ``BW_AI_*`` endpoints, ``az login``): one ENGAGEMENT
angles call and one screenshot extraction on the real ``gpt-6.2-sol`` route, through the
production router.

Synthetic data only; nothing leaves the in-memory store and ledger. Skipped by default.
"""

from __future__ import annotations

import io
import os

import pytest
from azure.identity.aio import AzureCliCredential
from PIL import Image, ImageDraw, ImageFont

from blogwriter.ai import AIRouter, AISettings, MemoryLedger, create_providers
from blogwriter.ai.providers.auth import entra_token_provider
from blogwriter.db import MemoryStore
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.testing import FakeClock
from blogwriter.engagement import create_item, extract_screenshot, generate_angles
from blogwriter.engagement.lint import link_findings
from blogwriter.engagement.testing import save_policies, usable_pov, usable_story

LIVE = os.environ.get("BW_LIVE_AI") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="live AI checks need BW_LIVE_AI=1")

COMMENT = (
    "Interesting take on agent identity. How do you scope tokens when one MCP server fronts "
    "several downstream APIs? Per agent, per tool, or per API?"
)


def screenshot() -> bytes:
    image = Image.new("RGB", (900, 260), "#ffffff")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    draw.text((24, 20), "Commenter Name  \u00b7  2nd  \u00b7  3h", fill="#666666", font=font)
    draw.text((24, 80), "How do you scope agent tokens", fill="#111111", font=font)
    draw.text((24, 120), "when one MCP server fronts several APIs?", fill="#111111", font=font)
    draw.text((24, 200), "Like  \u00b7  Reply", fill="#666666", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def test_live_angles_and_screenshot() -> None:
    settings = AISettings()
    assert settings.configured, "set the BW_AI_* endpoints for the live check"
    credential = AzureCliCredential()
    router = AIRouter.from_settings(
        settings,
        create_providers(settings, entra_token_provider(credential)),
        ledger=MemoryLedger(),
    )
    store = MemoryStore()
    clock = FakeClock()
    await save_policies(store)
    story = usable_story(
        title="Per-tool tokens for agents behind one MCP server",
        problem="One broad token let any agent call every API the server fronted.",
        decisions="We minted a token per tool call, scoped to one API, with a 5-minute lifetime.",
        lessons="Revoking one tool stopped touching the others; batch jobs needed their own.",
        pillars=["agents"],
    )
    pov = usable_pov(
        pillar="agents", statement="Agents need their own identities, never borrowed user tokens."
    )
    await create_doc(store, story)
    await create_doc(store, pov)
    try:
        item = await create_item(store, item_kind="comment", pasted_text=COMMENT, now=clock.now())
        stored = await generate_angles(store, router, item.doc.id, now=clock.now())
        angles = stored.doc.angles
        assert 1 <= len(angles) <= 3
        for angle in angles:
            assert angle.grounded_in
            assert set(angle.grounded_in) <= {story.id, pov.id}
            assert not link_findings(angle.text)
        assert stored.doc.served_by[-1].prompt_version == "angles.v1"
        extracted = await extract_screenshot(
            store, router, screenshot(), content_type="image/png", now=clock.now()
        )
        assert "scope agent tokens" in extracted.text.casefold()
        assert "Commenter Name" not in extracted.text
    finally:
        await router.aclose()
        await credential.close()
