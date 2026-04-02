"""Runtime config helpers — resolve settings from DB overrides or env vars."""

import os
import logging

logger = logging.getLogger(__name__)


def get_blog_base_url(user_id: str | None = None) -> str:
    """Resolve BLOG_BASE_URL: user setting override → env var → empty string.

    Args:
        user_id: If provided, check the user's settings first.
    """
    if user_id:
        try:
            from backend.db.cosmos_client import get_user_profile
            profile = get_user_profile(user_id)
            if profile:
                url = profile.get("settings", {}).get("blog_base_url", "")
                if url:
                    return url.rstrip("/")
        except Exception as exc:
            logger.debug(f"Could not read user settings for blog_base_url: {exc}")

    # Check all user profiles for a set value (single-user shortcut)
    if not user_id:
        try:
            from backend.db.cosmos_client import _get_user_profiles_container
            container = _get_user_profiles_container()
            items = list(container.query_items(
                query="SELECT TOP 1 c.settings.blog_base_url FROM c WHERE IS_DEFINED(c.settings.blog_base_url) AND c.settings.blog_base_url != ''",
                enable_cross_partition_query=True,
            ))
            if items and items[0].get("blog_base_url"):
                return items[0]["blog_base_url"].rstrip("/")
        except Exception as exc:
            logger.debug(f"Could not query user profiles for blog_base_url: {exc}")

    return os.environ.get("BLOG_BASE_URL", "").rstrip("/")
