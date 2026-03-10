"""Tests for blog_service module — URL detection and parsing logic."""

import pytest
from unittest.mock import patch, MagicMock

from backend.services.blog_service import (
    _is_github_url,
    _build_analysis_context,
    _parse_blog_response,
    _normalize_architecture_overview,
    analyze_source,
)


# ---------- _is_github_url ----------

class TestIsGithubUrl:
    def test_standard_github_repo(self):
        assert _is_github_url("https://github.com/user/repo") is True

    def test_github_repo_with_www(self):
        assert _is_github_url("https://www.github.com/user/repo") is True

    def test_github_repo_http(self):
        assert _is_github_url("http://github.com/user/repo") is True

    def test_github_repo_with_trailing_path(self):
        assert _is_github_url("https://github.com/user/repo/tree/main") is True

    def test_not_github(self):
        assert _is_github_url("https://example.com/page") is False

    def test_empty_string(self):
        assert _is_github_url("") is False

    def test_github_profile_not_repo(self):
        # Only /user/repo matches — a bare profile doesn't
        assert _is_github_url("https://github.com/user") is False


# ---------- _build_analysis_context ----------

class TestBuildAnalysisContext:
    def test_github_context(self):
        analysis = {
            "full_name": "org/project",
            "description": "Cool tool",
            "stars": 500,
            "forks": 50,
            "languages": {"Python": 80, "JS": 20},
            "readme": "# Hello\nWorld",
            "key_files": {"main.py": "print('hi')"},
        }
        ctx = _build_analysis_context(analysis, "github")
        assert "org/project" in ctx
        assert "Cool tool" in ctx
        assert "Python" in ctx

    def test_webpage_context(self):
        analysis = {
            "title": "My Page",
            "url": "https://example.com",
            "description": "A test page",
            "headings": [{"level": "h1", "text": "Main"}],
            "content": "Some body text.",
            "code_blocks": [],
        }
        ctx = _build_analysis_context(analysis, "webpage")
        assert "My Page" in ctx
        assert "Main" in ctx

    def test_webpage_context_includes_media_assets(self):
        analysis = {
            "title": "My Page",
            "url": "https://example.com",
            "description": "A test page",
            "headings": [{"level": "h1", "text": "Main"}],
            "content": "Some body text.",
            "code_blocks": [],
            "media_assets": [
                {
                    "type": "diagram",
                    "url": "https://example.com/assets/architecture.png",
                    "alt": "Architecture overview",
                },
                {
                    "type": "image",
                    "url": "https://example.com/assets/screenshot.png",
                    "alt": "",
                },
            ],
        }
        ctx = _build_analysis_context(analysis, "webpage")
        assert "### Source Images & Diagrams" in ctx
        assert "[diagram] https://example.com/assets/architecture.png" in ctx
        assert "(alt: Architecture overview)" in ctx
        assert "[image] https://example.com/assets/screenshot.png" in ctx

    def test_webpage_context_includes_rich_extracted_fields(self):
        analysis = {
            "title": "Deep Dive Page",
            "url": "https://example.com/deep-dive",
            "description": "A detailed page",
            "metadata": {
                "author": "Jane Doe",
                "og:type": "article",
            },
            "headings": [{"level": "h2", "text": "Overview"}],
            "content": "Main extracted content.",
            "links": [
                {"text": "Architecture Docs", "url": "https://example.com/docs"},
            ],
            "list_items": [
                "Use managed identity",
                "Enable observability",
            ],
            "tables": [
                {
                    "headers": ["Feature", "Status"],
                    "rows": [["AI Gateway", "Enabled"], ["Tracing", "Enabled"]],
                }
            ],
            "json_ld": ['{"@type":"Article","headline":"Deep Dive"}'],
            "code_blocks": [],
            "media_assets": [],
        }

        ctx = _build_analysis_context(analysis, "webpage")

        assert "### Page Metadata" in ctx
        assert "author: Jane Doe" in ctx
        assert "### Important Links" in ctx
        assert "Architecture Docs: https://example.com/docs" in ctx
        assert "### Structured Lists" in ctx
        assert "- Use managed identity" in ctx
        assert "### Tables Found" in ctx
        assert "| Feature | Status |" in ctx
        assert "### Structured Data (JSON-LD)" in ctx
        assert "```json" in ctx


