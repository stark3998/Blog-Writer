"""Blog Service — Core Generation Pipeline.

Handles URL analysis and blog post generation using Azure OpenAI GPT-4o.
Extracted from the original BlogWriterAgent class for use with FastAPI.
"""

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from backend.tools.github_analyzer import analyze_github_repo
from backend.tools.webpage_analyzer import analyze_webpage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _is_github_url(url: str) -> bool:
    """Check if the URL points to a GitHub repository."""
    return bool(re.match(r"https?://(www\.)?github\.com/[^/]+/[^/]+", url.strip()))


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    """Create and return an Azure OpenAI client and model name."""
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o")

    logger.debug(
        f"Initializing Azure OpenAI client: endpoint={endpoint}, model={model}, api_version={api_version}"
    )

    if api_key:
        logger.debug("Using API key authentication")
        # Use API key authentication if provided (for local dev)
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=None,
            api_key=api_key,
            api_version=api_version,
        )
        return client, model
    else:
        logger.debug("Using DefaultAzureCredential authentication")
        # Use Azure AD authentication (for production)
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


def _build_analysis_context(analysis: dict[str, Any], source_type: str) -> str:
    """Format the analysis data into a context string for the LLM."""

    def _truncate(value: str, max_len: int = 280) -> str:
        value = value.strip()
        return value if len(value) <= max_len else value[: max_len - 3] + "..."

    if source_type == "github":
        parts = [
            "## Source: GitHub Repository",
            f"**URL:** {analysis.get('repo_url', '')}",
            f"**Name:** {analysis.get('full_name', '')}",
            f"**Description:** {analysis.get('description', 'No description')}",
            f"**Primary Language:** {analysis.get('primary_language', 'Unknown')}",
            f"**Stars:** {analysis.get('stars', 0)} | **Forks:** {analysis.get('forks', 0)}",
            f"**License:** {analysis.get('license', 'None')}",
            f"**Topics:** {', '.join(analysis.get('topics', []))}",
            "",
            "### Language Breakdown",
        ]
        for lang, pct in analysis.get("languages", {}).items():
            parts.append(f"- {lang}: {pct}")

        parts.append("")
        parts.append("### File Tree (key files)")
        tree = analysis.get("file_tree", [])
        for fp in tree[:100]:
            parts.append(f"- {fp}")
        if len(tree) > 100:
            parts.append(f"- ... and {len(tree) - 100} more files")

        parts.append("")
        parts.append("### README")
        parts.append(analysis.get("readme", "(No README found)"))

        key_files = analysis.get("key_files", {})
        if key_files:
            parts.append("")
            parts.append("### Key Files")
            for fp, content in key_files.items():
                parts.append(f"\n#### {fp}\n```\n{content}\n```")

        return "\n".join(parts)
    else:
        parts = [
            "## Source: Webpage",
            f"**URL:** {analysis.get('url', '')}",
            f"**Title:** {analysis.get('title', '')}",
            f"**Description:** {analysis.get('description', '')}",
            "",
            "### Headings Structure",
        ]
        for h in analysis.get("headings", []):
            indent = "  " * (int(h["level"][1]) - 1)
            parts.append(f"{indent}- [{h['level']}] {h['text']}")

        metadata = analysis.get("metadata", {})
        if metadata:
            parts.append("")
            parts.append("### Page Metadata")
            for key, value in metadata.items():
                parts.append(f"- {key}: {_truncate(str(value), 350)}")

        parts.append("")
        parts.append("### Main Content")
        parts.append(analysis.get("content", "(No content extracted)"))

        links = analysis.get("links", [])
        if links:
            parts.append("")
            parts.append("### Important Links")
            for link in links[:40]:
                label = _truncate(link.get("text", ""), 120)
                url = _truncate(link.get("url", ""), 220)
                if label:
                    parts.append(f"- {label}: {url}")
                else:
                    parts.append(f"- {url}")

        list_items = analysis.get("list_items", [])
        if list_items:
            parts.append("")
            parts.append("### Structured Lists")
            for item in list_items[:40]:
                parts.append(f"- {_truncate(str(item), 220)}")

        tables = analysis.get("tables", [])
        if tables:
            parts.append("")
            parts.append("### Tables Found")
            for index, table in enumerate(tables[:6], 1):
                headers = table.get("headers", [])
                rows = table.get("rows", [])
                parts.append(f"\n#### Table {index}")
                if headers:
                    parts.append(
                        "| " + " | ".join(_truncate(str(h), 80) for h in headers) + " |"
                    )
                    parts.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in rows[:6]:
                    cells = [_truncate(str(cell), 120) for cell in row]
                    parts.append("| " + " | ".join(cells) + " |")

        json_ld = analysis.get("json_ld", [])
        if json_ld:
            parts.append("")
            parts.append("### Structured Data (JSON-LD)")
            for index, block in enumerate(json_ld[:5], 1):
                parts.append(f"\n#### JSON-LD Block {index}")
                parts.append("```json")
                parts.append(_truncate(str(block), 1200))
                parts.append("```")

        code_blocks = analysis.get("code_blocks", [])
        if code_blocks:
            parts.append("")
            parts.append("### Code Blocks Found")
            for i, block in enumerate(code_blocks, 1):
                lang = block.get("language", "")
                parts.append(f"\n#### Code Block {i}" + (f" ({lang})" if lang else ""))
                parts.append(f"```{lang}\n{block['code']}\n```")

        media_assets = analysis.get("media_assets", [])
        if media_assets:
            parts.append("")
            parts.append("### Source Images & Diagrams")
            for asset in media_assets:
                asset_type = asset.get("type", "image")
                asset_url = asset.get("url", "")
                alt_text = asset.get("alt", "")
                if alt_text:
                    parts.append(f"- [{asset_type}] {asset_url} (alt: {alt_text})")
                else:
                    parts.append(f"- [{asset_type}] {asset_url}")

        return "\n".join(parts)


