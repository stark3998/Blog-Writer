"""Cosmos DB Client — Persistence layer for blog drafts.

Uses Azure Cosmos DB NoSQL API with either API Key or DefaultAzureCredential
to store and retrieve blog post drafts.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

logger = logging.getLogger(__name__)


_client: CosmosClient | None = None
_container = None
_linkedin_session_container = None
_linkedin_state_container = None
_twitter_session_container = None
_twitter_state_container = None
_medium_session_container = None
_published_blogs_container = None
_feed_sources_container = None
_crawled_articles_container = None
_crawl_jobs_container = None
_prompts_container = None
_keywords_container = None
_user_profiles_container = None


def _get_client() -> CosmosClient:
    """Get or create Cosmos client (lazy initialization)."""
    global _client

    if _client is not None:
        return _client

    endpoint = os.environ.get("COSMOS_ENDPOINT", "")
    api_key = os.environ.get("COSMOS_KEY")

    if not endpoint:
        logger.error("COSMOS_ENDPOINT environment variable is not set")
        raise RuntimeError(
            "COSMOS_ENDPOINT environment variable is not set. "
            "Set it to your Azure Cosmos DB account endpoint."
        )

    if api_key:
        logger.debug("Using API key authentication for Cosmos DB")
        _client = CosmosClient(url=endpoint, credential=api_key)
    else:
        logger.debug("Using DefaultAzureCredential authentication for Cosmos DB")
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        _client = CosmosClient(url=endpoint, credential=credential)

    return _client


def _get_database():
    """Get or create Cosmos database."""
    endpoint = os.environ.get("COSMOS_ENDPOINT", "")
    database_name = os.environ.get("COSMOS_DATABASE", "blog-writer")

    logger.info(
        f"Initializing Cosmos DB client: endpoint={endpoint}, database={database_name}"
    )
    client = _get_client()
    return client.create_database_if_not_exists(id=database_name)


def _create_container_if_not_exists(container_name: str):
    """Create and return a container with optional throughput settings."""
    database = _get_database()
    logger.debug(f"Creating container if not exists: {container_name}")

    throughput = os.environ.get("COSMOS_THROUGHPUT")
    container_kwargs = {
        "id": container_name,
        "partition_key": PartitionKey(path="/id"),
    }

    if throughput:
        try:
            container_kwargs["offer_throughput"] = int(throughput)
            logger.debug(f"Creating container with throughput: {throughput} RU/s")
        except ValueError:
            logger.warning(
                f"Invalid COSMOS_THROUGHPUT value: {throughput}, ignoring"
            )
    else:
        logger.debug(
            "Creating container without specified throughput (serverless or default)"
        )

    return database.create_container_if_not_exists(**container_kwargs)


def _get_container():
    """Get or create the Cosmos DB container (lazy initialization)."""
    global _client, _container

    if _container is not None:
        logger.debug("Using cached Cosmos DB container")
        return _container

    container_name = "drafts"

    _container = _create_container_if_not_exists(container_name)

    return _container


def _get_linkedin_session_container():
    """Get or create LinkedIn session container."""
    global _linkedin_session_container

    if _linkedin_session_container is not None:
        return _linkedin_session_container

    container_name = os.environ.get(
        "LINKEDIN_COSMOS_SESSION_CONTAINER", "linkedin-sessions"
    )
    _linkedin_session_container = _create_container_if_not_exists(container_name)
    return _linkedin_session_container


def _get_linkedin_state_container():
    """Get or create LinkedIn OAuth state container."""
    global _linkedin_state_container

    if _linkedin_state_container is not None:
        return _linkedin_state_container

    container_name = os.environ.get(
        "LINKEDIN_COSMOS_STATE_CONTAINER", "linkedin-oauth-states"
    )
    _linkedin_state_container = _create_container_if_not_exists(container_name)
    return _linkedin_state_container


def upsert_linkedin_session(
    session_id: str, token_data: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    """Create or update a LinkedIn OAuth session token record."""
    container = _get_linkedin_session_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": session_id,
        "sessionId": session_id,
        "accessToken": token_data.get("access_token", ""),
        "expiresAt": float(token_data.get("expires_at", 0) or 0),
        "personUrn": token_data.get("person_urn", ""),
        "memberId": token_data.get("member_id", ""),
        "updatedAt": now,
    }
    if user_id:
        item["userId"] = user_id
    container.upsert_item(body=item)
    return item


def get_linkedin_session(session_id: str) -> dict[str, Any] | None:
    """Get LinkedIn OAuth session token record by session ID."""
    container = _get_linkedin_session_container()
    try:
        return dict(container.read_item(item=session_id, partition_key=session_id))
    except CosmosResourceNotFoundError:
        return None


def delete_linkedin_session(session_id: str) -> bool:
    """Delete LinkedIn OAuth session token record."""
    container = _get_linkedin_session_container()
    try:
        container.delete_item(item=session_id, partition_key=session_id)
        return True
    except CosmosResourceNotFoundError:
        return False


def store_linkedin_oauth_state(
    state: str, session_id: str, expires_at: float
) -> dict[str, Any]:
    """Store OAuth state mapping for callback verification."""
    container = _get_linkedin_state_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": state,
        "state": state,
        "sessionId": session_id,
        "expiresAt": float(expires_at),
        "createdAt": now,
    }
    container.upsert_item(body=item)
    return item


def consume_linkedin_oauth_state(state: str) -> str | None:
    """Read-and-delete OAuth state mapping, returning session_id if valid."""
    container = _get_linkedin_state_container()
    try:
        item = dict(container.read_item(item=state, partition_key=state))
    except CosmosResourceNotFoundError:
        return None

    try:
        container.delete_item(item=state, partition_key=state)
    except CosmosResourceNotFoundError:
        pass

    expires_at = float(item.get("expiresAt", 0) or 0)
    if expires_at and time.time() >= expires_at:
        return None

    return str(item.get("sessionId", "") or "") or None


# ---------- Twitter/X Sessions ----------


def _get_twitter_session_container():
    """Get or create Twitter session container."""
    global _twitter_session_container
    if _twitter_session_container is not None:
        return _twitter_session_container
    container_name = os.environ.get("TWITTER_COSMOS_SESSION_CONTAINER", "twitter-sessions")
    _twitter_session_container = _create_container_if_not_exists(container_name)
    return _twitter_session_container


def _get_twitter_state_container():
    """Get or create Twitter OAuth state container."""
    global _twitter_state_container
    if _twitter_state_container is not None:
        return _twitter_state_container
    container_name = os.environ.get("TWITTER_COSMOS_STATE_CONTAINER", "twitter-oauth-states")
    _twitter_state_container = _create_container_if_not_exists(container_name)
    return _twitter_state_container


def upsert_twitter_session(
    session_id: str, token_data: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    """Create or update a Twitter OAuth session token record."""
    container = _get_twitter_session_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": session_id,
        "sessionId": session_id,
        "accessToken": token_data.get("access_token", ""),
        "refreshToken": token_data.get("refresh_token", ""),
        "expiresAt": float(token_data.get("expires_at", 0) or 0),
        "username": token_data.get("username", ""),
        "twitterUserId": token_data.get("user_id", ""),
        "updatedAt": now,
    }
    if user_id:
        item["userId"] = user_id
    container.upsert_item(body=item)
    return item


def get_twitter_session(session_id: str) -> dict[str, Any] | None:
    """Get Twitter OAuth session by session ID."""
    container = _get_twitter_session_container()
    try:
        return dict(container.read_item(item=session_id, partition_key=session_id))
    except CosmosResourceNotFoundError:
        return None


def delete_twitter_session(session_id: str) -> bool:
    """Delete Twitter OAuth session."""
    container = _get_twitter_session_container()
    try:
        container.delete_item(item=session_id, partition_key=session_id)
        return True
    except CosmosResourceNotFoundError:
        return False


def store_twitter_oauth_state(
    state: str, session_id: str, code_verifier: str, expires_at: float
) -> dict[str, Any]:
    """Store OAuth state mapping for Twitter callback verification (includes PKCE verifier)."""
    container = _get_twitter_state_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": state,
        "state": state,
        "sessionId": session_id,
        "codeVerifier": code_verifier,
        "expiresAt": float(expires_at),
        "createdAt": now,
    }
    container.upsert_item(body=item)
    return item


def consume_twitter_oauth_state(state: str) -> dict[str, str] | None:
    """Read-and-delete OAuth state mapping, returning session_id and code_verifier if valid."""
    container = _get_twitter_state_container()
    try:
        item = dict(container.read_item(item=state, partition_key=state))
    except CosmosResourceNotFoundError:
        return None

    try:
        container.delete_item(item=state, partition_key=state)
    except CosmosResourceNotFoundError:
        pass

    expires_at = float(item.get("expiresAt", 0) or 0)
    if expires_at and time.time() >= expires_at:
        return None

    session_id = str(item.get("sessionId", "") or "")
    code_verifier = str(item.get("codeVerifier", "") or "")
    if not session_id:
        return None

    return {"session_id": session_id, "code_verifier": code_verifier}


# ---------- Medium Sessions ----------


def _get_medium_session_container():
    """Get or create Medium session container."""
    global _medium_session_container
    if _medium_session_container is not None:
        return _medium_session_container
    container_name = os.environ.get("MEDIUM_COSMOS_SESSION_CONTAINER", "medium-sessions")
    _medium_session_container = _create_container_if_not_exists(container_name)
    return _medium_session_container


def upsert_medium_session(
    session_id: str, token_data: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    """Create or update a Medium session record."""
    container = _get_medium_session_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": session_id,
        "sessionId": session_id,
        "accessToken": token_data.get("access_token", ""),
        "authorId": token_data.get("author_id", ""),
        "username": token_data.get("username", ""),
        "name": token_data.get("name", ""),
        "updatedAt": now,
    }
    if user_id:
        item["userId"] = user_id
    container.upsert_item(body=item)
    return item


def get_medium_session(session_id: str) -> dict[str, Any] | None:
    """Get Medium session by session ID."""
    container = _get_medium_session_container()
    try:
        return dict(container.read_item(item=session_id, partition_key=session_id))
    except CosmosResourceNotFoundError:
        return None


def delete_medium_session(session_id: str) -> bool:
    """Delete Medium session."""
    container = _get_medium_session_container()
    try:
        container.delete_item(item=session_id, partition_key=session_id)
        return True
    except CosmosResourceNotFoundError:
        return False


def list_drafts(limit: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
    """List all blog drafts, ordered by most recently updated.

    Args:
        limit: Maximum number of drafts to return.
        user_id: If provided, filter to drafts owned by this user.

    Returns:
        List of draft documents (without full content for list view).
    """
    container = _get_container()
    start_time = time.time()

    fields = (
        "c.id, c.title, c.slug, c.excerpt, c.sourceUrl, "
        "c.sourceType, c.origin, c.tags, c.createdAt, c.updatedAt"
    )
    params: list[dict[str, Any]] = [{"name": "@limit", "value": limit}]

    if user_id:
        query = (
            f"SELECT {fields} FROM c "
            "WHERE c.userId = @userId "
            "ORDER BY c.updatedAt DESC OFFSET 0 LIMIT @limit"
        )
        params.append({"name": "@userId", "value": user_id})
    else:
        query = (
            f"SELECT {fields} FROM c "
            "ORDER BY c.updatedAt DESC OFFSET 0 LIMIT @limit"
        )

    logger.debug(f"Querying drafts with limit={limit}, user_id={user_id}")
    items = list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )

    elapsed = time.time() - start_time
    logger.info(f"Listed {len(items)} drafts in {elapsed:.3f}s")

    return items


def get_draft(draft_id: str) -> dict[str, Any] | None:
    """Get a single draft by ID.

    Args:
        draft_id: The draft document ID.

    Returns:
        The draft document, or None if not found.
    """
    container = _get_container()
    start_time = time.time()
    
    logger.debug(f"Fetching draft: {draft_id}")
    try:
        item = container.read_item(item=draft_id, partition_key=draft_id)
        elapsed = time.time() - start_time
        logger.info(f"Draft fetched successfully in {elapsed:.3f}s: {draft_id}")
        return dict(item)
    except CosmosResourceNotFoundError:
        elapsed = time.time() - start_time
        logger.warning(f"Draft not found in {elapsed:.3f}s: {draft_id}")
        return None


def create_draft(
    title: str,
    slug: str,
    excerpt: str,
    content: str,
    source_url: str,
    source_type: str,
    origin: str = "user",
    tags: list[str] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Create a new blog draft.

    Args:
        title: Blog post title.
        slug: URL slug.
        excerpt: Short excerpt.
        content: Full MDX/Markdown content.
        source_url: The original URL that was analyzed.
        source_type: 'github' or 'webpage'.

    Returns:
        The created draft document.
    """
    container = _get_container()
    start_time = time.time()
    now = datetime.now(timezone.utc).isoformat()

    draft = {
        "id": str(uuid.uuid4()),
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "content": content,
        "sourceUrl": source_url,
        "sourceType": source_type,
        "origin": origin,
        "tags": tags or [],
        "createdAt": now,
        "updatedAt": now,
    }
    if user_id:
        draft["userId"] = user_id

    logger.debug(f"Creating draft: id={draft['id']}, title={title}, source_type={source_type}")
    container.create_item(body=draft)
    
    elapsed = time.time() - start_time
    logger.info(f"Draft created in {elapsed:.3f}s: {draft['id']} ({len(content)} chars)")
    
    return draft


