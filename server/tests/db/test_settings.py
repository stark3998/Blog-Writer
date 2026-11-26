"""CosmosSettings: ``BW_COSMOS_ENDPOINT`` and ``BW_COSMOS_DATABASE`` (safe defaults, strict)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from blogwriter.db import DEFAULT_DATABASE, CosmosSettings
from blogwriter.db.settings import validate_endpoint

REAL = "https://cosmos-blogwriter-v2-7c3167.documents.azure.com:443/"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BW_COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("BW_COSMOS_DATABASE", raising=False)


def test_defaults_connect_nowhere() -> None:
    settings = CosmosSettings()
    assert settings.endpoint is None
    assert settings.database == DEFAULT_DATABASE == "blog-writer"


def test_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_COSMOS_ENDPOINT", REAL)
    monkeypatch.setenv("BW_COSMOS_DATABASE", "blog-writer-test")
    settings = CosmosSettings()
    assert settings.endpoint == REAL
    assert settings.database == "blog-writer-test"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_endpoint_means_unset(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("BW_COSMOS_ENDPOINT", blank)
    assert CosmosSettings().endpoint is None


@pytest.mark.parametrize(
    "endpoint",
    [REAL, "https://localhost:8081", "https://acct.documents.azure.com/", f"  {REAL}  "],
)
def test_valid_endpoints(endpoint: str) -> None:
    assert CosmosSettings(endpoint=endpoint).endpoint == endpoint.strip()
    assert validate_endpoint(endpoint) == endpoint.strip()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://acct.documents.azure.com/",
        "ftp://acct.documents.azure.com/",
        "https://",
        "acct.documents.azure.com",
        "https://acct.documents.azure.com/dbs/blog-writer",
        "https://acct.documents.azure.com/?key=x",
        "https://acct.documents.azure.com/#frag",
        "https://user:secret@acct.documents.azure.com/",
    ],
)
def test_invalid_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        CosmosSettings(endpoint=endpoint)


@pytest.mark.parametrize("database", ["", "a/b", "trailing "])
def test_invalid_database_names(database: str) -> None:
    with pytest.raises(ValidationError):
        CosmosSettings(database=database)


def test_settings_are_frozen() -> None:
    settings = CosmosSettings(endpoint=REAL)
    with pytest.raises(ValidationError):
        settings.endpoint = "https://other.documents.azure.com/"  # pyright: ignore[reportAttributeAccessIssue]


def test_unknown_arguments_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CosmosSettings(key="not-a-setting")  # pyright: ignore[reportCallIssue]
