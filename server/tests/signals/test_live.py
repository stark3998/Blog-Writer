"""Live check (``BW_LIVE_AI=1`` plus the ``BW_AI_*`` endpoints, ``az login``): one TRIAGE batch on
the real ``gpt-6-luna`` route and one scouting QUICK_SEARCH, through the production router.

Public synthetic data only; nothing is stored (in-memory ledger). Skipped by default.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from azure.identity.aio import AzureCliCredential

from blogwriter.ai import AIRouter, AISettings, MemoryLedger, create_providers
from blogwriter.ai.providers.auth import entra_token_provider
from blogwriter.ai.types import Task
from blogwriter.domain.base import check_https_url, to_iso
from blogwriter.domain.settings import Blocklist, FormatPacks, PillarSettings
from blogwriter.signals.candidates import Candidate, scout_partition
from blogwriter.signals.scout import ScoutSearch, scout_request
from blogwriter.signals.testing import T0
from blogwriter.signals.triage import TriageContext, TriageOutput, triage_request

LIVE = os.environ.get("BW_LIVE_AI") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="live AI checks need BW_LIVE_AI=1")


def context() -> TriageContext:
    stamp = to_iso(T0)
    pillars = PillarSettings.default(stamp)
    packs = FormatPacks.default(stamp)
    blocklist = Blocklist.default(stamp).model_copy(update={"configured": True})
    return TriageContext(
        blocklist=blocklist,
        pillars=tuple({"id": p.id, "name": p.name, "topics": p.topics} for p in pillars.pillars),
        pillar_ids=frozenset(p.id for p in pillars.pillars),
        flagship_formats=tuple({"id": p.id, "name": p.name} for p in packs.packs),
        flagship_ids=frozenset(p.id for p in packs.packs),
        inbox=(),
        quick_take_hours=72,
    )


async def test_live_triage_and_scout() -> None:
    settings = AISettings()
    assert settings.configured, "set the BW_AI_* endpoints for the live check"
    credential = AzureCliCredential()
    router = AIRouter.from_settings(
        settings,
        create_providers(settings, entra_token_provider(credential)),
        ledger=MemoryLedger(),
    )
    try:
        items = [
            Candidate(
                url="https://modelcontextprotocol.io/specification/draft/basic/authorization",
                title="MCP authorization spec adds resource indicators for tokens",
                publisher="modelcontextprotocol.io",
                published_at=to_iso(T0 - timedelta(hours=5)),
                partition=scout_partition("agents"),
                pillar="agents",
            ),
            Candidate(
                url="https://example.com/funding",
                title="Startup raises a Series B for AI-powered marketing",
                publisher="example.com",
                published_at=to_iso(T0 - timedelta(hours=2)),
                partition=scout_partition("agents"),
            ),
        ]
        result = await router.run(
            Task.TRIAGE, triage_request(context(), items, now=T0), origin="owner"
        )
        assert not result.refused
        verdicts = {verdict.item: verdict for verdict in result.output(TriageOutput).verdicts}
        assert set(verdicts) == {1, 2}
        assert verdicts[1].relevance > verdicts[2].relevance

        pillar = PillarSettings.default(to_iso(T0)).pillars[0]
        search = ScoutSearch(
            key=f"pillar:{pillar.id}",
            topic=f"{pillar.name}: {pillar.topics}",
            allowed_domains=tuple(pillar.research_domains),
            partition=scout_partition(pillar.id),
            pillar=pillar.id,
        )
        found = await router.run(Task.QUICK_SEARCH, scout_request(search, now=T0), origin="owner")
        for citation in found.completion.citations:
            check_https_url(citation.url)
    finally:
        await router.aclose()
        await credential.close()