def update_draft(draft_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update an existing draft.

    Args:
        draft_id: The draft document ID.
        updates: Dictionary of fields to update.

    Returns:
        The updated draft document, or None if not found.
    """
    container = _get_container()
    start_time = time.time()
    
    logger.debug(f"Updating draft: {draft_id}, fields={list(updates.keys())}")
    try:
        existing = container.read_item(item=draft_id, partition_key=draft_id)
    except CosmosResourceNotFoundError:
        elapsed = time.time() - start_time
        logger.warning(f"Draft not found for update in {elapsed:.3f}s: {draft_id}")
        return None

    # Apply updates
    allowed_fields = {"title", "slug", "excerpt", "content"}
    for key, value in updates.items():
        if key in allowed_fields:
            existing[key] = value

    existing["updatedAt"] = datetime.now(timezone.utc).isoformat()

    container.replace_item(item=draft_id, body=existing)
    
    elapsed = time.time() - start_time
    logger.info(f"Draft updated in {elapsed:.3f}s: {draft_id}")
    
    return dict(existing)


def delete_all_drafts() -> int:
    """Delete all drafts. Returns count of deleted items."""
    container = _get_container()
    start_time = time.time()
    query = "SELECT c.id FROM c"
    items = list(
        container.query_items(query=query, enable_cross_partition_query=True)
    )
    count = 0
    for item in items:
        try:
            container.delete_item(item=item["id"], partition_key=item["id"])
            count += 1
        except CosmosResourceNotFoundError:
            pass
    elapsed = time.time() - start_time
    logger.info(f"Deleted {count} drafts in {elapsed:.3f}s")
    return count


def delete_draft(draft_id: str) -> bool:
    """Delete a draft by ID.

    Args:
        draft_id: The draft document ID.

    Returns:
        True if deleted, False if not found.
    """
    container = _get_container()
    start_time = time.time()

    logger.debug(f"Deleting draft: {draft_id}")
    try:
        container.delete_item(item=draft_id, partition_key=draft_id)
        elapsed = time.time() - start_time
        logger.info(f"Draft deleted in {elapsed:.3f}s: {draft_id}")
        return True
    except CosmosResourceNotFoundError:
        elapsed = time.time() - start_time
        logger.warning(f"Draft not found for deletion in {elapsed:.3f}s: {draft_id}")
        return False


# ---------- Published Blogs ----------


def _get_published_blogs_container():
    """Get or create the published blogs container."""
    global _published_blogs_container

    if _published_blogs_container is not None:
        return _published_blogs_container

    _published_blogs_container = _create_container_if_not_exists("published-blogs")
    return _published_blogs_container


def publish_blog(
    slug: str,
    title: str,
    excerpt: str,
    html_content: str,
    mdx_content: str,
    source_url: str,
    source_type: str = "",
    tags: list[str] | None = None,
    date: str = "",
) -> dict[str, Any]:
    """Publish a blog post to Cosmos DB.

    Uses slug as the document ID so re-publishing overwrites the previous version.
    """
    container = _get_published_blogs_container()
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "id": slug,
        "slug": slug,
        "title": title,
        "excerpt": excerpt,
        "htmlContent": html_content,
        "mdxContent": mdx_content,
        "sourceUrl": source_url,
        "sourceType": source_type,
        "tags": tags or [],
        "date": date,
        "publishedAt": now,
        "updatedAt": now,
    }

    container.upsert_item(body=item)
    logger.info(f"Blog published: {slug} ({len(html_content)} chars HTML)")
    return item


def get_published_blog(slug: str) -> dict[str, Any] | None:
    """Get a published blog by slug."""
    container = _get_published_blogs_container()
    try:
        return dict(container.read_item(item=slug, partition_key=slug))
    except CosmosResourceNotFoundError:
        return None


def list_published_blogs(limit: int = 50) -> list[dict[str, Any]]:
    """List published blogs (metadata only), most recent first."""
    container = _get_published_blogs_container()
    query = (
        "SELECT c.id, c.slug, c.title, c.excerpt, c.sourceUrl, "
        "c.publishedAt, c.updatedAt "
        "FROM c ORDER BY c.publishedAt DESC OFFSET 0 LIMIT @limit"
    )
    return list(
        container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
            enable_cross_partition_query=True,
        )
    )


# ---------- Feed Sources ----------


def _get_feed_sources_container():
    """Get or create the feed sources container."""
    global _feed_sources_container
    if _feed_sources_container is not None:
        return _feed_sources_container
    _feed_sources_container = _create_container_if_not_exists("feed-sources")
    return _feed_sources_container


def create_feed_source(
    name: str,
    base_url: str,
    feed_url: str = "",
    feed_type: str = "rss",
    topics: list[str] | None = None,
    auto_publish_blog: bool = False,
    auto_publish_linkedin: bool = False,
    crawl_interval_minutes: int = 60,
    max_article_age_days: int = 7,
    max_articles_to_generate: int = 1,
) -> dict[str, Any]:
    """Create a new feed source configuration."""
    container = _get_feed_sources_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "baseUrl": base_url,
        "feedUrl": feed_url,
        "feedType": feed_type,
        "topics": topics or ["cloud security", "azure", "ai"],
        "autoPublishBlog": auto_publish_blog,
        "autoPublishLinkedIn": auto_publish_linkedin,
        "crawlIntervalMinutes": crawl_interval_minutes,
        "maxArticleAgeDays": max_article_age_days,
        "maxArticlesToGenerate": max_articles_to_generate,
        "enabled": True,
        "lastCrawledAt": "",
        "createdAt": now,
        "updatedAt": now,
    }
    container.create_item(body=item)
    logger.info(f"Feed source created: {item['id']} ({name})")
    return item


def list_feed_sources(enabled_only: bool = False) -> list[dict[str, Any]]:
    """List all feed sources."""
    container = _get_feed_sources_container()
    if enabled_only:
        query = "SELECT * FROM c WHERE c.enabled = true ORDER BY c.createdAt DESC"
    else:
        query = "SELECT * FROM c ORDER BY c.createdAt DESC"
    return list(
        container.query_items(query=query, enable_cross_partition_query=True)
    )


def get_feed_source(source_id: str) -> dict[str, Any] | None:
    """Get a feed source by ID."""
    container = _get_feed_sources_container()
    try:
        return dict(container.read_item(item=source_id, partition_key=source_id))
    except CosmosResourceNotFoundError:
        return None


def update_feed_source(source_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update an existing feed source."""
    container = _get_feed_sources_container()
    try:
        existing = container.read_item(item=source_id, partition_key=source_id)
    except CosmosResourceNotFoundError:
        return None

    allowed = {
        "name", "baseUrl", "feedUrl", "feedType", "topics",
        "autoPublishBlog", "autoPublishLinkedIn", "crawlIntervalMinutes",
        "maxArticleAgeDays", "maxArticlesToGenerate",
        "enabled", "lastCrawledAt",
    }
    for key, value in updates.items():
        if key in allowed:
            existing[key] = value
    existing["updatedAt"] = datetime.now(timezone.utc).isoformat()
    container.replace_item(item=source_id, body=existing)
    logger.info(f"Feed source updated: {source_id}")
    return dict(existing)


def delete_feed_source(source_id: str) -> bool:
    """Delete a feed source."""
    container = _get_feed_sources_container()
    try:
        container.delete_item(item=source_id, partition_key=source_id)
        logger.info(f"Feed source deleted: {source_id}")
        return True
    except CosmosResourceNotFoundError:
        return False


# ---------- Crawled Articles ----------


def _get_crawled_articles_container():
    """Get or create the crawled articles container."""
    global _crawled_articles_container
    if _crawled_articles_container is not None:
        return _crawled_articles_container
    _crawled_articles_container = _create_container_if_not_exists("crawled-articles")
    return _crawled_articles_container


def get_crawled_article(article_id: str) -> dict[str, Any] | None:
    """Get a crawled article by ID."""
    container = _get_crawled_articles_container()
    try:
        return dict(container.read_item(item=article_id, partition_key=article_id))
    except CosmosResourceNotFoundError:
        return None


def upsert_crawled_article(article: dict[str, Any]) -> dict[str, Any]:
    """Create or update a crawled article record."""
    container = _get_crawled_articles_container()
    container.upsert_item(body=article)
    return article


def has_linkedin_post_today() -> bool:
    """Check if a LinkedIn post was already published today (UTC).

    Looks for any crawled-article record with a non-empty linkedinPostId
    whose crawledAt falls on today's UTC date.
    """
    container = _get_crawled_articles_container()
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    today_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59")
    query = (
        "SELECT VALUE COUNT(1) FROM c "
        "WHERE c.linkedinPostId != '' "
        "AND c.crawledAt >= @todayStart "
        "AND c.crawledAt <= @todayEnd"
    )
    params = [
        {"name": "@todayStart", "value": today_start},
        {"name": "@todayEnd", "value": today_end},
    ]
    results = list(
        container.query_items(query=query, parameters=params, enable_cross_partition_query=True)
    )
    count = results[0] if results else 0
    return count > 0


def list_failed_crawled_articles(
    feed_source_id: str, max_retries: int = 3, hours: int = 48
) -> list[dict[str, Any]]:
    """List failed crawled articles eligible for retry.

    Returns articles with status 'error' and retryCount < max_retries
    that were crawled within the last `hours` hours.
    """
    container = _get_crawled_articles_container()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    query = (
        "SELECT * FROM c WHERE c.feedSourceId = @feedSourceId "
        "AND c.status = 'error' "
        "AND (NOT IS_DEFINED(c.retryCount) OR c.retryCount < @maxRetries) "
        "AND c.crawledAt >= @cutoff "
        "ORDER BY c.crawledAt DESC"
    )
    params = [
        {"name": "@feedSourceId", "value": feed_source_id},
        {"name": "@maxRetries", "value": max_retries},
        {"name": "@cutoff", "value": cutoff},
    ]
    return list(
        container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        )
    )


