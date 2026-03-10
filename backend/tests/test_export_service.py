"""Tests for export_service module — format conversion logic."""

import pytest

from backend.services.export_service import (
    export_blog,
    _strip_frontmatter,
    _strip_mdx_components,
    _convert_to_markdown,
    _convert_to_html,
)


SAMPLE_MDX = """---
title: "Test Blog"
slug: "test-blog"
excerpt: "Testing export"
date: "2024-01-01"
---

import { BlogLayout } from '../components/BlogLayout'

# Test Heading

This is a test paragraph with **bold** and *italic* text.

## Code Example

```python
def hello():
    print("Hello, world!")
```

<BlogLayout />

- Item one
- Item two
- Item three
"""


# ---------- _strip_frontmatter ----------

class TestStripFrontmatter:
    def test_strips_yaml_frontmatter(self):
        metadata, body = _strip_frontmatter(SAMPLE_MDX)
        assert metadata["title"] == "Test Blog"
        assert metadata["slug"] == "test-blog"
        assert "---" not in body.split("\n")[0]

    def test_no_frontmatter(self):
        metadata, body = _strip_frontmatter("# Just a heading")
        assert metadata == {}
        assert body == "# Just a heading"


# ---------- _strip_mdx_components ----------

class TestStripMdxComponents:
    def test_strips_import_and_jsx(self):
        result = _strip_mdx_components(SAMPLE_MDX)
        assert "import {" not in result
        assert "<BlogLayout />" not in result
        assert "# Test Heading" in result


# ---------- _convert_to_markdown ----------

class TestConvertToMarkdown:
    def test_produces_clean_markdown(self):
        md = _convert_to_markdown(SAMPLE_MDX)
        assert "import {" not in md
        assert "<BlogLayout />" not in md
        assert "# Test Heading" in md
        # frontmatter is re-serialized as YAML between --- delimiters
        assert "title: Test Blog" in md


# ---------- _convert_to_html ----------

class TestConvertToHtml:
    def test_produces_html_with_structure(self):
        html = _convert_to_html(SAMPLE_MDX)
        assert "<!DOCTYPE html>" in html
        assert "<h1>" in html or "<h1" in html
        assert "Hello, world!" in html

    def test_title_in_html(self):
        html = _convert_to_html(SAMPLE_MDX)
        assert "Test Blog" in html


# ---------- export_blog ----------

class TestExportBlog:
    def test_export_mdx(self):
        data, filename, ct = export_blog(SAMPLE_MDX, "mdx")
        assert filename.endswith(".mdx")
        assert ct == "text/mdx"
        assert b"# Test Heading" in data

    def test_export_markdown(self):
        data, filename, ct = export_blog(SAMPLE_MDX, "md")
        assert filename.endswith(".md")
        assert ct == "text/markdown"
        assert b"import" not in data  # MDX imports stripped

    def test_export_html(self):
        data, filename, ct = export_blog(SAMPLE_MDX, "html")
        assert filename.endswith(".html")
        assert ct == "text/html"
        assert b"<!DOCTYPE html>" in data

    def test_export_invalid_format(self):
        with pytest.raises(ValueError):
            export_blog(SAMPLE_MDX, "txt")  # type: ignore
