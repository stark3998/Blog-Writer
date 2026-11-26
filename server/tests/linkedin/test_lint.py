"""Tests for blogwriter.linkedin.lint (rules from the v2 plan's LinkedIn Studio > Lint)."""

from __future__ import annotations

import pytest

from blogwriter.linkedin.lint import (
    HOOK_MAX_CHARS,
    MAX_COMMENTARY_UNITS,
    Finding,
    Severity,
    fold_index,
    has_blocking,
    lint_post,
)

APPROVED = ["AISecurity", "IdentitySecurity", "AIGovernance", "MCP"]


def lint(text: str, *, allow_links: bool = False) -> list[Finding]:
    return lint_post(text, approved_hashtags=APPROVED, allow_links=allow_links)


def codes(text: str, *, allow_links: bool = False) -> set[str]:
    return {f.code for f in lint(text, allow_links=allow_links)}


def blocking_codes(text: str, *, allow_links: bool = False) -> set[str]:
    return {f.code for f in lint(text, allow_links=allow_links) if f.severity is Severity.BLOCK}


REALISTIC_POST = """We moved 2,000 apps from Okta to Entra ID in 14 months. The hard part: not SAML.

It was the 312 apps nobody owned.

Here is what we did, in order:

1. Built an app inventory from sign-in logs, not from the CMDB. 41% of the CMDB was stale.
2. Tagged every app with an owner before migration day. No owner, no migration slot.
3. Grouped apps by protocol: SAML first, then OIDC, then the header-based legacy ones.
4. Ran each wave in report-only Conditional Access for a week before enforcing.
5. Kept a rollback path per wave: the Okta app stayed active for 30 days after cutover.

What I would change next time:

We treated service accounts as an afterthought. That cost us three weekends.
On the next program I would inventory non-human identities in week one, rotate their secrets
during the migration window, and give each one an owner just like an app.

The other lesson: communications beat tooling. A two-line email to each app owner, sent
two weeks before their wave, removed more risk than any script we wrote.

If you are planning a similar move, start with ownership. The protocol work is the easy part.

#IdentitySecurity #AISecurity"""


# ----------------------------------------------------------------------------------------------
# Realistic post
# ----------------------------------------------------------------------------------------------


def test_realistic_post_has_no_blocking_findings() -> None:
    assert 1_000 < len(REALISTIC_POST) < 2_000
    findings = lint(REALISTIC_POST)
    assert not has_blocking(findings), findings


def test_realistic_post_has_no_findings_at_all() -> None:
    assert lint(REALISTIC_POST) == []


# ----------------------------------------------------------------------------------------------
# Length (escaped payload, UTF-16 units) and leading blanks
# ----------------------------------------------------------------------------------------------


