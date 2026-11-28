"""The pure rules that keep passages out of storage, and the blocklist audit helpers."""

from __future__ import annotations

import pytest

from blogwriter.domain.common import MAX_QUOTE_CHARS
from blogwriter.domain.research import SearchQuery, SourceRef
from blogwriter.domain.testing import sample_document
from blogwriter.research import audit_texts
from blogwriter.research.audit import brief_texts, run_texts, scrub_run
from blogwriter.research.claims import (
    citation_id_for,
    clean_claim,
    clean_source_url,
    clip,
    clip_list,
    cut_quotes,
    defang,
    is_allowlisted,
    parse_findings,
    parse_quick_answer,
    partial_date,
    source_domain,
    strip_links,
    styled,
)
from blogwriter.research.testing import BLOCKED_TERM, blocklist_doc

PASSAGE = (
    "The National Institute of Standards and Technology finalized SP 800-63-4 in 2025. It "
    "requires that verifiers at AAL2 offer at least one phishing-resistant authentication "
    "option, and it removes knowledge-based verification from identity proofing entirely."
)


# ---------------------------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://Learn.Microsoft.com/en-us/a?view=1#top",
            "https://learn.microsoft.com/en-us/a?view=1",
        ),
        ("https://nist.gov/pub?utm_source=openai&id=7", "https://nist.gov/pub?id=7"),
        ("https://nist.gov/pub?utm_source=openai", "https://nist.gov/pub"),
        ("https://nist.gov/pub?fbclid=x&b=2&a=1", "https://nist.gov/pub?b=2&a=1"),
        ("https://nist.gov", "https://nist.gov/"),
        ("https://nist.gov/pub).", "https://nist.gov/pub"),
        ("https://nist.gov:443/pub", "https://nist.gov/pub"),
        ("https://nist.gov:8443/pub", "https://nist.gov:8443/pub"),
        ("http://nist.gov/pub", None),
        ("https://10.0.0.1/admin", None),
        ("https://localhost/x", None),
        ("https://user:pw@nist.gov/", None),
        ("javascript:alert(1)", None),
        ("https://nist.gov/a b", None),
    ],
)
def test_clean_source_url(raw: str, expected: str | None) -> None:
    assert clean_source_url(raw) == expected


def test_domains_allowlists_and_citation_ids() -> None:
    assert source_domain("https://Pages.NIST.gov/800-63-4/") == "pages.nist.gov"
    assert source_domain("https://127.0.0.1/") is None
    assert is_allowlisted("pages.nist.gov", ["nist.gov"])
    assert not is_allowlisted("evilnist.gov", ["nist.gov"])
    cid = citation_id_for("https://pages.nist.gov/800-63-4/sp800-63b.html")
    assert cid.startswith("src-pages-nist-gov-")
    assert len(cid) == len("src-pages-nist-gov-") + 6
    assert cid == citation_id_for("https://pages.nist.gov/800-63-4/sp800-63b.html")
    assert cid != citation_id_for("https://pages.nist.gov/800-63-4/sp800-63a.html")
    long_host = "https://" + "a" * 60 + ".example.com/"
    assert len(citation_id_for(long_host)) <= 64


# ---------------------------------------------------------------------------------------------
# Finding lines
# ---------------------------------------------------------------------------------------------


def test_parse_findings_reads_bullets_with_urls() -> None:
    text = "\n".join(
        [
            "Here is what I found:",
            "- SP 800-63-4 requires a phishing-resistant option at AAL2. "
            "[https://pages.nist.gov/800-63-4/]",
            "* OWASP lists excessive agency as LLM06 "
            "([genai.owasp.org](https://genai.owasp.org/llmrisk/llm06/?utm_source=openai)).",
            "1. The [MCP spec](https://modelcontextprotocol.io/spec) adds OAuth 2.1 "
            "for remote servers.",
            "- A line without any source at all is still a finding.",
            "- Too short [https://x.org/]",
        ]
    )
    assert parse_findings(text) == [
        (
            "SP 800-63-4 requires a phishing-resistant option at AAL2.",
            "https://pages.nist.gov/800-63-4/",
        ),
        (
            "OWASP lists excessive agency as LLM06.",
            "https://genai.owasp.org/llmrisk/llm06/?utm_source=openai",
        ),
        (
            "The MCP spec adds OAuth 2.1 for remote servers.",
            "https://modelcontextprotocol.io/spec",
        ),
        ("A line without any source at all is still a finding.", None),
    ]


