"""audit_html: htmlContent must stay inside contracts/sanitizer-profile.json."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

import pytest

from blogwriter.publish.contract import PublishedContract, audit_html

DROPPED: Final = "is dropped by Python but kept by browsers"
CUT_OFF: Final = (
    "the HTML ends inside a tag, comment or raw-text element, which would swallow the page markup "
    "after it"
)
DIAGRAM: Final = "d89582c5f6a372074092544cdb278f3388bd94e8cc574d9d2cc2a78fb7b77e61"

# Every tag, attribute rule, class alternative, id form and URL form the profile allows.
KITCHEN_SINK: Final = f"""\
<h2 id="intro">Intro</h2>
<h3 id="details-1">Details</h3>
<h4>Small heading</h4>
<p>Text with <strong>bold</strong>, <em>em</em>, <code>code</code>, a hard<br>
break, &lt;escaped&gt; markup &amp; entities like &copy;, &#169; and &#39;.</p>
<p><a href="https://example.com/a?b=1&amp;c=2" title="Example" rel="noopener noreferrer">x</a>
<a href="mailto:a@b.example" rel="noopener noreferrer">mail</a>
<a href="#intro" rel="noopener noreferrer">fragment</a>
<a href="/blog/other-post" rel="noopener noreferrer">site</a>
<a href="/blog-media/x/full.png" rel="noopener noreferrer">full size</a></p>
<p><img src="/blog-media/x/diagram.svg" alt="A diagram" title="Diagram"
 width="800" height="400"></p>
<p><img src="https://images.example/photo.png" alt="Photo"></p>
<ul><li>one</li><li>two</li></ul>
<ol start="3"><li>three</li></ol>
<ul class="contains-task-list">
<li class="task-list-item"><input class="task-list-item-checkbox" disabled="disabled"
 type="checkbox" checked> done</li>
<li class="task-list-item"><input disabled type="checkbox"
 class="task-list-item-checkbox"> todo</li>
