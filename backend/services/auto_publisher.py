"""Auto Publisher Service — Generate blog + LinkedIn posts from crawled articles."""

import logging
import os
from typing import Any

from backend.db.cosmos_client import (
    create_draft,
    get_linkedin_session,
    list_feed_sources,
    publish_blog,
)
from backend.services.blog_service import analyze_source, generate_blog_post, _parse_blog_response
from backend.services.export_service import _convert_to_html, _strip_frontmatter
from backend.services.image_generator import ensure_hero_image
from backend.services.linkedin_service import compose_linkedin_post
from backend.tools.linkedin_publisher import publish_member_post

logger = logging.getLogger(__name__)


def _get_active_linkedin_session_id() -> str | None:
    """Find the first active LinkedIn OAuth session, if any.

    Checks the LINKEDIN_AUTO_SESSION_ID env var first, then scans for any valid session.
    """
    session_id = os.environ.get("LINKEDIN_AUTO_SESSION_ID", "").strip()
    if session_id:
        session = get_linkedin_session(session_id)
        if session and session.get("accessToken"):
            return session_id
    return None


def process_relevant_article(
    article: dict[str, Any], feed_source: dict[str, Any]
) -> dict[str, Any]:
    """Process a relevant article: generate blog, optionally publish, compose LinkedIn post.

    Args:
        article: Dict with keys: url, title, summary, published.
        feed_source: The feed source config from Cosmos DB.

    Returns:
        Dict with keys: draft_id, linkedin_post_id, status.
    """
    article_url = article["url"]
    auto_publish_blog = feed_source.get("autoPublishBlog", False)
    auto_publish_linkedin = feed_source.get("autoPublishLinkedIn", False)

    logger.info(f"Processing article: {article['title'][:80]} ({article_url})")

    result: dict[str, Any] = {
        "draft_id": "",
        "linkedin_post_id": "",
        "status": "drafted",
    }

    # Step 1: Analyze and generate blog post
    analysis = analyze_source(article_url)
    source_type = analysis.get("_source_type", "webpage")
    blog_data = generate_blog_post(analysis)

    # Step 1.5: Ensure hero image (source image or AI-generated)
    media_assets = blog_data.get("media_assets", [])
    topics = feed_source.get("topics", ["cloud security", "azure", "ai"])
    mdx_content, hero_image_url = ensure_hero_image(
        blog_content=blog_data["mdx_content"],
        title=blog_data["title"],
        excerpt=blog_data["excerpt"],
        media_assets=media_assets,
        topics=topics,
    )
    blog_data["mdx_content"] = mdx_content
    result["hero_image_url"] = hero_image_url

    # Step 2: Save as draft
    draft = create_draft(
        title=blog_data["title"],
        slug=blog_data["slug"],
        excerpt=blog_data["excerpt"],
        content=blog_data["mdx_content"],
        source_url=article_url,
        source_type=source_type,
    )
    result["draft_id"] = draft["id"]
    logger.info(f"Draft created: {draft['id']} for article {article_url}")

    # Step 3: Auto-publish blog if configured
    if auto_publish_blog:
        try:
            html_content = _convert_to_html(blog_data["mdx_content"])
            metadata, _ = _strip_frontmatter(blog_data["mdx_content"])
            tags = []
            if isinstance(metadata, dict) and "tags" in metadata:
                import re
                tags = re.findall(r'"([^"]+)"', str(metadata.get("tags", "")))

            publish_blog(
                slug=blog_data["slug"],
                title=blog_data["title"],
                excerpt=blog_data["excerpt"],
                html_content=html_content,
                mdx_content=blog_data["mdx_content"],
                source_url=article_url,
                source_type=source_type,
                tags=tags,
            )
            result["status"] = "published"
            logger.info(f"Blog auto-published: {blog_data['slug']}")
        except Exception as exc:
            logger.error(f"Auto-publish blog failed for {article_url}: {exc}")
            # Draft was already saved, so continue

    # Step 4: Compose LinkedIn post (promote our blog, attribute the source)
    blog_base = os.environ.get("BLOG_BASE_URL", "").rstrip("/")
    blog_url = f"{blog_base}/blog/{blog_data['slug']}" if blog_base else ""

    try:
        linkedin_data = compose_linkedin_post(
            blog_content=blog_data["mdx_content"],
            title=blog_data["title"],
            excerpt=blog_data["excerpt"],
            blog_url=blog_url,
            source_url=article_url,
        )
        # Ensure LinkedIn post uses the hero image if compose didn't find one
        if hero_image_url and not linkedin_data.get("image_url"):
            linkedin_data["image_url"] = hero_image_url
        result["linkedin_post_text"] = linkedin_data.get("post_text", "")
        logger.info(f"LinkedIn post composed for: {blog_data['title'][:60]}")
    except Exception as exc:
        logger.error(f"LinkedIn compose failed for {article_url}: {exc}")
        linkedin_data = None

    # Step 5: Auto-publish LinkedIn if configured and session is active
    if auto_publish_linkedin and linkedin_data:
        session_id = _get_active_linkedin_session_id()
        if session_id:
            try:
                li_result = publish_member_post(
                    session_id=session_id,
                    post_text=linkedin_data["post_text"],
                    visibility="PUBLIC",
                    image_url=linkedin_data.get("image_url", ""),
                )
                result["linkedin_post_id"] = li_result.get("post_id", "")
                result["status"] = "published"
                logger.info(f"LinkedIn post auto-published: {li_result.get('post_id', '')}")
            except Exception as exc:
                logger.error(f"LinkedIn auto-publish failed: {exc}")
        else:
            logger.warning("Auto-publish LinkedIn requested but no active session found")

    return result
