"""``signals.scout``: weekly QUICK_SEARCH per pillar and per scouting query, then triage."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from blogwriter.ai.types import Citation, Sensitivity, Task, WebSearchTool
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import find_docs, read_doc
from blogwriter.domain.jobtypes import SIGNALS_SCOUT
from blogwriter.domain.settings import PillarSettings
from blogwriter.domain.signals import Idea, Signal, Source
from blogwriter.domain.testing import FakeServices, ScriptedReply
from blogwriter.signals.candidates import scout_partition
from blogwriter.signals.prompts import SCOUT_PROMPT, load_prompt
from blogwriter.signals.scout import MAX_CITATIONS_PER_SEARCH, SCOUT_MAX_TOOL_CALLS, citation_items
from blogwriter.signals.settings import SignalsSettings
from blogwriter.signals.testing import T0, SignalsSeed, run_signals_job, triage_output, verdict

PILLARS = PillarSettings.default(to_iso(T0)).pillars


def search_reply(*urls: str, queries: tuple[str, ...] = ()) -> ScriptedReply:
    return ScriptedReply(
        text="A short list of developments.",
        citations=tuple(
            Citation(url=url, title=f"Title for {url.rsplit('/', 1)[-1]}") for url in urls
        ),
        search_queries=queries,
    )


async def scout_ideas(services: FakeServices) -> list[Idea]:
    return [s.doc for s in await find_docs(services.store, Idea)]


async def test_scouting_files_scout_ideas(
    services: FakeServices, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    query = (await seed.scout("workload identity federation limits", pillar="identity")).doc
    assert services.ai is not None
    services.ai.add(
        Task.QUICK_SEARCH,
        search_reply("https://modelcontextprotocol.io/spec/auth", queries=("mcp auth changes",)),
        search_reply("https://learn.microsoft.com/entra/workload-ids"),
        search_reply(),  # governance: nothing new this week
        search_reply("https://learn.microsoft.com/entra/workload-ids", "https://openid.net/wif"),
    )
    services.ai.add(
        Task.TRIAGE, triage_output(verdict(1), verdict(2, pillar="identity"), verdict(3, 0.2))
    )
    job = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert job.status == "succeeded", job.error

    calls = services.ai.calls_for(Task.QUICK_SEARCH)
    assert len(calls) == 4
    for call, pillar in zip(calls, PILLARS, strict=False):
        request = call.request
        (tool,) = request.tools
        assert isinstance(tool, WebSearchTool)
        assert tool.allowed_domains == tuple(pillar.research_domains)
        assert request.max_tool_calls == SCOUT_MAX_TOOL_CALLS
        assert request.sensitivity is Sensitivity.PUBLIC
        assert request.system == load_prompt(SCOUT_PROMPT)
        assert pillar.name in request.messages[0].text
        assert call.origin == "background"
    last = calls[3].request
    assert "workload identity federation limits" in last.messages[0].text
    assert last.tools[0] == WebSearchTool(allowed_domains=tuple(PILLARS[1].research_domains))

    (triage,) = services.ai.calls_for(Task.TRIAGE)
    sent = triage.request.messages[0].text.split("<items>", 1)[1]
    assert sent.count("learn.microsoft.com/entra/workload-ids") == 1  # one URL, two searches

    filed = {idea.url: idea for idea in await scout_ideas(services)}
    assert set(filed) == {
        "https://modelcontextprotocol.io/spec/auth",
        "https://learn.microsoft.com/entra/workload-ids",
    }
    spec = filed["https://modelcontextprotocol.io/spec/auth"]
    assert spec.origin == "scout"
    assert spec.source_id is None
    assert spec.publisher == "modelcontextprotocol.io"
    agents_signals = await find_docs(
        services.store, Signal, partition_key=scout_partition("agents")
    )
    assert [s.doc.idea_id for s in agents_signals] == [spec.id]
    query_signals = await find_docs(services.store, Signal, partition_key=query.id)
    assert len(query_signals) == 2
    touched = (await read_doc(services.store, Source, query.id)).doc
    assert touched.last_success_at == to_iso(T0)
    assert touched.items_seen == 2


async def test_a_blocklisted_scout_query_is_never_searched(
    services: FakeServices, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist("Contoso")
    await seed.scout("Contoso tenant migration lessons")
    assert services.ai is not None
    services.ai.add(Task.QUICK_SEARCH, search_reply(), search_reply(), search_reply())
    job = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert job.status == "succeeded"
    calls = services.ai.calls_for(Task.QUICK_SEARCH)
    assert len(calls) == 3
    assert all("contoso" not in call.request.messages[0].text.casefold() for call in calls)
    search = job.step("search")
    assert search is not None
    assert isinstance(search.output, dict)
    assert search.output["blocked"] == 1


async def test_a_model_query_hitting_the_blocklist_drops_its_results(
    services: FakeServices,
    seed: SignalsSeed,
    settings: SignalsSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed.blocklist("Contoso")
    assert services.ai is not None
    services.ai.add(
        Task.QUICK_SEARCH,
        search_reply("https://example.org/leak", queries=("contoso incident",)),
        search_reply("https://learn.microsoft.com/entra/ok"),
        ScriptedReply(refused=True),
    )
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    with caplog.at_level(logging.INFO):
        job = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert job.status == "succeeded"
    assert [idea.url for idea in await scout_ideas(services)] == [
        "https://learn.microsoft.com/entra/ok"
    ]
    alerts = [r for r in caplog.records if r.getMessage() == "alert_event"]
    assert [getattr(r, "alert_kind", None) for r in alerts] == ["quarantined"]
    assert getattr(alerts[0], "ref_id", None) == job.id
    search = job.step("search")
    assert search is not None
    assert isinstance(search.output, dict)
    assert (search.output["blocked"], search.output["refused"]) == (1, 1)
    assert "contoso" not in caplog.text.casefold()


async def test_scouting_dedupes_across_weeks(
    services: FakeServices, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    assert services.ai is not None
    for _ in range(2):
        services.ai.add(
            Task.QUICK_SEARCH,
            search_reply("https://genai.owasp.org/llm-top-10"),
            search_reply(),
            search_reply(),
        )
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    services.clock.advance(timedelta(days=7))
    second = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert second.status == "succeeded"
    assert len(services.ai.calls_for(Task.TRIAGE)) == 1
    assert len(await scout_ideas(services)) == 1


async def test_paused_scouting_is_skipped(
    services: FakeServices, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    await seed.paused()
    job = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert job.status == "succeeded"
    assert job.result == {"skipped": "paused"}
    assert services.ai is not None
    assert services.ai.calls == []


async def test_scouting_fails_closed(
    services: FakeServices, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    job = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_not_configured"
    await seed.blocklist("nist.gov")  # a research allowlist entry matches
    blocked = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert blocked.error is not None
    assert blocked.error.code == "blocklist_hit"
    assert services.ai is not None
    assert services.ai.calls == []


def test_citations_become_https_items() -> None:
    citations = (
        Citation(url="http://insecure.example.com/a", title="Insecure"),
        Citation(url="https://www.example.com/b", title=None),
        Citation(url="https://www.example.com/c", title="  A <i>clean</i>   title "),
        *(
            Citation(url=f"https://many.example.com/{n}", title=f"Item {n}")
            for n in range(MAX_CITATIONS_PER_SEARCH + 5)
        ),
    )
    items = citation_items(citations)
    assert items[0].url == "https://www.example.com/c"
    assert items[0].title == "A clean title"
    assert items[0].publisher == "example.com"
    assert items[0].published_at is None
    assert len(items) == MAX_CITATIONS_PER_SEARCH
