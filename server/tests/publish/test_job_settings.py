"""``PublishSettings``: the environment prefix, safe defaults, validation and the secret."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.publish.jobs import schedule_entries
from blogwriter.publish.settings import PublishSettings, publish_settings
from blogwriter.publish.testing import TEST_TOKEN, make_publish_settings

ENV = (
    "BW_PUBLISH_CONTRACTS_DIR",
    "BW_PUBLISH_PORTFOLIO_REPOSITORY",
    "BW_PUBLISH_PORTFOLIO_REF",
    "BW_PUBLISH_PORTFOLIO_WORKFLOW",
    "BW_PUBLISH_PORTFOLIO_TOKEN",
    "BW_PUBLISH_SITE_ORIGIN",
    "BW_PUBLISH_PREVIEW_WORKFLOW",
    "BW_PUBLISH_BUILD_TIMEOUT_SECONDS",
    "BW_PUBLISH_VERIFY_TIMEOUT_SECONDS",
    "BW_PUBLISH_POLL_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    publish_settings.cache_clear()
    yield
    publish_settings.cache_clear()


def test_defaults_are_safe_and_not_configured() -> None:
    settings = PublishSettings()
    assert settings.portfolio_ref == "main"
    assert settings.build_timeout_seconds == 1800
    assert settings.verify_timeout_seconds == 900
    assert settings.poll_seconds == 20
    assert not settings.portfolio_configured
    assert not settings.preview_configured
    assert settings.missing_for_publish() == [
        "BW_PUBLISH_PORTFOLIO_REPOSITORY",
        "BW_PUBLISH_PORTFOLIO_WORKFLOW",
        "BW_PUBLISH_PORTFOLIO_TOKEN",
        "BW_PUBLISH_SITE_ORIGIN",
    ]
    with pytest.raises(ValueError, match="SITE_ORIGIN"):
        settings.article_url("x")


def test_reads_the_environment_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BW_PUBLISH_PORTFOLIO_REPOSITORY", "owner/site")
    monkeypatch.setenv("BW_PUBLISH_PORTFOLIO_WORKFLOW", "deploy.yml")
    monkeypatch.setenv("BW_PUBLISH_PORTFOLIO_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("BW_PUBLISH_SITE_ORIGIN", "https://Example.com/")
    monkeypatch.setenv("BW_PUBLISH_CONTRACTS_DIR", str(tmp_path))
    monkeypatch.setenv("BW_PUBLISH_POLL_SECONDS", "5")
    settings = publish_settings()
    assert settings is publish_settings()
    assert settings.portfolio_configured
    assert settings.site_origin == "https://example.com"
    assert settings.article_url("my-post") == "https://example.com/blog/my-post"
    assert settings.contracts_dir == tmp_path
    assert settings.poll_seconds == 5
    assert TEST_TOKEN not in repr(settings)
    assert TEST_TOKEN not in str(settings.model_dump())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("portfolio_repository", "not a repo"),
        ("portfolio_repository", "owner/.."),
        ("portfolio_workflow", "../deploy.yml"),
        ("portfolio_workflow", "deploy.txt"),
        ("portfolio_ref", "../main"),
        ("site_origin", "http://example.com"),
        ("site_origin", "https://example.com/blog"),
        ("portfolio_token", "short"),
        ("portfolio_token", "has a space in the middle of it"),
        ("poll_seconds", 0),
    ],
)
def test_invalid_values_are_refused(field: str, value: object) -> None:
    with pytest.raises(ValidationError) as caught:
        make_publish_settings(**{field: value})
    assert "has a space" not in str(caught.value)


def test_preview_needs_its_own_workflow() -> None:
    assert make_publish_settings().preview_configured
    assert not make_publish_settings(preview_workflow=None).preview_configured
    assert not make_publish_settings(site_origin=None).preview_configured
    assert make_publish_settings(portfolio_workflow="12345").portfolio_configured


def test_the_package_seeds_no_schedule() -> None:
    assert schedule_entries(CadenceSettings.default(SAMPLE_NOW)) == []