def delete_crawled_article(article_id: str) -> bool:
    """Delete a crawled article by ID."""
    container = _get_crawled_articles_container()
    try:
        container.delete_item(item=article_id, partition_key=article_id)
        logger.info(f"Crawled article deleted: {article_id}")
        return True
    except CosmosResourceNotFoundError:
        return False


def delete_all_crawled_articles() -> int:
    """Delete all crawled articles across all feeds. Returns count deleted."""
    container = _get_crawled_articles_container()
    query = "SELECT c.id FROM c"
    items = list(
        container.query_items(query=query, enable_cross_partition_query=True)
    )
    count = 0
    for item in items:
        try:
            container.delete_item(item=item["id"], partition_key=item["id"])
            count += 1
        except CosmosResourceNotFoundError:
            pass
    logger.info(f"Deleted all {count} crawled articles")
    return count


def delete_crawled_articles_by_feed(feed_source_id: str) -> int:
    """Delete all crawled articles for a feed source. Returns count deleted."""
    container = _get_crawled_articles_container()
    query = "SELECT c.id FROM c WHERE c.feedSourceId = @feedSourceId"
    params = [{"name": "@feedSourceId", "value": feed_source_id}]
    items = list(
        container.query_items(query=query, parameters=params, enable_cross_partition_query=True)
    )
    count = 0
    for item in items:
        try:
            container.delete_item(item=item["id"], partition_key=item["id"])
            count += 1
        except CosmosResourceNotFoundError:
            pass
    logger.info(f"Deleted {count} crawled articles for feed source: {feed_source_id}")
    return count


