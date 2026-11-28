"""Gate 7: the outbound allowlist, tracking parameters, raw HTML, clearance per version."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from blogwriter.domain.articles import Article, Clearance, OwnerSignalRef
from blogwriter.domain.common import Citation, Finding
from blogwriter.domain.settings import EmployerPolicy, PillarSettings
from blogwriter.domain.testing import SAMPLE_NOW, sample_document
from blogwriter.gates.document import prepare_text
from blogwriter.gates.outbound import (
    allowed_domains,
    clearance_findings,
    clearance_required,
    host_allowed,
    link_findings,
    policy_findings,
)
from blogwriter.gates.testing import SAMPLE_META, sample_brief, usable_story

PILLARS = PillarSettings.default(SAMPLE_NOW)
SITE = "owner-site.example"


def check(body: str, *, site_host: str | None = SITE, allowlisted: bool = True) -> list[Finding]:
    brief = sample_brief(
        citations=[
            Citation(
                id="src-vendor",
                url="https://vendor.example.org/report",
                title="A vendor report",
                domain="vendor.example.org",
                allowlisted=allowlisted,
            )
        ],
        quotes=[],
    )
    text = prepare_text(SAMPLE_META, body)
    return link_findings(text, pillars=PILLARS, brief=brief, site_host=site_host)


def codes(findings: Sequence[Finding]) -> list[str]:
    return [finding.code for finding in findings]


@pytest.mark.parametrize(
    "body",
    [
        "See [the guide](https://learn.microsoft.com/entra/identity/) for details.",
        "See [the docs](https://docs.learn.microsoft.com/x) (a subdomain of the allowlist).",
        "Read [the report](https://vendor.example.org/report) from the brief.",
        "My [earlier post](https://owner-site.example/blog/agents) and [a section](#setup).",
        "A [site-relative link](/blog/agents) and an image:\n\n![Diagram](/blog-media/x/a.png)",
        "Write to [me](mailto:me@owner-site.example) if you disagree.",
        "Plain text with no links at all, and `<iframe>` inside code is fine.",
    ],
)
def test_allowed_links_pass(body: str) -> None:
    assert check(body) == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("See [this](https://random-blog.example.net/post).", "outbound-domain"),
        ("See [this](https://notlearn.microsoft.com.evil.example/x).", "outbound-domain"),
        ("An image ![x](https://images.example.net/a.png) inline.", "outbound-domain"),
        ("See [this](http://learn.microsoft.com/x).", "outbound-scheme"),
        ("See [this](javascript:alert(1)).", "outbound-scheme"),
        ("See [this](//learn.microsoft.com/x).", "outbound-scheme"),
        ("Mail [someone](mailto:someone@other.example).", "outbound-mailto"),
        (
            "See [the guide](https://learn.microsoft.com/x?utm_source=linkedin).",
            "tracking-parameter",
        ),
        ("See [the guide](https://learn.microsoft.com/x?fbclid=abc&a=1).", "tracking-parameter"),
        ("<div>raw html</div>", "outbound-html"),
        ('<iframe src="https://evil.example"></iframe>', "outbound-iframe"),
        ('<form action="/x"><input name="q"></form>', "outbound-form"),
        ("Text with an inline <script>x</script> tag.", "outbound-html"),
    ],
)
def test_refused_links_and_html_are_hard(body: str, code: str) -> None:
    findings = check(body)
    assert code in codes(findings)
    for finding in findings:
        assert finding.severity == "block"
        assert not finding.overridable
        assert finding.start is not None


def test_citation_domains_need_the_allowlist() -> None:
    body = "Read [the report](https://vendor.example.org/report) from the brief."
    assert codes(check(body, allowlisted=False)) == ["outbound-domain"]


def test_without_a_site_origin_the_site_is_like_any_domain() -> None:
    body = "My [earlier post](https://owner-site.example/blog/agents)."
    assert codes(check(body, site_host=None)) == ["outbound-domain"]
    assert codes(check("Write to [me](mailto:me@owner-site.example).", site_host=None)) == [
        "outbound-mailto"
    ]


def test_domains_cover_subdomains_only_on_label_boundaries() -> None:
    domains = allowed_domains(pillar="identity", pillars=PILLARS, brief=None, site_host=None)
    assert "learn.microsoft.com" in domains
    assert host_allowed("LEARN.microsoft.com.", domains)
    assert host_allowed("sub.learn.microsoft.com", domains)
    assert not host_allowed("evillearn.microsoft.com", domains)
    assert allowed_domains(pillar=None, pillars=PILLARS, brief=None, site_host=None) == frozenset()


def article(**fields: object) -> Article:
    values: dict[str, object] = {"meta": SAMPLE_META, "markdown": "Body."}
    values.update(fields)
    return sample_document(Article, **values)


def test_clearance_is_bound_to_this_exact_version() -> None:
    sha = "a" * 64
    case_study = article(meta=SAMPLE_META.model_copy(update={"format": "case-study"}))
    assert clearance_required(case_study, stories=[])
    assert codes(clearance_findings(case_study, stories=[], content_sha256=sha)) == [
        "clearance-required"
    ]
    attested = case_study.model_copy(
        update={"clearance": Clearance(required=True, content_sha256=sha, attested_at=SAMPLE_NOW)}
    )
    assert clearance_findings(attested, stories=[], content_sha256=sha) == []
    edited = clearance_findings(attested, stories=[], content_sha256="b" * 64)
    assert codes(edited) == ["clearance-required"]
    assert not edited[0].overridable


def test_anonymized_stories_require_clearance_even_without_the_flag() -> None:
    story = usable_story(confidentiality="anonymized")
    uses_story = article(owner_signals=[OwnerSignalRef(kind="story", id=story.id)])
    assert not uses_story.clearance.required
    assert clearance_required(uses_story, stories=[story])
    assert not clearance_required(article(), stories=[story])  # not referenced
    public = usable_story(confidentiality="public")
    assert not clearance_required(
        article(owner_signals=[OwnerSignalRef(kind="story", id=public.id)]), stories=[public]
    )


def test_an_unsaved_policy_warns() -> None:
    unsaved = EmployerPolicy.default(SAMPLE_NOW)
    findings = policy_findings(unsaved)
    assert [(f.code, f.severity) for f in findings] == [("policy-not-configured", "warn")]
    assert policy_findings(unsaved.model_copy(update={"configured": True})) == []
