"""Webpage Analyzer Tool.

Fetches and parses any webpage URL to extract structured text content
for blog post generation: title, description, headings, body text,
and code blocks.
"""

import re
import json
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

MAX_CONTENT_LENGTH = 20_000
MAX_LINKS = 100
MAX_TABLES = 10
MAX_TABLE_ROWS = 8
MAX_LIST_ITEMS = 80
MAX_JSON_LD = 10
MAX_JSON_LD_LENGTH = 3_000

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
                {"language": lang, "code": code.get_text().strip()[:5000]}
            )
    return blocks[:15]


def _extract_headings(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract heading structure (h1-h4)."""
    headings: list[dict[str, str]] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = _clean_text(tag.get_text())
        if text:
            headings.append({"level": tag.name, "text": text})
    return headings


def _extract_main_content(soup: BeautifulSoup) -> str:
    """Extract the main article/body content as plain text."""
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

    for tag_name in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
        for el in main.find_all(tag_name):
            el.decompose()

    for el in main.find_all(attrs={"aria-hidden": "true"}):
        if isinstance(el, Tag):
            el.decompose()

    text = main.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if len(text) > MAX_CONTENT_LENGTH:
        text = text[:MAX_CONTENT_LENGTH] + "\n\n... (truncated)"

    return text


def _extract_media_assets(soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
    """Extract and classify image/diagram assets from a webpage."""
    media_assets: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    diagram_hints = ("diagram", "architecture", "workflow", "flowchart", "sequence")

    for image in soup.find_all("img"):
        if not isinstance(image, Tag):
            continue

        source = str(
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
            or ""
        ).strip()
        if not source:
            continue

        resolved_url = urljoin(page_url, source)
        if not resolved_url or resolved_url in seen_urls:
            continue

        alt_text = _clean_text(str(image.get("alt", "")))
        image_classes = image.get("class") or []
        classes_text = (
            " ".join(image_classes)
            if isinstance(image_classes, list)
            else str(image_classes)
        )
        classification_source = f"{source} {alt_text} {classes_text}".lower()
        media_type = (
            "diagram"
            if any(hint in classification_source for hint in diagram_hints)
            else "image"
        )

        media_assets.append(
            {
                "type": media_type,
                "url": resolved_url,
                "alt": alt_text,
            }
        )
        seen_urls.add(resolved_url)

        if len(media_assets) >= 20:
            break

    return media_assets


def _extract_metadata(soup: BeautifulSoup) -> dict[str, str]:
    """Extract common meta/OpenGraph/Twitter metadata fields."""
    fields = [
        "author",
        "keywords",
        "article:published_time",
        "article:modified_time",
        "og:type",
        "og:site_name",
        "og:image",
        "twitter:card",
        "twitter:site",
        "twitter:creator",
        "twitter:image",
    ]

    metadata: dict[str, str] = {}
    for field in fields:
        if field.startswith("og:") or field.startswith("article:") or field.startswith("twitter:"):
            tag = soup.find("meta", attrs={"property": field})
            if not tag:
                tag = soup.find("meta", attrs={"name": field})
        else:
            tag = soup.find("meta", attrs={"name": field})

        if tag and isinstance(tag, Tag):
            value = _clean_text(str(tag.get("content", "")))
            if value:
                metadata[field] = value[:500]

    return metadata


def _extract_links(soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
    """Extract important links from page anchors."""
    links: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue

        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue

        resolved_url = urljoin(page_url, href)
        if resolved_url in seen_urls:
            continue

        text = _clean_text(anchor.get_text())
        links.append({"text": text[:200], "url": resolved_url})
        seen_urls.add(resolved_url)

        if len(links) >= MAX_LINKS:
            break

    return links


def _extract_list_items(soup: BeautifulSoup) -> list[str]:
    """Extract list item text from ul/ol blocks."""
    items: list[str] = []
    seen: set[str] = set()

    for item in soup.find_all("li"):
        text = _clean_text(item.get_text())
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text[:300])
        if len(items) >= MAX_LIST_ITEMS:
            break

    return items


def _extract_tables(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract compact table data from HTML tables."""
    tables: list[dict[str, Any]] = []

    for table in soup.find_all("table")[:MAX_TABLES]:
        headers: list[str] = []
        header_cells = table.find_all("th")
        if header_cells:
            headers = [_clean_text(cell.get_text())[:120] for cell in header_cells]

        rows: list[list[str]] = []
        tr_tags = table.find_all("tr")
        for row in tr_tags[:MAX_TABLE_ROWS]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            row_data = [_clean_text(cell.get_text())[:200] for cell in cells]
            if any(row_data):
                rows.append(row_data)

        if rows:
            tables.append({"headers": headers, "rows": rows})

    return tables


def _extract_json_ld(soup: BeautifulSoup) -> list[str]:
    """Extract JSON-LD script blocks as compact strings."""
    blocks: list[str] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if len(blocks) >= MAX_JSON_LD:
            break
        if not isinstance(script, Tag):
            continue

        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue

        try:
            parsed = json.loads(raw)
            compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            compact = re.sub(r"\s+", " ", raw)

        blocks.append(compact[:MAX_JSON_LD_LENGTH])

    return blocks


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

    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = _clean_text(title_tag.get_text())

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and isinstance(meta_desc, Tag):
        description = str(meta_desc.get("content", ""))

    og_title = ""
    og_desc = ""
    og_tag = soup.find("meta", attrs={"property": "og:title"})
    if og_tag and isinstance(og_tag, Tag):
        og_title = str(og_tag.get("content", ""))
    og_tag = soup.find("meta", attrs={"property": "og:description"})
    if og_tag and isinstance(og_tag, Tag):
        og_desc = str(og_tag.get("content", ""))

    final_url = str(resp.url or url)

    return {
        "url": final_url,
        "title": title or og_title,
        "description": description or og_desc,
        "metadata": _extract_metadata(soup),
        "headings": _extract_headings(soup),
        "content": _extract_main_content(soup),
        "code_blocks": _extract_code_blocks(soup),
        "media_assets": _extract_media_assets(soup, final_url),
        "links": _extract_links(soup, final_url),
        "tables": _extract_tables(soup),
        "list_items": _extract_list_items(soup),
        "json_ld": _extract_json_ld(soup),
    }