def list_crawled_articles(
    feed_source_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List crawled articles, optionally filtered by feed source."""
    container = _get_crawled_articles_container()
    if feed_source_id:
        query = (
            "SELECT * FROM c WHERE c.feedSourceId = @feedSourceId "
            "ORDER BY c.crawledAt DESC OFFSET 0 LIMIT @limit"
        )
        params = [
            {"name": "@feedSourceId", "value": feed_source_id},
            {"name": "@limit", "value": limit},
        ]
    else:
        query = "SELECT * FROM c ORDER BY c.crawledAt DESC OFFSET 0 LIMIT @limit"
        params = [{"name": "@limit", "value": limit}]
    return list(
        container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        )
    )


def list_relevant_crawled_articles(
    exclude_feed_ids: list[str] | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List relevant crawled articles sorted by relevance score descending.

    Optionally exclude articles belonging to specific feed source IDs
    (e.g. feeds with auto-publish enabled).
    """
    container = _get_crawled_articles_container()

    if exclude_feed_ids:
        # Cosmos DB doesn't support NOT IN with parameterised lists directly,
        # so we build the exclusion list inline (safe – these are UUIDs we control).
        escaped = ", ".join(f"'{fid}'" for fid in exclude_feed_ids)
        query = (
            f"SELECT * FROM c WHERE c.isRelevant = true "
            f"AND c.feedSourceId NOT IN ({escaped}) "
            f"ORDER BY c.relevanceScore DESC OFFSET 0 LIMIT @limit"
        )
        params = [{"name": "@limit", "value": limit}]
    else:
        query = (
            "SELECT * FROM c WHERE c.isRelevant = true "
            "ORDER BY c.relevanceScore DESC OFFSET 0 LIMIT @limit"
        )
        params = [{"name": "@limit", "value": limit}]

    return list(
        container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        )
    )


