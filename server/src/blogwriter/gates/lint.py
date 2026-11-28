"""Gate 5, lint: dialect conformance, standards ids, links that resolve, diagrams that render.

Deterministic part (:func:`dialect_findings`, :func:`standards_findings`): the publish linter
over **exactly** ``compose_document(meta, body)`` with the brief's citation ids
(``publish.lint.lint_article``; errors block and are never overridable), converted with
``from_publish_finding(..., body_start=frontmatter_length(meta))`` so spans index the body;
standards ids (``validate_standard_ids``); deprecated product names from the pillars' renames
(warn) and escaped citation labels left by the normalizer (warn).

Network part (:func:`network_findings`): every distinct https link and image is fetched with
the egress helper (at most ``BW_GATES_MAX_LINKS_CHECKED``; GET, small byte cap) and every
Mermaid fence is rendered with ``services.render.render_mermaid_svg``.

=======================  ========  ===============================================================
code                     blocks    when
=======================  ========  ===============================================================
publish lint codes       errors    ``lint_article`` (``raw-html``, ``citation-unknown-source``...)
``meta-incomplete``      hard      frontmatter fields unset
``frontmatter-invalid``  hard      a frontmatter value the dialect refuses
``diagram-failed``       hard      a Mermaid fence that does not render
``diagram-unchecked``    hard      diagrams but no render service
``link-blocked``         hard      the egress policy refuses the address
``link-broken``          yes       HTTP 4xx/5xx
``link-unreachable``     yes       timeout, DNS, TLS or connection failure
``links-unchecked``      yes       more distinct links than the cap
``standard-id-*``        see       :mod:`blogwriter.gates.standards`
``deprecated-name``      no        a pillar rename's old name
``citation-escaped``     no        ``\\[^label\\]`` text (an undefined citation the normalizer
                                   escaped)
=======================  ========  ===============================================================
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Collection
from datetime import datetime
from typing import Final

from blogwriter.core.egress import (
    EgressBlockedError,
    EgressError,
    EgressResponseError,
    EgressTooLargeError,
)
from blogwriter.domain.common import Finding, from_publish_finding
from blogwriter.domain.ports import HttpGetter, RenderFailed, RenderService
from blogwriter.domain.settings import PillarSettings, StandardsCatalog
from blogwriter.gates.document import ArticleText
from blogwriter.gates.markdown import body_links, mermaid_diagrams
from blogwriter.gates.settings import GatesSettings
from blogwriter.gates.standards import validate_standard_ids
from blogwriter.publish.lint import lint_article

__all__ = [
    "LINK_PROBE_BYTES",
    "dialect_findings",
    "network_findings",
    "standards_findings",
]

LINK_PROBE_BYTES: Final = 256 * 1024
"""Enough to see a status; a larger body still proves the link resolves."""
_LINK_CONCURRENCY: Final = 4
_ESCAPED_CITATION_RE: Final = re.compile(r"\\\[\^[^\]\s\\]+\\\]")


def dialect_findings(text: ArticleText, *, source_ids: Collection[str] | None) -> list[Finding]:
    """The publish linter over the composed document (or the body alone when the frontmatter
    cannot be composed: the ``meta-incomplete`` blocker then stands for it)."""
    findings: list[Finding] = []
    if text.compose_error is not None:
        findings.append(text.compose_error)
    document = text.checked
    raw = lint_article(
        document, require_frontmatter=text.document is not None, source_ids=source_ids
    )
    findings.extend(
        from_publish_finding(finding, document, body_start=text.body_start) for finding in raw
    )
    for match in _ESCAPED_CITATION_RE.finditer(text.body):
        findings.append(
            Finding(
                code="citation-escaped",
                severity="warn",
                message="Looks like a citation that lost its source. Cite it again or remove it.",
                start=match.start(),
                end=match.end(),
                overridable=True,
            )
        )
    return findings


def standards_findings(
    text: ArticleText,
    *,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    now: datetime | None = None,
) -> list[Finding]:
    """Standards ids (in the prose and inline code: ids are often written as code) and
    deprecated names (in the prose)."""
    findings = validate_standard_ids(_prose_and_inline_code(text), catalog, now=now)
    findings.extend(_deprecated_names(text.view.text, pillars))
    return findings


def _prose_and_inline_code(text: ArticleText) -> str:
    """The prose view with inline code put back (same length as the body)."""
    chars = list(text.view.text)
    for start, end in text.view.code_spans:
        chars[start:end] = text.body[start:end]
    return "".join(chars)


def _deprecated_names(prose: str, pillars: PillarSettings) -> list[Finding]:
    renames: dict[str, str] = {}
    for pillar in pillars.pillars:
        renames.update(pillar.renames)
    findings: list[Finding] = []
    for old, new in sorted(renames.items(), key=lambda item: -len(item[0])):
        if not old.strip():
            continue
        pattern = re.compile(rf"(?<![\w-]){re.escape(old)}(?![\w-])", re.IGNORECASE)
        for match in pattern.finditer(prose):
            if any(
                f.start is not None and f.start <= match.start() < (f.end or 0) for f in findings
            ):
                continue
            findings.append(
                Finding(
                    code="deprecated-name",
                    severity="warn",
                    message=f"Deprecated name. Use “{new[:200]}”.",
                    start=match.start(),
                    end=match.end(),
                    overridable=True,
                )
            )
    return findings


# --------------------------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------------------------


def _probe_targets(text: ArticleText) -> list[tuple[str, int, int]]:
    seen: dict[str, tuple[int, int]] = {}
    for link in body_links(text.body):
        if link.url.lower().startswith("https://") and link.url not in seen:
            seen[link.url] = (link.start, link.end)
    return [(url, start, end) for url, (start, end) in seen.items()]


async def network_findings(
    text: ArticleText,
    *,
    egress: HttpGetter,
    render: RenderService | None,
    settings: GatesSettings,
) -> list[Finding]:
    """Links that resolve and diagrams that render (see the module docs)."""
    findings = await _link_findings(text, egress=egress, settings=settings)
    findings.extend(await _diagram_findings(text, render=render))
    return findings


async def _link_findings(
    text: ArticleText, *, egress: HttpGetter, settings: GatesSettings
) -> list[Finding]:
    targets = await asyncio.to_thread(_probe_targets, text)
    checked = targets[: settings.max_links_checked]
    skipped = targets[settings.max_links_checked :]
    findings: list[Finding] = []
    if skipped:
        start, end = skipped[0][1], skipped[0][2]
        findings.append(
            Finding(
                code="links-unchecked",
                severity="block",
                message=(
                    f"{len(skipped)} more links were not checked (the limit is "
                    f"{settings.max_links_checked}). Check them by hand before publishing."
                ),
                start=start,
                end=end,
                overridable=True,
            )
        )
    semaphore = asyncio.Semaphore(_LINK_CONCURRENCY)

    async def probe(url: str, start: int, end: int) -> Finding | None:
        async with semaphore:
            return await _probe(
                egress, url, start, end, limit_seconds=settings.link_timeout_seconds
            )

    results = await asyncio.gather(*(probe(url, start, end) for url, start, end in checked))
    findings.extend(result for result in results if result is not None)
    return findings


async def _probe(
    egress: HttpGetter, url: str, start: int, end: int, *, limit_seconds: float
) -> Finding | None:
    ref = url if len(url) <= 200 and _printable(url) else None
    try:
        async with asyncio.timeout(limit_seconds):
            response = await egress.get(url, max_bytes=LINK_PROBE_BYTES)
    except (EgressTooLargeError, EgressResponseError):
        return None  # the server answered; the body is just big or oddly encoded
    except EgressBlockedError:
        return Finding(
            code="link-blocked",
            severity="block",
            message="This link points somewhere the app refuses to fetch (a private address).",
            start=start,
            end=end,
            ref=ref,
            overridable=False,
        )
    except (EgressError, TimeoutError):
        return Finding(
            code="link-unreachable",
            severity="block",
            message="This link did not respond (timeout or connection failure).",
            start=start,
            end=end,
            ref=ref,
            overridable=True,
        )
    if response.status >= 400:
        return Finding(
            code="link-broken",
            severity="block",
            message=f"This link answers HTTP {response.status}.",
            start=start,
            end=end,
            ref=ref,
            overridable=True,
        )
    return None


def _printable(text: str) -> bool:
    return all("!" <= char <= "~" and char not in '"\\' for char in text)


async def _diagram_findings(text: ArticleText, *, render: RenderService | None) -> list[Finding]:
    diagrams = await asyncio.to_thread(mermaid_diagrams, text.body)
    if not diagrams:
        return []
    if render is None:
        return [
            Finding(
                code="diagram-unchecked",
                severity="block",
                message="Diagrams cannot be checked: the renderer is not available.",
                start=diagram.start,
                end=diagram.end,
                overridable=False,
            )
            for diagram in diagrams
        ]
    findings: list[Finding] = []
    for diagram in diagrams:
        try:
            await render.render_mermaid_svg(diagram.source)
        except RenderFailed as exc:
            findings.append(_diagram_failed(diagram.start, diagram.end, exc.code))
        except Exception:  # any failure to render is a failing diagram
            findings.append(_diagram_failed(diagram.start, diagram.end, "error"))
    return findings


def _diagram_failed(start: int, end: int, code: str) -> Finding:
    reason = code if re.fullmatch(r"[a-z_]{1,40}", code) else "error"
    return Finding(
        code="diagram-failed",
        severity="block",
        message=f"This diagram does not render ({reason}).",
        start=start,
        end=end,
        overridable=False,
    )