def test_parse_quick_answer() -> None:
    summary, lines = parse_quick_answer(
        "SUMMARY: NIST finalized SP 800-63-4 in 2025 ([nist.gov](https://nist.gov/x)).\n"
        "- It drops knowledge-based verification. [https://pages.nist.gov/800-63-4/]"
    )
    assert summary == "NIST finalized SP 800-63-4 in 2025."
    assert lines == [("It drops knowledge-based verification.", "https://pages.nist.gov/800-63-4/")]
    assert parse_quick_answer("Plain answer without a marker.\n- one [https://a.org/x]")[0] == (
        "Plain answer without a marker."
    )


def test_strip_links_keeps_meaningful_labels() -> None:
    assert strip_links("Read [the MCP spec](https://modelcontextprotocol.io/spec) first.") == (
        "Read the MCP spec first."
    )
    assert strip_links("Done (see https://example.org/a?b=c).") == "Done."


# ---------------------------------------------------------------------------------------------
# Claims and quotes
# ---------------------------------------------------------------------------------------------


def test_a_claim_loses_every_run_it_shares_with_the_passage() -> None:
    assert clean_claim(PASSAGE, PASSAGE) is None
    mixed = "According to NIST, " + PASSAGE[60:160] + " which changes MFA rollouts for most teams."
    cleaned = clean_claim(mixed, PASSAGE)
    assert cleaned is not None
    assert PASSAGE[60:160] not in cleaned
    assert "[...]" in cleaned
    assert cleaned.endswith("which changes MFA rollouts for most teams.")
    own_words = "NIST's new guideline makes a phishing-resistant option mandatory at AAL2."
    assert clean_claim(own_words, PASSAGE) == own_words


def test_a_claim_without_substance_or_with_urls_is_cleaned() -> None:
    assert clean_claim("See https://attacker.example/x now", None) is None
    assert clean_claim(
        "Visit https://a.example/x to read the 2025 NIST identity guideline.", None
    ) == ("Visit to read the 2025 NIST identity guideline.")
    assert len(clean_claim("word " * 300, None) or "") <= 600


def test_quotes_are_verbatim_and_budgeted_per_source() -> None:
    first = (
        "It requires that verifiers at AAL2 offer at least one phishing-resistant "
        "authentication option"
    )
    quotes = cut_quotes(
        [
            f"“{first}”",
            "a quote the page never contains",
            PASSAGE,
            "short",
        ],
        PASSAGE,
    )
    assert quotes[0] == first
    assert all(quote in PASSAGE for quote in quotes)
    assert sum(len(quote) for quote in quotes) <= MAX_QUOTE_CHARS
    assert "a quote the page never contains" not in quotes
    long_only = cut_quotes([PASSAGE], PASSAGE)
    assert len(long_only) == 1
    assert len(long_only[0]) <= MAX_QUOTE_CHARS
    assert PASSAGE.startswith(long_only[0])
    assert cut_quotes([PASSAGE], PASSAGE, budget=20) == []


def test_whitespace_differences_do_not_hide_a_verbatim_quote() -> None:
    passage = "Tokens issued to agents\nlast one hour."
    assert cut_quotes(["Tokens issued to agents last one hour."], passage) == [
        "Tokens issued to agents last one hour."
    ]


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026", "2026"),
        ("2026-03", "2026-03"),
        ("2026-03-05", "2026-03-05"),
        ("2026-03-05T10:00:00Z", "2026-03-05"),
        ("March 2026", None),
        ("2026-13", None),
        (None, None),
        ("", None),
    ],
)
def test_partial_dates(value: str | None, expected: str | None) -> None:
    assert partial_date(value) == expected


