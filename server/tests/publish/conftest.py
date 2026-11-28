"""Fixtures for the published-blogs v2 contract tests.

``data/full-article.html`` and ``data/full-article.json`` are the reviewed render goldens of
``contracts/fixtures/corpus/17-full-article.md`` from the Phase 0 editor-dialect spike
(markdown-it-py renderer + nh3 with the shared profile). They stand in for real ``htmlContent``
and metadata until the server renderer lands.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from blogwriter.publish.contract import PublishedContract, default_contract

DATA_DIR = Path(__file__).parent / "data"
REPO_DIR = Path(__file__).resolve().parents[3]

Doc = dict[str, Any]
DocFactory = Callable[[], Doc]


def _sha256(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture
def contracts_dir() -> Path:
    return REPO_DIR / "contracts"


@pytest.fixture
def contract() -> PublishedContract:
    return default_contract()


@pytest.fixture
def now() -> datetime:
    """The reference instant every document test validates against."""
    return datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def full_article_html() -> str:
    return (DATA_DIR / "full-article.html").read_text(encoding="utf-8")


@pytest.fixture
def published_doc(full_article_html: str) -> DocFactory:
    """A realistic published document using every optional field; a fresh copy per call."""
    meta: dict[str, Any] = json.loads((DATA_DIR / "full-article.json").read_text(encoding="utf-8"))
    front: dict[str, Any] = meta["frontmatter"]
    slug: str = front["slug"]
    media = f"/blog-media/{slug}"
    diagram = f"{media}/gateway-3f2a9c.svg"
    doc: Doc = {
        "id": slug,
        "slug": slug,
        "schemaVersion": 2,
        "status": "published",
        "title": front["title"],
        "excerpt": front["excerpt"],
        "htmlContent": full_article_html
        + f'<p><img src="{diagram}" alt="Gateway diagram" width="1200" height="640"></p>\n',
        "mdxContent": f'---\ntitle: "{front["title"]}"\nslug: {slug}\n---\n\nBody.\n',
        "sourceUrl": "https://genai.owasp.org/llm-top-10/",
        "sourceType": "original",
        "publishedAt": "2026-11-20T14:30:00Z",
        "updatedAt": "2026-11-24T09:15:00Z",
        "date": "2026-11-20",
        "tags": front["tags"],
        "pillar": front["pillar"],
        "format": front["format"],
        "series": front["series"],
        "heroImage": {
            "url": f"{media}/hero-7d1e0b.png",
            "alt": "Least-privilege tool calls for AI agents",
            "width": 1600,
            "height": 900,
        },
        "ogImage": {"url": f"{media}/og-7d1e0b.png", "width": 1200, "height": 630},
        "assets": [
            {"path": f"{media}/{name}", "blob": f"{slug}/{name}", "sha256": _sha256(name)}
            for name in ("hero-7d1e0b.png", "og-7d1e0b.png", "gateway-3f2a9c.svg")
        ],
        "readingTimeMinutes": meta["readingTimeMinutes"],
        "wordCount": meta["wordCount"],
        "toc": [
            {"id": entry["id"], "text": entry["text"], "level": entry["level"]}
            for entry in meta["toc"]
        ],
        "citations": [
            {
                "id": entry["id"],
                "title": entry["text"],
                "url": entry["url"],
                "publisher": entry["text"].split(".")[0],
                "date": "2025",
            }
            for entry in meta["citations"]
        ],
        "corrections": [{"date": "2026-11-24", "note": "Clarified the <token lifetime> example."}],
        "canonicalUrl": f"https://jatinmadan.com/blog/{slug}",
        "seo": {
            "metaTitle": "Least-privilege tool calls for AI agents",
            "metaDescription": front["excerpt"],
        },
    }
    return lambda: copy.deepcopy(doc)


@pytest.fixture
def minimal_doc() -> DocFactory:
    """A published document with only the required fields, empty lists and remote images."""
    doc: Doc = {
        "id": "minimal-post",
        "slug": "minimal-post",
        "schemaVersion": 2,
        "status": "published",
        "title": "Minimal post",
        "excerpt": "The smallest valid published document.",
        "htmlContent": "<p>Hello.</p>",
        "publishedAt": "2026-11-26T11:59:00Z",
        "updatedAt": "2026-11-26T11:59:00Z",
        "tags": [],
        "pillar": "agents",
        "format": "news-analysis",
        "heroImage": {
            "url": "https://images.example/hero.png",
            "alt": "Hero",
            "width": 1600,
            "height": 900,
        },
        "ogImage": {"url": "https://images.example/og.png", "width": 1200, "height": 630},
        "assets": [],
        "readingTimeMinutes": 1,
        "wordCount": 1,
        "toc": [],
        "citations": [],
        "corrections": [],
    }
    return lambda: copy.deepcopy(doc)


@pytest.fixture
def redirect_doc() -> DocFactory:
    doc: Doc = {
        "id": "least-privilege-tool-calls",
        "slug": "least-privilege-tool-calls",
        "schemaVersion": 2,
        "status": "redirect",
        "redirectTo": "least-privilege-tool-calls-for-ai-agents",
        "updatedAt": "2026-11-25T08:00:00Z",
    }
    return lambda: copy.deepcopy(doc)


@pytest.fixture
def unpublished_doc() -> DocFactory:
    doc: Doc = {
        "id": "withdrawn-post",
        "slug": "withdrawn-post",
        "schemaVersion": 2,
        "status": "unpublished",
        "updatedAt": "2026-11-25T08:00:00Z",
    }
    return lambda: copy.deepcopy(doc)


@pytest.fixture
def samples(
    published_doc: DocFactory,
    minimal_doc: DocFactory,
    redirect_doc: DocFactory,
    unpublished_doc: DocFactory,
) -> dict[str, DocFactory]:
    return {
        "published": published_doc,
        "minimal": minimal_doc,
        "redirect": redirect_doc,
        "unpublished": unpublished_doc,
    }