def analyze_source(url: str) -> dict[str, Any]:
    """Analyze a URL — auto-detects GitHub repos vs. general webpages.

    Args:
        url: A GitHub repository URL or any webpage URL.

    Returns:
        Analysis data dictionary with source type.
    """
    url = url.strip()
    start_time = time.time()

    logger.info(f"Analyzing source URL: {url}")

    if _is_github_url(url):
        logger.debug(f"Detected GitHub repository URL")
        analysis = analyze_github_repo(url)
        analysis["_source_type"] = "github"
    else:
        logger.debug(f"Detected webpage URL")
        analysis = analyze_webpage(url)
        analysis["_source_type"] = "webpage"

    elapsed = time.time() - start_time
    logger.info(
        f"Source analysis complete in {elapsed:.2f}s: type={analysis['_source_type']}, keys={len(analysis)}"
    )

    return analysis


def _looks_like_mermaid_architecture(text: str) -> bool:
    """Check if text appears to be raw mermaid architecture content."""
    return any(
        marker in text
        for marker in ["graph TD", "graph LR", "subgraph", "-->", "|", "end"]
    )


def _extract_architecture_groups(text: str) -> list[tuple[str, list[str]]]:
    """Extract subgraph titles and node labels from mermaid-like content."""
    groups: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_items: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        subgraph_with_label = re.match(r'^subgraph\s+[^\[]+\["(.+?)"\]\s*$', line)
        subgraph_simple = re.match(r"^subgraph\s+(.+?)\s*$", line)

        if subgraph_with_label:
            if current_title:
                groups.append((current_title, current_items))
            current_title = subgraph_with_label.group(1).strip()
            current_items = []
            continue

        if subgraph_simple:
            if current_title:
                groups.append((current_title, current_items))
            current_title = subgraph_simple.group(1).strip().strip('"')
            current_items = []
            continue

        if line == "end":
            if current_title:
                groups.append((current_title, current_items))
            current_title = None
            current_items = []
            continue

        if current_title:
            node = re.match(r"^[A-Za-z0-9_]+\[(.+?)\]\s*$", line)
            if node:
                current_items.append(node.group(1).strip())

    if current_title:
        groups.append((current_title, current_items))

    return groups


def _ascii_box(title: str, items: list[str]) -> str:
    """Render one ASCII box section."""
    bullet_lines = [f"• {item}" for item in items] if items else ["• (details omitted)"]
    content_lines = [title] + bullet_lines
    width = max(44, min(72, max(len(line) for line in content_lines) + 4))

    top = "┌" + "─" * width + "┐"
    sep = "├" + "─" * width + "┤"
    bottom = "└" + "─" * width + "┘"

    lines = [top]
    lines.append("│" + title.ljust(width) + "│")
    lines.append(sep)
    for line in bullet_lines:
        lines.append("│" + line.ljust(width) + "│")
    lines.append(bottom)
    return "\n".join(lines)


def _convert_architecture_to_ascii(text: str) -> str:
    """Convert raw mermaid-like architecture text to a stable ASCII diagram."""
    groups = _extract_architecture_groups(text)
    if not groups:
        groups = [
            (
                "Architecture",
                ["Enterprise data sources", "Foundry platform", "AI applications"],
            )
        ]

    rendered: list[str] = []
    for index, (title, items) in enumerate(groups):
        rendered.append(_ascii_box(title, items))
        if index < len(groups) - 1:
            rendered.append(" " * 22 + "↓")

    return "```\n" + "\n".join(rendered) + "\n```"