# ---------- Crawl Jobs ----------


def _get_crawl_jobs_container():
    """Get or create the crawl jobs container."""
    global _crawl_jobs_container
    if _crawl_jobs_container is not None:
        return _crawl_jobs_container
    _crawl_jobs_container = _create_container_if_not_exists("crawl-jobs")
    return _crawl_jobs_container


def create_crawl_job(feed_source_id: str) -> dict[str, Any]:
    """Create a new crawl job record."""
    container = _get_crawl_jobs_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": str(uuid.uuid4()),
        "feedSourceId": feed_source_id,
        "startedAt": now,
        "completedAt": "",
        "articlesFound": 0,
        "articlesRelevant": 0,
        "articlesProcessed": 0,
        "status": "running",
        "error": "",
    }
    container.create_item(body=item)
    return item


def update_crawl_job(job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update a crawl job record."""
    container = _get_crawl_jobs_container()
    try:
        existing = container.read_item(item=job_id, partition_key=job_id)
    except CosmosResourceNotFoundError:
        return None
    for key, value in updates.items():
        existing[key] = value
    container.replace_item(item=job_id, body=existing)
    return dict(existing)


def list_crawl_jobs(
    feed_source_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List crawl jobs, optionally filtered by feed source."""
    container = _get_crawl_jobs_container()
    if feed_source_id:
        query = (
            "SELECT * FROM c WHERE c.feedSourceId = @feedSourceId "
            "ORDER BY c.startedAt DESC OFFSET 0 LIMIT @limit"
        )
        params = [
            {"name": "@feedSourceId", "value": feed_source_id},
            {"name": "@limit", "value": limit},
        ]
    else:
        query = "SELECT * FROM c ORDER BY c.startedAt DESC OFFSET 0 LIMIT @limit"
        params = [{"name": "@limit", "value": limit}]
    return list(
        container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True
        )
    )


