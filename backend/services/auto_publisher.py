"""Auto Publisher Service — Generate blog + LinkedIn posts from crawled articles."""

import json
import logging
import os
from typing import Any

from backend.db.cosmos_client import (
    create_draft,
    get_linkedin_session,
    list_feed_sources,
    publish_blog,
    has_linkedin_post_today,
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
        origin="rss_crawl",
        tags=feed_source.get("topics", []),
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

    # LinkedIn data is returned for deferred selection (best-post-per-day)
    if linkedin_data:
        result["linkedin_data"] = {
            "title": blog_data["title"],
            "post_text": linkedin_data.get("post_text", ""),
            "image_url": linkedin_data.get("image_url", "") or hero_image_url or "",
            "article_url": article_url,
            "blog_url": blog_url,
        }

    return result


def select_best_post(candidates: list[dict[str, Any]]) -> int:
    """Use the post selector prompt to pick the most technical LinkedIn post.

    Args:
        candidates: List of dicts with keys: title, post_text, article_url.

    Returns:
        Index of the best candidate (0-based).
    """
    if len(candidates) <= 1:
        return 0

    from backend.routers.prompts import load_prompt_content

    system_prompt = load_prompt_content("post_selector_prompt")

    payload = [
        {
            "index": i,
            "title": c["title"],
            "post_text": c["post_text"],
            "article_url": c.get("article_url", ""),
        }
        for i, c in enumerate(candidates)
    ]

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o")

    if api_key:
        client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
    else:
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        client = AzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=token_provider, api_version=api_version)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.3,
            max_completion_tokens=500,
        )
        raw = response.choices[0].message.content or "{}"
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        idx = int(result.get("selected_index", 0))
        reasoning = result.get("reasoning", "")
        logger.info(f"Best post selected: index={idx}, reasoning={reasoning}")
        if 0 <= idx < len(candidates):
            return idx
        logger.warning(f"Selected index {idx} out of range, defaulting to 0")
        return 0
    except Exception as exc:
        logger.error(f"Post selector failed: {exc}, defaulting to first candidate")
        return 0


def publish_best_linkedin_post(
    candidates: list[dict[str, Any]],
    feed_source: dict[str, Any],
) -> dict[str, Any] | None:
    """Select the best LinkedIn post from candidates and publish it.

    Checks the daily limit first. Returns publish result or None if skipped.
    """
    if not candidates:
        logger.info("No LinkedIn candidates to select from")
        return None

    auto_publish_linkedin = feed_source.get("autoPublishLinkedIn", False)
    if not auto_publish_linkedin:
        logger.info("Auto-publish LinkedIn is disabled for this feed source")
        return {"skipped": True, "reason": "auto_publish_disabled"}

    session_id = _get_active_linkedin_session_id()
    if not session_id:
        logger.warning("Auto-publish LinkedIn requested but no active session found")
        return {"skipped": True, "reason": "no_linkedin_session"}

    # Check daily limit
    if has_linkedin_post_today():
        logger.info("LinkedIn post already published today — skipping")
        return {"skipped": True, "reason": "daily_limit"}

    # Select the best post
    best_idx = select_best_post(candidates)
    best = candidates[best_idx]

    try:
        li_result = publish_member_post(
            session_id=session_id,
            post_text=best["post_text"],
            visibility="PUBLIC",
            image_url=best.get("image_url", ""),
        )
        logger.info(f"Best LinkedIn post published: {li_result.get('post_id', '')} — '{best['title'][:60]}'")
        return {
            "post_id": li_result.get("post_id", ""),
            "selected_index": best_idx,
            "title": best["title"],
            "status_code": li_result.get("status_code", 0),
        }
    except Exception as exc:
        logger.error(f"LinkedIn auto-publish failed for best post: {exc}")
        return None
