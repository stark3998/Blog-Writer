"""Quick search: one public ``web_search`` call, stored as citations, claims and a short answer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx
import pytest

from blogwriter.ai.ledger import Origin
from blogwriter.ai.providers import ProviderError
from blogwriter.ai.router import RouterEvent, RouterResult
from blogwriter.ai.types import Citation as SearchCitation
from blogwriter.ai.types import Request, Sensitivity, Task, WebSearchTool
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.research import QuickSearch
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner, ScriptedReply, make_app
from blogwriter.research import get_router, quick_search
from blogwriter.research.claims import citation_id_for
from blogwriter.research.settings import research_settings
from blogwriter.research.testing import (
    BLOCKED_TERM,
    page_fetcher,
    research_services,
    save_blocklist,
    store_dump,
)

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
NIST = "https://pages.nist.gov/800-63-4/sp800-63b.html"
OWASP = "https://genai.owasp.org/llmrisk/llm062025-excessive-agency/"
ANSWER = (
    "SUMMARY: NIST SP 800-63-4 makes verifiers at AAL2 offer a phishing-resistant option "
    "([pages.nist.gov](https://pages.nist.gov/800-63-4/sp800-63b.html?utm_source=openai)).\n"
    "- Verifiers at AAL2 must offer at least one phishing-resistant authenticator. "
    f"[{NIST}?utm_source=openai]\n"
    "- OWASP lists excessive agency as risk LLM06 in its 2025 list. "
    f"[{OWASP}]\n"
    "- A claim citing a page the search never returned is dropped. "
    "[https://made-up.example.com/page]"
)


def nist_reply(text: str = ANSWER, **overrides: Any) -> ScriptedReply:
    fields: dict[str, Any] = {
        "text": text,
        "citations": (
            SearchCitation(url=f"{NIST}?utm_source=openai", title="NIST SP 800-63B"),
            SearchCitation(url=OWASP, title="LLM06:2025 Excessive Agency"),
        ),
        "search_sources": (NIST, OWASP),
        "search_queries": ("NIST 800-63-4 AAL2 phishing resistant",),
    }
    fields.update(overrides)
    return ScriptedReply(**fields)


@pytest.fixture
def services() -> FakeServices:
    return research_services(fetcher=page_fetcher())


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    async with asgi_client(make_app(services, [get_router()], settings=SETTINGS)) as http:
        yield http


def body(response: httpx.Response) -> dict[str, Any]:
    data: dict[str, Any] = response.json()
    return data


async def test_quick_search_stores_an_answer_claims_and_citations(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    ai = services.ai
    assert isinstance(ai, ScriptedModelRunner)
    ai.add(Task.QUICK_SEARCH, nist_reply())

    response = await client.post(
        "/api/research/quick",
        json={
            "query": "What does SP 800-63-4 require at AAL2?",
            "pillar": "identity",
            "articleId": "art-1",
        },
        headers=WRITE,
    )

    assert response.status_code == 201, response.text
    assert response.headers["etag"] == body(response)["etag"]
    item = body(response)["item"]
    nist_id, owasp_id = citation_id_for(NIST), citation_id_for(OWASP)
    assert item["answer"] == (
        "NIST SP 800-63-4 makes verifiers at AAL2 offer a phishing-resistant option. "
        f"[^{nist_id}][^{owasp_id}]"
    )
    assert [(claim["text"], claim["citationId"]) for claim in item["claims"]] == [
        ("Verifiers at AAL2 must offer at least one phishing-resistant authenticator.", nist_id),
        ("OWASP lists excessive agency as risk LLM06 in its 2025 list.", owasp_id),
    ]
    assert {claim["status"] for claim in item["claims"]} == {"unverified"}
    citations = {citation["id"]: citation for citation in item["citations"]}
    assert set(citations) == {nist_id, owasp_id}
    assert citations[nist_id]["url"] == NIST  # tracking parameters removed
    assert citations[nist_id]["allowlisted"] is True  # pages.nist.gov is on the identity allowlist
    assert citations[owasp_id]["allowlisted"] is False
    assert item["queries"] == [{"text": "NIST 800-63-4 AAL2 phishing resistant", "phase": "quick"}]
    assert item["servedBy"][0]["step"] == "quick_search"
    assert item["servedBy"][0]["promptVersion"] == "quick.v1"
    assert item["articleId"] == "art-1"
    assert item["pillar"] == "identity"
    assert "made-up.example.com" not in response.text

    (call,) = ai.calls_for(Task.QUICK_SEARCH)
    assert call.origin == "owner"
    assert call.request.sensitivity is Sensitivity.PUBLIC
    assert not call.request.background
    (tool,) = call.request.tools
    assert isinstance(tool, WebSearchTool)
    assert "pages.nist.gov" in tool.allowed_domains
    assert call.request.max_tool_calls == 4
    # Quick search reads no page: nothing was fetched, no page text exists to store.
    assert services.fetcher is not None
    assert services.fetcher.fetched == []


async def test_quick_search_get_and_list(client: httpx.AsyncClient, services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    ai = services.ai
    assert isinstance(ai, ScriptedModelRunner)
    ai.add(Task.QUICK_SEARCH, nist_reply(), nist_reply())
    first = await quick_search(services, "Query one", pillar=None, origin="owner", idea_id="idea-1")
    services.clock.advance(1)
    second = await quick_search(
        services, "Query two", pillar=None, origin="owner", idea_id="idea-1"
    )

    one = await client.get(f"/api/research/quick/{first.doc.id}", headers=READ)
    assert one.status_code == 200
    assert body(one)["item"]["query"] == "Query one"
    page = body(await client.get("/api/research/quick?ideaId=idea-1&limit=1", headers=READ))
    assert [item["item"]["id"] for item in page["items"]] == [second.doc.id]
    rest = body(
        await client.get(
            f"/api/research/quick?ideaId=idea-1&cursor={page['nextCursor']}", headers=READ
        )
    )
    assert [item["item"]["id"] for item in rest["items"]] == [first.doc.id]
    assert body(await client.get("/api/research/quick?postId=post-x", headers=READ))["items"] == []
    assert (await client.get("/api/research/quick/qs-missing", headers=READ)).status_code == 404


async def test_open_search_without_a_pillar(services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    ai = services.ai
    assert isinstance(ai, ScriptedModelRunner)
    ai.add(Task.QUICK_SEARCH, nist_reply())
    await quick_search(services, "Agent identity limits", pillar="no-such-pillar", origin="owner")
    (tool,) = ai.calls_for(Task.QUICK_SEARCH)[0].request.tools
    assert isinstance(tool, WebSearchTool)
    assert tool.allowed_domains == ()


async def test_the_query_passes_the_blocklist_first(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    unsaved = await client.post(
        "/api/research/quick", json={"query": "Agent identity"}, headers=WRITE
    )
    assert unsaved.status_code == 409
    assert body(unsaved)["code"] == "blocklist_not_configured"
    await save_blocklist(services.store, BLOCKED_TERM)
    hit = await client.post(
        "/api/research/quick", json={"query": f"{BLOCKED_TERM} breach details"}, headers=WRITE
    )
    assert hit.status_code == 422
    assert body(hit)["code"] == "confidential_text"
    assert BLOCKED_TERM not in hit.text
    assert services.ai is not None
    assert services.ai.calls == []


async def test_blocklisted_results_are_dropped_and_a_leaked_query_alerts(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await save_blocklist(services.store, BLOCKED_TERM)
    ai = services.ai
    assert isinstance(ai, ScriptedModelRunner)
    ai.add(
        Task.QUICK_SEARCH,
        nist_reply(
            text=(
                f"SUMMARY: {BLOCKED_TERM} adopted phishing-resistant MFA first.\n"
                f"- {BLOCKED_TERM} cut phishing incidents by half after the change. [{NIST}]\n"
                f"- OWASP lists excessive agency as risk LLM06 in its 2025 list. [{OWASP}]"
            ),
            search_queries=(f"{BLOCKED_TERM} phishing resistant MFA", "AAL2 phishing resistant"),
        ),
    )
    stored = await quick_search(
        services, "Phishing-resistant MFA adoption", pillar=None, origin="owner"
    )

    assert stored.doc.answer == ""
    assert [claim.text for claim in stored.doc.claims] == [
        "OWASP lists excessive agency as risk LLM06 in its 2025 list."
    ]
    assert [query.text for query in stored.doc.queries] == ["AAL2 phishing resistant"]
    assert BLOCKED_TERM not in await store_dump(services.store)
    alerts = [r for r in caplog.records if r.getMessage() == "alert_event"]
    assert [(r.__dict__["alert_kind"], r.__dict__["ref_id"]) for r in alerts] == [
        ("quarantined", stored.doc.id)
    ]
    for record in caplog.records:
        assert BLOCKED_TERM not in record.getMessage() + repr(record.__dict__)


async def test_a_refused_quick_search_is_stored_empty(services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    ai = services.ai
    assert isinstance(ai, ScriptedModelRunner)
    ai.add(Task.QUICK_SEARCH, ScriptedReply(refused=True))
    stored = await quick_search(services, "Agent identity limits", pillar="agents", origin="owner")
    assert stored.doc.answer == ""
    assert stored.doc.claims == []
    assert stored.doc.citations == []
    assert stored.doc.served_by[0].refused
    assert (await read_doc(services.store, QuickSearch, stored.doc.id)).doc == stored.doc


async def test_quick_search_without_a_model_is_a_503(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    services.ai = None
    response = await client.post(
        "/api/research/quick", json={"query": "Agent identity"}, headers=WRITE
    )
    assert response.status_code == 503
    assert body(response)["code"] == "ai_unavailable"


async def test_a_failing_model_is_a_503(client: httpx.AsyncClient, services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    ai = services.ai
    assert isinstance(ai, ScriptedModelRunner)
    failure = ProviderError("all models failed", provider="openai", retryable=True)
    ai.add(Task.QUICK_SEARCH, ScriptedReply(error=failure))
    response = await client.post(
        "/api/research/quick", json={"query": "Agent identity"}, headers=WRITE
    )
    assert response.status_code == 503
    assert body(response)["code"] == "ai_unavailable"
    assert response.headers["retry-after"] == "5"


class SlowModel:
    async def run(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> RouterResult:
        await asyncio.sleep(5)
        raise AssertionError("not reached")  # pragma: no cover

    async def stream(
        self, task: Task, request: Request, *, origin: Origin, job_id: str | None = None
    ) -> AsyncGenerator[RouterEvent]:  # pragma: no cover - unused
        raise AssertionError("not used")
        yield


async def test_a_slow_quick_search_is_a_504(
    client: httpx.AsyncClient, services: FakeServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BW_RESEARCH_QUICK_TIMEOUT_SECONDS", "0.05")
    research_settings.cache_clear()
    await save_blocklist(services.store, BLOCKED_TERM)
    services.ai = SlowModel()  # type: ignore[assignment]
    response = await client.post(
        "/api/research/quick", json={"query": "Agent identity"}, headers=WRITE
    )
    assert response.status_code == 504
    assert body(response)["code"] == "timeout"