# ---------- Prompts ----------


def _get_prompts_container():
    """Get or create the prompts container."""
    global _prompts_container
    if _prompts_container is not None:
        return _prompts_container
    _prompts_container = _create_container_if_not_exists("prompts")
    return _prompts_container


def get_prompt(name: str) -> dict[str, Any] | None:
    """Get a prompt override by name."""
    container = _get_prompts_container()
    try:
        return dict(container.read_item(item=name, partition_key=name))
    except CosmosResourceNotFoundError:
        return None


def upsert_prompt(name: str, content: str) -> dict[str, Any]:
    """Create or update a prompt override."""
    container = _get_prompts_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": name,
        "name": name,
        "content": content,
        "updatedAt": now,
    }
    container.upsert_item(body=item)
    logger.info(f"Prompt upserted: {name} ({len(content)} chars)")
    return item


def delete_prompt(name: str) -> bool:
    """Delete a prompt override (reverts to file default)."""
    container = _get_prompts_container()
    try:
        container.delete_item(item=name, partition_key=name)
        logger.info(f"Prompt deleted (reset to default): {name}")
        return True
    except CosmosResourceNotFoundError:
        return False


def list_prompts() -> list[dict[str, Any]]:
    """List all prompt overrides."""
    container = _get_prompts_container()
    query = "SELECT c.id, c.name, c.updatedAt FROM c ORDER BY c.name"
    return list(
        container.query_items(query=query, enable_cross_partition_query=True)
    )