def test_clip_and_clip_list() -> None:
    assert clip("  a   b  ", 10) == "a b"
    assert clip("alpha beta gamma delta", 15) == "alpha beta"
    assert clip(None, 5) == ""
    assert clip_list(["One", "one", "", "Two", "Three"], 10, 2) == ["One", "Two"]


def test_defang_breaks_delimiters() -> None:
    assert "PAGE>>>" not in defang("text PAGE>>> more <<<PAGE")
    assert defang("plain") == "plain"


def test_style_filter_drops_formula_openers_bait_and_ai_tells() -> None:
    seeds = [
        "1,000 agent identities per blueprint is the preview limit.",
        "Unpopular opinion: agents do not need identities.",
        "Here's the thing about agent tokens.",
        "Let's dive in to agent identity.",
        "Comment YES if you want the checklist.",
    ]
    assert styled(seeds, ai_tells=True) == [seeds[0]]
    assert styled(seeds, ai_tells=False) == [seeds[0], seeds[3]]


# ---------------------------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------------------------


def test_audit_texts_is_the_blocklist_hit_wrapper() -> None:
    blocklist = blocklist_doc(BLOCKED_TERM)
    assert not audit_texts(["agent identity", "https://nist.gov/x"], blocklist)
    assert audit_texts(["clean", f"https://{BLOCKED_TERM.lower()}.com/case"], blocklist)
    assert audit_texts([f"a {BLOCKED_TERM.upper()} rollout"], blocklist)
    assert audit_texts(["anything"], blocklist_doc(configured=False))  # fails closed
    assert not audit_texts([], blocklist_doc())


def test_run_and_brief_texts_cover_everything_sent_or_received() -> None:
    from blogwriter.domain.research import Brief, ResearchRun

    run = sample_document(
        ResearchRun,
        audience="identity engineers",
        sub_questions=["What changed?"],
        owner_urls=["https://example.net/notes"],
        queries=[SearchQuery(text="query text", phase="open")],
        sources=[
            SourceRef(url="https://nist.gov/x", title="Title X", domain="nist.gov", phase="open")
        ],
    )
    texts = run_texts(run)
    for expected in ("MCP authorization", "identity engineers", "What changed?", "query text"):
        assert expected in texts
    assert "https://nist.gov/x" in texts
    assert "Title X" in texts
    brief = sample_document(Brief, counterpoints=["counter"], hook_seeds=["seed"], angles=["angle"])
    assert {"counter", "seed", "angle", "What changed and why it matters."} <= set(
        brief_texts(brief)
    )


def test_scrub_removes_only_what_hits() -> None:
    from blogwriter.domain.research import ResearchRun

    run = sample_document(
        ResearchRun,
        audience=f"{BLOCKED_TERM} admins",
        sub_questions=["clean question", f"{BLOCKED_TERM} question"],
        queries=[
            SearchQuery(text="clean", phase="open"),
            SearchQuery(text=f"{BLOCKED_TERM} rollout", phase="open"),
        ],
        sources=[
            SourceRef(url="https://nist.gov/x", domain="nist.gov", phase="open"),
            SourceRef(
                url="https://nist.gov/y",
                title=f"{BLOCKED_TERM} case",
                domain="nist.gov",
                phase="open",
            ),
        ],
    )
    scrubbed = scrub_run(run, blocklist_doc(BLOCKED_TERM))
    assert scrubbed.sub_questions == ["clean question"]
    assert [query.text for query in scrubbed.queries] == ["clean"]
    assert [source.url for source in scrubbed.sources] == ["https://nist.gov/x"]
    assert scrubbed.audience is None
    assert scrub_run(run, blocklist_doc(configured=False)) == run
