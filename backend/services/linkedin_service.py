"""LinkedIn Service — Compose reach-optimized, insights-driven LinkedIn posts."""

import json
import logging
import os
import re
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

def _load_linkedin_prompt() -> str:
    from backend.routers.prompts import load_prompt_content
    return load_prompt_content("linkedin_post_prompt")


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


def _find_hashtag_line_index(lines: list[str]) -> int | None:
    """Return the index of the last line that is purely hashtags, or None."""
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped and all(word.startswith("#") for word in stripped.split()):
            return i
    return None


def _insert_before_hashtags(post_text: str, line: str) -> str:
    """Insert a line before the trailing hashtag line (or append at end)."""
    lines = post_text.rstrip().split("\n")
    idx = _find_hashtag_line_index(lines)
    if idx is not None:
        lines.insert(idx, line)
    else:
        lines.append(line)
    return "\n".join(lines)


# Pattern matching source_url used in a "primary / CTA" context
_PRIMARY_CTA_PHRASES = (
    r"read\s+my\s+|full\s+analysis|my\s+blog|my\s+write-?up"
    r"|check\s+out\s+my|dive\s+into|my\s+detailed|my\s+breakdown"
)


def _fixup_post_urls(post_text: str, blog_url: str, source_url: str) -> str:
    """Ensure blog_url is the primary link and source_url is secondary attribution."""
    if not blog_url:
        return post_text

    primary_pattern = re.compile(
        r"(" + _PRIMARY_CTA_PHRASES + r")[^:\n]*?" + re.escape(source_url),
        re.IGNORECASE,
    ) if source_url else None

    if blog_url not in post_text:
        # blog_url missing — replace source_url in CTA context, or first occurrence
        if source_url and source_url in post_text:
            m = primary_pattern.search(post_text) if primary_pattern else None
            if m:
                post_text = post_text[:m.start()] + m.group().replace(source_url, blog_url) + post_text[m.end():]
            else:
                post_text = post_text.replace(source_url, blog_url, 1)
        else:
            post_text = _insert_before_hashtags(post_text, f"\nRead my full analysis: {blog_url}")
    else:
        # blog_url present — but check if source_url is also used in a CTA context
        if primary_pattern and source_url in post_text:
            m = primary_pattern.search(post_text)
            if m:
                post_text = post_text[:m.start()] + m.group().replace(source_url, blog_url) + post_text[m.end():]

    # Ensure source_url appears at most once
    if source_url:
        count = post_text.count(source_url)
        if count > 1:
            first_end = post_text.index(source_url) + len(source_url)
            post_text = post_text[:first_end] + post_text[first_end:].replace(source_url, "")

        # If source_url was fully removed (replaced with blog_url), add it back as attribution
        if source_url not in post_text:
            post_text = _insert_before_hashtags(post_text, f"\nInspired by: {source_url}")

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

    # Auto-populate blog_url from BLOG_BASE_URL + slug if not provided
    if not blog_url:
        blog_base = os.environ.get("BLOG_BASE_URL", "").rstrip("/")
        if blog_base:
            slug = _extract_frontmatter_value(blog_content, "slug")
            if slug:
                blog_url = f"{blog_base}/blog/{slug}"
                logger.info(f"Auto-populated blog_url: {blog_url}")

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
        link_lines += f"Blog URL (YOUR published post — PRIMARY link, use in CTA): {blog_url}\n"
    if source_url:
        link_lines += f"Source URL (original article — mention ONCE at the end as attribution): {source_url}\n"
    if blog_url and source_url:
        link_lines += "RULE: Use blog_url for 'Read my analysis' CTA. Put source_url ONLY as 'Inspired by: ...' near the end.\n"

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
            "validation": None,
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

    # Run validation agent to check accuracy and URL placement
    validation = None
    try:
        from backend.services.validation_agent import validate_content
        validation = validate_content(
            content_type="linkedin_post",
            generated_content=generated_post,
            source_material=body,
            blog_url=blog_url,
            source_url=source_url,
        )
        if validation.get("corrected_content"):
            logger.info("Validation agent applied corrections to LinkedIn post")
            generated_post = validation["corrected_content"]
    except Exception as exc:
        logger.warning(f"Validation agent failed, falling back to regex fixup: {exc}")
        generated_post = _fixup_post_urls(generated_post, blog_url, source_url)

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
        "validation": validation,
    }
