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


def _extract_first_image_url(content: str) -> str:
    """Extract the first image URL from markdown content."""
    match = re.search(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", content)
    return match.group(1).strip() if match else ""


def _word_count(text: str) -> int:
    return len([word for word in re.split(r"\s+", text.strip()) if word])


def _fixup_post_urls(post_text: str, blog_url: str, source_url: str) -> str:
    """Ensure blog_url is the primary link and source_url appears at most once."""
    if not blog_url:
        return post_text

    # If blog_url is missing, replace the first source_url occurrence with it
    if blog_url not in post_text:
        if source_url and source_url in post_text:
            post_text = post_text.replace(source_url, blog_url, 1)
        else:
            # Append blog_url before the hashtag line (or at end)
            lines = post_text.rstrip().split("\n")
            hashtag_idx = None
            for i in range(len(lines) - 1, -1, -1):
                stripped = lines[i].strip()
                if stripped and all(word.startswith("#") for word in stripped.split()):
                    hashtag_idx = i
                    break
            link_line = f"\nRead my full analysis: {blog_url}"
            if hashtag_idx is not None:
                lines.insert(hashtag_idx, link_line)
            else:
                lines.append(link_line)
            post_text = "\n".join(lines)

    # Deduplicate source_url — keep only the first occurrence
    if source_url and post_text.count(source_url) > 1:
        first_end = post_text.index(source_url) + len(source_url)
        post_text = post_text[:first_end] + post_text[first_end:].replace(source_url, "")

    return post_text


def compose_linkedin_post(
    blog_content: str,
    title: str = "",
    excerpt: str = "",
    post_format: str = "feed_post",
    additional_context: str = "",
    blog_url: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """Compose an insights-driven LinkedIn post from blog content."""
    if post_format not in ("feed_post", "long_form"):
        raise ValueError("post_format must be 'feed_post' or 'long_form'")

    resolved_title = title.strip() or _extract_frontmatter_value(blog_content, "title")
    resolved_excerpt = excerpt.strip() or _extract_frontmatter_value(blog_content, "excerpt")
    image_url = _extract_first_image_url(blog_content)
    body = _strip_frontmatter(blog_content)

    # Strip source_url from body so the LLM doesn't confuse it with blog_url
    if source_url:
        body = body.replace(source_url, "[original source]")

    if not body:
        raise ValueError("Blog content is empty")

    system_prompt = _load_linkedin_prompt()
    client, model = _get_openai_client()

    # Build link context for the prompt
    link_lines = ""
    if blog_url:
        link_lines += f"Blog URL (YOUR published post — this is the PRIMARY link to promote): {blog_url}\n"
    if source_url:
        link_lines += f"Source URL (original article — secondary reference/attribution): {source_url}\n"

    user_prompt = (
        f"Format: {post_format}\n"
        f"Title: {resolved_title or '(missing title)'}\n"
        f"Excerpt: {resolved_excerpt or '(missing excerpt)'}\n"
        f"{link_lines}"
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
        fallback_post = _fixup_post_urls(raw_output.strip(), blog_url, source_url)
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
            "image_url": image_url,
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

    generated_post = _fixup_post_urls(generated_post, blog_url, source_url)

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
        "image_url": image_url,
    }
