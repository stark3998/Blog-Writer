"""Notification Service — Rich webhook notifications for pipeline events.

Supports Microsoft Teams (Adaptive Cards), Slack, and generic JSON webhooks.
Auto-detects the target from the webhook URL.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _get_webhook_url() -> str:
    return os.environ.get("WEBHOOK_URL", "").strip()


def _is_teams_webhook(url: str) -> bool:
    return "webhook.office.com" in url or "workflows.office.com" in url


def _is_slack_webhook(url: str) -> bool:
    return "hooks.slack.com" in url


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_RATING_STARS = {
    (0.0, 0.3): "Low",
    (0.3, 0.6): "Medium",
    (0.6, 0.8): "High",
    (0.8, 1.01): "Very High",
}


def _score_label(score: float) -> str:
    for (lo, hi), label in _RATING_STARS.items():
        if lo <= score < hi:
            return label
    return "Unknown"


def _score_bar(score: float, width: int = 10) -> str:
    """Visual bar like ████████░░ 0.82"""
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled) + f" {score:.2f}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Teams Adaptive Cards — one builder per event type
# ---------------------------------------------------------------------------

def _teams_card_wrapper(body_items: list[dict], actions: list[dict] | None = None) -> dict:
    """Wrap body items in a standard Adaptive Card envelope."""
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body_items,
    }
    if actions:
        card["actions"] = actions
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def _teams_blog_published(p: dict[str, Any]) -> dict:
    title = p.get("title", "Untitled")
    excerpt = p.get("excerpt", "")
    blog_url = p.get("blog_url", "")
    source_url = p.get("source_url", "")
    hero = p.get("hero_image_url", "")
    tags = p.get("tags", [])
    topics = p.get("topics", [])

    body: list[dict] = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "📝",
                            "size": "large",
                        }
                    ],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "Blog Published",
                            "weight": "bolder",
                            "size": "medium",
                            "color": "good",
                        },
                        {
                            "type": "TextBlock",
                            "text": _timestamp(),
                            "size": "small",
                            "isSubtle": True,
                            "spacing": "none",
                        },
                    ],
                },
            ],
        },
    ]

    # Hero image
    if hero:
        body.append({
            "type": "Image",
            "url": hero,
            "size": "stretch",
            "altText": title,
        })

    # Title + excerpt
    body.append({
        "type": "TextBlock",
        "text": title,
        "weight": "bolder",
        "size": "medium",
        "wrap": True,
    })
    if excerpt:
        body.append({
            "type": "TextBlock",
            "text": _truncate(excerpt, 300),
            "wrap": True,
            "isSubtle": True,
        })

    # Tags / topics
    tag_labels = tags or topics
    if tag_labels:
        body.append({
            "type": "TextBlock",
            "text": " · ".join(tag_labels[:6]),
            "size": "small",
            "color": "accent",
            "wrap": True,
        })

    # Facts
    facts = []
    if source_url:
        facts.append({"title": "Source", "value": f"[Original Article]({source_url})"})
    if blog_url:
        facts.append({"title": "Blog", "value": f"[Read Post]({blog_url})"})
    if facts:
        body.append({"type": "FactSet", "facts": facts})

    # Action buttons
    actions = []
    if blog_url:
        actions.append({"type": "Action.OpenUrl", "title": "Read Blog Post", "url": blog_url})
    if source_url:
        actions.append({"type": "Action.OpenUrl", "title": "View Source", "url": source_url})

    return _teams_card_wrapper(body, actions or None)


def _teams_linkedin_published(p: dict[str, Any]) -> dict:
    title = p.get("title", "Untitled")
    excerpt = p.get("excerpt", "")
    post_preview = p.get("post_text_preview", "")
    blog_url = p.get("blog_url", "")
    article_url = p.get("article_url", "")
    image_url = p.get("image_url", "")
    hashtags = p.get("hashtags", [])
    post_id = p.get("post_id", "")

    body: list[dict] = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{"type": "TextBlock", "text": "🔗", "size": "large"}],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "LinkedIn Post Published",
                            "weight": "bolder",
                            "size": "medium",
                            "color": "good",
                        },
                        {
                            "type": "TextBlock",
                            "text": _timestamp(),
                            "size": "small",
                            "isSubtle": True,
                            "spacing": "none",
                        },
                    ],
                },
            ],
        },
    ]

    if image_url:
        body.append({"type": "Image", "url": image_url, "size": "stretch", "altText": title})

    body.append({
        "type": "TextBlock",
        "text": title,
        "weight": "bolder",
        "size": "medium",
        "wrap": True,
    })

    if excerpt:
        body.append({
            "type": "TextBlock",
            "text": _truncate(excerpt, 250),
            "wrap": True,
            "isSubtle": True,
        })

    # Post preview
    if post_preview:
        body.append({
            "type": "Container",
            "style": "emphasis",
            "items": [
                {
                    "type": "TextBlock",
                    "text": "Post Preview",
                    "weight": "bolder",
                    "size": "small",
                },
                {
                    "type": "TextBlock",
                    "text": _truncate(post_preview, 300),
                    "wrap": True,
                    "size": "small",
                },
            ],
        })

    if hashtags:
        body.append({
            "type": "TextBlock",
            "text": " ".join(hashtags[:5]),
            "size": "small",
            "color": "accent",
        })

    facts = []
    if post_id:
        facts.append({"title": "Post ID", "value": post_id})
    if blog_url:
        facts.append({"title": "Blog", "value": f"[Read Post]({blog_url})"})
    if article_url:
        facts.append({"title": "Source", "value": f"[Original]({article_url})"})
    if facts:
        body.append({"type": "FactSet", "facts": facts})

    actions = []
    if blog_url:
        actions.append({"type": "Action.OpenUrl", "title": "Read Blog", "url": blog_url})

    return _teams_card_wrapper(body, actions or None)


def _teams_crawl_completed(p: dict[str, Any]) -> dict:
    feed_name = p.get("feed_source_name", "Unknown Feed")
    found = p.get("articles_found", 0)
    new = p.get("new_articles", 0)
    relevant = p.get("articles_relevant", 0)
    processed = p.get("articles_processed", 0)
    li_post = p.get("linkedin_published", "")
    top_articles = p.get("top_articles", [])

    body: list[dict] = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{"type": "TextBlock", "text": "🔄", "size": "large"}],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": f"Crawl Completed — {feed_name}",
                            "weight": "bolder",
                            "size": "medium",
                            "color": "good" if processed > 0 else "default",
                        },
                        {
                            "type": "TextBlock",
                            "text": _timestamp(),
                            "size": "small",
                            "isSubtle": True,
                            "spacing": "none",
                        },
                    ],
                },
            ],
        },
        # Stats bar
        {
            "type": "ColumnSet",
            "columns": [
                _stats_column("Found", str(found)),
                _stats_column("New", str(new)),
                _stats_column("Relevant", str(relevant)),
                _stats_column("Processed", str(processed)),
            ],
        },
    ]

    # Top articles with rating
    if top_articles:
        body.append({
            "type": "TextBlock",
            "text": "Top Articles Selected",
            "weight": "bolder",
            "size": "small",
            "spacing": "medium",
        })
        for i, art in enumerate(top_articles[:3]):
            score = art.get("relevance_score", 0)
            topics = art.get("matched_topics", [])
            body.append({
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "width": "auto",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": f"#{i + 1}",
                                "weight": "bolder",
                                "color": "accent",
                            }
                        ],
                    },
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": _truncate(art.get("title", ""), 100),
                                "wrap": True,
                                "weight": "bolder",
                                "size": "small",
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Rating: {_score_bar(score)}  ({_score_label(score)})",
                                "size": "small",
                                "isSubtle": True,
                                "fontType": "monospace",
                                "spacing": "none",
                            },
                            {
                                "type": "TextBlock",
                                "text": " · ".join(topics) if topics else "",
                                "size": "small",
                                "color": "accent",
                                "spacing": "none",
                            },
                        ],
                    },
                ],
            })

    if li_post:
        body.append({
            "type": "TextBlock",
            "text": f"✅ LinkedIn post published: {li_post}",
            "size": "small",
            "color": "good",
        })

    return _teams_card_wrapper(body)


def _stats_column(label: str, value: str) -> dict:
    return {
        "type": "Column",
        "width": "1",
        "items": [
            {
                "type": "TextBlock",
                "text": value,
                "size": "extraLarge",
                "weight": "bolder",
                "horizontalAlignment": "center",
            },
            {
                "type": "TextBlock",
                "text": label,
                "size": "small",
                "isSubtle": True,
                "horizontalAlignment": "center",
                "spacing": "none",
            },
        ],
    }


def _teams_pipeline_error(p: dict[str, Any]) -> dict:
    feed_name = p.get("feed_source_name", "")
    stage = p.get("stage", "unknown")
    error = p.get("error", "No details")
    title_text = p.get("title", "")

    body: list[dict] = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{"type": "TextBlock", "text": "❌", "size": "large"}],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "Pipeline Error",
                            "weight": "bolder",
                            "size": "medium",
                            "color": "attention",
                        },
                        {
                            "type": "TextBlock",
                            "text": _timestamp(),
                            "size": "small",
                            "isSubtle": True,
                            "spacing": "none",
                        },
                    ],
                },
            ],
        },
    ]

    facts = [{"title": "Stage", "value": stage}]
    if feed_name:
        facts.append({"title": "Feed", "value": feed_name})
    if title_text:
        facts.append({"title": "Article", "value": title_text})
    body.append({"type": "FactSet", "facts": facts})

    body.append({
        "type": "Container",
        "style": "attention",
        "items": [
            {
                "type": "TextBlock",
                "text": _truncate(error, 500),
                "wrap": True,
                "size": "small",
                "fontType": "monospace",
            }
        ],
    })

    return _teams_card_wrapper(body)


def _teams_session_expiring(p: dict[str, Any]) -> dict:
    days = p.get("days_remaining", "?")
    message = p.get("message", "")

    body: list[dict] = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{"type": "TextBlock", "text": "⚠️", "size": "large"}],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "LinkedIn Session Expiring",
                            "weight": "bolder",
                            "size": "medium",
                            "color": "warning",
                        },
                        {
                            "type": "TextBlock",
                            "text": _timestamp(),
                            "size": "small",
                            "isSubtle": True,
                            "spacing": "none",
                        },
                    ],
                },
            ],
        },
        {
            "type": "TextBlock",
            "text": f"**{days} days remaining** before your LinkedIn OAuth token expires.",
            "wrap": True,
            "size": "medium",
        },
    ]

    if message:
        body.append({
            "type": "TextBlock",
            "text": message,
            "wrap": True,
            "isSubtle": True,
        })

    return _teams_card_wrapper(body)


_TEAMS_BUILDERS: dict[str, Any] = {
    "blog_published": _teams_blog_published,
    "linkedin_published": _teams_linkedin_published,
    "crawl_completed": _teams_crawl_completed,
    "pipeline_error": _teams_pipeline_error,
    "linkedin_session_expiring": _teams_session_expiring,
}


def _build_teams_body(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    builder = _TEAMS_BUILDERS.get(event_type)
    if builder:
        return builder(payload)
    # Fallback: generic card
    return _teams_card_wrapper([
        {
            "type": "TextBlock",
            "text": f"Blog Writer: {event_type}",
            "weight": "bolder",
            "size": "medium",
        },
        {
            "type": "TextBlock",
            "text": str(payload),
            "wrap": True,
            "size": "small",
        },
    ])


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def _build_slack_body(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    title = event_type.replace("_", " ").title()
    lines = [f"*Blog Writer: {title}*"]

    if event_type == "crawl_completed":
        lines.append(
            f"Feed: {payload.get('feed_source_name', '?')} | "
            f"Found: {payload.get('articles_found', 0)} | "
            f"Relevant: {payload.get('articles_relevant', 0)} | "
            f"Processed: {payload.get('articles_processed', 0)}"
        )
        for art in payload.get("top_articles", [])[:3]:
            score = art.get("relevance_score", 0)
            lines.append(f"  • {art.get('title', '')} — {_score_bar(score)}")
    elif event_type == "blog_published":
        lines.append(f"*{payload.get('title', '')}*")
        if payload.get("excerpt"):
            lines.append(f"_{_truncate(payload['excerpt'], 200)}_")
        if payload.get("blog_url"):
            lines.append(f"<{payload['blog_url']}|Read Post>")
    elif event_type == "linkedin_published":
        lines.append(f"*{payload.get('title', '')}*")
        if payload.get("post_text_preview"):
            lines.append(f"```{_truncate(payload['post_text_preview'], 200)}```")
        if payload.get("blog_url"):
            lines.append(f"<{payload['blog_url']}|Blog> | <{payload.get('article_url', '')}|Source>")
    elif event_type == "pipeline_error":
        lines.append(f"Stage: {payload.get('stage', '?')} | Feed: {payload.get('feed_source_name', '?')}")
        lines.append(f"```{_truncate(payload.get('error', ''), 300)}```")
    elif event_type == "linkedin_session_expiring":
        lines.append(payload.get("message", ""))
    else:
        for k, v in payload.items():
            if v or v == 0:
                lines.append(f"{k}: {v}")

    return {"text": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Generic JSON
# ---------------------------------------------------------------------------

def _build_generic_body(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify(event_type: str, payload: dict[str, Any]) -> bool:
    """Send a webhook notification for a pipeline event.

    Auto-detects Teams (Adaptive Card), Slack, or generic JSON from the URL.
    Fire-and-forget: logs errors but never raises. Returns True if sent.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return False

    if _is_teams_webhook(webhook_url):
        body = _build_teams_body(event_type, payload)
    elif _is_slack_webhook(webhook_url):
        body = _build_slack_body(event_type, payload)
    else:
        body = _build_generic_body(event_type, payload)

    try:
        resp = requests.post(
            webhook_url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.warning(
                f"Webhook returned {resp.status_code} for event '{event_type}': "
                f"{resp.text[:200]}"
            )
            return False
        logger.debug(f"Webhook sent: event={event_type}, status={resp.status_code}")
        return True
    except requests.RequestException as exc:
        logger.warning(f"Webhook failed for event '{event_type}': {exc}")
        return False
