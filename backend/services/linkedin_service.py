"""LinkedIn Service — Compose reach-optimized, insights-driven LinkedIn posts."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

LINKEDIN_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "linkedin_post_prompt.md"


def _load_linkedin_prompt() -> str:
    return LINKEDIN_PROMPT_PATH.read_text(encoding="utf-8")


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("LINKEDIN_POST_MODEL", os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o"))

    logger.debug(
        f"Initializing LinkedIn composer client: endpoint={endpoint}, model={model}, api_version={api_version}"
    )

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


def _normalize_hashtags(hashtags: list[str]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for item in hashtags:
        tag = item.strip().replace(" ", "")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        tag = re.sub(r"[^#A-Za-z0-9_]", "", tag)
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        clean.append(tag)
        if len(clean) >= 5:
            break
    return clean[:5]


def _word_count(text: str) -> int:
    return len([word for word in re.split(r"\s+", text.strip()) if word])


def compose_linkedin_post(
    blog_content: str,
    title: str = "",
    excerpt: str = "",
    post_format: str = "feed_post",
    additional_context: str = "",
) -> dict[str, Any]:
    """Compose an insights-driven LinkedIn post from blog content."""
    if post_format not in ("feed_post", "long_form"):
        raise ValueError("post_format must be 'feed_post' or 'long_form'")

    resolved_title = title.strip() or _extract_frontmatter_value(blog_content, "title")
    resolved_excerpt = excerpt.strip() or _extract_frontmatter_value(blog_content, "excerpt")
    body = _strip_frontmatter(blog_content)

    if not body:
        raise ValueError("Blog content is empty")

    system_prompt = _load_linkedin_prompt()
    client, model = _get_openai_client()

    user_prompt = (
        f"Format: {post_format}\n"
        f"Title: {resolved_title or '(missing title)'}\n"
        f"Excerpt: {resolved_excerpt or '(missing excerpt)'}\n"
        f"Additional context: {additional_context or '(none)'}\n\n"
        f"Blog content:\n{body}\n\n"
        "Generate an insights-driven LinkedIn post optimized for reach and meaningful engagement."
    )

    logger.info(
        f"Composing LinkedIn post: format={post_format}, title_len={len(resolved_title)}, body_len={len(body)}"
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=1.0,
        max_completion_tokens=1800,
    )

    raw_output = response.choices[0].message.content or ""

    try:
        parsed = json.loads(_clean_json_response(raw_output))
    except Exception:
        logger.warning("LinkedIn composer returned non-JSON response; using fallback parse")
        fallback_post = raw_output.strip()
        hashtags = re.findall(r"#[A-Za-z0-9_]+", fallback_post)
        clean_tags = _normalize_hashtags(hashtags) or ["#SoftwareEngineering", "#TechLeadership", "#AI"]
        return {
            "format": post_format,
            "title": resolved_title,
            "excerpt": resolved_excerpt,
            "summary": resolved_excerpt,
            "insights": [],
            "my_2_cents": "",
            "hashtags": clean_tags,
            "post_text": fallback_post,
            "word_count": _word_count(fallback_post),
        }

    hook = str(parsed.get("hook", "")).strip()
    summary = str(parsed.get("summary", "")).strip() or resolved_excerpt
    insights = [str(item).strip() for item in parsed.get("insights", []) if str(item).strip()]
    my_2_cents = str(parsed.get("my_2_cents", "")).strip()
    cta = str(parsed.get("cta", "")).strip()
    hashtags = _normalize_hashtags([str(tag) for tag in parsed.get("hashtags", [])])

    generated_post = str(parsed.get("post_text", "")).strip()
    if not generated_post:
        sections: list[str] = []
        if hook:
            sections.append(hook)
        if summary:
            sections.append(summary)
        if insights:
            sections.append("\n".join([f"• {point}" for point in insights[:3]]))
        if my_2_cents:
            sections.append(f"My 2 cents: {my_2_cents}")
        if cta:
            sections.append(cta)
        if hashtags:
            sections.append(" ".join(hashtags))
        generated_post = "\n\n".join([section for section in sections if section])

    if not hashtags:
        hashtags = ["#SoftwareEngineering", "#AI", "#Leadership"]

    return {
        "format": post_format,
        "title": resolved_title,
        "excerpt": resolved_excerpt,
        "summary": summary,
        "insights": insights,
        "my_2_cents": my_2_cents,
        "hashtags": hashtags,
        "post_text": generated_post,
        "word_count": _word_count(generated_post),
    }
