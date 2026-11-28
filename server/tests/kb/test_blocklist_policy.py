"""The blocklist (server-assigned ``t-<n>`` ids, ``configured`` on any save, terms that never
leave the process) and the employer policy (``configured``, the AI-use confirmation time)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.testing import FakeServices

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CREATE = {**WRITE, "If-None-Match": "*"}
MARKER = "Zorblax Industries"
"""A made-up client name: it must never show up outside the blocklist routes."""
SUBSTRING = "Quuxcorp"


def headers(etag: str | None) -> dict[str, str]:
    return CREATE if etag is None else {**WRITE, "If-Match": etag}


async def save_terms(
    client: httpx.AsyncClient, terms: list[dict[str, Any]], etag: str | None
) -> httpx.Response:
    return await client.put("/api/kb/blocklist", headers=headers(etag), json={"terms": terms})


async def item(client: httpx.AsyncClient, path: str) -> tuple[dict[str, Any], str | None]:
    body = (await client.get(f"/api/kb/{path}", headers=READ)).json()
    return body["item"], body["etag"]


def editable(document: dict[str, Any]) -> dict[str, Any]:
    server = {"id", "schemaVersion", "createdAt", "updatedAt", "configured", "aiUseConfirmedAt"}
    return {key: value for key, value in document.items() if key not in server}


async def test_the_server_assigns_term_ids(client: httpx.AsyncClient) -> None:
    first = await save_terms(
        client,
        [
            {"term": f"  {MARKER} "},
            {"term": SUBSTRING, "category": "project", "match": "substring"},
        ],
        None,
    )
    assert first.status_code == 200
    saved = first.json()["item"]
    assert saved["configured"] is True
    assert [(t["id"], t["term"], t["category"], t["match"]) for t in saved["terms"]] == [
        ("t-1", MARKER, "client", "word"),
        ("t-2", SUBSTRING, "project", "substring"),
    ]
    # Known ids stay with their terms; new terms (and unknown ids) get numbers above them.
    second = await save_terms(
        client,
        [{"id": "t-2", "term": f"{SUBSTRING} Labs"}, {"term": "Fizzbuzz"}],
        first.headers["etag"],
    )
    assert [t["id"] for t in second.json()["item"]["terms"]] == ["t-2", "t-3"]
    third = await save_terms(
        client,
        [{"id": "t-3", "term": "Fizzbuzz"}, {"id": "t-99", "term": "Wobble"}],
        second.headers["etag"],
    )
    assert [t["id"] for t in third.json()["item"]["terms"]] == ["t-3", "t-4"]


async def test_an_empty_save_configures_the_blocklist(client: httpx.AsyncClient) -> None:
    response = await save_terms(client, [], None)
    assert response.status_code == 200
    assert response.json()["item"] == {**response.json()["item"], "terms": [], "configured": True}
    inputs = (await client.get("/api/kb/inputs", headers=READ)).json()["items"]
    assert next(i for i in inputs if i["code"] == "blocklist")["done"] is True


@pytest.mark.parametrize(
    ("terms", "loc"),
    [
        ([{"term": MARKER}, {"term": MARKER.upper()}], ["body", "terms", 1, "term"]),
        ([{"term": MARKER * 6}], ["body", "terms", 0, "term"]),
        ([{"term": " Z "}], ["body", "terms", 0, "term"]),
    ],
)
async def test_bad_terms_are_refused_without_echo(
    client: httpx.AsyncClient, terms: list[dict[str, Any]], loc: list[Any]
) -> None:
    response = await save_terms(client, terms, None)
    assert response.status_code == 422
    assert loc in [error["loc"] for error in response.json()["errors"]]
    assert "Zorblax" not in response.text


async def test_a_known_id_cannot_be_used_twice(client: httpx.AsyncClient) -> None:
    first = await save_terms(client, [{"term": "Fizzbuzz"}], None)
    response = await save_terms(
        client,
        [{"id": "t-1", "term": "Fizzbuzz"}, {"id": "t-1", "term": "Wobble"}],
        first.headers["etag"],
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "terms", 1, "id"]


async def test_settings_text_is_screened_once_the_blocklist_is_saved(
    client: httpx.AsyncClient,
) -> None:
    settings, _ = await item(client, "pillars")
    pillars = settings["pillars"]
    pillars[0]["description"] = f"Lessons from {MARKER}."
    before = await client.put("/api/kb/pillars", headers=CREATE, json={"pillars": pillars})
    assert before.status_code == 200  # nothing to screen against yet

    await save_terms(client, [{"term": MARKER}], None)
    settings, etag = await item(client, "pillars")
    pillars = settings["pillars"]
    refused = await client.put("/api/kb/pillars", headers=headers(etag), json={"pillars": pillars})
    assert refused.status_code == 422
    problem = refused.json()
    assert problem["code"] == "confidential_text"
    assert [(f["field"], f["ref"], f["start"]) for f in problem["findings"]] == [
        ("pillars.0.description", "t-1", len("Lessons from "))
    ]


async def test_terms_never_leave_through_logs_problems_or_other_responses(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    saved = await save_terms(
        client, [{"term": MARKER}, {"term": SUBSTRING, "match": "substring"}], None
    )
    assert saved.status_code == 200
    etag = saved.headers["etag"]

    # Problems on the blocklist route itself: 422, 412, 428.
    seen.append((await save_terms(client, [{"term": MARKER}, {"term": MARKER.lower()}], etag)).text)
    seen.append((await save_terms(client, [{"term": MARKER}], '"stale"')).text)
    no_precondition = {"terms": [{"term": MARKER}]}
    seen.append((await client.put("/api/kb/blocklist", headers=WRITE, json=no_precondition)).text)

    # Settings that mention a term are refused by field and term id.
    settings, _ = await item(client, "pillars")
    pillars = settings["pillars"]
    pillars[1]["audience"] = f"Teams like {MARKER}"
    seen.append(
        (await client.put("/api/kb/pillars", headers=CREATE, json={"pillars": pillars})).text
    )
    packs, _ = await item(client, "formats")
    packs["packs"][0]["name"] = f"{SUBSTRING.lower()}-style case study"
    seen.append((await client.put("/api/kb/formats", headers=CREATE, json=editable(packs))).text)
    lanes, _ = await item(client, "lanes")
    lanes["series"] = [{"id": "notes", "pillar": "agents", "title": f"{MARKER} notes"}]
    seen.append((await client.put("/api/kb/lanes", headers=CREATE, json=editable(lanes))).text)
    brand, _ = await item(client, "brand")
    brand["authorHeadline"] = f"Architect at {MARKER}"
    seen.append((await client.put("/api/kb/brand", headers=CREATE, json=editable(brand))).text)
    policy, _ = await item(client, "policy")
    policy["forbiddenTopics"] = [f"{MARKER} pricing"]
    seen.append((await client.put("/api/kb/policy", headers=CREATE, json=editable(policy))).text)

    # Every other kb response.
    for path in ("pillars", "formats", "lanes", "brand", "policy", "cadence", "models"):
        seen.append((await client.get(f"/api/kb/{path}", headers=READ)).text)
    seen.append((await client.get("/api/kb/autonomy", headers=READ)).text)
    seen.append((await client.post("/api/kb/autonomy/pause", headers=WRITE)).text)
    seen.append((await client.get("/api/kb/inputs", headers=READ)).text)

    assert sum('"confidential_text"' in text for text in seen) == 5
    for text in seen:
        assert "zorblax" not in text.casefold()
        assert SUBSTRING.casefold() not in text.casefold()
    for record in caplog.records:
        dumped = f"{record.getMessage()} {record.__dict__}".casefold()
        assert "zorblax" not in dumped
        assert SUBSTRING.casefold() not in dumped


# --------------------------------------------------------------------------------------------
# Employer policy
# --------------------------------------------------------------------------------------------


async def test_policy_save_marks_configured_and_stamps_ai_use(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    policy, etag = await item(client, "policy")
    assert policy["configured"] is False
    body = {
        **editable(policy),
        "disclaimerShort": "  Views are my own. ",
        "disclaimerLong": " ",
        "forbiddenTopics": [" Unreleased products ", "unreleased products", ""],
    }
    first = await client.put("/api/kb/policy", headers=headers(etag), json=body)
    assert first.status_code == 200
    saved = first.json()["item"]
    assert saved["configured"] is True
    assert saved["disclaimerShort"] == "Views are my own."
    assert saved["disclaimerLong"] is None
    assert saved["forbiddenTopics"] == ["Unreleased products"]
    assert saved["aiUseConfirmed"] is False
    assert saved["aiUseConfirmedAt"] is None

    confirmed = await client.put(
        "/api/kb/policy",
        headers=headers(first.headers["etag"]),
        json={**body, "aiUseConfirmed": True},
    )
    stamp = confirmed.json()["item"]["aiUseConfirmedAt"]
    assert stamp == "2026-11-26T12:00:00.000000Z"

    services.clock.advance(3600)
    kept = await client.put(
        "/api/kb/policy",
        headers=headers(confirmed.headers["etag"]),
        json={**body, "aiUseConfirmed": True},
    )
    assert kept.json()["item"]["aiUseConfirmedAt"] == stamp
    assert kept.json()["item"]["updatedAt"] == "2026-11-26T13:00:00.000000Z"

    withdrawn = await client.put("/api/kb/policy", headers=headers(kept.headers["etag"]), json=body)
    assert withdrawn.json()["item"]["aiUseConfirmedAt"] is None
    inputs = (await client.get("/api/kb/inputs", headers=READ)).json()["items"]
    done = {i["code"]: i["done"] for i in inputs}
    assert (done["employer_policy"], done["ai_use_confirmed"]) == (True, False)


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        ({"disclaimerShort": "Views\x07"}, ["body", "disclaimerShort"]),
        ({"disclaimerShort": "x" * 41}, ["body", "disclaimerShort"]),
        ({"clearanceStatement": "short"}, ["body", "clearanceStatement"]),
    ],
)
async def test_policy_values_out_of_range(
    client: httpx.AsyncClient, change: dict[str, str], loc: list[str]
) -> None:
    policy, _ = await item(client, "policy")
    response = await client.put(
        "/api/kb/policy", headers=CREATE, json={**editable(policy), **change}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == loc
