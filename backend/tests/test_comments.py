"""Tests for the Comments router — CRUD operations for draft comments."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.user import UserInfo

client = TestClient(app)

TEST_USER = UserInfo(user_id="test-user-1", name="Test User", email="test@example.com")

SAMPLE_COMMENT = {
    "id": "comment-1",
    "draftId": "draft-1",
    "userId": "test-user-1",
    "userName": "Test User",
    "content": "Great paragraph!",
    "lineNumber": 5,
    "parentId": "",
    "resolved": False,
    "createdAt": "2024-06-01T10:00:00Z",
    "updatedAt": "2024-06-01T10:00:00Z",
}


class TestCreateComment:
    @patch("backend.routers.comments.create_comment")
    def test_create_comment_success(self, mock_create):
        mock_create.return_value = SAMPLE_COMMENT

        res = client.post(
            "/api/comments/",
            json={
                "draft_id": "draft-1",
                "content": "Great paragraph!",
                "line_number": 5,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "comment-1"
        assert data["content"] == "Great paragraph!"
        assert data["lineNumber"] == 5
        # In local dev mode (ENTRA_CLIENT_ID=""), auth returns local-dev user
        mock_create.assert_called_once_with(
            draft_id="draft-1",
            user_id="local-dev",
            user_name="Local Developer",
            content="Great paragraph!",
            line_number=5,
            parent_id=None,
        )

    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.create_comment")
    def test_create_reply_comment(self, mock_create, mock_auth):
        reply = {
            **SAMPLE_COMMENT,
            "id": "comment-2",
            "parentId": "comment-1",
            "content": "Thanks!",
        }
        mock_create.return_value = reply

        res = client.post(
            "/api/comments/",
            json={
                "draft_id": "draft-1",
                "content": "Thanks!",
                "parent_id": "comment-1",
            },
        )
        assert res.status_code == 200
        assert res.json()["parentId"] == "comment-1"


class TestListComments:
    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.list_comments")
    def test_list_comments_for_draft(self, mock_list, mock_auth):
        mock_list.return_value = [SAMPLE_COMMENT]

        res = client.get("/api/comments/draft-1")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["draftId"] == "draft-1"
        mock_list.assert_called_once_with("draft-1")

    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.list_comments")
    def test_list_comments_empty(self, mock_list, mock_auth):
        mock_list.return_value = []

        res = client.get("/api/comments/draft-no-comments")
        assert res.status_code == 200
        assert res.json() == []


class TestUpdateComment:
    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.update_comment")
    def test_update_comment_content(self, mock_update, mock_auth):
        updated = {**SAMPLE_COMMENT, "content": "Updated content"}
        mock_update.return_value = updated

        res = client.put(
            "/api/comments/comment-1",
            json={"content": "Updated content"},
        )
        assert res.status_code == 200
        assert res.json()["content"] == "Updated content"
        mock_update.assert_called_once_with("comment-1", {"content": "Updated content"})

    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.update_comment")
    def test_update_comment_resolve(self, mock_update, mock_auth):
        updated = {**SAMPLE_COMMENT, "resolved": True}
        mock_update.return_value = updated

        res = client.put(
            "/api/comments/comment-1",
            json={"resolved": True},
        )
        assert res.status_code == 200
        assert res.json()["resolved"] is True

    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.update_comment")
    def test_update_comment_not_found(self, mock_update, mock_auth):
        mock_update.return_value = None

        res = client.put(
            "/api/comments/nonexistent",
            json={"content": "new"},
        )
        assert res.status_code == 404

    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    def test_update_comment_no_updates(self, mock_auth):
        res = client.put(
            "/api/comments/comment-1",
            json={},
        )
        assert res.status_code == 400
        assert "No updates" in res.json()["detail"]


class TestDeleteComment:
    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.delete_comment")
    def test_delete_comment_success(self, mock_delete, mock_auth):
        mock_delete.return_value = True

        res = client.delete("/api/comments/comment-1")
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"
        assert res.json()["id"] == "comment-1"

    @patch("backend.routers.comments.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.comments.delete_comment")
    def test_delete_comment_not_found(self, mock_delete, mock_auth):
        mock_delete.return_value = False

        res = client.delete("/api/comments/nonexistent")
        assert res.status_code == 404