# ---------- _parse_blog_response ----------

class TestParseBlogResponse:
    def test_parses_mdx_with_frontmatter(self):
        raw = """---
title: "Test Post"
excerpt: "A test"
---

# Content here

SLUG: test-post
"""
        result = _parse_blog_response(raw)
        assert result["title"] == "Test Post"
        assert result["slug"] == "test-post"
        assert result["excerpt"] == "A test"
        assert "# Content here" in result["mdx_content"]

    def test_parses_without_frontmatter(self):
        raw = "# Just a heading\n\nSome text."
        result = _parse_blog_response(raw)
        assert result["title"] == "Generated Blog Post"
        assert result["slug"] != ""
        assert result["mdx_content"] == raw

    def test_frontmatter_with_different_quoting(self):
        raw = """---
title: "Quoted Title"
excerpt: "Short desc"
---

Body text.

SLUG: explicit-slug
"""
        result = _parse_blog_response(raw)
        assert result["title"] == "Quoted Title"
        assert result["slug"] == "explicit-slug"

    def test_architecture_unfenced_mermaid_converted_to_ascii(self):
        raw = """---
title: "Architecture Test"
excerpt: "Desc"
---

## Architecture Overview
graph TD

subgraph EnterpriseData["Enterprise Data Sources"]
A[Operational Databases]
B[Documents & Knowledge Bases]
end

subgraph Applications["AI Applications"]
C[Internal Assistants]
D[Customer Apps]
end

A --> C

## Conclusion
Done.

SLUG: architecture-test
"""
        result = _parse_blog_response(raw)
        assert "```" in result["mdx_content"]
        assert "```mermaid" not in result["mdx_content"]
        assert "Enterprise Data Sources" in result["mdx_content"]
        assert "AI Applications" in result["mdx_content"]

    def test_architecture_valid_mermaid_kept(self):
        raw = """## Architecture Overview

```mermaid
graph TD
    A[One] --> B[Two]
```

## Conclusion
Done.
"""
        result = _parse_blog_response(raw)
        assert "```mermaid" in result["mdx_content"]
        assert "graph TD" in result["mdx_content"]


class TestArchitectureNormalization:
    def test_non_architecture_sections_unchanged(self):
        text = """## Key Technical Observations
graph TD
A[One] --> B[Two]
"""
        normalized = _normalize_architecture_overview(text)
        assert normalized == text


# ---------- analyze_source ----------

class TestAnalyzeSource:
    @patch("backend.services.blog_service.analyze_github_repo")
    def test_github_url_uses_github_analyzer(self, mock_gh):
        mock_gh.return_value = {"full_name": "user/repo"}
        result = analyze_source("https://github.com/user/repo")
        mock_gh.assert_called_once_with("https://github.com/user/repo")
        assert result["_source_type"] == "github"

    @patch("backend.services.blog_service.analyze_webpage")
    def test_webpage_url_uses_webpage_analyzer(self, mock_wp):
        mock_wp.return_value = {"title": "Page"}
        result = analyze_source("https://example.com/page")
        mock_wp.assert_called_once_with("https://example.com/page")
        assert result["_source_type"] == "webpage"

    @patch("backend.services.blog_service.analyze_webpage")
    def test_empty_url_falls_through_to_webpage(self, mock_wp):
        """Empty string is not a GitHub URL, so it falls to webpage analyzer."""
        mock_wp.return_value = {"title": ""}
        result = analyze_source("")
        mock_wp.assert_called_once_with("")
        assert result["_source_type"] == "webpage"