def test_length_boundary_counts_escapes() -> None:
    # Each "_" is escaped as "\_": 1,500 of them is exactly 3,000 units.
    assert "too-long" not in codes("_" * (MAX_COMMENTARY_UNITS // 2))
    assert "too-long" in blocking_codes("_" * (MAX_COMMENTARY_UNITS // 2 + 1))


def test_length_boundary_plain_text() -> None:
    assert "too-long" not in codes("a" * MAX_COMMENTARY_UNITS)
    assert "too-long" in codes("a" * (MAX_COMMENTARY_UNITS + 1))


def test_length_counts_astral_emoji_as_two_units() -> None:
    text = "a" * (MAX_COMMENTARY_UNITS - 2) + "\U0001f680"
    assert "too-long" not in codes(text)
    assert "too-long" in codes("a" + text)


@pytest.mark.parametrize("text", ["\nHello", " Hello", "\t\tHello", "\n\nHello"])
def test_leading_blank_blocks(text: str) -> None:
    assert "leading-blank" in blocking_codes(text)


def test_no_leading_blank_for_normal_start() -> None:
    assert "leading-blank" not in codes("Hello\n\nworld")


def test_invalid_control_character_blocks() -> None:
    assert blocking_codes("bad\x00text") == {"invalid-text"}


# ----------------------------------------------------------------------------------------------
# Hook and fold
# ----------------------------------------------------------------------------------------------


def test_hook_exactly_at_limit_passes() -> None:
    hook = "a" * HOOK_MAX_CHARS
    assert "hook-past-fold" not in codes(hook + "\nbody")


def test_hook_one_past_limit_warns() -> None:
    hook = "a" * (HOOK_MAX_CHARS + 1)
    findings = [f for f in lint(hook + "\nbody") if f.code == "hook-past-fold"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARN
    assert findings[0].start == HOOK_MAX_CHARS


def test_blank_lines_count_toward_the_fold() -> None:
    text = "Short hook.\n\n\nRest"
    # Three newlines exhaust the three mobile lines, so the fold falls on the third newline.
    assert fold_index(text) == text.index("\n") + 2
    assert "hook-past-fold" not in codes(text)


def test_wrapped_lines_move_the_fold() -> None:
    assert fold_index("x" * 300) == HOOK_MAX_CHARS
    assert fold_index("ab") == 2


# ----------------------------------------------------------------------------------------------
# Links and domain-like words
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Read it at https://example.com/post", "external-link"),
        ("Docs: http://example.org", "external-link"),
        ("See www.example.com for details", "external-link"),
        ("Mail me@example.com any time", "email-address"),
        ("Check the README.md first", "domain-like"),
        ("Run setup.py before install", "domain-like"),
        ("Then run install.sh", "domain-like"),
        ("I tried x.ai last week", "domain-like"),
        ("Our tool foo.io helps", "domain-like"),
        ("Built on ASP.NET Core", "domain-like"),
        ("Grab the docs from learn.microsoft.com", "domain-like"),
    ],
)
def test_links_block(text: str, code: str) -> None:
    assert code in blocking_codes(text)


@pytest.mark.parametrize(
    "text",
    [
        "Controls, e.g. MFA, i.e. the basics.",
        "In the U.S. and the EU, etc.",
        "Upgrade to v1.2.3 or 2.0 before Friday.",
        "Pi is 3.14 and the call is at 10.30.",
        "We used Node.js and Next.js for the portal.",
        "OAuth 2.0 and OIDC 1.0 are not the same.",
        "Section 4.2.1 of SP 800-63-4 covers this.",
        "The ratio was 0.5x, then 1.5x.",
        "Score: 9.8 (critical).",
        "Ends with a period.Next sentence typo",
    ],
)
def test_prose_is_not_a_link(text: str) -> None:
    found = codes(text)
    assert not found & {"external-link", "email-address", "domain-like", "filename-like"}, found


def test_allow_links_permits_urls_but_not_domain_like_words() -> None:
    text = "Source: https://example.com/report and README.md"
    found = blocking_codes(text, allow_links=True)
    assert "external-link" not in found
    assert "domain-like" in found


@pytest.mark.parametrize("text", ["Edit config.yaml then", "Load data.json", "Read notes.txt"])
def test_filename_like_warns(text: str) -> None:
    findings = [f for f in lint(text) if f.code == "filename-like"]
    assert findings
    assert all(f.severity is Severity.WARN for f in findings)


def test_url_span_is_reported() -> None:
    text = "Go to https://example.com now"
    (finding,) = [f for f in lint(text) if f.code == "external-link"]
    assert text[finding.start : finding.end] == "https://example.com"


# ----------------------------------------------------------------------------------------------
# Hashtags
# ----------------------------------------------------------------------------------------------


def test_three_approved_hashtags_pass() -> None:
    assert "too-many-hashtags" not in codes("Post body.\n\n#AISecurity #MCP #AIGovernance")


def test_four_approved_hashtags_block() -> None:
    text = "Post body.\n\n#AISecurity #MCP #AIGovernance #IdentitySecurity"
    assert "too-many-hashtags" in blocking_codes(text)


def test_unapproved_hashtag_warns() -> None:
    findings = [f for f in lint("Body.\n\n#Random") if f.code == "unapproved-hashtag"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARN


def test_approved_hashtag_any_case_does_not_warn() -> None:
    assert "unapproved-hashtag" not in codes("Body.\n\n#aisecurity")


@pytest.mark.parametrize("text", ["We write C# daily.", "foo#bar is a fragment", "Issue #42 fixed"])
def test_hash_inside_words_or_numbers_is_not_a_hashtag(text: str) -> None:
    assert "unapproved-hashtag" not in codes(text)


def test_fullwidth_number_sign_warns() -> None:
    assert "lookalike-hashtag" in codes("Body \uff03AISecurity")


# ----------------------------------------------------------------------------------------------
# Characters: emoji, invisible characters, styled text, punctuation walls
# ----------------------------------------------------------------------------------------------


def test_two_emoji_pass_three_warn() -> None:
    assert "too-many-emoji" not in codes("Ship it \U0001f680\U0001f680")
    found = [
        f for f in lint("Ship it \U0001f680\U0001f680\U0001f680") if f.code == "too-many-emoji"
    ]
    assert found
    assert found[0].severity is Severity.WARN


@pytest.mark.parametrize(
    "cluster",
    [
        "\U0001f468\u200d\U0001f469\u200d\U0001f467",  # family (ZWJ sequence)
        "\U0001f1fa\U0001f1f8",  # flag (regional indicator pair)
        "1\ufe0f\u20e3",  # keycap
        "\U0001f44d\U0001f3fd",  # thumbs up + skin tone
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",  # England flag
        "\u2764\ufe0f",  # heart + VS16
    ],
)
def test_emoji_clusters_count_once_and_are_not_invisible(cluster: str) -> None:
    text = f"One {cluster} two {cluster}"
    found = codes(text)
    assert "too-many-emoji" not in found
    assert "invisible-character" not in found


@pytest.mark.parametrize(
    "char",
    ["\u200b", "\u2060", "\ufeff", "\u00ad", "\u200e", "\u202e", "\u034f", "\u3164", "\u180e"],
)
def test_invisible_characters_block(char: str) -> None:
    findings = [f for f in lint(f"Hid{char}den text") if f.code == "invisible-character"]
    assert findings
    assert findings[0].severity is Severity.BLOCK
    assert findings[0].start == 3


def test_zwj_between_letters_blocks() -> None:
    assert "invisible-character" in blocking_codes("ab\u200dcd")


def test_variation_selector_after_letter_blocks() -> None:
    assert "invisible-character" in blocking_codes("A\ufe0f letter")


def test_math_bold_blocks() -> None:
    bold = "\U0001d401\U0001d428\U0001d425\U0001d41d"  # "Bold" in math bold
    findings = [f for f in lint(f"This is {bold} text") if f.code == "unicode-styled-text"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.BLOCK


def test_exclamation_marks() -> None:
    assert "exclamation" not in codes("Shipped it!")
    assert "exclamation" in codes("Shipped it! Finally!")


def test_em_dash_wall() -> None:
    assert "em-dash-wall" not in codes("One \u2014 two \u2014 three")
    assert "em-dash-wall" in codes("One \u2014 two \u2014 three \u2014 four")


# ----------------------------------------------------------------------------------------------
# Phrases: bait, formula openers, AI tells
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Comment YES and I'll send it.",
        "Want the checklist? Comment \u201cyes\u201d below.",
        "Type YES for the template.",
        "Save this.",
        "Useful? Save this for later!",
        "Save this post",
        "Follow for more.",
        "Done. Follow me for more on identity.",
        "Repost if this helped.",
        "Like if you agree.",
        "Share if useful.",
        "Tag someone who needs this.",
        "Drop your thoughts below.",
        "Agree?",
    ],
)
def test_engagement_bait_blocks(text: str) -> None:
    assert "engagement-bait" in blocking_codes(f"Real point first.\n\n{text}")


@pytest.mark.parametrize(
    "text",
    [
        "It looks like if you rotate keys weekly, outages drop.",
        "Remember to save this setting before you exit.",
        "We tag someone's account only after review.",  # not 'tag someone who...' imperative
        "I like it if teams own their apps.",
        "Leaders share if and only if they trust the data.",
        "Do you comment yesterday's PR?",
    ],
)
def test_prose_is_not_bait(text: str) -> None:
    assert "engagement-bait" not in codes(text)


@pytest.mark.parametrize(
    "text",
    [
        "Unpopular opinion: MFA is not enough.",
        "Here's the thing: tokens leak.",
        "Here\u2019s the thing about agents.",
        "Here is the thing nobody says.",
        "SMS MFA is dead.",
        "Most people get agent identity wrong.",
        "Nobody talks about service accounts.",
        "Let that sink in.",
    ],
)
def test_formula_openers_block(text: str) -> None:
    assert "formula-opener" in blocking_codes(text)


def test_let_that_sink_in_blocks_anywhere() -> None:
    text = "We found 312 orphaned apps.\n\nLet that sink in."
    findings = [f for f in lint(text) if f.code == "formula-opener"]
    assert len(findings) == 1


@pytest.mark.parametrize(
    "text",
    [
        "The thing that surprised me was ownership.",
        "Our old perimeter model is dead weight now.",  # 'is dead' not at the start
        "Most of the work was ownership.",
    ],
)
def test_non_formula_openers_pass(text: str) -> None:
    assert "formula-opener" not in codes(text)


@pytest.mark.parametrize(
    "text",
    [
        "Let's dive in.",
        "This is a game-changer for SOC teams.",
        "We leverage Entra ID.",
        "It's worth noting that logs lag.",
        "A seamless rollout.",
        "The threat landscape shifted.",
        "I'm excited to share our results.",
        "TL;DR: rotate secrets.",
    ],
)
def test_ai_tells_warn(text: str) -> None:
    findings = [f for f in lint(text) if f.code == "ai-tell"]
    assert findings
    assert all(f.severity is Severity.WARN for f in findings)


def test_critical_is_not_an_ai_tell() -> None:
    assert "ai-tell" not in codes("CVE-2026-1234 is a critical vulnerability.")


def test_findings_are_sorted_by_position() -> None:
    findings = lint("Unpopular opinion: README.md matters! Really!")
    assert findings == sorted(findings, key=lambda f: (f.start, f.end, f.code))


# ----------------------------------------------------------------------------------------------
# Regression: performance, merged runs, and a security-prose false-positive corpus
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [("x." * 1499) + "@", "a." * 1500, "abc-def." * 375, "https://" + "a/" * 1495, "@" * 3000],
)
def test_adversarial_inputs_lint_quickly(text: str) -> None:
    import time

    start = time.perf_counter()
    lint(text)
    assert time.perf_counter() - start < 0.5  # linear scans; measured < 10 ms locally


def test_runs_of_invisible_characters_merge_into_one_finding() -> None:
    text = "ab" + "\u200b" * 50 + "cd"
    found = [f for f in lint(text) if f.code == "invisible-character"]
    assert len(found) == 1
    assert (found[0].start, found[0].end) == (2, 52)


SECURITY_PROSE = [
    "We enforced phishing-resistant MFA with FIDO2 keys for all 4,200 admins.",
    "Conditional Access blocked legacy auth; sign-in logs showed 0.3% of traffic still used it.",
    "Token theft is the real risk: primary refresh tokens outlive passwords.",
    "MCP servers need their own identity, not a shared API key.",
    "OAuth 2.1 removes the implicit flow, finally.",
    "NIST AI RMF has four functions: Govern, Map, Measure, Manage.",
    "MITRE ATLAS AML.T0051 covers LLM prompt injection.",
    "CVE-2026-21345 scored 9.8, so we patched within 48 hours.",
    "OWASP LLM01:2025 is prompt injection; LLM06 is excessive agency.",
    "SP 800-63-4 replaced the old AAL guidance in 2025.",
    "We migrated SAML apps first, then OIDC, then the header-based ones.",
    "Our SOC runs 14 agents; each has a scoped managed identity.",
    "The PIM activation window dropped from 8h to 1h.",
    "Entra ID Governance access reviews caught 212 stale guests.",
    "We used Python 3.12, FastAPI and Azure Container Apps.",
    "Version 2.0 of the policy shipped on 2026-11-02 at 10.30 ET.",
    "Approx. 40% of apps had no owner (vs. 12% expected).",
    "The agent called the tool 3x per request, i.e. too often.",
    "Risk tiers: Tier 1 (read-only), Tier 2 (write), Tier 3 (money movement).",
    "I'd start with ownership; the protocol work is the easy part.",
    "Workload identity federation removed 37 client secrets.",
    "Rotating secrets quarterly wasn't enough; we moved to federation.",
    "Defender for Cloud Apps flagged the OAuth consent grant.",
    "Okta to Entra took 14 months and 9 waves.",
]


@pytest.mark.parametrize("text", SECURITY_PROSE)
def test_security_prose_has_no_findings(text: str) -> None:
    assert lint(text) == []
