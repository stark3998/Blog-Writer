"""Tests for FastAPI routers — API endpoint integration tests."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ---------- Health ----------

class TestHealthEndpoint:
    def test_health_returns_200(self):
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"


# ---------- Generate (non-streaming) ----------

class TestGenerateEndpoint:
    @patch("backend.routers.generate.generate_blog_post")
    @patch("backend.routers.generate.analyze_source")
    def test_generate_success(self, mock_analyze, mock_generate):
        mock_analyze.return_value = {"_source_type": "webpage", "title": "Test"}
        mock_generate.return_value = {
            "mdx_content": "# Hello",
            "slug": "hello",
            "title": "Hello",
            "excerpt": "A test",
        }

        res = client.post("/api/generate", json={"url": "https://example.com"})
        assert res.status_code == 200
        data = res.json()
        assert data["title"] == "Hello"
        assert data["source_type"] == "webpage"

    def test_generate_empty_url(self):
        res = client.post("/api/generate", json={"url": ""})
        # The service should raise ValueError for empty URL
        assert res.status_code in (400, 500)


# ---------- Edit (non-streaming) ----------

class TestEditEndpoint:
    @patch("backend.routers.edit.edit_blog_content")
    def test_edit_success(self, mock_edit):
        mock_edit.return_value = "# Updated Content"

        res = client.post(
            "/api/edit",
            json={"content": "# Old Content", "prompt": "Add conclusion"},
        )
        assert res.status_code == 200
        assert res.json()["content"] == "# Updated Content"


# ---------- Export ----------

class TestExportEndpoint:
    def test_export_mdx(self):
        res = client.post(
            "/api/export",
            json={"content": "# Test\n\nHello world", "format": "mdx"},
        )
        assert res.status_code == 200
        assert "attachment" in res.headers.get("content-disposition", "")

    def test_export_markdown(self):
        res = client.post(
            "/api/export",
            json={"content": "# Test\n\nHello world", "format": "md"},
        )
        assert res.status_code == 200

    def test_export_html(self):
        res = client.post(
            "/api/export",
            json={"content": "# Test\n\nHello world", "format": "html"},
        )
        assert res.status_code == 200
        assert b"<!DOCTYPE html>" in res.content

    def test_export_invalid_format(self):
        res = client.post(
            "/api/export",
            json={"content": "# Test", "format": "xyz"},
        )
        assert res.status_code == 422  # Pydantic validation


# ---------- Blogs CRUD (mocked Cosmos) ----------

class TestBlogsCRUD:
    @patch("backend.routers.blogs.list_drafts")
    def test_list_drafts(self, mock_list):
        mock_list.return_value = [
            {
                "id": "1",
                "title": "Test",
                "slug": "test",
                "excerpt": "",
                "sourceUrl": "",
                "sourceType": "manual",
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
            }
        ]
        res = client.get("/api/blogs")
        assert res.status_code == 200
        assert len(res.json()) == 1

    @patch("backend.routers.blogs.get_draft")
    def test_get_draft(self, mock_get):
        mock_get.return_value = {
            "id": "1",
            "title": "Test",
            "slug": "test",
            "excerpt": "",
            "content": "# Hello",
            "sourceUrl": "",
            "sourceType": "manual",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }
        res = client.get("/api/blogs/1")
        assert res.status_code == 200
        assert res.json()["content"] == "# Hello"

    @patch("backend.routers.blogs.get_draft")
    def test_get_draft_not_found(self, mock_get):
        mock_get.return_value = None
        res = client.get("/api/blogs/nonexistent")
        assert res.status_code == 404

    @patch("backend.routers.blogs.create_draft")
    def test_create_draft(self, mock_create):
        mock_create.return_value = {
            "id": "2",
            "title": "New",
            "slug": "new",
            "excerpt": "",
            "content": "# New Post",
            "sourceUrl": "",
            "sourceType": "manual",
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
        }
        res = client.post(
            "/api/blogs",
            json={
                "title": "New",
                "slug": "new",
                "content": "# New Post",
            },
        )
        assert res.status_code == 201
        assert res.json()["id"] == "2"

    @patch("backend.routers.blogs.delete_draft")
    def test_delete_draft(self, mock_delete):
        mock_delete.return_value = True
        res = client.delete("/api/blogs/1")
        assert res.status_code == 204

    @patch("backend.routers.blogs.delete_draft")
    def test_delete_draft_not_found(self, mock_delete):
        mock_delete.return_value = False
        res = client.delete("/api/blogs/nonexistent")
        assert res.status_code == 404


# ---------- Publish (mocked) ----------

class TestPublishEndpoint:
    @patch("backend.routers.publish.publish_blog_post")
    def test_publish_success(self, mock_pub):
        mock_pub.return_value = {
            "pr_url": "https://github.com/user/repo/pull/1",
            "branch": "blog/test",
            "file_path": "content/blog/test.mdx",
        }
        res = client.post(
            "/api/publish",
            json={
                "content": "# Test",
                "slug": "test",
                "title": "Test Post",
            },
        )
        assert res.status_code == 200
        assert "pr_url" in res.json()


# ---------- LinkedIn Compose (mocked) ----------

class TestLinkedInComposeEndpoint:
    @patch("backend.routers.linkedin.compose_linkedin_post")
    def test_compose_with_content_success(self, mock_compose):
        mock_compose.return_value = {
            "format": "feed_post",
            "title": "My Post",
            "excerpt": "Short summary",
            "summary": "Summary",
            "insights": ["Insight 1", "Insight 2"],
            "my_2_cents": "Balanced perspective",
            "hashtags": ["#AI", "#SoftwareEngineering", "#Leadership"],
            "post_text": "A strong hook...",
            "word_count": 210,
        }

        res = client.post(
            "/api/linkedin/compose",
            json={
                "content": "# Blog content",
                "post_format": "feed_post",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "feed_post"
        assert data["word_count"] == 210
        assert len(data["hashtags"]) == 3

    @patch("backend.routers.linkedin.compose_linkedin_post")
    @patch("backend.routers.linkedin.get_draft")
    def test_compose_with_draft_id_success(self, mock_get_draft, mock_compose):
        mock_get_draft.return_value = {
            "id": "draft-1",
            "title": "Draft title",
            "excerpt": "Draft excerpt",
            "content": "# Draft content",
        }
        mock_compose.return_value = {
            "format": "long_form",
            "title": "Draft title",
            "excerpt": "Draft excerpt",
            "summary": "Summary",
            "insights": ["Insight"],
            "my_2_cents": "Perspective",
            "hashtags": ["#AI", "#Tech"],
            "post_text": "Long form copy...",
            "word_count": 420,
        }

        res = client.post(
            "/api/linkedin/compose",
            json={
                "draft_id": "draft-1",
                "post_format": "long_form",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "long_form"
        mock_get_draft.assert_called_once_with("draft-1")

    @patch("backend.routers.linkedin.get_draft")
    def test_compose_draft_not_found(self, mock_get_draft):
        mock_get_draft.return_value = None
        res = client.post(
            "/api/linkedin/compose",
            json={
                "draft_id": "missing",
            },
        )
        assert res.status_code == 404

    def test_compose_missing_content_and_draft(self):
        res = client.post(
            "/api/linkedin/compose",
            json={
                "content": "",
            },
        )
        assert res.status_code == 400


class TestLinkedInOAuthAndPublishEndpoint:
    @patch("backend.routers.linkedin.start_oauth")
    def test_oauth_start_success(self, mock_start):
        mock_start.return_value = {
            "session_id": "session-1",
            "state": "state-1",
            "auth_url": "https://www.linkedin.com/oauth/v2/authorization?...",
        }
        res = client.get("/api/linkedin/oauth/start")
        assert res.status_code == 200
        assert res.json()["session_id"] == "session-1"

    @patch("backend.routers.linkedin.handle_oauth_callback")
    def test_oauth_callback_success(self, mock_callback):
        mock_callback.return_value = {
            "session_id": "session-1",
            "person_urn": "urn:li:person:abc",
            "expires_at": 9999999999.0,
        }
        res = client.post(
            "/api/linkedin/oauth/callback",
            json={"code": "auth-code", "state": "state-1"},
        )
        assert res.status_code == 200
        assert res.json()["person_urn"] == "urn:li:person:abc"

    @patch("backend.routers.linkedin.get_connection_status")
    def test_oauth_status(self, mock_status):
        mock_status.return_value = {
            "connected": True,
            "session_id": "session-1",
            "person_urn": "urn:li:person:abc",
            "expires_at": 9999999999.0,
        }
        res = client.get("/api/linkedin/status?session_id=session-1")
        assert res.status_code == 200
        assert res.json()["connected"] is True

    @patch("backend.routers.linkedin.publish_member_post")
    def test_publish_with_post_text_success(self, mock_publish):
        mock_publish.return_value = {
            "session_id": "session-1",
            "post_id": "urn:li:share:123",
            "visibility": "PUBLIC",
            "status_code": 201,
        }
        res = client.post(
            "/api/linkedin/publish",
            json={
                "session_id": "session-1",
                "post_text": "Hello LinkedIn! #AI",
            },
        )
        assert res.status_code == 200
        assert res.json()["post_id"] == "urn:li:share:123"
        assert res.json()["composed"] is False

    @patch("backend.routers.linkedin.publish_member_post")
    @patch("backend.routers.linkedin.compose_linkedin_post")
    def test_publish_with_composition_success(self, mock_compose, mock_publish):
        mock_compose.return_value = {
            "format": "feed_post",
            "title": "Title",
            "excerpt": "Excerpt",
            "summary": "Summary",
            "insights": ["Insight 1"],
            "my_2_cents": "My take",
            "hashtags": ["#AI"],
            "post_text": "Composed copy #AI",
            "word_count": 120,
        }
        mock_publish.return_value = {
            "session_id": "session-1",
            "post_id": "urn:li:share:456",
            "visibility": "PUBLIC",
            "status_code": 201,
        }
        res = client.post(
            "/api/linkedin/publish",
            json={
                "session_id": "session-1",
                "content": "# Blog\n\ncontent",
            },
        )
        assert res.status_code == 200
        assert res.json()["composed"] is True
        assert res.json()["post_id"] == "urn:li:share:456"

    def test_publish_missing_text_and_content(self):
        res = client.post(
            "/api/linkedin/publish",
            json={
                "session_id": "session-1",
            },
        )
        assert res.status_code == 400
