"""Webpage Analyzer Tool.

Fetches and parses any webpage URL to extract structured text content
for blog post generation: title, description, headings, body text,
and code blocks.
"""

import re
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

# Maximum content length to return (characters)
MAX_CONTENT_LENGTH = 20_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _clean_text(text: str) -> str:
    """Clean extracted text: normalize whitespace and strip."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_code_blocks(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract code blocks from <pre><code> elements."""
    blocks: list[dict[str, str]] = []
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if code and isinstance(code, Tag):
            # Try to detect language from class
            classes = code.get("class") or []
            if isinstance(classes, list):
                lang = ""
                for cls in classes:
                    if isinstance(cls, str) and cls.startswith(("language-", "lang-")):
                        lang = cls.split("-", 1)[1]
                        break
            else:
                lang = ""
            blocks.append(
                {
                    "language": lang,
                    "code": code.get_text().strip()[
                        :5000
                    ],  # cap at 5000 chars per block
                }
            )
    return blocks[:15]  # cap at 15 code blocks


def _extract_headings(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract heading structure (h1-h4)."""
    headings: list[dict[str, str]] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = _clean_text(tag.get_text())
        if text:
            headings.append({"level": tag.name, "text": text})
    return headings


def _extract_main_content(soup: BeautifulSoup) -> str:
    """Extract the main article/body content as plain text.

    Prioritizes <article>, <main>, or role='main' elements.
    Falls back to <body> if none found.
    """
    # Try semantic elements first
    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"(content|article|post|entry)", re.I))
    )

    if not main:
        main = soup.find("body")

    if not main:
        return ""

    # Remove script, style, nav, footer, header, aside elements
    for tag_name in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
        for el in main.find_all(tag_name):
            el.decompose()

    # Remove hidden elements
    for el in main.find_all(attrs={"aria-hidden": "true"}):
        if isinstance(el, Tag):
            el.decompose()

    text = main.get_text(separator="\n")
    # Clean up: remove excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if len(text) > MAX_CONTENT_LENGTH:
        text = text[:MAX_CONTENT_LENGTH] + "\n\n... (truncated)"

    return text


def analyze_webpage(url: str) -> dict[str, Any]:
    """Analyze a webpage and return structured content data.

    Args:
        url: Any webpage URL to analyze.

    Returns:
        Dictionary with page title, description, headings structure,
        main text content, and extracted code blocks.
    """
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract title
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = _clean_text(title_tag.get_text())

    # Extract meta description
    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and isinstance(meta_desc, Tag):
        description = str(meta_desc.get("content", ""))

    # Extract Open Graph data
    og_title = ""
    og_desc = ""
    og_tag = soup.find("meta", attrs={"property": "og:title"})
    if og_tag and isinstance(og_tag, Tag):
        og_title = str(og_tag.get("content", ""))
    og_tag = soup.find("meta", attrs={"property": "og:description"})
    if og_tag and isinstance(og_tag, Tag):
        og_desc = str(og_tag.get("content", ""))

    return {
        "url": url,
        "title": title or og_title,
        "description": description or og_desc,
        "headings": _extract_headings(soup),
        "content": _extract_main_content(soup),
        "code_blocks": _extract_code_blocks(soup),
    }
