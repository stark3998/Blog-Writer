"""Tests for the Schedule router — create, list, cancel scheduled publishes."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.user import UserInfo

client = TestClient(app)

TEST_USER = UserInfo(user_id="test-user-1", name="Test User", email="test@example.com")

# A future datetime for tests
FUTURE_DT = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
PAST_DT = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

SAMPLE_SCHEDULE = {
    "id": "sched-1",
    "draftId": "draft-1",
    "scheduledAt": FUTURE_DT,
    "platforms": ["blog", "linkedin"],
    "status": "pending",
    "createdAt": "2024-06-01T10:00:00Z",
    "completedAt": "",
    "error": "",
}


class TestCreateSchedule:
    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.create_scheduled_publish")
    @patch("backend.routers.schedule.get_draft")
    def test_create_schedule_success(self, mock_get_draft, mock_create, mock_auth):
        mock_get_draft.return_value = {"id": "draft-1", "title": "Test Draft"}
        mock_create.return_value = SAMPLE_SCHEDULE

        res = client.post(
            "/api/schedule",
            json={
                "draft_id": "draft-1",
                "scheduled_at": FUTURE_DT,
                "platforms": ["blog", "linkedin"],
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["id"] == "sched-1"
        assert data["status"] == "pending"
        assert "blog" in data["platforms"]

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.get_draft")
    def test_create_schedule_draft_not_found(self, mock_get_draft, mock_auth):
        mock_get_draft.return_value = None

        res = client.post(
            "/api/schedule",
            json={
                "draft_id": "nonexistent",
                "scheduled_at": FUTURE_DT,
                "platforms": ["blog"],
            },
        )
        assert res.status_code == 404
        assert "Draft not found" in res.json()["detail"]

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.get_draft")
    def test_create_schedule_past_datetime(self, mock_get_draft, mock_auth):
        mock_get_draft.return_value = {"id": "draft-1", "title": "Test Draft"}

        res = client.post(
            "/api/schedule",
            json={
                "draft_id": "draft-1",
                "scheduled_at": PAST_DT,
                "platforms": ["blog"],
            },
        )
        assert res.status_code == 400
        assert "future" in res.json()["detail"].lower()

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    def test_create_schedule_invalid_platform(self, mock_auth):
        res = client.post(
            "/api/schedule",
            json={
                "draft_id": "draft-1",
                "scheduled_at": FUTURE_DT,
                "platforms": ["invalid_platform"],
            },
        )
        assert res.status_code == 400
        assert "Invalid platforms" in res.json()["detail"]

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    def test_create_schedule_empty_platforms(self, mock_auth):
        res = client.post(
            "/api/schedule",
            json={
                "draft_id": "draft-1",
                "scheduled_at": FUTURE_DT,
                "platforms": [],
            },
        )
        assert res.status_code == 400
        assert "At least one platform" in res.json()["detail"]

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.get_draft")
    def test_create_schedule_invalid_datetime(self, mock_get_draft, mock_auth):
        mock_get_draft.return_value = {"id": "draft-1", "title": "Test Draft"}

        res = client.post(
            "/api/schedule",
            json={
                "draft_id": "draft-1",
                "scheduled_at": "not-a-datetime",
                "platforms": ["blog"],
            },
        )
        assert res.status_code == 400


class TestListSchedules:
    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.list_scheduled_publishes")
    def test_list_schedules_returns_list(self, mock_list, mock_auth):
        mock_list.return_value = [SAMPLE_SCHEDULE]

        res = client.get("/api/schedule")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["id"] == "sched-1"
        mock_list.assert_called_once_with(status=None, limit=50)

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.list_scheduled_publishes")
    def test_list_schedules_filter_by_status(self, mock_list, mock_auth):
        mock_list.return_value = []

        res = client.get("/api/schedule?status=pending")
        assert res.status_code == 200
        mock_list.assert_called_once_with(status="pending", limit=50)


class TestCancelSchedule:
    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.cancel_scheduled_publish")
    @patch("backend.routers.schedule.get_scheduled_publish")
    def test_cancel_pending_schedule(self, mock_get, mock_cancel, mock_auth):
        mock_get.return_value = {**SAMPLE_SCHEDULE, "status": "pending"}
        cancelled = {**SAMPLE_SCHEDULE, "status": "cancelled"}
        mock_cancel.return_value = cancelled

        res = client.delete("/api/schedule/sched-1")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"
        mock_cancel.assert_called_once_with("sched-1")

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.get_scheduled_publish")
    def test_cancel_not_found(self, mock_get, mock_auth):
        mock_get.return_value = None

        res = client.delete("/api/schedule/nonexistent")
        assert res.status_code == 404

    @patch("backend.routers.schedule.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.schedule.get_scheduled_publish")
    def test_cancel_already_completed(self, mock_get, mock_auth):
        mock_get.return_value = {**SAMPLE_SCHEDULE, "status": "completed"}

        res = client.delete("/api/schedule/sched-1")
        assert res.status_code == 400
        assert "Cannot cancel" in res.json()["detail"]
