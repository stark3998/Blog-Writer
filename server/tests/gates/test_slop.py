"""The structural slop detectors: a golden positive and negative corpus per detector, spans,
messages, thresholds, Markdown masking and parity with the LinkedIn AI-tell lint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blogwriter.domain.common import from_linkedin_finding
from blogwriter.domain.ports import SlopChecker
from blogwriter.gates.slop import (
    ARTICLE_THRESHOLDS,
    PLAIN_THRESHOLDS,
    SLOP_CODES,
    STRUCTURAL_CODES,
    SlopDetectors,
    measure,
)
from blogwriter.gates.text import markdown_view, plain_view
from blogwriter.linkedin.lint import lint_post

SEPARATOR = "---8<---"
DETECTORS = SlopDetectors()


def corpus(goldens: Path, code: str, kind: str) -> list[str]:
    text = (goldens / "slop" / f"{code}.{kind}.txt").read_text(encoding="utf-8")
    return [sample.strip("\n") for sample in text.split(SEPARATOR) if sample.strip()]


@pytest.mark.parametrize("code", SLOP_CODES)
def test_every_positive_sample_is_found(goldens: Path, code: str) -> None:
    samples = corpus(goldens, code, "positive")
    assert samples
    for sample in samples:
        codes = [finding.code for finding in DETECTORS.check(sample)]
        assert code in codes, sample


@pytest.mark.parametrize("code", SLOP_CODES)
def test_no_negative_sample_is_found(goldens: Path, code: str) -> None:
    samples = corpus(goldens, code, "negative")
    assert samples
    for sample in samples:
        codes = [finding.code for finding in DETECTORS.check(sample)]
        assert code not in codes, sample


SLOP_POST = """In today's rapidly evolving landscape, identity is everything.

Security isn't a tool, it's a mindset.

Teams need clarity, alignment, and trust.

The result? Faster delivery.

What if your identity platform could do more? It can, and it should.

Here's the thing: nobody reads policies.

Faster. Cheaper. Safer.

