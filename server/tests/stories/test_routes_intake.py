"""``POST /api/stories/intake/redact`` and ``/check``."""

from __future__ import annotations

import httpx

from blogwriter.ai.types import Task
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner, make_app, make_services
from blogwriter.stories import StoriesSettings, get_router
from blogwriter.stories.testing import save_policies

WRITE = {**principal_headers(OWNER_OID), **core_write_headers(make_settings())}
RAW = "Contoso asked me to move 2,137 apps; mail avery.t@fabrikam-bank.com."


async def test_redact_returns_findings_and_a_proposal(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, "A client asked me to move 2,000+ apps.")
    response = await client.post(
        "/api/stories/intake/redact",
        json={"text": RAW, "purpose": "interview_answer", "pillar": "identity"},
        headers=WRITE,
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["proposal"] == "A client asked me to move 2,000+ apps."
    assert body["reason"] is None
    assert body["anonymizerUsed"] is True
    spans = {(f["code"], RAW[f["start"] : f["end"]]) for f in body["findings"]}
    assert ("blocklisted-term", "Contoso") in spans
    assert ("email-address", "avery.t@fabrikam-bank.com") in spans
    assert "Contoso" not in str([f["message"] for f in body["findings"]])


async def test_redact_in_strict_mode_answers_without_a_proposal() -> None:
    services = make_services(ai=ScriptedModelRunner(allow_private=False))
    await save_policies(services.store)
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        response = await http.post(
            "/api/stories/intake/redact", json={"text": RAW, "purpose": "story"}, headers=WRITE
        )
    assert response.status_code == 200
    assert response.json()["reason"] == "strict_mode"
    assert response.json()["proposal"] is None


async def test_redact_refuses_text_over_the_limit_without_echoing_it(
    services: FakeServices,
) -> None:
    small = StoriesSettings(intake_max_chars=200, seed_max_chars=1000)
    app = make_app(services, routers=[get_router(small)], settings=make_settings())
    text = "Contoso " + "x" * 300
    async with asgi_client(app) as http:
        response = await http.post(
            "/api/stories/intake/redact", json={"text": text, "purpose": "story"}, headers=WRITE
        )
        hard = await http.post(
            "/api/stories/intake/redact",
            json={"text": "Contoso " * 3000, "purpose": "story"},
            headers=WRITE,
        )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert response.json()["errors"][0]["loc"] == ["body", "text"]
    assert hard.status_code == 422
    for answer in (response, hard):
        assert "Contoso" not in answer.text
    assert services.ai is not None
    assert services.ai.calls == []


async def test_redact_refuses_unknown_fields(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/stories/intake/redact",
        json={"text": "hello", "purpose": "story", "extra": 1},
        headers=WRITE,
    )
    assert response.status_code == 422


async def test_check_reports_local_findings_only(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    flagged = await client.post(
        "/api/stories/intake/check", json={"text": "Ping @avery_t about 2,137 apps."}, headers=WRITE
    )
    clean = await client.post(
        "/api/stories/intake/check",
        json={"text": "I moved 2,000+ apps to Microsoft Entra ID."},
        headers=WRITE,
    )
    assert flagged.status_code == 200
    assert flagged.json()["ok"] is False
    assert [f["code"] for f in flagged.json()["findings"]] == ["handle", "exact-figure"]
    assert clean.json() == {"ok": True, "findings": []}
    assert services.ai is not None
    assert services.ai.calls == []


async def test_warnings_alone_keep_the_check_ok(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/stories/intake/check", json={"text": "We moved 2,137 apps."}, headers=WRITE
    )
    assert response.json()["ok"] is True
    assert response.json()["findings"][0]["severity"] == "warn"


async def test_intake_needs_the_owner_and_csrf(client: httpx.AsyncClient) -> None:
    anonymous = await client.post("/api/stories/intake/check", json={"text": "hi"})
    no_csrf = await client.post(
        "/api/stories/intake/check", json={"text": "hi"}, headers=principal_headers(OWNER_OID)
    )
    assert anonymous.status_code == 401
    assert no_csrf.status_code == 403
