"""Image Generator Service — Source image selection + AI hero image generation."""

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# URL patterns that indicate non-content images (icons, trackers, logos)
SKIP_PATTERNS = [
    "favicon", "icon", "logo", "badge", "avatar", "sprite",
    "tracking", "pixel", "1x1", "spacer", "blank", "placeholder",
    "gravatar", "wp-emoji", "smilies", "ads", "banner-ad",
    ".svg", "data:image", "base64",
]

# Patterns that suggest a good hero image candidate
PREFER_PATTERNS = [
    "diagram", "architecture", "hero", "cover", "featured",
    "header", "banner", "thumbnail", "og-image", "social",
    "main", "primary", "article", "post",
]


def pick_best_source_image(media_assets: list[dict[str, str]]) -> str | None:
    """Select the best hero image candidate from extracted media assets.

    Filters out icons/logos/trackers and prefers images with descriptive
    alt text or hero-like URL patterns.

    Returns the image URL or None if no good candidates.
    """
    if not media_assets:
        return None

    candidates: list[tuple[int, str]] = []

    for asset in media_assets:
        url = asset.get("url", "").strip()
        if not url:
            continue

        url_lower = url.lower()
        alt = asset.get("alt", "").lower()
        asset_type = asset.get("type", "image").lower()

        # Skip non-content images
        if any(pat in url_lower for pat in SKIP_PATTERNS):
            continue

        # Score the image
        score = 10  # base score

        # Prefer images with descriptive alt text
        if alt and len(alt) > 10:
            score += 20

        # Prefer diagram/architecture types
        if asset_type == "diagram":
            score += 30

        # Prefer hero-like URL patterns
        if any(pat in url_lower or pat in alt for pat in PREFER_PATTERNS):
            score += 25

        # Penalize very short alt text (likely decorative)
        if alt and len(alt) < 4:
            score -= 10

        # Prefer common image formats
        if any(url_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            score += 5

        candidates.append((score, url))

    if not candidates:
        return None

    # Return highest scored candidate
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_url = candidates[0][1]
    logger.info(f"Selected source image (score={candidates[0][0]}): {best_url[:100]}")
    return best_url


def generate_hero_image(title: str, excerpt: str, topics: list[str]) -> str:
    """Generate a hero image using gpt-image-1-mini via Azure OpenAI.

    Args:
        title: Blog post title.
        excerpt: Blog post excerpt/summary.
        topics: Relevant topic tags.

    Returns:
        URL of the generated image.

    Raises:
        RuntimeError: If image generation fails.
    """
    from backend.services.blog_service import _get_openai_client

    client, _ = _get_openai_client()
    image_model = os.environ.get("IMAGE_MODEL_DEPLOYMENT_NAME", "gpt-image-1-mini")

    topics_str = ", ".join(topics[:3]) if topics else "technology"

    prompt = (
        f"Create a professional, modern hero image for a technical blog post. "
        f"The blog is about: {title}. "
        f"Topics: {topics_str}. "
        f"Style: Clean, minimalist tech illustration with abstract geometric shapes, "
        f"gradient colors (blues, purples, teals), and subtle circuit/network patterns. "
        f"No text, no words, no letters, no watermarks. "
        f"Suitable as a wide banner image for a blog post header."
    )

    logger.info(f"Generating hero image for: {title[:60]} (model={image_model})")

    try:
        response = client.images.generate(
            model=image_model,
            prompt=prompt,
            n=1,
            size="1792x1024",
        )

        image_url = response.data[0].url
        if not image_url:
            raise RuntimeError("Image generation returned no URL")

        logger.info(f"Hero image generated: {image_url[:100]}")
        return image_url

    except Exception as exc:
        logger.error(f"Hero image generation failed: {exc}")
        raise RuntimeError(f"Image generation failed: {exc}")


def _inject_hero_image(content: str, image_url: str, alt_text: str) -> str:
    """Inject a hero image into MDX content after the frontmatter block."""
    # Find end of frontmatter (second ---)
    fm_match = re.match(r"(---[\s\S]*?---\n*)", content)
    if fm_match:
        frontmatter = fm_match.group(1)
        body = content[fm_match.end():]
        return f"{frontmatter}\n![{alt_text}]({image_url})\n\n{body}"
    else:
        # No frontmatter, prepend
        return f"![{alt_text}]({image_url})\n\n{content}"


def ensure_hero_image(
    blog_content: str,
    title: str,
    excerpt: str,
    media_assets: list[dict[str, str]],
    topics: list[str],
) -> tuple[str, str]:
    """Ensure blog content has a hero image. Returns (updated_content, hero_image_url).

    Strategy:
    1. If blog already has markdown images, use the first one as hero_image_url
    2. Try picking the best source image from media_assets
    3. Fall back to AI-generated image via gpt-image-1-mini
    """
    # Step 1: Check if blog already contains images
    existing_match = re.search(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", blog_content)
    if existing_match:
        hero_url = existing_match.group(1).strip()
        logger.info(f"Blog already contains image: {hero_url[:80]}")
        return blog_content, hero_url

    # Step 2: Try to pick from source media_assets
    source_image = pick_best_source_image(media_assets)
    if source_image:
        logger.info(f"Injecting source image as hero: {source_image[:80]}")
        updated = _inject_hero_image(blog_content, source_image, title)
        return updated, source_image

    # Step 3: Generate with AI
    try:
        generated_url = generate_hero_image(title, excerpt, topics)
        logger.info(f"Injecting AI-generated hero image")
        updated = _inject_hero_image(blog_content, generated_url, title)
        return updated, generated_url
    except Exception as exc:
        logger.warning(f"Could not generate hero image: {exc}; returning content as-is")
        return blog_content, ""