Ultimately, the future belongs to those who adapt. What do you think?"""


def test_golden_findings_of_a_slop_post(goldens: Path) -> None:
    found = [
        {
            "code": f.code,
            "start": f.start,
            "end": f.end,
            "text": SLOP_POST[f.start or 0 : f.end or 0],
        }
        for f in DETECTORS.check(SLOP_POST)
    ]
    golden = json.loads((goldens / "slop" / "slop-post.findings.json").read_text())
    assert found == golden


def test_findings_are_warnings_that_never_quote_structural_text() -> None:
    findings = DETECTORS.check(SLOP_POST)
    assert findings
    for finding in findings:
        assert finding.severity == "warn"
        assert finding.overridable
        if finding.code in STRUCTURAL_CODES:
            assert "mindset" not in finding.message
            assert "policies" not in finding.message


def test_spans_point_at_the_construction() -> None:
    text = "We moved 2,000 apps. Security isn't a tool, it's a mindset. Then we slept."
    finding = next(f for f in DETECTORS.check(text) if f.code == "isnt-x-its-y")
    assert finding.start is not None
    assert finding.end is not None
    assert text[finding.start : finding.end].startswith("isn't a tool")


def test_ai_tells_match_linkedin_lint_exactly() -> None:
    text = (
        "TL;DR: we leverage a robust, seamless landscape. It\u2019s worth noting that "
        "game-changing, cutting-edge tools empower teams. I'm so excited to share it."
    )
    ours = sorted((f.start, f.end, f.message) for f in DETECTORS.check(text) if f.code == "ai-tell")
    theirs = sorted(
        (f.start, f.end, f.message)
        for f in (from_linkedin_finding(item) for item in lint_post(text, approved_hashtags=[]))
        if f.code == "ai-tell"
    )
    assert ours == theirs
    assert len(ours) >= 9


def test_triads_of_names_numbers_or_code_are_not_slop() -> None:
    text = (
        "We compared Okta, Entra, and Ping on SCIM. We tested on Windows, macOS, and Linux. "
        "The limits were 10, 50, and 200 requests."
    )
    assert not [f for f in DETECTORS.check(text) if f.code == "rule-of-three"]


def test_one_triad_alone_is_not_a_density_problem() -> None:
    text = "Good platforms are secure, scalable, and simple. " + "We moved 2,000 apps. " * 30
    assert not [f for f in DETECTORS.check(text) if f.code == "rule-of-three"]


def test_run_thresholds_differ_for_articles_and_plain_text() -> None:
    assert PLAIN_THRESHOLDS.one_sentence_run == 4
    assert ARTICLE_THRESHOLDS.one_sentence_run == 3
    three = "We moved 2,000 apps.\n\nIt took 14 months.\n\nNobody noticed.\n\n" + (
        "The fix was one line, and finding it took a week of diffing exports and logs. "
        "That is the part I would plan for."
    )
    assert not [f for f in DETECTORS.check(three) if f.code == "one-sentence-run"]
    assert [f for f in DETECTORS.check_markdown(three) if f.code == "one-sentence-run"]


def test_flat_rhythm_needs_enough_sentences() -> None:
    short = "Teams should review every application first. Owners should sign off early."
    assert not [f for f in DETECTORS.check(short) if f.code == "flat-rhythm"]


def test_low_specificity_points_at_vague_paragraphs(goldens: Path) -> None:
    vague = corpus(goldens, "low-specificity", "positive")[0]
    opener = "We started the migration in 2024 and it went the way most do."
    text = f"{opener}\n\n{vague}\n\n{vague}"
    findings = [f for f in DETECTORS.check(text) if f.code == "low-specificity"]
    assert len(findings) == 2
    assert all(f.start is not None and f.start > len(opener) for f in findings)
    short_paragraphs = "\n\n".join(["We should plan the work and then do the work well."] * 7)
    document_level = [f for f in DETECTORS.check(short_paragraphs) if f.code == "low-specificity"]
    assert [(f.start, f.end) for f in document_level] == [(None, None)]


def test_measure_counts_specifics() -> None:
    counts = measure(plain_view("We moved 2,000 apps from Okta to Microsoft Entra ID with SCIM."))
    assert counts.words == 12
    assert counts.specifics == 4  # 2,000 / Okta / Microsoft Entra ID / SCIM
    assert counts.specifics_per_100_words == pytest.approx(400 / 12)
    code = measure(markdown_view("Run `az login` first, then `az account show` to check it."))
    assert code.specifics == 2
    assert measure(plain_view("")).specifics_per_100_words == 0.0


MARKDOWN = """Intro with a [robust link](https://example.com/leverage?utm_source=x) and \
`leverage_value` in code.[^src-1]

```python
# leverage the robust, seamless landscape
print("isn't a tool, it's a mindset")
```

| Feature | Value |
| --- | --- |
| speed, focus, and trust | robust |

Security isn't a tool, it's a mindset.

[^src-1]: Publisher. *A robust title*. [site](https://example.com/b)
"""


def test_markdown_syntax_code_tables_and_citations_are_not_prose() -> None:
    findings = DETECTORS.check_markdown(MARKDOWN)
    spans = [(f.code, MARKDOWN[f.start or 0 : f.end or 0]) for f in findings]
    assert ("ai-tell", "robust") in spans  # the link text is prose
    assert all("leverage" not in text for _, text in spans)  # URL, code and code block masked
    isnt = [text for code, text in spans if code == "isnt-x-its-y"]
    assert isnt == ["isn't a tool, it's"]
    # The footnote definition (a citation) is never read.
    assert not [f for f in findings if (f.start or 0) >= MARKDOWN.index("[^src-1]:")]


def test_slop_detectors_satisfy_the_port() -> None:
    checker: SlopChecker = SlopDetectors()
    assert checker.check("") == []
    assert markdown_view("").text == ""
