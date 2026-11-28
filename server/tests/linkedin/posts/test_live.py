"""Live check (``BW_LIVE_AI=1`` plus the ``BW_AI_*`` endpoints, ``az login``): one native post
drafted on the real DRAFT route and its derived claims checked on the real FACT_CHECK route,
through the production router.

Synthetic data only; nothing leaves the in-memory store and ledger. Skipped by default.
"""

from __future__ import annotations

import os

import pytest
from azure.identity.aio import AzureCliCredential

from blogwriter.ai import AIRouter, AISettings, MemoryLedger, create_providers
from blogwriter.ai.providers.auth import entra_token_provider
from blogwriter.domain.docstore import upsert_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import FakeClock, make_services, sample_document
from blogwriter.linkedin.littletext import api_commentary, parse_display
from blogwriter.linkedin.posts.checks import check_post
from blogwriter.linkedin.posts.derive import draft_post, load_generation_context, save_draft
from blogwriter.linkedin.posts.testing import T0, seed_settings, usable_pov, usable_story

LIVE = os.environ.get("BW_LIVE_AI") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="live AI checks need BW_LIVE_AI=1")

STORY_ID = "story-0193f00000000000000000000099"
POV_ID = "pov-0193f00000000000000000000099"
LINK_CODES = {"external-link", "domain-like", "email-address"}


async def test_live_native_post_and_claims() -> None:
    settings = AISettings()
    assert settings.configured, "set the BW_AI_* endpoints for the live check"
    credential = AzureCliCredential()
    router = AIRouter.from_settings(
        settings,
        create_providers(settings, entra_token_provider(credential)),
        ledger=MemoryLedger(),
    )
    services = make_services(clock=FakeClock(T0), ai=router)
    try:
        await seed_settings(services)
        await usable_story(services, STORY_ID)
        await usable_pov(services, POV_ID)
        post = sample_document(
            Post,
            status=PostStatus.GENERATING,
            lane="native",
            pillar="identity",
            format="text",
            story_ids=[STORY_ID],
            pov_ids=[POV_ID],
        )
        await upsert_doc(services.store, post)
        context = await load_generation_context(services.store, now=T0)
        changes = await draft_post(services, post, context, origin="owner", job_id=None)
        saved = await save_draft(services.store, post.id, changes, now=T0)
        assert saved is not None
        assert len(saved.hook_variants) == 3
        assert all(len(variant.text) <= 400 for variant in saved.hook_variants)
        assert saved.payload == api_commentary(parse_display(saved.display_text, saved.hashtags))
        checked = await check_post(services, saved, origin="owner", job_id=None)
        assert checked.lint is not None
        blocking = {f.code for f in checked.lint.findings if f.severity == "block"}
        assert not blocking & LINK_CODES  # the default prompts produce zero links
        assert checked.derived_claims is not None
        assert checked.served_by[-1].step == "derived_claims"
    finally:
        await router.aclose()
        await credential.close()
