"""Cosmos DB Client — Persistence layer for blog drafts.

Uses Azure Cosmos DB NoSQL API with either API Key or DefaultAzureCredential
to store and retrieve blog post drafts.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

logger = logging.getLogger(__name__)


_client: CosmosClient | None = None
_container = None
_linkedin_session_container = None
_linkedin_state_container = None


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


def upsert_linkedin_session(session_id: str, token_data: dict[str, Any]) -> dict[str, Any]:
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


def list_drafts(limit: int = 50) -> list[dict[str, Any]]:
    """List all blog drafts, ordered by most recently updated.

    Args:
        limit: Maximum number of drafts to return.

    Returns:
        List of draft documents (without full content for list view).
    """
    container = _get_container()
    start_time = time.time()
    
    query = (
        "SELECT c.id, c.title, c.slug, c.excerpt, c.sourceUrl, "
        "c.sourceType, c.createdAt, c.updatedAt "
        "FROM c ORDER BY c.updatedAt DESC OFFSET 0 LIMIT @limit"
    )
    
    logger.debug(f"Querying drafts with limit={limit}")
    items = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
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
        "createdAt": now,
        "updatedAt": now,
    }

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
