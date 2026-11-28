"""The reply and comment lint: a positive and a negative case per rule, goldens and the route."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.common import Finding, from_linkedin_finding
from blogwriter.domain.confidentiality import BLOCKLIST_CODE, BLOCKLIST_NOT_CONFIGURED_CODE
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.engagement import EngagementItem
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist, EmployerPolicy
from blogwriter.domain.testing import FakeServices, FakeSlopChecker, sample_document
from blogwriter.engagement import is_substantive, lint_reply
from blogwriter.engagement.lint import (
    COPIED_RUN_BLOCK_WORDS,
    COPIED_RUN_WARN_WORDS,
    MAX_COMMENT_CHARS,
    ai_tell_findings,
    bait_findings,
    blocking_findings,
    copied_runs,
    length_findings,
    link_findings,
    opener_findings,
    overlap_share,
    restating_findings,
    slop_findings,
)
from blogwriter.engagement.testing import TEST_TERMS
from blogwriter.linkedin.lint import ai_tell_findings as linkedin_ai_tell_findings
from blogwriter.linkedin.lint import lint_post

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
GOLDENS = Path(__file__).resolve().parent / "goldens"
SOURCE = (
    "We rolled out per-tool tokens for our agents last quarter and the hardest part was "
    "convincing app owners that five minute lifetimes would not break their batch jobs."
)

PARAPHRASE = (
    "We rolled out per tool tokens for agents last quarter; the hardest part was convincing "
    "owners five minute lifetimes would not break batch jobs."
)


def blocklist(*, configured: bool = True) -> Blocklist:
    return sample_document(Blocklist, terms=list(TEST_TERMS), configured=configured)


def codes(findings: list[Finding]) -> list[tuple[str, str, int | None, int | None]]:
    return [(f.code, f.severity, f.start, f.end) for f in findings]


class BrokenSlop:
    def check(self, text: str) -> list[Finding]:
        raise RuntimeError(f"detector bug on {text}")


# ---------------------------------------------------------------------------------------------
# Links (the same patterns as linkedin.lint)
# ---------------------------------------------------------------------------------------------

LINK_TEXT = (
    "See https://example.com/a?b=1 or www.example.org, mail a.b+c@example.co.uk, read README.md "
    "and x.ai. Not links: e.g. U.S. 3.14, v2.1, 10.30am, config.json, Microsoft Entra ID."
)


def test_link_findings_match_linkedin_lint() -> None:
    linkedin = [
        (finding.code, finding.start, finding.end)
        for finding in lint_post(LINK_TEXT, approved_hashtags=[])
        if finding.code in {"external-link", "email-address", "domain-like"}
    ]
    ours = link_findings(LINK_TEXT)
    assert [(f.code, f.start, f.end) for f in ours] == linkedin
    assert [f.code for f in ours] == [
        "external-link",
        "external-link",
        "email-address",
        "domain-like",
        "domain-like",
    ]
    assert all(f.severity == "block" for f in ours)
    assert all("example" not in f.message for f in ours)


def test_plain_prose_has_no_link_findings() -> None:
    text = "We moved 2,000+ apps in 3.5 months; v2.1 of the runbook is what I'd share, e.g. step 4."
    assert link_findings(text) == []


# ---------------------------------------------------------------------------------------------
# AI tells (linkedin.lint's recipe)
# ---------------------------------------------------------------------------------------------

AI_TELL_TEXT = (
    "Let\u2019s dive in: we leverage robust, seamless tooling. It\u2019s worth noting the "
    "landscape changed. A game-changer? A paradigm shift, and we utilized it. Unleveraged x."
)


@pytest.mark.parametrize("text", [AI_TELL_TEXT, "TL;DR we harness it.", "Plain words only."])
def test_ai_tells_equal_linkedin_lint(text: str) -> None:
    expected = [
        from_linkedin_finding(finding)
        for finding in lint_post(text, approved_hashtags=[])
        if finding.code == "ai-tell"
    ]
    assert ai_tell_findings(text) == expected


@pytest.mark.parametrize("text", [AI_TELL_TEXT, "  tldr: it slipped.", "bad\x00text, robust."])
def test_ai_tells_are_the_linkedin_lint_function(text: str) -> None:
    """Contract I-9: one implementation, ``linkedin.lint.ai_tell_findings`` (it also checks text
    that is not valid display text)."""
    expected = [from_linkedin_finding(finding) for finding in linkedin_ai_tell_findings(text)]
    assert ai_tell_findings(text) == expected
    assert expected


def test_ai_tells_are_warnings_with_spans() -> None:
    found = ai_tell_findings(AI_TELL_TEXT)
    assert len(found) == 9
    assert {f.severity for f in found} == {"warn"}
    first = found[0]
    assert AI_TELL_TEXT[first.start or 0 : first.end] == "Let\u2019s dive in"
    assert not any(AI_TELL_TEXT[f.start or 0 : f.end] == "Unleveraged" for f in found)


# ---------------------------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------------------------


def test_over_1000_characters_warns_on_the_overflow() -> None:
    assert length_findings("x" * MAX_COMMENT_CHARS) == []
    [finding] = length_findings("x" * (MAX_COMMENT_CHARS + 7))
    assert (finding.code, finding.severity) == ("too-long", "warn")
    assert (finding.start, finding.end) == (MAX_COMMENT_CHARS, MAX_COMMENT_CHARS + 7)
    assert "1,007 characters" in finding.message


# ---------------------------------------------------------------------------------------------
# Restating
# ---------------------------------------------------------------------------------------------


def test_a_long_copied_run_blocks_and_a_shorter_one_warns() -> None:
    copied = "the hardest part was convincing app owners that five minute lifetimes would not"
    assert len(copied.split()) == COPIED_RUN_BLOCK_WORDS + 1
    draft = f"Agreed, {copied.upper()} matter for us too."
    [finding] = restating_findings(draft, [SOURCE])
    assert finding.severity == "block"
    assert draft[finding.start or 0 : finding.end] == copied.upper()
    assert f"Repeats {COPIED_RUN_BLOCK_WORDS + 1} words" in finding.message
    short = "convincing app owners that five minute lifetimes would"
    assert len(short.split()) == COPIED_RUN_WARN_WORDS
    [warned] = restating_findings(f"Yes: {short} hold. We used two.", [SOURCE])
    assert warned.severity == "warn"
    seven = "app owners that five minute lifetimes would"
    longer = (
        f"Funny how {seven} scare people until a canary run per job proves otherwise in a week."
    )
    assert restating_findings(longer, [SOURCE]) == []


def test_punctuation_and_case_do_not_hide_a_copy() -> None:
    draft = "We Rolled-Out PER TOOL tokens, for our agents; last quarter. And the hardest part!"
    assert copied_runs(draft, [SOURCE]) == [(0, len(draft) - 1, 15)]


def test_a_paraphrase_that_mostly_repeats_the_post_warns() -> None:
    draft = PARAPHRASE
    share = overlap_share(draft, [SOURCE])
    assert share is not None
    assert share >= 0.5
    [finding] = restating_findings(draft, [SOURCE])
    assert (finding.severity, finding.start, finding.end) == ("warn", None, None)
    assert "Mostly restates" in finding.message


def test_a_reply_that_adds_something_is_clean() -> None:
    draft = (
        "We hit the batch problem too: a nightly export ran 40 minutes, so we gave that job "
        "its own service principal with a one-hour token and alerts on renewal failures."
    )
    assert restating_findings(draft, [SOURCE]) == []
    assert overlap_share("Too short to judge.", [SOURCE]) is None
    assert restating_findings(SOURCE, []) == []
    assert restating_findings(SOURCE, ["   "]) == []


def test_the_owners_own_post_is_a_source_too() -> None:
    post = "Five lessons from moving two thousand apps off Okta in one year with a tiny team"
    draft = f"{post}, again."
    assert restating_findings(draft, [SOURCE]) == []
    [finding] = restating_findings(draft, [SOURCE, post])
    assert finding.severity == "block"


# ---------------------------------------------------------------------------------------------
# Openers and bait
# ---------------------------------------------------------------------------------------------

GENERIC = [
    "Great post! We did the same.",
    "great question, and the answer is scope.",
    "Thanks for sharing this.",
    "Thank you so much for sharing.",
    "Love this. Tokens per tool.",
    "Well said, the batch jobs bite.",
    "Spot on: five minutes.",
    "So true.",
    "Couldn\u2019t agree more with the lifetimes.",
    "Totally agree.",
    "This is gold.",
    "Very insightful.",
    "Insightful post, thanks.",
    "Absolutely. We saw it too.",
    "100% this.",
    "This. Exactly this.",
    "\U0001f44f Great insights here.",
]
SPECIFIC = [
    "Greatly reduced blast radius once we split the tokens.",
    "This approach broke our nightly export for a week.",
    "Absolutely not what we saw: lifetimes were fine.",
    "Thanks to a two-hour freeze window, we rotated everything at once.",
    "Love-hate relationship with short lifetimes here.",
    "1000 apps later, the batch jobs were the only problem.",
]


@pytest.mark.parametrize("draft", GENERIC)
def test_generic_openers_warn(draft: str) -> None:
    [finding] = opener_findings(draft)
    assert (finding.code, finding.severity, finding.start) == ("generic-opener", "warn", 0)


@pytest.mark.parametrize("draft", SPECIFIC)
def test_specific_openers_pass(draft: str) -> None:
    assert opener_findings(draft) == []


def test_engagement_bait_blocks() -> None:
    [finding] = bait_findings("We split tokens per tool. Agree?")
    assert (finding.code, finding.severity) == ("engagement-bait", "block")
    assert bait_findings("Follow for more on agent identity.")[0].severity == "block"
    assert bait_findings("Do you agree with the five minute default for batch jobs?") == []


# ---------------------------------------------------------------------------------------------
# Blocklist, slop and the whole lint
# ---------------------------------------------------------------------------------------------


def test_the_blocklist_blocks_with_term_ids_only() -> None:
    draft = "We did this for Contoso and the Falcon rollout."
    found = [
        f
        for f in lint_reply(draft, sources=[], blocklist=blocklist(), slop=None)
        if f.code == BLOCKLIST_CODE
    ]
    assert [(f.ref, draft[f.start or 0 : f.end]) for f in found] == [
        ("t-1", "Contoso"),
        ("t-2", "Falcon"),
    ]
    assert all(f.severity == "block" and not f.overridable for f in found)
    assert all("Contoso" not in f.message and "Falcon" not in f.message for f in found)


def test_an_unsaved_blocklist_fails_closed() -> None:
    clean = "We gave the batch job its own principal."
    found = lint_reply(clean, sources=[], blocklist=blocklist(configured=False), slop=None)
    [hard] = [f for f in found if f.severity == "block"]
    assert hard.code == BLOCKLIST_NOT_CONFIGURED_CODE
    assert not hard.overridable
    assert blocking_findings(clean, sources=[], blocklist=blocklist(configured=False))


def test_slop_findings_pass_through_and_a_missing_checker_is_reported() -> None:
    rule = Finding(code="rule-of-three", severity="warn", message="A triad.", start=0, end=4)
    assert slop_findings("Fast, cheap, safe.", FakeSlopChecker([rule])) == [rule]
    [missing] = slop_findings("text", None)
    assert (missing.code, missing.severity) == ("slop-unavailable", "warn")


def test_a_failing_slop_checker_is_reported_and_logs_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    [finding] = slop_findings("secret draft words", BrokenSlop())
    assert finding.code == "slop-unavailable"
    assert "secret draft words" not in caplog.text
    assert any(record.getMessage() == "engagement_slop_failed" for record in caplog.records)


def test_findings_are_sorted_and_not_duplicated() -> None:
    draft = "Great post! We leverage tokens."
    duplicate = Finding(code="ai-tell", severity="warn", message="An AI tell.", start=15, end=23)
    found = lint_reply(draft, sources=[], blocklist=blocklist(), slop=FakeSlopChecker([duplicate]))
    assert codes(found) == [
        ("generic-opener", "warn", 0, 10),
        ("ai-tell", "warn", 15, 23),
    ]


def test_substantive_comments() -> None:
    assert not is_substantive("Per tool tokens, five minutes, no exceptions at all.", sources=[])
    twelve = "Per tool tokens with five minute lifetimes and alerts on every failed renewal."
    assert is_substantive(twelve, sources=[SOURCE])
    assert not is_substantive(f"{SOURCE} Indeed.", sources=[SOURCE])
    assert not is_substantive(PARAPHRASE, sources=[SOURCE])
    quoting = (
        "When you say five minute lifetimes would not break their batch jobs, how did you prove "
        "it? We needed a canary run per job before owners believed us."
    )
    assert is_substantive(quoting, sources=[SOURCE])


def golden_cases() -> list[dict[str, Any]]:
    data = json.loads((GOLDENS / "lint.json").read_text(encoding="utf-8"))
    return cast(list[dict[str, Any]], data)


@pytest.mark.parametrize("case", golden_cases(), ids=lambda case: str(case["name"]))
def test_lint_goldens(case: dict[str, Any]) -> None:
    found = lint_reply(
        str(case["draft"]),
        sources=[str(source) for source in case["sources"]],
        blocklist=blocklist(),
        slop=FakeSlopChecker(),
    )
    assert [[f.code, f.severity, f.start, f.end] for f in found] == case["findings"]


# ---------------------------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------------------------


async def create_item(client: httpx.AsyncClient, **changes: Any) -> dict[str, Any]:
    body = {"itemKind": "comment", "pastedText": SOURCE, **changes}
    response = await client.post("/api/engagement/items", json=body, headers=WRITE)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def test_the_route_lints_against_the_pasted_text_and_stores_nothing(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await create_item(client)
    item_id = payload["item"]["id"]
    draft = f"  {SOURCE}"
    response = await client.post(
        f"/api/engagement/items/{item_id}/lint", json={"draftText": draft}, headers=WRITE
    )
    assert response.status_code == 200, response.text
    findings = response.json()["findings"]
    restating = [f for f in findings if f["code"] == "restates-post"]
    assert restating[0]["severity"] == "block"
    assert restating[0]["start"] == 2
    stored = await read_doc(services.store, EngagementItem, item_id)
    assert stored.etag == payload["etag"]
    assert stored.doc.draft_text is None
    assert services.ai is not None
    assert services.ai.calls == []


async def test_a_reply_is_also_checked_against_the_owners_post(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    post_text = "Five lessons from moving two thousand apps off Okta in one year with a tiny team"
    post = sample_document(Post, display_text=post_text)
    await create_doc(services.store, post)
    payload = await create_item(client, itemKind="reply", postId=post.id)
    response = await client.post(
        f"/api/engagement/items/{payload['item']['id']}/lint",
        json={"draftText": f"{post_text}!"},
        headers=WRITE,
    )
    assert any(f["code"] == "restates-post" for f in response.json()["findings"])


async def test_the_route_reports_slop_and_a_missing_checker(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await create_item(client)
    url = f"/api/engagement/items/{payload['item']['id']}/lint"
    services.slop = FakeSlopChecker(
        [Finding(code="flat-rhythm", severity="warn", message="Flat rhythm.")]
    )
    with_slop = await client.post(url, json={"draftText": "Short reply."}, headers=WRITE)
    assert [f["code"] for f in with_slop.json()["findings"]] == ["flat-rhythm"]
    services.slop = None
    without = await client.post(url, json={"draftText": "Short reply."}, headers=WRITE)
    assert [f["code"] for f in without.json()["findings"]] == ["slop-unavailable"]


async def test_the_route_validates_the_draft(client: httpx.AsyncClient) -> None:
    payload = await create_item(client)
    url = f"/api/engagement/items/{payload['item']['id']}/lint"
    for body in ({"draftText": "   "}, {"draftText": "x" * 1251}, {"draftText": ""}, {}):
        response = await client.post(url, json=body, headers=WRITE)
        assert response.status_code == 422, body
    long_ok = await client.post(url, json={"draftText": "x" * 1250}, headers=WRITE)
    assert long_ok.status_code == 200
    assert "too-long" in [f["code"] for f in long_ok.json()["findings"]]


def test_a_forbidden_topic_blocks_a_reply_draft() -> None:
    policy = sample_document(EmployerPolicy, forbidden_topics=["pending acquisition"])
    draft = "Agreed. Our pending acquisition taught us to scope tokens per tool."
    findings = lint_reply(
        draft, sources=[SOURCE], blocklist=blocklist(), slop=FakeSlopChecker(), policy=policy
    )
    forbidden = [f for f in findings if f.code == "forbidden-topic"]
    assert [(f.severity, f.ref) for f in forbidden] == [("block", "topic-1")]
    assert draft[forbidden[0].start or 0 : forbidden[0].end] == "pending acquisition"
    without = lint_reply(draft, sources=[SOURCE], blocklist=blocklist(), slop=FakeSlopChecker())
    assert all(f.code != "forbidden-topic" for f in without)
