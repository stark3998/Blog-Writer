"""Twitter Service — Compose concise, engagement-optimized tweets from blog content."""

import json
import logging
import os
import re
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

TWEET_CHAR_LIMIT = 280


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("TWITTER_POST_MODEL", os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o"))

    if api_key:
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=None,
            api_key=api_key,
            api_version=api_version,
        )
        return client, model

    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )
    return client, model


def _extract_frontmatter_value(content: str, key: str) -> str:
    match = re.search(rf'^{key}:\s*"(.+?)"\s*$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    match = re.search(rf'^{key}:\s*(.+?)\s*$', content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _strip_frontmatter(content: str) -> str:
    return re.sub(r"^---[\s\S]*?---\n*", "", content, count=1).strip()


def _clean_json_response(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def compose_tweet(
    blog_content: str,
    title: str = "",
    excerpt: str = "",
    blog_url: str = "",
    additional_context: str = "",
) -> dict[str, Any]:
    """Compose an engaging tweet from blog content."""
    resolved_title = title.strip() or _extract_frontmatter_value(blog_content, "title")
    resolved_excerpt = excerpt.strip() or _extract_frontmatter_value(blog_content, "excerpt")
    body = _strip_frontmatter(blog_content)

    if not body:
        raise ValueError("Blog content is empty")

    client, model = _get_openai_client()

    system_prompt = """You are a Twitter/X content expert. Create an engaging tweet to promote a blog post.

Rules:
- The tweet MUST be under 280 characters (including any URL)
- Be concise, punchy, and attention-grabbing
- Use 1-3 relevant hashtags (counted in character limit)
- If a blog_url is provided, include it in the tweet (URLs count as 23 characters on Twitter)
- Do NOT use markdown formatting
- Focus on the key insight or takeaway that will make people click
- Use a conversational, authentic tone

Return JSON with these fields:
{
  "tweet_text": "the full tweet text ready to post",
  "hashtags": ["#tag1", "#tag2"],
  "char_count": 142
}"""

    url_note = f"\nBlog URL (include in tweet, counts as 23 chars): {blog_url}" if blog_url else ""

    user_prompt = (
        f"Title: {resolved_title or '(missing)'}\n"
        f"Excerpt: {resolved_excerpt or '(missing)'}\n"
        f"{url_note}\n"
        f"Additional context: {additional_context or '(none)'}\n\n"
        f"Blog content (first 2000 chars):\n{body[:2000]}\n\n"
        "Generate an engaging tweet to promote this blog post."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,
        max_completion_tokens=500,
    )

    raw_output = response.choices[0].message.content or ""

    try:
        parsed = json.loads(_clean_json_response(raw_output))
        tweet_text = str(parsed.get("tweet_text", "")).strip()
        hashtags = [str(h).strip() for h in parsed.get("hashtags", []) if str(h).strip()]
    except Exception:
        tweet_text = raw_output.strip()
        hashtags = re.findall(r"#[A-Za-z0-9_]+", tweet_text)

    # Run humanizer agent to make the tweet sound authentically human
    try:
        from backend.services.humanizer_agent import humanize_post
        humanized = humanize_post(tweet_text, "tweet", body[:2000])
        if humanized.get("humanized_text") and humanized["humanized_text"] != tweet_text:
            logger.info(f"Humanizer applied to tweet: {humanized.get('changes_summary', '')}")
            tweet_text = humanized["humanized_text"]
            hashtags = re.findall(r"#[A-Za-z0-9_]+", tweet_text) or hashtags
    except Exception as exc:
        logger.warning(f"Humanizer agent failed for tweet, using original: {exc}")

    # Ensure tweet fits in character limit
    if len(tweet_text) > TWEET_CHAR_LIMIT:
        tweet_text = tweet_text[:TWEET_CHAR_LIMIT - 3] + "..."

    return {
        "tweet_text": tweet_text,
        "hashtags": hashtags[:5],
        "char_count": len(tweet_text),
        "title": resolved_title,
        "excerpt": resolved_excerpt,
    }
