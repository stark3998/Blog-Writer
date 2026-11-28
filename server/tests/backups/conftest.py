"""Shared fixtures for the backups tests: no services left installed, and a synthetic v1
database (every v1 container shape the migration reads, plus session containers holding a
canary token that must never be read)."""

from __future__ import annotations

import base64
import copy
import io
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from PIL import Image

from blogwriter.domain.ports import uninstall_services

TOKEN_CANARY = "TOKEN-CANARY-7f3a"  # noqa: S105 - a canary, not a secret
CONTENT_CANARY = "CONTENT-CANARY-91be"
"""Text inside v1 documents that must never appear in the migration's output or logs."""

type V1Data = dict[str, list[dict[str, Any]]]


def _image_b64(width: int, height: int, color: str, image_format: str) -> str:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=image_format)
    return base64.b64encode(out.getvalue()).decode("ascii")


PLACEHOLDER = "![x](data:image/gif;base64,R0lGODlhAQABAAAAACw=)"
POST_MARKDOWN = f"""---
title: "Agent identity in MCP servers"
date: "2026-03-01"
excerpt: "Why agents need their own identities."
tags: ["AI Agents", "MCP"]
---

![Agent identity in MCP servers](/api/images/img-hero)

# Agent identity in MCP servers

import Callout from "./callout"

Agents should not borrow the user's token. {CONTENT_CANARY} matters for **MCP** servers.

<Callout type="warning">Scope every grant.</Callout>

## The problem

Borrowed tokens leak scope. ![diagram](/api/images/img-2) and a placeholder {PLACEHOLDER}.

<img src="https://example.com/chart.png" alt="A chart">

### Details

```python
token = get_token()  # ![not an image](/api/images/never-read)
```

###### Deep detail

## What to do Monday

- Scope tokens per tool call
- Log every grant
"""


