"""Medium Service — Compose Medium-optimized article content from blog content."""

import logging
import re
from typing import Any

from backend.services.export_service import _convert_to_html, _strip_frontmatter

logger = logging.getLogger(__name__)


def prepare_medium_article(
    blog_content: str,
    title: str = "",
    excerpt: str = "",
    blog_url: str = "",
) -> dict[str, Any]:
    """Prepare blog content for Medium publishing.

    Converts MDX/Markdown to HTML and extracts metadata.
    """
    metadata, _ = _strip_frontmatter(blog_content)

    resolved_title = title.strip() or metadata.get("title", "Untitled")
    resolved_excerpt = excerpt.strip() or metadata.get("excerpt", "")

    tags: list[str] = []
    if "tags" in metadata:
        tags = re.findall(r'"([^"]+)"', metadata["tags"])

    html_content = _convert_to_html(blog_content)

    # Add canonical URL attribution if available
    if blog_url:
        html_content += f'\n<p><em>Originally published at <a href="{blog_url}">{blog_url}</a></em></p>'

    return {
        "title": resolved_title,
        "excerpt": resolved_excerpt,
        "html_content": html_content,
        "tags": tags[:5],
    }
