"""Hashtag Agent — Generates trending, high-reach hashtags for social media posts.

Runs as a separate AI call that analyzes the content's major topics,
evaluates them against current platform trends, and returns optimized hashtags.
"""

import json
import logging
import os
import re
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a LinkedIn hashtag strategist. Your job is to generate the most effective
hashtags for a LinkedIn post based on the article content provided.

## Your Process

1. **Topic Extraction**: Identify the 5-8 major topics/themes from the content.
2. **Trend Evaluation**: For each topic, determine the hashtag variant that is
   most commonly used and has the highest engagement on LinkedIn right now.
   Prefer established community hashtags (e.g. #GenerativeAI over #GenAITools,
   #CloudSecurity over #SecuringTheCloud).
3. **Mix Strategy**: Return a balanced set of:
   - 2-3 **broad/trending** hashtags (high volume, e.g. #AI, #CloudComputing)
   - 2-3 **mid-tier niche** hashtags (moderate volume, targeted, e.g. #AzureAI, #DevSecOps)
   - 1-2 **specific/long-tail** hashtags (low competition, very targeted, e.g. #AIGuardrails)
4. **Platform Rules**: LinkedIn posts perform best with 3-7 hashtags. Never exceed 8.

## Output Format

Return ONLY valid JSON, no markdown fences:

{
  "topics": ["topic1", "topic2", ...],
  "hashtags": [
    {"tag": "#Example", "category": "broad|niche|specific", "reason": "why this tag"}
  ],
  "final_tags": ["#Tag1", "#Tag2", ...]
}
"""


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("HASHTAG_MODEL", os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o"))

    if api_key:
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
    else:
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


def _clean_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def generate_hashtags(
    content: str,
    title: str = "",
    excerpt: str = "",
    platform: str = "linkedin",
) -> dict[str, Any]:
    """Generate optimized hashtags by analyzing content topics against trends.

    Returns:
        {
            "topics": [...],
            "hashtags": [{"tag": "...", "category": "...", "reason": "..."}],
            "final_tags": ["#Tag1", "#Tag2", ...]
        }
    """
    if not content.strip():
        return {"topics": [], "hashtags": [], "final_tags": ["#SoftwareEngineering", "#AI", "#Tech"]}

    client, model = _get_openai_client()

    user_prompt = (
        f"Platform: {platform}\n"
        f"Title: {title or '(not provided)'}\n"
        f"Excerpt: {excerpt or '(not provided)'}\n\n"
        f"Content:\n{content[:4000]}\n\n"
        "Analyze the major topics and generate the best-performing hashtags."
    )

    logger.info(f"Hashtag agent: generating for title='{title[:60]}', content_len={len(content)}")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_completion_tokens=800,
    )

    raw = response.choices[0].message.content or ""

    try:
        parsed = json.loads(_clean_json(raw))
        final_tags = parsed.get("final_tags", [])
        # Normalize tags
        clean_tags = []
        seen: set[str] = set()
        for tag in final_tags:
            t = tag.strip().replace(" ", "")
            if not t.startswith("#"):
                t = "#" + t
            t = re.sub(r"[^#A-Za-z0-9_]", "", t)
            if t.lower() not in seen:
                seen.add(t.lower())
                clean_tags.append(t)

        parsed["final_tags"] = clean_tags[:8]
        logger.info(f"Hashtag agent: generated {len(clean_tags)} tags — {clean_tags}")
        return parsed
    except Exception:
        logger.warning("Hashtag agent returned non-JSON; extracting with regex")
        tags = re.findall(r"#[A-Za-z0-9_]+", raw)
        unique = list(dict.fromkeys(tags))[:8]
        return {
            "topics": [],
            "hashtags": [],
            "final_tags": unique or ["#SoftwareEngineering", "#AI", "#Tech"],
        }