</ul>
<blockquote><p>Quote</p></blockquote>
<pre class="highlight"><code class="language-python"><span class="k">def</span> \
<span class="nf">f</span>(): <span class="k">return</span> <span class="mi">1</span> \
<span class="o">&lt;</span> <span class="mi">2</span></code></pre>
<pre><code class="language-c++">int x;</code></pre>
<figure class="mermaid" data-diagram="{DIAGRAM}">\
<pre class="mermaid-source">graph TD; A--&gt;B</pre></figure>
<aside class="callout callout-note"><p>Note</p></aside>
<aside class="callout callout-tip"><p>Tip</p></aside>
<aside class="callout callout-warning"><p>Warning</p></aside>
<table><thead><tr><th style="text-align:left">a</th><th style="text-align:center">b</th>\
<th style="text-align:right">c</th><th>d</th></tr></thead>
<tbody><tr><td style="text-align:left">1</td><td>2</td><td>3</td><td></td></tr></tbody>\
</table>
<hr>
<p>Claim.<sup class="footnote-ref"><a href="#fn1" id="fnref1">[1]</a></sup> Again.\
<sup class="footnote-ref"><a href="#fn1" id="fnref1:1">[1]</a></sup></p>
<hr class="footnotes-sep">
<section class="footnotes"><ol class="footnotes-list">
<li id="fn1" class="footnote-item"><p>Source. \
<a href="#fnref1" class="footnote-backref">back</a> \
<a href="#fnref1:1" class="footnote-backref">back</a></p></li>
</ol></section>
"""


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)


def test_kitchen_sink_covers_every_allowed_tag(contract: PublishedContract) -> None:
    collector = _TagCollector()
    collector.feed(KITCHEN_SINK)
    assert collector.tags == contract.profile.tags


def test_kitchen_sink_conforms(contract: PublishedContract) -> None:
    audit = contract.audit_html(KITCHEN_SINK)
    assert audit.findings == ()
    assert audit.headings == ((2, "intro"), (3, "details-1"))
    assert audit.ids == ("intro", "details-1", "fnref1", "fnref1:1", "fn1")
    assert audit.media == ("/blog-media/x/full.png", "/blog-media/x/diagram.svg")


def test_real_renderer_output_conforms(contract: PublishedContract, full_article_html: str) -> None:
    audit = contract.audit_html(full_article_html)
    assert audit.findings == ()
    assert len(audit.headings) == 10


def test_module_function_uses_the_default_contract() -> None:
    assert audit_html("<p>ok</p>").findings == ()
    assert audit_html("<script>x</script>").findings == ("tag <script> is not allowed",)


def test_every_hostile_html_case_is_caught(
    contract: PublishedContract, contracts_dir: Path
) -> None:
    cases = sorted((contracts_dir / "fixtures" / "xss" / "html").glob("*.html"))
    assert len(cases) == 20
    for case in cases:
        findings = contract.audit_html(case.read_text(encoding="utf-8")).findings
        if case.name == "20-allowed-markup-survives.html":
            assert findings == (), case.name
        else:
            assert findings, case.name


RULES: Final = [
    # tags and markup outside elements
    ("<SCRIPT>alert(1)</SCRIPT>", "tag <script> is not allowed"),
    ("<svg><circle/></svg>", "tag <svg> is not allowed"),
    ("<p>1 < 2</p>", "text contains an unescaped '<'"),
    ("<p>a</p><!-- hidden -->", "comments are not allowed"),
    ("<!DOCTYPE html><p>a</p>", "declarations (such as <!DOCTYPE>) are not allowed"),
    ("<![CDATA[x]]>", "CDATA sections are not allowed"),
    ("<?php echo 1; ?>", "processing instructions are not allowed"),
    # attributes
    ('<p title="x">a</p>', "attribute title on <p> is not allowed"),
    ('<p onclick="x()">a</p>', "attribute onclick on <p> is not allowed"),
    ('<h4 id="a">a</h4>', "attribute id on <h4> is not allowed"),
    ('<a href="#a" target="_blank">a</a>', "attribute target on <a> is not allowed"),
    ('<img src="/a.png" srcset="/b.png 2x" alt="a">', "attribute srcset on <img> is not allowed"),
    ('<a href="#a" href="#b">a</a>', "attribute href appears twice on <a>"),
    ('<a href="#a" rel="nofollow">a</a>', 'rel on <a> must be "noopener noreferrer"'),
    ("<img alt>", "attribute alt on <img> needs a value"),
    # URLs (values are entity-decoded first, as a browser would)
    ('<a href="javascript:alert(1)">a</a>', 'href="javascript:alert(1)" on <a> is not allowed'),
    ('<a href="&#106;avascript:x">a</a>', 'href="javascript:x" on <a> is not allowed'),
    ('<a href="https://x/\x01y">a</a>', 'href="https://x/\\u0001y" on <a> is not allowed'),
    ('<a href="https://x/\x7fy">a</a>', 'href="https://x/\\u007fy" on <a> is not allowed'),
    ('<a href="http://x.example">a</a>', 'href="http://x.example" on <a> is not allowed'),
    ('<a href="//x.example">a</a>', 'href="//x.example" on <a> is not allowed'),
    ('<a href="relative/path">a</a>', 'href="relative/path" on <a> is not allowed'),
    ('<img src="mailto:a@b.example" alt="a">', 'src="mailto:a@b.example" on <img> is not allowed'),
    (
        '<img src="data:image/png;base64,AA" alt="a">',
        'src="data:image/png;base64,AA" on <img> is not allowed',
    ),
    # numeric references Python's parser drops but browsers keep (a parser differential)
    ('<a href="https://x/&#1;y">a</a>', f"character reference &#1; in <a> {DROPPED}"),
    ('<a href="&#x7F;https://x">a</a>', f"character reference &#x7F; in <a> {DROPPED}"),
    ("<p>a&#1;b</p>", f"character reference &#1; in text {DROPPED}"),
    # classes, ids and values
    ('<span class="k evil">a</span>', 'class "evil" on <span> is not allowed'),
    ('<span class=" ">a</span>', "empty class on <span>"),
    ('<aside class="callout callout-x">a</aside>', 'class "callout-x" on <aside> is not allowed'),
    ('<code class="language-&lt;x&gt;">a</code>', 'class "language-<x>" on <code> is not allowed'),
    ('<h2 id="Bad Id">a</h2>', 'id="Bad Id" on <h2> is not allowed'),
    ('<h2 id="__proto__">a</h2>', 'id="__proto__" on <h2> is not allowed'),
    ('<a href="#a" id="anything">a</a>', 'id="anything" on <a> is not allowed'),
    ('<td style="color:red">a</td>', 'style="color:red" on <td> is not allowed'),
    ('<td style="text-align: center">a</td>', 'style="text-align: center" on <td> is not allowed'),
    ('<img src="/a.png" alt="a" width="0">', 'width="0" on <img> is not allowed'),
    ('<img src="/a.png" alt="a" height="100%">', 'height="100%" on <img> is not allowed'),
    ('<ol start="-1"><li>a</li></ol>', 'start="-1" on <ol> is not allowed'),
    ('<figure data-diagram="abc">a</figure>', 'data-diagram="abc" on <figure> is not allowed'),
    # forced and boolean attributes
    ('<input type="checkbox">', '<input> must have disabled="disabled"'),
    ('<input disabled="disabled">', '<input> must have type="checkbox"'),
    ('<input disabled="disabled" type="text">', 'type="text" on <input> is not allowed'),
    ('<input disabled="no" type="checkbox">', "disabled on <input> must be boolean"),
    ('<input disabled type="checkbox" checked="yes">', "checked on <input> must be boolean"),
    # self-contained fragments: the portfolio inserts htmlContent into its page, where an
    # unfinished tag swallows the next end tag and becomes a live element
    ("<p>a</p><img src=x onerror=alert(1)//", CUT_OFF),
    ('<p>a</p><img src="/a.png" alt="a"', CUT_OFF),
    ('<p>a</p><a href="https://example.com', CUT_OFF),
    ("<p>a</p></p", CUT_OFF),
    ("<p>a</p><!-- x", CUT_OFF),
    ("<p>a</p><script>x", CUT_OFF),
    ("<p>a</p>&<img src=x onerror=alert(1)//", CUT_OFF),
    # ... and must not close, misnest or leave open anything around it
    ("<p>a</p></div><p>b</p>", "end tag </div> is not allowed"),
    ("<p>a</p></section>", "end tag </section> closes nothing"),
    ("<p>a<br></br></p>", "end tag </br> is not allowed"),
    ('<p><a href="#a">x</p>', "<a> is not closed before </p>"),
    ('<p><a href="https://example.com">the rest of the page', "<a> is not closed"),
    ("<table><tbody><tr><td>x", "<table> is not closed"),
    ("<p/>text", "<p> is not closed"),  # browsers ignore the self-closing flag
]


@pytest.mark.parametrize(("html", "finding"), RULES)
def test_rules(contract: PublishedContract, html: str, finding: str) -> None:
    assert finding in contract.audit_html(html).findings


@pytest.mark.parametrize(
    "html",
    [
        "",
        "plain text",
        "<p>a</p>\n",
        "<p>x</p>\n<p>y</p>\n\n",
        "<p>a</p>tail &amp; more",
        "<p>a</p>&amp",  # browsers read a bare &amp as "&"
        "<p>a &#; b</p>",  # Python's parser pauses after "&#", yet the fragment is complete
        '<p><img src="/a.png" alt="a"/><br/></p>',
    ],
)
def test_self_contained_fragments_pass(contract: PublishedContract, html: str) -> None:
    assert contract.audit_html(html).findings == ()


@pytest.mark.parametrize(
    "tail",
    ["</blogwriter-fragment-end>", "</blogwriter-fragment-end ", "</blogwriter-fragment-end"],
)
def test_the_end_marker_cannot_be_forged(contract: PublishedContract, tail: str) -> None:
    findings = contract.audit_html(f"<p>a</p>{tail}<img src=x onerror=alert(1)//").findings
    assert CUT_OFF in findings
    findings = contract.audit_html(f"<p>a</p>{tail}").findings
    assert findings


def test_deep_nesting_is_tracked(contract: PublishedContract) -> None:
    balanced = "<em>" * 5000 + "<sup>x</sup>" * 5000 + "</em>" * 5000
    assert contract.audit_html(balanced).findings == ()
    findings = contract.audit_html("<em>" * 5000 + "<sup>x</sup>" * 5000).findings
    assert findings == ("<em> is not closed",) * 5000


def test_end_tags_of_reported_tags_add_nothing(contract: PublishedContract) -> None:
    assert contract.audit_html("<div><p>a</p></div>").findings == ("tag <div> is not allowed",)


def test_long_values_are_truncated_in_findings(contract: PublishedContract) -> None:
    findings = contract.audit_html(f'<a href="javascript:{"x" * 500}">a</a>').findings
    assert len(findings) == 1
    assert len(findings[0]) < 120


def test_long_names_are_truncated_in_findings(contract: PublishedContract) -> None:
    name = "x" * 5000
    html = f"<{name}>a</{name}><p {name}=1 {name}=2>b</p></{name}>"
    findings = contract.audit_html(html).findings
    assert len(findings) == 3  # the tag, the attribute and its repeat; </x...> adds nothing
    assert all(len(finding) < 120 for finding in findings)


def test_parser_failure_is_a_finding(
    contract: PublishedContract, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: HTMLParser, data: str) -> None:
        raise AssertionError("unexpected input")

    monkeypatch.setattr(HTMLParser, "feed", explode)
    assert contract.audit_html("<p>a</p>").findings == (
        "the HTML could not be parsed (AssertionError)",
    )


def test_findings_quote_values_as_json(contract: PublishedContract) -> None:
    (finding,) = contract.audit_html('<a href="javascript:&quot;x&quot;">a</a>').findings
    assert re.fullmatch(r'href="javascript:\\"x\\"" on <a> is not allowed', finding)
