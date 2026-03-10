"""Publish Router — Publish blog posts to Cosmos DB and serve them publicly."""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.db.cosmos_client import get_published_blog, list_published_blogs, publish_blog
from backend.services.export_service import _convert_to_html, _strip_frontmatter

router = APIRouter(tags=["publish"])


def _blog_base_url() -> str:
    return os.environ.get("BLOG_BASE_URL", "http://localhost:8080").rstrip("/")


class PublishRequest(BaseModel):
    content: str
    slug: str
    title: str
    excerpt: str = ""
    source_url: str = ""
    source_type: str = ""
    tags: list[str] = []


class PublishResponse(BaseModel):
    blog_url: str
    slug: str
    title: str


class PublishedBlogResponse(BaseModel):
    slug: str
    title: str
    excerpt: str
    html_content: str
    source_url: str
    source_type: str
    tags: list[str]
    date: str
    published_at: str


# ---------- Publish ----------


@router.post("/api/publish", response_model=PublishResponse)
async def publish_blog_post(request: PublishRequest):
    """Publish a blog post to Cosmos DB."""
    try:
        html_content = _convert_to_html(request.content)

        metadata, _ = _strip_frontmatter(request.content)
        title = request.title or metadata.get("title", "Untitled")
        excerpt = request.excerpt or metadata.get("excerpt", "")
        source_url = request.source_url or metadata.get("source_url", "")
        source_type = request.source_type or metadata.get("source_type", "")
        date = metadata.get("date", "")

        # Extract tags from frontmatter if not provided
        tags = request.tags
        if not tags and "tags" in metadata:
            import re
            tags = re.findall(r'"([^"]+)"', metadata["tags"])

        result = publish_blog(
            slug=request.slug,
            title=title,
            excerpt=excerpt,
            html_content=html_content,
            mdx_content=request.content,
            source_url=source_url,
            source_type=source_type,
            tags=tags,
            date=date,
        )

        blog_url = f"{_blog_base_url()}/blog/{result['slug']}"
        return PublishResponse(blog_url=blog_url, slug=result["slug"], title=title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Publish failed: {str(e)}")


# ---------- Serve published blogs ----------


@router.get("/api/blog/{slug}", response_model=PublishedBlogResponse)
async def get_blog_json(slug: str):
    """Return published blog data as JSON (for frontend SPA rendering)."""
    blog = get_published_blog(slug)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    return PublishedBlogResponse(
        slug=blog["slug"],
        title=blog["title"],
        excerpt=blog.get("excerpt", ""),
        html_content=blog["htmlContent"],
        source_url=blog.get("sourceUrl", ""),
        source_type=blog.get("sourceType", ""),
        tags=blog.get("tags", []),
        date=blog.get("date", ""),
        published_at=blog.get("publishedAt", ""),
    )


@router.get("/api/blogs/published")
async def list_blogs(limit: int = 50):
    """List all published blogs (metadata only)."""
    return list_published_blogs(limit=limit)


@router.get("/blog/{slug}", response_class=HTMLResponse)
async def serve_blog_html(slug: str):
    """Serve a published blog as a full HTML page with Open Graph meta tags."""
    blog = get_published_blog(slug)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")

    title = blog.get("title", "Blog Post")
    excerpt = blog.get("excerpt", "")
    source_url = blog.get("sourceUrl", "")
    tags = blog.get("tags", [])
    blog_url = f"{_blog_base_url()}/blog/{slug}"
    html_body = blog.get("htmlContent", "")

    source_html = ""
    if source_url:
        source_html = f'<p style="font-size:0.875rem;color:#888;margin-bottom:1rem;">Source: <a href="{source_url}" style="color:#6366f1;">{source_url}</a></p>'

    tags_html = ""
    if tags:
        tag_spans = " ".join(
            f'<span style="display:inline-block;padding:0.15rem 0.6rem;border-radius:9999px;font-size:0.75rem;background:#eef2ff;color:#4f46e5;margin-right:0.25rem;">{t}</span>'
            for t in tags
        )
        tags_html = f'<div style="margin-bottom:1.5rem;">{tag_spans}</div>'

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{excerpt}">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{excerpt}">
<meta property="og:url" content="{blog_url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{excerpt}">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 800px;
    margin: 0 auto;
    padding: 2rem;
    line-height: 1.7;
    color: #1a1a1a;
    background: #fff;
  }}
  h1 {{ font-size: 2rem; margin-top: 2rem; color: #111; }}
  h2 {{ font-size: 1.5rem; margin-top: 1.8rem; color: #222; border-bottom: 1px solid #eee; padding-bottom: 0.3rem; }}
  h3 {{ font-size: 1.25rem; margin-top: 1.5rem; color: #333; }}
  code {{
    background: #f4f4f5;
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
    font-size: 0.9em;
    font-family: 'Fira Code', 'Consolas', monospace;
  }}
  pre {{
    background: #1e1e2e;
    color: #cdd6f4;
    padding: 1rem;
    border-radius: 8px;
    overflow-x: auto;
    line-height: 1.5;
  }}
  pre code {{
    background: none;
    padding: 0;
    color: inherit;
  }}
  blockquote {{
    border-left: 4px solid #6366f1;
    margin: 1rem 0;
    padding: 0.5rem 1rem;
    background: #f8f8ff;
    color: #555;
  }}
  img {{ max-width: 100%; border-radius: 8px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
  th {{ background: #f4f4f5; font-weight: 600; }}
  a {{ color: #6366f1; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{source_html}
{tags_html}
{html_body}
</body>
</html>"""
    return HTMLResponse(content=page)