def v1_database() -> V1Data:
    return {
        "published-blogs": [
            {
                "id": "agent-identity-mcp",
                "slug": "agent-identity-mcp",
                "title": "Agent identity in MCP servers",
                "excerpt": "Why agents need their own identities.",
                "htmlContent": '<!DOCTYPE html><html><body><div class="frontmatter"><h1>Agent '
                "identity</h1></div><p>old render</p></body></html>",
                "mdxContent": POST_MARKDOWN,
                "sourceUrl": "https://example.com/source",
                "sourceType": "webpage",
                "tags": ["AI Agents", "MCP"],
                "date": "2026-03-01",
                "publishedAt": "2026-03-01T12:34:56.789012+00:00",
                "updatedAt": "2026-03-02T08:00:00.000001+00:00",
                "_rid": "rid",
                "_self": "self",
                "_etag": '"etag"',
                "_attachments": "attachments/",
                "_ts": 1772438400,
            },
            {
                "id": "okta-to-entra-lessons",
                "slug": "okta-to-entra-lessons",
                "title": "Okta to Entra: three lessons",
                "excerpt": "",
                "mdxContent": "## Lesson one\n\nMigrate the identity provider last, after "
                "every app speaks OIDC.\n",
                "sourceUrl": "",
                "sourceType": "github",
                "tags": [],
                "date": "",
                "publishedAt": "2026-04-10T09:00:00+00:00",
                "updatedAt": "2026-04-10T09:00:00+00:00",
            },
        ],
        "images": [
            {
                "id": "img-hero",
                "imageBase64": _image_b64(1536, 1024, "#336699", "JPEG"),
                "contentType": "image/jpeg",
            },
            {
                "id": "img-2",
                "imageBase64": _image_b64(400, 300, "#aa3300", "PNG"),
                "contentType": "image/png",
            },
            {
                "id": "img-svg",
                "imageBase64": base64.b64encode(b"<svg/>").decode("ascii"),
                "contentType": "image/svg+xml",
            },
        ],
        "drafts": [
            {
                "id": "0f1e2d3c-1111-2222-3333-444455556666",
                "title": "Agent identity in MCP servers",
                "slug": "agent-identity-mcp",
                "excerpt": "Why agents need their own identities.",
                "content": POST_MARKDOWN,
                "sourceUrl": "https://example.com/source",
                "sourceType": "webpage",
                "origin": "user",
                "tags": ["ai"],
                "createdAt": "2026-02-28T10:00:00+00:00",
                "updatedAt": "2026-03-01T12:00:00+00:00",
                "publishedSlug": "agent-identity-mcp",
                "publishedAt": "2026-03-01T12:34:56+00:00",
                "userId": "owner-user-id",
            },
            {
                "id": "1a2b3c4d-0000-0000-0000-000000000002",
                "title": "Feed news about identity",
                "slug": "feed-news-identity",
                "excerpt": "Auto-generated.",
                "content": "Generated text about Entra.\n",
                "sourceType": "webpage",
                "origin": "rss_crawl",
                "tags": ["identity"],
                "createdAt": "2026-05-01T00:00:00+00:00",
                "updatedAt": "2026-05-01T00:00:00+00:00",
            },
            {
                "id": "1a2b3c4d-0000-0000-0000-000000000003",
                "title": "My notes on agent governance",
                "slug": "",
                "excerpt": "",
                "content": "Some **owner** notes on NIST AI RMF governance.\n",
                "sourceType": "unknown",
                "origin": "user",
                "tags": [],
                "createdAt": "2026-06-01T00:00:00+00:00",
                "updatedAt": "2026-06-02T00:00:00+00:00",
            },
        ],
        "feed-sources": [
            {
                "id": "fs-1",
                "name": "Azure blog",
                "baseUrl": "https://azure.microsoft.com/blog",
                "feedUrl": "https://azure.microsoft.com/blog/feed/",
                "feedType": "rss",
                "topics": ["cloud security", "azure", "ai"],
                "autoPublishBlog": True,
                "autoPublishLinkedIn": True,
                "autoPublishTwitter": True,
                "crawlIntervalMinutes": 60,
                "maxArticleAgeDays": 7,
                "maxArticlesToGenerate": 3,
                "enabled": True,
                "lastCrawledAt": "2026-11-20T00:00:00+00:00",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-11-20T00:00:00+00:00",
            },
            {
                "id": "fs-2",
                "name": "Old identity blog",
                "baseUrl": "http://identity.example.org",
                "feedUrl": "",
                "feedType": "html",
                "topics": ["identity"],
                "autoPublishBlog": False,
                "enabled": False,
                "lastCrawledAt": "",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-02-01T00:00:00+00:00",
            },
        ],
        "crawled-articles": [
            {
                "id": "c1",
                "feedSourceId": "fs-1",
                "articleUrl": "https://azure.microsoft.com/blog/post-1?utm_source=feed",
                "title": f"Crawled title {CONTENT_CANARY}",
                "status": "skipped",
                "crawledAt": "2026-11-20T00:00:00+00:00",
            },
            {
                "id": "c2",
                "feedSourceId": "fs-deleted",
                "articleUrl": "https://example.com/orphan",
                "title": "Orphan",
                "crawledAt": "2026-11-20T00:00:00+00:00",
            },
            {
                "id": "c3",
                "feedSourceId": "fs-1",
                "articleUrl": "https://azure.microsoft.com/blog/old",
                "title": "Old",
                "crawledAt": "2025-01-01T00:00:00+00:00",
            },
        ],
        "keywords": [
            {
                "id": "cloud security",
                "topic": "cloud security",
                "keywords": ["entra", "zero trust", "entra", "conditional access"],
                "updatedAt": "2026-05-01T00:00:00+00:00",
            }
        ],
        "voice-profiles": [
            {
                "id": "vp-1",
                "userId": "owner-user-id",
                "name": "Default voice",
                "description": "How I write.",
                "tone": "direct",
                "styleNotes": "Short sentences.\nNo hype.",
                "sampleText": f"I write plainly. {CONTENT_CANARY}",
                "isDefault": True,
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-02T00:00:00+00:00",
            }
        ],
        "prompts": [{"id": "system", "name": "system", "content": "prompt text"}],
        "user-profiles": [{"id": "owner-user-id", "name": "Owner", "settings": {}}],
        "linkedin-sessions": [{"id": "s1", "accessToken": TOKEN_CANARY}],
        "linkedin-oauth-states": [{"id": "st1", "state": TOKEN_CANARY}],
        "twitter-sessions": [{"id": "s2", "accessToken": TOKEN_CANARY}],
        "medium-sessions": [{"id": "s3", "token": TOKEN_CANARY}],
    }


@pytest.fixture
def v1_data() -> V1Data:
    """A fresh synthetic v1 database (tests may change it)."""
    return copy.deepcopy(v1_database())


@pytest.fixture
def image_b64() -> Callable[[int, int, str, str], str]:
    """``image_b64(width, height, color, format)``: base64 of a real image, as v1 stored it."""
    return _image_b64


@pytest.fixture
def canaries() -> tuple[str, str]:
    """``(token, content)``: strings inside v1 data that the migration must never print."""
    return TOKEN_CANARY, CONTENT_CANARY


@pytest.fixture(autouse=True)
def no_installed_services() -> Iterator[None]:
    uninstall_services()
    yield
    uninstall_services()
