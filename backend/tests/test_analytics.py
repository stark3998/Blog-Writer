"""Tests for the Analytics router — event tracking and metrics."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.user import UserInfo

client = TestClient(app)

TEST_USER = UserInfo(user_id="test-user-1", name="Test User", email="test@example.com")


class TestTrackEvent:
    @patch("backend.routers.analytics.record_post_event")
    def test_post_event_records_successfully(self, mock_record):
        res = client.post(
            "/api/analytics/event",
            json={
                "slug": "my-blog-post",
                "event_type": "page_view",
                "platform": "blog",
            },
        )
        assert res.status_code == 200
        assert res.json()["status"] == "recorded"
        mock_record.assert_called_once_with(
            slug="my-blog-post",
            event_type="page_view",
            platform="blog",
            metadata={},
        )

    @patch("backend.routers.analytics.record_post_event")
    def test_post_event_with_metadata(self, mock_record):
        res = client.post(
            "/api/analytics/event",
            json={
                "slug": "my-blog-post",
                "event_type": "share",
                "platform": "linkedin",
                "metadata": {"referrer": "twitter"},
            },
        )
        assert res.status_code == 200
        mock_record.assert_called_once_with(
            slug="my-blog-post",
            event_type="share",
            platform="linkedin",
            metadata={"referrer": "twitter"},
        )


class TestGetPostAnalytics:
    @patch("backend.routers.analytics.get_post_analytics")
    def test_get_post_stats_returns_analytics(self, mock_get):
        mock_get.return_value = {
            "slug": "my-post",
            "days": 30,
            "events": {"page_view": 150, "share": 12},
        }

        res = client.get("/api/analytics/post/my-post")
        assert res.status_code == 200
        data = res.json()
        assert data["slug"] == "my-post"
        assert data["days"] == 30
        assert data["events"]["page_view"] == 150
        mock_get.assert_called_once_with("my-post", 30)

    @patch("backend.routers.analytics.get_post_analytics")
    def test_get_post_stats_with_custom_days(self, mock_get):
        mock_get.return_value = {"slug": "my-post", "days": 7, "events": {}}

        res = client.get("/api/analytics/post/my-post?days=7")
        assert res.status_code == 200
        mock_get.assert_called_once_with("my-post", 7)


class TestAnalyticsOverview:
    @patch("backend.routers.analytics.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.analytics.get_analytics_overview")
    def test_get_overview_returns_list(self, mock_overview, mock_auth):
        mock_overview.return_value = [
            {"slug": "post-1", "events": {"page_view": 100}},
            {"slug": "post-2", "events": {"page_view": 50, "share": 5}},
        ]

        res = client.get("/api/analytics/overview")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        assert data[0]["slug"] == "post-1"
        mock_overview.assert_called_once_with(30)

    @patch("backend.routers.analytics.get_current_user", return_value=TEST_USER)
    @patch("backend.routers.analytics.get_analytics_overview")
    def test_get_overview_with_custom_days(self, mock_overview, mock_auth):
        mock_overview.return_value = []

        res = client.get("/api/analytics/overview?days=14")
        assert res.status_code == 200
        mock_overview.assert_called_once_with(14)
