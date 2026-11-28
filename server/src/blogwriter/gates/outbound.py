"""Gate 7, outbound content: where the article links, and what it embeds. Every rule is hard.

- **Links and images** go only to the article pillar's research domains, to brief citations
  whose domain is allowlisted (``Citation.allowlisted``: on the pillar allowlist or approved by
  the owner) or to the owner's site (``BW_GATES_SITE_ORIGIN``). A domain covers its
  subdomains. ``#fragment`` and site-relative ``/path`` targets stay on the site. ``mailto:``
  only for an address at the site's domain. Anything else (``http:``, ``javascript:``, ``data:``,
  ``//host``) is refused.
- **No tracking parameters** (``utm_*`` and :data:`blogwriter.domain.hashing.TRACKING_PARAMETERS`).
- **No raw HTML, iframes or forms** (the dialect renders raw HTML as text, but none may ship).
- **Clearance**: when the article needs it (a case study, an anonymized story, or
  ``clearance.required``), the owner's attestation must name this exact version's SHA-256.
- An employer policy the owner never saved is a warning (``policy-not-configured``).

==========================  ==============================================================
code                        when
==========================  ==============================================================
``outbound-domain``         a link or image to a host that is not allowed
``outbound-scheme``         a target that is not https, ``mailto:``, ``#`` or ``/path``
``outbound-mailto``         an e-mail address outside the site's domain
``tracking-parameter``      ``utm_*``, ``fbclid``, ``gclid``... in a link
``outbound-html``           raw HTML
``outbound-iframe``         ``iframe``, ``frame``, ``object``, ``embed``
``outbound-form``           ``form``, ``input``, ``button``, ``select``, ``textarea``
``clearance-required``      no valid attestation for this version
``policy-not-configured``   (warn) the employer policy was never saved
==========================  ==============================================================
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Final
from urllib.parse import parse_qsl, urlsplit

from blogwriter.domain.articles import Article
from blogwriter.domain.common import Finding
from blogwriter.domain.hashing import TRACKING_PARAMETERS
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import EmployerPolicy, PillarSettings
from blogwriter.domain.stories import StoryEntry
from blogwriter.gates.document import ArticleText
from blogwriter.gates.markdown import HtmlFragment, Link, body_links, raw_html

__all__ = [
    "allowed_domains",
    "clearance_findings",
    "clearance_required",
    "host_allowed",
    "link_findings",
    "policy_findings",
]

_FRAME_TAGS: Final = frozenset({"iframe", "frame", "frameset", "object", "embed", "applet"})
_FORM_TAGS: Final = frozenset({"form", "input", "button", "select", "textarea", "option"})


def allowed_domains(
    *, pillar: str | None, pillars: PillarSettings, brief: Brief | None, site_host: str | None
) -> frozenset[str]:
    """Host names a link may point to (each covers its subdomains)."""
    domains: set[str] = set()
    chosen = pillars.get(pillar) if pillar is not None else None
    if chosen is not None:
        domains.update(chosen.research_domains)
    if brief is not None:
        domains.update(citation.domain for citation in brief.citations if citation.allowlisted)
    if site_host:
        domains.add(site_host)
    return frozenset(domain.lower().rstrip(".") for domain in domains)


def host_allowed(host: str, domains: Collection[str]) -> bool:
    name = host.lower().rstrip(".")
    return any(name == domain or name.endswith("." + domain) for domain in domains)


def _ref(url: str) -> str | None:
    if len(url) > 200 or not all("!" <= char <= "~" and char not in '"\\' for char in url):
        return None
    return url


def _block(code: str, message: str, start: int, end: int, ref: str | None = None) -> Finding:
    return Finding(
        code=code,
        severity="block",
        message=message,
        start=start,
        end=end,
        ref=ref,
        overridable=False,
    )


def link_findings(
    text: ArticleText,
    *,
    pillars: PillarSettings,
    brief: Brief | None,
    site_host: str | None,
) -> list[Finding]:
    """Gate 7's link, tracking and HTML rules over the body (deterministic, no I/O)."""
    domains = allowed_domains(
        pillar=text.meta.pillar, pillars=pillars, brief=brief, site_host=site_host
    )
    findings: list[Finding] = []
    for link in body_links(text.body):
        findings.extend(_check_link(link, domains, site_host))
    findings.extend(_html_findings(raw_html(text.body)))
    return findings


def _check_link(link: Link, domains: Collection[str], site_host: str | None) -> list[Finding]:
    url = link.url.strip()
    what = "image" if link.kind == "image" else "link"
    if url.startswith("#") or (url.startswith("/") and not url.startswith("//")):
        return []
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "mailto":
        address = parts.path.rsplit("@", 1)
        domain = address[1] if len(address) == 2 else ""
        if site_host and domain and host_allowed(domain, [site_host]):
            return []
        return [
            _block(
                "outbound-mailto",
                "An e-mail link outside your site's domain.",
                link.start,
                link.end,
            )
        ]
    if scheme != "https" or not parts.hostname:
        return [
            _block(
                "outbound-scheme",
                f"This {what} target is not allowed (https, #section or /path only).",
                link.start,
                link.end,
                _ref(url),
            )
        ]
    findings: list[Finding] = []
    if not host_allowed(parts.hostname, domains):
        findings.append(
            _block(
                "outbound-domain",
                f"This {what} goes to a domain that is not on the pillar allowlist, an "
                "allowlisted brief source or your site.",
                link.start,
                link.end,
                _ref(url),
            )
        )
    tracking = [
        key
        for key, _ in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower().startswith("utm_") or key.lower() in TRACKING_PARAMETERS
    ]
    if tracking:
        findings.append(
            _block(
                "tracking-parameter",
                "Remove the tracking parameters from this link.",
                link.start,
                link.end,
                _ref(url),
            )
        )
    return findings


def _html_findings(fragments: Iterable[HtmlFragment]) -> list[Finding]:
    findings: list[Finding] = []
    for fragment in fragments:
        if fragment.tag in _FRAME_TAGS:
            code, message = "outbound-iframe", "Embedded frames are not allowed."
        elif fragment.tag in _FORM_TAGS:
            code, message = "outbound-form", "Forms are not allowed."
        else:
            code, message = "outbound-html", "Raw HTML is not allowed. Use the editor's blocks."
        findings.append(_block(code, message, fragment.start, fragment.end))
    return findings


def clearance_required(article: Article, *, stories: Iterable[StoryEntry]) -> bool:
    """Whether this article needs the owner's per-version attestation (computed here too, so a
    missing flag never waives it)."""
    used = {ref.id for ref in article.owner_signals if ref.kind == "story"}
    anonymized = any(
        story.id in used and story.confidentiality == "anonymized" for story in stories
    )
    return article.clearance.required or article.meta.format == "case-study" or anonymized


def clearance_findings(
    article: Article, *, stories: Iterable[StoryEntry], content_sha256: str
) -> list[Finding]:
    if not clearance_required(article, stories=stories):
        return []
    clearance = article.clearance
    if clearance.attested_at is not None and clearance.content_sha256 == content_sha256:
        return []
    return [
        Finding(
            code="clearance-required",
            severity="block",
            message=(
                "This version needs your clearance attestation (case study or anonymized "
                "story). Attest it after your last edit."
            ),
            overridable=False,
        )
    ]


def policy_findings(policy: EmployerPolicy) -> list[Finding]:
    if policy.configured:
        return []
    return [
        Finding(
            code="policy-not-configured",
            severity="warn",
            message="The employer policy is not set up yet (Settings > Policy).",
            overridable=True,
        )
    ]
