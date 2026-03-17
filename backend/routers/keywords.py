"""Keywords Router — View and edit keyword lists used by the relevance pre-filter."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.cosmos_client import (
    get_topic_keywords,
    upsert_topic_keywords,
    delete_topic_keywords,
    list_topic_keyword_overrides,
)
from backend.services.relevance_classifier import TOPIC_KEYWORDS

router = APIRouter(prefix="/api/keywords", tags=["keywords"])


# ---------- Models ----------


class TopicKeywordsInfo(BaseModel):
    topic: str
    keywords: list[str]
    keyword_count: int
    is_customized: bool


class TopicKeywordsDetail(BaseModel):
    topic: str
    keywords: list[str]
    default_keywords: list[str]
    keyword_count: int
    is_customized: bool


class KeywordsUpdateRequest(BaseModel):
    keywords: list[str]


class KeywordsAddRequest(BaseModel):
    keywords: list[str]


# ---------- Helpers ----------


def _get_active_keywords(topic: str) -> list[str]:
    """Get active keywords: Cosmos override if present, otherwise code default."""
    override = get_topic_keywords(topic)
    if override is not None:
        return override
    return TOPIC_KEYWORDS.get(topic, [])


# ---------- Endpoints ----------


@router.get("", response_model=list[TopicKeywordsInfo])
async def list_all_topics():
    """List all topics with their active keyword lists."""
    overrides = {item["topic"]: item for item in list_topic_keyword_overrides()}
    result = []
    for topic in TOPIC_KEYWORDS:
        is_customized = topic in overrides
        keywords = get_topic_keywords(topic) if is_customized else TOPIC_KEYWORDS[topic]
        if keywords is None:
            keywords = TOPIC_KEYWORDS[topic]
        result.append(
            TopicKeywordsInfo(
                topic=topic,
                keywords=keywords,
                keyword_count=len(keywords),
                is_customized=is_customized,
            )
        )
    return result


@router.get("/{topic}", response_model=TopicKeywordsDetail)
async def get_topic_detail(topic: str):
    """Get keywords for a specific topic (active + defaults)."""
    if topic not in TOPIC_KEYWORDS:
        raise HTTPException(status_code=404, detail=f"Unknown topic: {topic}")

    default_keywords = TOPIC_KEYWORDS[topic]
    override = get_topic_keywords(topic)
    is_customized = override is not None
    active = override if is_customized else default_keywords

    return TopicKeywordsDetail(
        topic=topic,
        keywords=active,
        default_keywords=default_keywords,
        keyword_count=len(active),
        is_customized=is_customized,
    )


@router.put("/{topic}", response_model=TopicKeywordsDetail)
async def update_topic_keywords(topic: str, request: KeywordsUpdateRequest):
    """Save a keyword override for a topic."""
    if topic not in TOPIC_KEYWORDS:
        raise HTTPException(status_code=404, detail=f"Unknown topic: {topic}")

    # Normalize: lowercase, deduplicate, sort
    keywords = sorted(set(kw.lower().strip() for kw in request.keywords if kw.strip()))
    if not keywords:
        raise HTTPException(status_code=400, detail="Keywords list cannot be empty")

    upsert_topic_keywords(topic, keywords)
    default_keywords = TOPIC_KEYWORDS[topic]

    return TopicKeywordsDetail(
        topic=topic,
        keywords=keywords,
        default_keywords=default_keywords,
        keyword_count=len(keywords),
        is_customized=True,
    )


@router.delete("/{topic}")
async def reset_topic_keywords(topic: str):
    """Reset a topic's keywords to code defaults."""
    if topic not in TOPIC_KEYWORDS:
        raise HTTPException(status_code=404, detail=f"Unknown topic: {topic}")

    delete_topic_keywords(topic)
    return {"status": "reset", "topic": topic}


@router.post("/{topic}/add", response_model=TopicKeywordsDetail)
async def add_keywords_to_topic(topic: str, request: KeywordsAddRequest):
    """Add keywords to an existing topic (merges with current list)."""
    if topic not in TOPIC_KEYWORDS:
        raise HTTPException(status_code=404, detail=f"Unknown topic: {topic}")

    current = _get_active_keywords(topic)
    new_kws = [kw.lower().strip() for kw in request.keywords if kw.strip()]
    merged = sorted(set(current + new_kws))

    upsert_topic_keywords(topic, merged)
    default_keywords = TOPIC_KEYWORDS[topic]

    return TopicKeywordsDetail(
        topic=topic,
        keywords=merged,
        default_keywords=default_keywords,
        keyword_count=len(merged),
        is_customized=True,
    )