# ---------- Keywords ----------


def _get_keywords_container():
    """Get or create the keywords container."""
    global _keywords_container
    if _keywords_container is not None:
        return _keywords_container
    _keywords_container = _create_container_if_not_exists("keywords")
    return _keywords_container


def get_topic_keywords(topic: str) -> list[str] | None:
    """Get keyword override for a topic. Returns None if no override exists."""
    container = _get_keywords_container()
    try:
        item = dict(container.read_item(item=topic, partition_key=topic))
        return item.get("keywords", [])
    except CosmosResourceNotFoundError:
        return None


def upsert_topic_keywords(topic: str, keywords: list[str]) -> dict[str, Any]:
    """Create or update keyword list for a topic."""
    container = _get_keywords_container()
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": topic,
        "topic": topic,
        "keywords": keywords,
        "updatedAt": now,
    }
    container.upsert_item(body=item)
    logger.info(f"Keywords upserted for topic '{topic}': {len(keywords)} keywords")
    return item


def delete_topic_keywords(topic: str) -> bool:
    """Delete keyword override for a topic (reverts to code defaults)."""
    container = _get_keywords_container()
    try:
        container.delete_item(item=topic, partition_key=topic)
        logger.info(f"Keywords deleted (reset to default): {topic}")
        return True
    except CosmosResourceNotFoundError:
        return False


