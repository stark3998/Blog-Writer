"""Gate 5: dialect lint over exactly ``compose_document``, standards, links and diagrams."""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping, Sequence

import pytest

from blogwriter.core.egress import EgressBlockedError, EgressResponse, EgressTransportError
from blogwriter.domain.common import Finding
from blogwriter.domain.compose import compose_document, frontmatter_length
from blogwriter.domain.ports import RenderFailed
from blogwriter.domain.settings import PillarSettings
from blogwriter.domain.testing import SAMPLE_NOW, FakeRenderService, StaticHttp
from blogwriter.gates import lint as gate_lint
from blogwriter.gates.document import prepare_text
from blogwriter.gates.lint import dialect_findings, network_findings, standards_findings
from blogwriter.gates.settings import GatesSettings
from blogwriter.gates.standards import seed_catalog
from blogwriter.gates.testing import SAMPLE_BODY, SAMPLE_CITATION, SAMPLE_META, SAMPLE_URL
from blogwriter.publish.lint import Finding as PublishFinding

PILLARS = PillarSettings.default(SAMPLE_NOW)


def codes(findings: Sequence[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def test_gate_5_lints_exactly_the_composed_document(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, bool, Collection[str] | None]] = []
    real = gate_lint.lint_article

    def spy(
        source: str, *, require_frontmatter: bool = True, source_ids: Collection[str] | None = None
    ) -> list[PublishFinding]:
        seen.append((source, require_frontmatter, source_ids))
        return real(source, require_frontmatter=require_frontmatter, source_ids=source_ids)

    monkeypatch.setattr(gate_lint, "lint_article", spy)
    text = prepare_text(SAMPLE_META, SAMPLE_BODY)
    assert dialect_findings(text, source_ids={SAMPLE_CITATION}) == []
    assert seen == [(compose_document(SAMPLE_META, SAMPLE_BODY), True, {SAMPLE_CITATION})]
    assert text.body_start == frontmatter_length(SAMPLE_META)


def test_dialect_errors_are_hard_and_point_at_the_body() -> None:
    body = "First paragraph.\n\n<div>raw</div>\n\nA claim.[^src-unknown]\n\n[^src-unknown]: X.\n"
    text = prepare_text(SAMPLE_META, body)
    findings = dialect_findings(text, source_ids={SAMPLE_CITATION})
    by_code = {finding.code: finding for finding in findings}
    assert {"raw-html", "citation-unknown-source"} <= set(by_code)
    raw = by_code["raw-html"]
    assert (raw.severity, raw.overridable) == ("block", False)
    assert raw.start is not None
    assert raw.end is not None
    assert body[raw.start : raw.end] == "<div>raw</div>"


def test_incomplete_meta_blocks_and_the_body_is_still_linted() -> None:
    meta = SAMPLE_META.model_copy(update={"slug": None, "excerpt": None})
    text = prepare_text(meta, "A claim.[^missing]\n\n<b>bold</b>\n")
    findings = dialect_findings(text, source_ids=set())
    assert text.document is None
    first = findings[0]
    assert (first.code, first.severity, first.overridable) == ("meta-incomplete", "block", False)
    assert "slug" in first.message
    assert "excerpt" in first.message
    assert "raw-html" in codes(findings)


def test_a_title_the_dialect_refuses_is_frontmatter_invalid() -> None:
    meta = SAMPLE_META.model_copy(update={"title": "A <b>bold</b> title"})
    findings = dialect_findings(prepare_text(meta, "Body text.\n"), source_ids=None)
    assert codes(findings)[0] == "frontmatter-invalid"


def test_escaped_citations_and_deprecated_names_warn() -> None:
    body = "We moved off Azure AD in 2025.\\[^lost-source\\]\n"
    text = prepare_text(SAMPLE_META, body)
    escaped = [f for f in dialect_findings(text, source_ids=None) if f.code == "citation-escaped"]
    assert [(f.severity, f.overridable) for f in escaped] == [("warn", True)]
    names = standards_findings(text, catalog=seed_catalog(SAMPLE_NOW), pillars=PILLARS)
    assert [(f.code, f.severity) for f in names] == [("deprecated-name", "warn")]
    assert body[names[0].start or 0 : names[0].end or 0] == "Azure AD"
    assert "Microsoft Entra ID" in names[0].message


def test_standards_ids_in_prose_and_inline_code_are_validated() -> None:
    body = (
        "Prompt injection is LLM11, and `LLM12` in code counts too.\n\n"
        "```yaml\nid: LLM13\n```\n\n"
        "See [LLM01](https://genai.owasp.org/llmrisk/llm01).\n"
    )
    text = prepare_text(SAMPLE_META, body)
    findings = standards_findings(text, catalog=seed_catalog(SAMPLE_NOW), pillars=PILLARS)
    assert [(f.code, f.ref) for f in findings] == [
        ("standard-id-unknown", "LLM11"),
        ("standard-id-unknown", "LLM12"),
    ]
    assert [body[f.start or 0 : f.end or 0] for f in findings] == ["LLM11", "LLM12"]


class RaisingHttp:
    """An egress helper that fails the way ``SafeHttpClient`` does."""

    def __init__(self, error: Exception | None = None, *, delay: float = 0.0) -> None:
        self.error = error
        self.delay = delay
        self.requested: list[tuple[str, int | None]] = []

    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        self.requested.append((url, max_bytes))
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return EgressResponse(url=url, status=200, content_type="text/html", content=b"ok")


LINKED = f"See [the guide]({SAMPLE_URL}), [a section](#setup) and [me](mailto:me@site.example).\n"


async def run_network(
    body: str,
    http: StaticHttp | RaisingHttp,
    *,
    render: FakeRenderService | None = None,
    settings: GatesSettings | None = None,
) -> list[Finding]:
    return await network_findings(
        prepare_text(SAMPLE_META, body),
        egress=http,
        render=render,
        settings=settings or GatesSettings(),
    )


async def test_links_that_resolve_pass_and_only_https_links_are_fetched() -> None:
    http = StaticHttp()
    http.add(SAMPLE_URL, b"<html></html>")
    assert await run_network(LINKED, http) == []
    assert http.requested == [SAMPLE_URL]
    probe = RaisingHttp()
    await run_network(LINKED, probe)
    assert probe.requested == [(SAMPLE_URL, gate_lint.LINK_PROBE_BYTES)]


async def test_broken_links_block_overridably() -> None:
    findings = await run_network(LINKED, StaticHttp())  # 404 for every URL
    assert [(f.code, f.severity, f.overridable, f.ref) for f in findings] == [
        ("link-broken", "block", True, SAMPLE_URL)
    ]
    assert "404" in findings[0].message
    assert LINKED[findings[0].start or 0 : findings[0].end or 0] == SAMPLE_URL


@pytest.mark.parametrize(
    ("error", "code", "overridable"),
    [
        (EgressTransportError("connect_failed"), "link-unreachable", True),
        (EgressBlockedError("private_address"), "link-blocked", False),
    ],
)
async def test_unreachable_and_blocked_links(
    error: Exception, code: str, overridable: bool
) -> None:
    findings = await run_network(LINKED, RaisingHttp(error))
    assert [(f.code, f.overridable) for f in findings] == [(code, overridable)]


async def test_a_slow_link_times_out() -> None:
    settings = GatesSettings(link_timeout_seconds=0.01)
    findings = await run_network(LINKED, RaisingHttp(delay=1.0), settings=settings)
    assert codes(findings) == ["link-unreachable"]


async def test_links_over_the_cap_are_not_waved_through() -> None:
    body = "".join(f"Link [{n}](https://learn.microsoft.com/page-{n}).\n\n" for n in range(3))
    http = RaisingHttp()
    findings = await run_network(body, http, settings=GatesSettings(max_links_checked=1))
    assert len(http.requested) == 1
    assert [(f.code, f.severity, f.overridable) for f in findings] == [
        ("links-unchecked", "block", True)
    ]
    assert "2 more links" in findings[0].message


DIAGRAM = "Intro.\n\n```mermaid\ngraph TD; A-->B\n```\n\nOutro.\n"


async def test_diagrams_render_or_fail_hard() -> None:
    render = FakeRenderService()
    assert await run_network(DIAGRAM, StaticHttp(), render=render) == []
    assert render.calls == ["render_mermaid_svg"]
    render.fail_with = RenderFailed("mermaid_error", "bad diagram")
    failed = await run_network(DIAGRAM, StaticHttp(), render=render)
    assert [(f.code, f.overridable) for f in failed] == [("diagram-failed", False)]
    assert "mermaid_error" in failed[0].message
    assert DIAGRAM[failed[0].start or 0 :].startswith("```mermaid")
    render.fail_with = RuntimeError("anything")
    assert codes(await run_network(DIAGRAM, StaticHttp(), render=render)) == ["diagram-failed"]


async def test_diagrams_without_a_renderer_cannot_pass() -> None:
    findings = await run_network(DIAGRAM, StaticHttp(), render=None)
    assert [(f.code, f.overridable) for f in findings] == [("diagram-unchecked", False)]
    assert await run_network("No diagrams here.\n", StaticHttp(), render=None) == []
