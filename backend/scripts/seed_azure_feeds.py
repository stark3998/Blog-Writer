"""Seed Azure news feed sources into Cosmos DB.

Run once to pre-configure the major Microsoft/Azure RSS feeds:
    python -m backend.scripts.seed_azure_feeds

Feeds are created with autoPublishBlog=True, autoPublishTwitter=True,
autoPublishLinkedIn=True so articles are published automatically after crawling.
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

AZURE_FEEDS = [
    {
        "name": "Azure Blog",
        "base_url": "https://azure.microsoft.com/en-us/blog/",
        "feed_url": "https://azure.microsoft.com/en-us/blog/feed/",
        "topics": ["azure", "cloud", "infrastructure"],
    },
    {
        "name": "Azure Updates",
        "base_url": "https://azure.microsoft.com/en-us/updates/",
        "feed_url": "https://azure.microsoft.com/en-us/updates/feed/",
        "topics": ["azure", "updates", "preview", "ga"],
    },
    {
        "name": "Microsoft Tech Community — Azure",
        "base_url": "https://techcommunity.microsoft.com/t5/azure/ct-p/Azure",
        "feed_url": "https://techcommunity.microsoft.com/t5/azure/ct-p/Azure/rss",
        "topics": ["azure", "cloud", "microsoft"],
    },
    {
        "name": "Microsoft Security Blog",
        "base_url": "https://www.microsoft.com/en-us/security/blog/",
        "feed_url": "https://www.microsoft.com/en-us/security/blog/feed/",
        "topics": ["security", "entra", "defender", "sentinel", "microsoft"],
    },
    {
        "name": "Microsoft Developer Blogs",
        "base_url": "https://devblogs.microsoft.com/",
        "feed_url": "https://devblogs.microsoft.com/feed/",
        "topics": ["azure", "developer", "ai", "foundry"],
    },
    {
        "name": "Microsoft Entra Blog",
        "base_url": "https://techcommunity.microsoft.com/t5/microsoft-entra-blog/bg-p/Identity",
        "feed_url": "https://techcommunity.microsoft.com/t5/microsoft-entra-blog/bg-p/Identity/rss",
        "topics": ["entra", "identity", "azure-ad", "security"],
    },
    {
        "name": "Microsoft AI Blog",
        "base_url": "https://blogs.microsoft.com/ai/",
        "feed_url": "https://blogs.microsoft.com/ai/feed/",
        "topics": ["ai", "foundry", "copilot", "azure-openai"],
    },
]


def seed_feeds(
    auto_publish_blog: bool = True,
    auto_publish_linkedin: bool = True,
    auto_publish_twitter: bool = True,
    crawl_interval_minutes: int = 60,
    max_article_age_days: int = 3,
    max_articles_to_generate: int = 1,
    dry_run: bool = False,
) -> None:
    """Create all Azure feed sources in Cosmos DB.

    Skips any feed whose baseUrl already exists to avoid duplicates.
    """
    from backend.db.cosmos_client import create_feed_source, list_feed_sources

    existing = list_feed_sources()
    existing_base_urls = {f.get("baseUrl", "").rstrip("/") for f in existing}

    created = 0
    skipped = 0

    for feed in AZURE_FEEDS:
        base_url = feed["base_url"].rstrip("/")
        if base_url in existing_base_urls:
            logger.info(f"Skipping (already exists): {feed['name']}")
            skipped += 1
            continue

        if dry_run:
            logger.info(f"[DRY RUN] Would create: {feed['name']} — {feed['feed_url']}")
            created += 1
            continue

        try:
            result = create_feed_source(
                name=feed["name"],
                base_url=feed["base_url"],
                feed_url=feed["feed_url"],
                feed_type="rss",
                topics=feed["topics"],
                auto_publish_blog=auto_publish_blog,
                auto_publish_linkedin=auto_publish_linkedin,
                auto_publish_twitter=auto_publish_twitter,
                crawl_interval_minutes=crawl_interval_minutes,
                max_article_age_days=max_article_age_days,
                max_articles_to_generate=max_articles_to_generate,
            )
            logger.info(f"Created: {feed['name']} (id={result['id']})")
            created += 1
        except Exception as exc:
            logger.error(f"Failed to create {feed['name']}: {exc}")

    logger.info(f"\nDone. Created={created}, Skipped (already exist)={skipped}")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv

    # Require Cosmos connection env vars before running
    required = ["COSMOS_ENDPOINT", "COSMOS_DATABASE_NAME"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.error(f"Missing required env vars: {', '.join(missing)}")
        sys.exit(1)

    seed_feeds(dry_run=dry_run)
