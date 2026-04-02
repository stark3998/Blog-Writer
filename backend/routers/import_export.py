"""Import Router — Bulk import blog posts from markdown, URLs, or WordPress exports."""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import create_draft
from backend.models.user import UserInfo
from backend.services.blog_service import analyze_source, generate_blog_post

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/import", tags=["import"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MarkdownEntry(BaseModel):
    title: str
    content: str
    source_url: str = ""
    tags: list[str] = []


class MarkdownImportRequest(BaseModel):
    entries: list[MarkdownEntry]


class UrlImportRequest(BaseModel):
    urls: list[str]


class WordPressImportRequest(BaseModel):
    xml_content: str


class ImportResult(BaseModel):
    draft_ids: list[str] = []
    errors: list[dict[str, Any]] = []


class WordPressImportResult(BaseModel):
    imported: int = 0
    skipped: int = 0
    draft_ids: list[str] = []
    errors: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Create a URL-friendly slug from a title."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"


def _extract_excerpt(content: str, max_length: int = 200) -> str:
    """Extract a plain-text excerpt from markdown content."""
    # Strip markdown headings, images, links markup
    text = re.sub(r"#+\s*", "", content)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\(.*?\)", r"\1", text)
    text = re.sub(r"[*_`~]", "", text)
    text = " ".join(text.split())
    if len(text) > max_length:
        text = text[:max_length].rsplit(" ", 1)[0] + "..."
    return text


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/markdown", response_model=ImportResult)
def import_markdown(
    body: MarkdownImportRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Import one or more blog posts from markdown text."""
    logger.info(f"User {user.user_id} importing {len(body.entries)} markdown entries")

    result = ImportResult()

    for idx, entry in enumerate(body.entries):
        try:
            slug = _slugify(entry.title)
            excerpt = _extract_excerpt(entry.content)
            draft = create_draft(
                title=entry.title,
                slug=slug,
                excerpt=excerpt,
                content=entry.content,
                source_url=entry.source_url,
                source_type="import",
                origin="import",
                tags=entry.tags if entry.tags else None,
                user_id=user.user_id,
            )
            result.draft_ids.append(draft["id"])
        except Exception as exc:
            logger.error(f"Failed to import markdown entry {idx} ({entry.title}): {exc}")
            result.errors.append({"index": idx, "title": entry.title, "error": str(exc)})

    logger.info(
        f"Markdown import complete: {len(result.draft_ids)} created, {len(result.errors)} errors"
    )
    return result


@router.post("/urls", response_model=ImportResult)
def import_urls(
    body: UrlImportRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Import blog posts by analyzing URLs and generating content."""
    logger.info(f"User {user.user_id} importing from {len(body.urls)} URLs")

    result = ImportResult()

    for url in body.urls:
        try:
            analysis = analyze_source(url)
            post = generate_blog_post(analysis)

            draft = create_draft(
                title=post["title"],
                slug=post["slug"],
                excerpt=post.get("excerpt", ""),
                content=post["mdx_content"],
                source_url=url,
                source_type=analysis.get("_source_type", "webpage"),
                origin="import",
                user_id=user.user_id,
            )
            result.draft_ids.append(draft["id"])
        except Exception as exc:
            logger.error(f"Failed to import URL {url}: {exc}")
            result.errors.append({"url": url, "error": str(exc)})

    logger.info(
        f"URL import complete: {len(result.draft_ids)} created, {len(result.errors)} errors"
    )
    return result


@router.post("/wordpress", response_model=WordPressImportResult)
def import_wordpress(
    body: WordPressImportRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Import blog posts from a WordPress WXR (XML) export."""
    logger.info(f"User {user.user_id} importing WordPress export")

    result = WordPressImportResult()

    try:
        root = ET.fromstring(body.xml_content)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid XML: {exc}")

    # WordPress WXR uses several namespaces
    ns = {
        "content": "http://purl.org/rss/1.0/modules/content/",
        "wp": "http://wordpress.org/export/1.2/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    }

    channel = root.find("channel")
    if channel is None:
        raise HTTPException(status_code=400, detail="Invalid WordPress export: no <channel> element found")

    items = channel.findall("item")
    logger.info(f"Found {len(items)} items in WordPress export")

    for item in items:
        # Only import posts (not pages, attachments, etc.)
        post_type_el = item.find("wp:post_type", ns)
        if post_type_el is not None and post_type_el.text != "post":
            result.skipped += 1
            continue

        # Only import published or draft posts
        status_el = item.find("wp:status", ns)
        status = status_el.text if status_el is not None else "publish"
        if status not in ("publish", "draft"):
            result.skipped += 1
            continue

        title_el = item.find("title")
        title = title_el.text if title_el is not None and title_el.text else "Untitled"

        content_el = item.find("content:encoded", ns)
        content = content_el.text if content_el is not None and content_el.text else ""

        # Extract tags (WordPress uses <category domain="post_tag">)
        tags = []
        for cat in item.findall("category"):
            if cat.get("domain") == "post_tag" and cat.text:
                tags.append(cat.text)

        # Extract link
        link_el = item.find("link")
        link = link_el.text if link_el is not None and link_el.text else ""

        try:
            slug = _slugify(title)
            excerpt = _extract_excerpt(content)
            draft = create_draft(
                title=title,
                slug=slug,
                excerpt=excerpt,
                content=content,
                source_url=link,
                source_type="wordpress",
                origin="import",
                tags=tags if tags else None,
                user_id=user.user_id,
            )
            result.draft_ids.append(draft["id"])
            result.imported += 1
        except Exception as exc:
            logger.error(f"Failed to import WordPress post '{title}': {exc}")
            result.errors.append({"title": title, "error": str(exc)})

    logger.info(
        f"WordPress import complete: {result.imported} imported, "
        f"{result.skipped} skipped, {len(result.errors)} errors"
    )
    return result
