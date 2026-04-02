"""Tests for the SEO router — SEO analysis and tracking."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.user import UserInfo
from backend.routers.seo import _analyze_seo

client = TestClient(app)

TEST_USER = UserInfo(user_id="test-user-1", name="Test User", email="test@example.com")

SAMPLE_HTML = """
<html>
<body>
<h1>My Blog Post Title</h1>
<h2>Introduction</h2>
<p>This is a sample blog post with some content. It has multiple sentences.
The content discusses important topics about cloud security and Azure.</p>
<h2>Main Section</h2>
<p>Here we have more content with <a href="/about">an internal link</a>
and <a href="https://example.com">an external link</a>.</p>
<img src="image.jpg" alt="Descriptive alt text" />
<img src="noalt.jpg" alt="" />
<img src="another.jpg" />
</body>
</html>
"""


class TestAnalyzeSeoFunction:
    def test_counts_words(self):
        result = _analyze_seo("<p>One two three four five</p>", "Title", "Desc")
        assert result["word_count"] == 5

    def test_counts_headings(self):
        result = _analyze_seo(SAMPLE_HTML, "My Blog Post Title", "A short excerpt")
        assert result["h1_count"] == 1
        assert result["h2_count"] == 2
        assert result["heading_count"] == 3  # 1 h1 + 2 h2

    def test_counts_images_and_alt_tags(self):
        result = _analyze_seo(SAMPLE_HTML, "Title", "Desc")
        assert result["image_count"] == 3
        assert result["images_with_alt"] == 1  # Only the one with non-empty alt

    def test_counts_links(self):
        result = _analyze_seo(SAMPLE_HTML, "Title", "Desc")
        assert result["internal_links"] == 1
        assert result["external_links"] == 1

    def test_title_and_excerpt_length(self):
        result = _analyze_seo("<p>Content</p>", "Short Title", "A medium length excerpt")
        assert result["title_length"] == len("Short Title")
        assert result["meta_description_length"] == len("A medium length excerpt")

    def test_readability_score_is_between_0_and_100(self):
        result = _analyze_seo(SAMPLE_HTML, "Title", "Desc")
        assert 0 <= result["readability_score"] <= 100

    def test_keyword_density_returns_dict(self):
        result = _analyze_seo(
            "<p>Azure Azure Azure cloud cloud security</p>",
            "Title",
            "Desc",
        )
        assert isinstance(result["keyword_density"], dict)
        assert "azure" in result["keyword_density"]

    def test_empty_html(self):
        result = _analyze_seo("", "", "")
        assert result["word_count"] == 0
        assert result["heading_count"] == 0
        assert result["image_count"] == 0


class TestAnalyzePostSeoEndpoint:
    @patch("backend.routers.seo.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.seo.record_seo_snapshot")
    @patch("backend.routers.seo.get_published_blog")
    def test_analyze_success(self, mock_get_blog, mock_record, mock_auth):
        mock_get_blog.return_value = {
            "htmlContent": SAMPLE_HTML,
            "title": "My Blog Post",
            "excerpt": "A short excerpt",
        }
        mock_record.return_value = {"id": "snapshot-1"}

        res = client.post("/api/seo/analyze/my-blog-post")
        assert res.status_code == 200
        data = res.json()
        assert data["slug"] == "my-blog-post"
        assert data["snapshot_id"] == "snapshot-1"
        assert "data" in data
        assert data["data"]["h1_count"] == 1

    @patch("backend.routers.seo.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.seo.get_published_blog")
    def test_analyze_blog_not_found(self, mock_get_blog, mock_auth):
        mock_get_blog.return_value = None

        res = client.post("/api/seo/analyze/nonexistent")
        assert res.status_code == 404


class TestSeoHistory:
    @patch("backend.routers.seo.get_seo_history")
    def test_get_history_returns_list(self, mock_history):
        mock_history.return_value = [
            {
                "id": "snap-1",
                "slug": "my-post",
                "data": {"word_count": 500},
                "createdAt": "2024-06-01T10:00:00Z",
            }
        ]

        res = client.get("/api/seo/history/my-post")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["slug"] == "my-post"
        mock_history.assert_called_once_with("my-post", 20)

    @patch("backend.routers.seo.get_seo_history")
    def test_get_history_with_limit(self, mock_history):
        mock_history.return_value = []

        res = client.get("/api/seo/history/my-post?limit=5")
        assert res.status_code == 200
        mock_history.assert_called_once_with("my-post", 5)


class TestSeoOverview:
    @patch("backend.routers.seo.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.seo.get_latest_seo_snapshots")
    def test_get_overview(self, mock_snapshots, mock_auth):
        mock_snapshots.return_value = [
            {
                "id": "snap-1",
                "slug": "post-1",
                "data": {"word_count": 500},
                "createdAt": "2024-06-01T10:00:00Z",
            },
        ]

        res = client.get("/api/seo/overview")
        assert res.status_code == 200
        assert len(res.json()) == 1
