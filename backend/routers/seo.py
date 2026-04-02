"""SEO Router — Track and analyze SEO metrics for published posts."""

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    get_published_blog,
    get_seo_history,
    get_latest_seo_snapshots,
    record_seo_snapshot,
)
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/seo", tags=["seo"])


class SEOSnapshotData(BaseModel):
    title_length: int = 0
    meta_description_length: int = 0
    word_count: int = 0
    heading_count: int = 0
    h1_count: int = 0
    h2_count: int = 0
    image_count: int = 0
    images_with_alt: int = 0
    internal_links: int = 0
    external_links: int = 0
    readability_score: float = 0
    keyword_density: dict[str, float] = {}


class SEOSnapshotResponse(BaseModel):
    id: str
    slug: str
    data: dict[str, Any]
    createdAt: str


def _analyze_seo(html_content: str, title: str, excerpt: str) -> dict[str, Any]:
    """Perform SEO analysis on blog content."""
    text = re.sub(r"<[^>]+>", "", html_content)
    words = text.split()
    word_count = len(words)

    headings = re.findall(r"<h[1-6][^>]*>", html_content, re.IGNORECASE)
    h1s = re.findall(r"<h1[^>]*>", html_content, re.IGNORECASE)
    h2s = re.findall(r"<h2[^>]*>", html_content, re.IGNORECASE)

    images = re.findall(r"<img[^>]*>", html_content, re.IGNORECASE)
    images_with_alt = len([i for i in images if 'alt="' in i and 'alt=""' not in i])

    internal_links = len(re.findall(r'href="/', html_content))
    external_links = len(re.findall(r'href="https?://', html_content))

    # Simple readability score (avg words per sentence)
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    avg_words_per_sentence = word_count / max(len(sentences), 1)
    readability = max(0, min(100, 100 - (avg_words_per_sentence - 15) * 5))

    # Top keyword density
    word_freq: dict[str, int] = {}
    for w in words:
        w_lower = w.lower().strip(".,!?;:")
        if len(w_lower) > 3:
            word_freq[w_lower] = word_freq.get(w_lower, 0) + 1
    top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    keyword_density = {w: round(c / max(word_count, 1) * 100, 2) for w, c in top_words}

    return {
        "title_length": len(title),
        "meta_description_length": len(excerpt),
        "word_count": word_count,
        "heading_count": len(headings),
        "h1_count": len(h1s),
        "h2_count": len(h2s),
        "image_count": len(images),
        "images_with_alt": images_with_alt,
        "internal_links": internal_links,
        "external_links": external_links,
        "readability_score": round(readability, 1),
        "keyword_density": keyword_density,
    }


@router.post("/analyze/{slug}")
async def analyze_post_seo(slug: str, user: UserInfo = Depends(get_current_user)):
    """Run SEO analysis on a published post and record the snapshot."""
    blog = get_published_blog(slug)
    if not blog:
        raise HTTPException(status_code=404, detail="Published blog not found")

    seo_data = _analyze_seo(
        html_content=blog.get("htmlContent", ""),
        title=blog.get("title", ""),
        excerpt=blog.get("excerpt", ""),
    )

    snapshot = record_seo_snapshot(slug, seo_data)
    return {"slug": slug, "data": seo_data, "snapshot_id": snapshot["id"]}


@router.get("/history/{slug}")
async def get_seo_tracking(slug: str, limit: int = 20):
    """Get SEO tracking history for a published post."""
    return get_seo_history(slug, limit)


@router.get("/overview")
async def get_seo_overview(user: UserInfo = Depends(get_current_user)):
    """Get latest SEO snapshots for all tracked posts."""
    return get_latest_seo_snapshots()
