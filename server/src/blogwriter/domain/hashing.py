"""Content hashes that several packages must compute identically.

- :func:`approval_hash`: the SHA-256 an approved LinkedIn post is bound to: everything the
  publisher sends except the author (text, title, media, format, poll, link exception). The
  publisher refuses to post when the post's current hash differs from the approved one.
- :func:`article_sha256`: identity of one exact article version (metadata plus Markdown body).
  Gate reports, clearance attestations and calibration bind to it.
- :func:`normalize_url` / :func:`url_sha256`: dedupe keys for feed items, ideas and citations
  (signals store only these hashes).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from blogwriter.domain.base import canonical_json, sha256_hex
from blogwriter.domain.common import MediaRef

__all__ = [
    "APPROVAL_HASH_VERSION",
    "TRACKING_PARAMETERS",
    "approval_hash",
    "article_sha256",
    "normalize_url",
    "url_sha256",
]

APPROVAL_HASH_VERSION: Final = 2
"""2: adds the format (which decides the content type), the poll and the link exception URL."""

TRACKING_PARAMETERS: Final = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "_hsenc",
        "_hsmi",
        "ref_src",
        "ref_url",
        "trk",
        "trackingid",
        "li_fat_id",
    }
)
"""Query parameters dropped by :func:`normalize_url` (plus every ``utm_*``)."""

_DEFAULT_PORTS: Final = {"http": 80, "https": 443}


def approval_hash(
    *,
    display_text: str,
    payload: str,
    document_title: str | None,
    media: Sequence[MediaRef],
    format: str,
    poll: Mapping[str, object] | None,
    link_url: str | None,
) -> str:
    """SHA-256 (hex) over exactly what goes to LinkedIn, the author aside: both text forms, the
    title, the media, the format (it picks the content type), the poll and the link exception.

    Media count by role, SHA-256 and alt text, in order. Any change to any of them changes the
    hash, so an edit after approval can never ship unnoticed.
    """
    material = {
        "v": APPROVAL_HASH_VERSION,
        "displayText": display_text,
        "payload": payload,
        "documentTitle": document_title,
        "media": [
            {"role": item.role, "sha256": item.sha256, "altText": item.alt_text} for item in media
        ],
        "format": format,
        "poll": dict(poll) if poll is not None else None,
        "linkUrl": link_url,
    }
    return sha256_hex(canonical_json(material))


def article_sha256(meta: Mapping[str, object], markdown: str) -> str:
    """SHA-256 (hex) of one article version: its metadata (camelCase JSON) and its body."""
    return sha256_hex(canonical_json({"meta": dict(meta), "markdown": markdown}))


def normalize_url(url: str) -> str:
    """A canonical form of an http(s) URL for dedupe (never for fetching).

    Lowercases scheme and host, drops a trailing dot, default ports, userinfo and the
    fragment, removes tracking parameters (``utm_*`` and :data:`TRACKING_PARAMETERS`) and sorts
    the remaining query parameters. An empty path becomes ``/``. ``ValueError`` for anything
    but an absolute http(s) URL with a host.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError("only absolute http(s) URLs with a host can be normalized")
    host = parts.hostname.rstrip(".").lower()
    try:
        port = parts.port
    except ValueError:
        raise ValueError("URL has an invalid port") from None
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    )
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_sha256(url: str) -> str:
    """SHA-256 (hex) of :func:`normalize_url`."""
    return sha256_hex(normalize_url(url))