def list_topic_keyword_overrides() -> list[dict[str, Any]]:
    """List all keyword overrides."""
    container = _get_keywords_container()
    query = "SELECT c.id, c.topic, c.updatedAt FROM c ORDER BY c.topic"
    return list(
        container.query_items(query=query, enable_cross_partition_query=True)
    )


# ---------- User Profiles ----------


def _get_user_profiles_container():
    """Get or create the user profiles container."""
    global _user_profiles_container
    if _user_profiles_container is not None:
        return _user_profiles_container
    _user_profiles_container = _create_container_if_not_exists("user-profiles")
    return _user_profiles_container


def get_or_create_user_profile(
    user_id: str, name: str, email: str
) -> dict[str, Any]:
    """Get existing user profile or create one on first login."""
    container = _get_user_profiles_container()
    now = datetime.now(timezone.utc).isoformat()

    try:
        existing = dict(container.read_item(item=user_id, partition_key=user_id))
        existing["lastLoginAt"] = now
        existing["name"] = name
        existing["email"] = email
        container.replace_item(item=user_id, body=existing)
        return existing
    except CosmosResourceNotFoundError:
        item = {
            "id": user_id,
            "name": name,
            "email": email,
            "linkedinSessionId": "",
            "settings": {},
            "createdAt": now,
            "lastLoginAt": now,
        }
        container.create_item(body=item)
        logger.info(f"User profile created: {user_id} ({email})")
        return item


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    """Get a user profile by ID."""
    container = _get_user_profiles_container()
    try:
        return dict(container.read_item(item=user_id, partition_key=user_id))
    except CosmosResourceNotFoundError:
        return None


def update_user_profile(user_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update an existing user profile."""
    container = _get_user_profiles_container()
    try:
        existing = container.read_item(item=user_id, partition_key=user_id)
    except CosmosResourceNotFoundError:
        return None

    allowed = {"name", "email", "linkedinSessionId", "settings"}
    for key, value in updates.items():
        if key in allowed:
            existing[key] = value
    existing["updatedAt"] = datetime.now(timezone.utc).isoformat()
    container.replace_item(item=user_id, body=existing)
    return dict(existing)