def _normalize_architecture_overview(mdx_content: str) -> str:
    """Normalize only the Architecture Overview section if unfenced mermaid-like content appears."""
    section_pattern = re.compile(
        r"(^##\s+Architecture Overview\s*$)([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    match = section_pattern.search(mdx_content)
    if not match:
        return mdx_content

    section_body = match.group(2)
    if re.search(r"```mermaid[\s\S]*?```", section_body, re.IGNORECASE):
        return mdx_content

    if not _looks_like_mermaid_architecture(section_body):
        return mdx_content

    logger.warning(
        "Architecture Overview contains unfenced mermaid-like content; converting to ASCII diagram"
    )
    replacement_body = "\n\n" + _convert_architecture_to_ascii(section_body) + "\n\n"
    return (
        mdx_content[: match.start(2)] + replacement_body + mdx_content[match.end(2) :]
    )


def _parse_blog_response(full_response: str) -> dict[str, str]:
    """Parse the LLM response to extract MDX content, slug, title, and excerpt."""
    slug_match = re.search(r"SLUG:\s*(.+?)(?:\s*$)", full_response, re.MULTILINE)
    if slug_match:
        slug = slug_match.group(1).strip()
        mdx_content = full_response[: slug_match.start()].strip()
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = "generated-post-" + today
        mdx_content = full_response.strip()

    # Remove any markdown code fences wrapping the entire MDX
    mdx_content = re.sub(r"^```mdx?\s*\n", "", mdx_content)
    mdx_content = re.sub(r"\n```\s*$", "", mdx_content)
    mdx_content = _normalize_architecture_overview(mdx_content)

    title_match = re.search(r'^title:\s*"(.+?)"', mdx_content, re.MULTILINE)
    excerpt_match = re.search(r'^excerpt:\s*"(.+?)"', mdx_content, re.MULTILINE)

    title = title_match.group(1) if title_match else "Generated Blog Post"
    excerpt = excerpt_match.group(1) if excerpt_match else ""

    return {
        "mdx_content": mdx_content,
        "slug": slug,
        "title": title,
        "excerpt": excerpt,
    }


def generate_blog_post(analysis: dict[str, Any]) -> dict[str, str]:
    """Generate a blog post from analysis data using GPT-4o (non-streaming).

    Args:
        analysis: The structured analysis data from analyze_source.

    Returns:
        Dictionary with 'mdx_content', 'slug', 'title', and 'excerpt'.
    """
    logger.info(
        f"Starting non-streaming blog generation from {analysis.get('_source_type', 'unknown')} source"
    )
    start_time = time.time()

    system_prompt = _load_system_prompt()
    media_assets = list(analysis.get("media_assets", []))
    source_type = analysis.pop("_source_type", "webpage")
    context = _build_analysis_context(analysis, source_type)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    user_message = (
        f"Today's date is {today}.\n\n"
        f"Please analyze the following source material and generate a complete blog post.\n\n"
        f"{context}\n\n"
        f"---\n\n"
        f"Generate the complete MDX blog post now. After the MDX content, on a new line, "
        f"output the slug in the format: SLUG: your-slug-here"
    )

    client, model = _get_openai_client()

    try:
        logger.debug(
            f"Making API call to {model} for blog generation: context_length={len(context)}"
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=1.0,
            max_completion_tokens=4096,
        )

        full_response = response.choices[0].message.content or ""
        elapsed = time.time() - start_time

        total_tokens = response.usage.total_tokens if response.usage else 0
        logger.info(
            f"Blog generation successful: elapsed={elapsed:.2f}s, "
            f"tokens={total_tokens}, "
            f"response_length={len(full_response)}"
        )

        result = _parse_blog_response(full_response)
        result["media_assets"] = media_assets
        return result
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"Blog generation failed after {elapsed:.2f}s: {type(e).__name__}: {str(e)}"
        )
        raise


async def generate_blog_post_stream(
    analysis: dict[str, Any],
) -> AsyncGenerator[str, None]:
    """Generate a blog post from analysis data using GPT-4o with streaming.

    Yields chunks of the generated MDX content as they arrive.

    Args:
        analysis: The structured analysis data from analyze_source.

    Yields:
        String chunks of the generated content.
    """
    logger.info(
        f"Starting streaming blog generation from {analysis.get('_source_type', 'unknown')} source"
    )
    start_time = time.time()
    chunk_count = 0

    system_prompt = _load_system_prompt()
    source_type = analysis.pop("_source_type", "webpage")
    context = _build_analysis_context(analysis, source_type)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    user_message = (
        f"Today's date is {today}.\n\n"
        f"Please analyze the following source material and generate a complete blog post.\n\n"
        f"{context}\n\n"
        f"---\n\n"
        f"Generate the complete MDX blog post now. After the MDX content, on a new line, "
        f"output the slug in the format: SLUG: your-slug-here"
    )

    client, model = _get_openai_client()

    try:
        logger.debug(f"Making streaming API call to {model} for blog generation")
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=1.0,
            max_completion_tokens=4096,
            stream=True,
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                chunk_count += 1
                logger.debug(f"Received chunk {chunk_count}: {len(content)} chars")
                yield content

        elapsed = time.time() - start_time
        logger.info(
            f"Streaming blog generation complete: elapsed={elapsed:.2f}s, chunks={chunk_count}"
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"Streaming blog generation failed after {elapsed:.2f}s: {type(e).__name__}: {str(e)}"
        )
        raise
