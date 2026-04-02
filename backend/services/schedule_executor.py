"""Schedule Executor — Runs due scheduled publishes.

Periodically called by the scheduler to find and execute pending
scheduled publishes whose scheduledAt time has passed.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from backend.services.config import get_blog_base_url

from backend.db.cosmos_client import (
    get_draft,
    get_due_scheduled_publishes,
    update_scheduled_publish,
    publish_blog,
    get_linkedin_session,
    get_twitter_session,
    get_medium_session,
)
from backend.services.export_service import _convert_to_html, _strip_frontmatter

logger = logging.getLogger(__name__)


def _publish_to_blog(draft: dict[str, Any]) -> dict[str, Any]:
    """Publish a draft as a blog post."""
    content = draft.get("content", "")
    html_content = _convert_to_html(content)
    metadata, _ = _strip_frontmatter(content)

    title = draft.get("title", metadata.get("title", "Untitled"))
    excerpt = draft.get("excerpt", metadata.get("excerpt", ""))
    source_url = draft.get("sourceUrl", "")
    source_type = draft.get("sourceType", "")
    slug = draft.get("slug", draft["id"])
    date = metadata.get("date", "") if isinstance(metadata, dict) else ""

    import re
    tags: list[str] = []
    if isinstance(metadata, dict) and "tags" in metadata:
        tags = re.findall(r'"([^"]+)"', str(metadata.get("tags", "")))

    result = publish_blog(
        slug=slug,
        title=title,
        excerpt=excerpt,
        html_content=html_content,
        mdx_content=content,
        source_url=source_url,
        source_type=source_type,
        tags=tags,
        date=date,
    )

    blog_url = f"/blog/{slug}"

    # Update draft with publish info
    from backend.db.cosmos_client import update_draft
    update_draft(draft["id"], {
        "publishedSlug": slug,
        "publishedAt": result.get("publishedAt", ""),
        "publishedUrl": blog_url,
    })

    return {"platform": "blog", "blog_url": blog_url, "slug": slug}


def _publish_to_linkedin(draft: dict[str, Any]) -> dict[str, Any]:
    """Compose and publish a LinkedIn post from the draft."""
    from backend.services.linkedin_service import compose_linkedin_post
    from backend.tools.linkedin_publisher import publish_member_post

    # Find an active LinkedIn session
    session_id = os.environ.get("LINKEDIN_AUTO_SESSION_ID", "").strip()
    if not session_id:
        raise RuntimeError("No LinkedIn session configured (LINKEDIN_AUTO_SESSION_ID)")

    session = get_linkedin_session(session_id)
    if not session or not session.get("accessToken"):
        raise RuntimeError(f"LinkedIn session '{session_id}' not found or has no access token")

    content = draft.get("content", "")
    title = draft.get("title", "")
    excerpt = draft.get("excerpt", "")
    source_url = draft.get("sourceUrl", "")
    blog_base = get_blog_base_url()
    slug = draft.get("slug", draft["id"])
    blog_url = f"{blog_base}/blog/{slug}" if blog_base else ""

    li_data = compose_linkedin_post(
        blog_content=content,
        title=title,
        excerpt=excerpt,
        blog_url=blog_url,
        source_url=source_url,
    )

    result = publish_member_post(
        session_id=session_id,
        post_text=li_data.get("post_text", ""),
        visibility="PUBLIC",
        image_url=li_data.get("image_url", ""),
    )

    return {
        "platform": "linkedin",
        "post_id": result.get("post_id", ""),
        "status_code": result.get("status_code", 0),
    }


def _publish_to_twitter(draft: dict[str, Any]) -> dict[str, Any]:
    """Compose and publish a tweet from the draft."""
    from backend.services.twitter_service import compose_tweet
    from backend.tools.twitter_publisher import publish_tweet

    # Find an active Twitter session
    session_id = os.environ.get("TWITTER_AUTO_SESSION_ID", "").strip()
    if not session_id:
        raise RuntimeError("No Twitter session configured (TWITTER_AUTO_SESSION_ID)")

    session = get_twitter_session(session_id)
    if not session or not session.get("accessToken"):
        raise RuntimeError(f"Twitter session '{session_id}' not found or has no access token")

    content = draft.get("content", "")
    title = draft.get("title", "")
    excerpt = draft.get("excerpt", "")
    source_url = draft.get("sourceUrl", "")
    blog_base = get_blog_base_url()
    slug = draft.get("slug", draft["id"])
    blog_url = f"{blog_base}/blog/{slug}" if blog_base else ""

    tweet_data = compose_tweet(
        blog_content=content,
        title=title,
        excerpt=excerpt,
        blog_url=blog_url,
        source_url=source_url,
    )

    result = publish_tweet(
        session_id=session_id,
        tweet_text=tweet_data.get("tweet_text", ""),
    )

    return {
        "platform": "twitter",
        "tweet_id": result.get("tweet_id", ""),
        "status_code": result.get("status_code", 0),
    }


def _publish_to_medium(draft: dict[str, Any]) -> dict[str, Any]:
    """Publish draft content to Medium."""
    from backend.services.medium_service import prepare_medium_article
    from backend.tools.medium_publisher import publish_article

    # Find an active Medium session
    session_id = os.environ.get("MEDIUM_AUTO_SESSION_ID", "").strip()
    if not session_id:
        raise RuntimeError("No Medium session configured (MEDIUM_AUTO_SESSION_ID)")

    session = get_medium_session(session_id)
    if not session or not session.get("accessToken"):
        raise RuntimeError(f"Medium session '{session_id}' not found or has no access token")

    content = draft.get("content", "")
    title = draft.get("title", "")
    excerpt = draft.get("excerpt", "")
    blog_base = get_blog_base_url()
    slug = draft.get("slug", draft["id"])
    blog_url = f"{blog_base}/blog/{slug}" if blog_base else ""

    prepared = prepare_medium_article(
        blog_content=content,
        title=title,
        excerpt=excerpt,
        blog_url=blog_url,
    )

    result = publish_article(
        session_id=session_id,
        title=prepared["title"],
        content_html=prepared["html_content"],
        tags=prepared.get("tags", []),
        canonical_url=blog_url,
        publish_status="public",
    )

    return {
        "platform": "medium",
        "post_id": result.get("post_id", ""),
        "url": result.get("url", ""),
    }


PLATFORM_HANDLERS = {
    "blog": _publish_to_blog,
    "linkedin": _publish_to_linkedin,
    "twitter": _publish_to_twitter,
    "medium": _publish_to_medium,
}


def execute_due_schedules() -> int:
    """Find and execute all due scheduled publishes.

    Returns the number of schedules processed.
    """
    try:
        due_items = get_due_scheduled_publishes()
    except Exception as exc:
        logger.error(f"Failed to query due scheduled publishes: {exc}")
        return 0

    if not due_items:
        return 0

    logger.info(f"Found {len(due_items)} due scheduled publish(es)")
    processed = 0

    for schedule in due_items:
        schedule_id = schedule["id"]
        draft_id = schedule.get("draftId", "")
        platforms = schedule.get("platforms", [])

        logger.info(f"Executing schedule {schedule_id}: draft={draft_id}, platforms={platforms}")

        # Fetch the draft
        draft = get_draft(draft_id)
        if draft is None:
            update_scheduled_publish(schedule_id, {
                "status": "failed",
                "completedAt": datetime.now(timezone.utc).isoformat(),
                "error": f"Draft '{draft_id}' not found",
            })
            processed += 1
            continue

        errors: list[str] = []
        successes: list[str] = []

        for platform in platforms:
            handler = PLATFORM_HANDLERS.get(platform)
            if handler is None:
                errors.append(f"Unknown platform: {platform}")
                continue

            try:
                result = handler(draft)
                successes.append(platform)
                logger.info(f"Schedule {schedule_id}: {platform} published successfully: {result}")
            except Exception as exc:
                error_msg = f"{platform}: {str(exc)}"
                errors.append(error_msg)
                logger.error(f"Schedule {schedule_id}: {platform} failed: {exc}")

        # Determine final status
        now = datetime.now(timezone.utc).isoformat()
        if errors and not successes:
            update_scheduled_publish(schedule_id, {
                "status": "failed",
                "completedAt": now,
                "error": "; ".join(errors),
            })
        elif errors:
            # Partial success
            update_scheduled_publish(schedule_id, {
                "status": "completed",
                "completedAt": now,
                "error": "Partial: " + "; ".join(errors),
            })
        else:
            update_scheduled_publish(schedule_id, {
                "status": "completed",
                "completedAt": now,
            })

        processed += 1

    logger.info(f"Processed {processed} scheduled publish(es)")
    return processed
