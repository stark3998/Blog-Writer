"""The v1 source is read-only and never touches session or state containers; settings."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from azure.core.exceptions import ServiceRequestError
from azure.cosmos.aio import ContainerProxy, DatabaseProxy

from blogwriter.backups.migrate_v1 import source as source_module
from blogwriter.backups.migrate_v1.settings import MigrateV1Settings
from blogwriter.backups.migrate_v1.source import (
    CosmosV1Source,
    MemoryV1Source,
    V1Container,
    V1Database,
    V1SourceError,
    is_excluded_container,
    strip_system_properties,
)

ENDPOINT = "https://cosmos-v1.documents.azure.com:443/"


def _sdk_database(database: DatabaseProxy) -> V1Database:
    return database  # pyright checks the real SDK satisfies the read-only protocol


def _sdk_container(container: ContainerProxy) -> V1Container:
    return container


def test_the_sdk_satisfies_the_read_only_protocols() -> None:
    assert _sdk_database is not None
    assert _sdk_container is not None


@pytest.mark.parametrize(
    "name",
    [
        "linkedin-sessions",
        "linkedin-oauth-states",
        "twitter-sessions",
        "twitter-oauth-states",
        "medium-sessions",
        "LinkedIn-Sessions-2",
        "oauth-cache",
        "api-tokens",
        "client-secrets",
    ],
)
def test_session_and_state_containers_are_excluded(name: str) -> None:
    assert is_excluded_container(name)


@pytest.mark.parametrize(
    "name",
    ["drafts", "published-blogs", "images", "feed-sources", "keywords", "scheduled-publishes"],
)
def test_content_containers_are_not_excluded(name: str) -> None:
    assert not is_excluded_container(name)


def test_system_properties_are_stripped_but_ts_kept() -> None:
    item = {"id": "a", "_rid": "r", "_self": "s", "_etag": "e", "_attachments": "x", "_lsn": 1,
            "_ts": 123, "body": "b"}  # fmt: skip
    assert strip_system_properties(item) == {"id": "a", "_ts": 123, "body": "b"}


async def test_the_memory_source_refuses_session_containers() -> None:
    source = MemoryV1Source({"linkedin-sessions": [{"id": "s"}], "drafts": [{"id": "d"}]})
    with pytest.raises(V1SourceError):
        source.iter_items("linkedin-sessions")
    with pytest.raises(V1SourceError):
        await source.read_item("twitter-oauth-states", "x")
    assert [item async for item in source.iter_items("drafts")] == [{"id": "d"}]
    assert source.iterated == ["drafts"]


# --------------------------------------------------------------------------------------------
# CosmosV1Source over a fake database that records calls
# --------------------------------------------------------------------------------------------


class FakeContainer:
    def __init__(self, items: list[dict[str, Any]], calls: list[str], *, fail: bool = False):
        self._items = items
        self._calls = calls
        self._fail = fail

    def query_items(
        self, query: str, *, parameters: list[dict[str, object]] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        self._calls.append(f"query_items:{query}")
        items, fail = self._items, self._fail

        async def generate() -> AsyncIterator[dict[str, Any]]:
            if fail:
                raise ServiceRequestError("connection reset by v1 with secret text")
            for item in items:
                wanted = parameters[0]["value"] if parameters else None
                if wanted is None or item.get("id") == wanted:
                    yield item

        return generate()


class FakeDatabase:
    def __init__(self, containers: dict[str, list[dict[str, Any]]], *, fail: bool = False):
        self.containers = containers
        self.calls: list[str] = []
        self._fail = fail

    def get_container_client(self, container: str) -> FakeContainer:
        self.calls.append(f"get_container_client:{container}")
        return FakeContainer(self.containers.get(container, []), self.calls, fail=self._fail)

    def list_containers(self) -> AsyncIterator[dict[str, Any]]:
        self.calls.append("list_containers")
        names = list(self.containers)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            for name in names:
                yield {"id": name, "partitionKey": {"paths": ["/id"]}}

        return generate()


async def test_the_cosmos_source_only_lists_and_queries() -> None:
    database = FakeDatabase(
        {"drafts": [{"id": "b"}, {"id": "a"}], "linkedin-sessions": [{"id": "s"}]}
    )
    source = CosmosV1Source(database)
    assert await source.list_containers() == ["drafts", "linkedin-sessions"]
    assert [item["id"] async for item in source.iter_items("drafts")] == ["b", "a"]
    assert await source.read_item("drafts", "a") == {"id": "a"}
    assert await source.read_item("drafts", "missing") is None
    with pytest.raises(V1SourceError):
        source.iter_items("linkedin-sessions")
    with pytest.raises(V1SourceError):
        await source.read_item("linkedin-sessions", "s")
    called = {call.split(":", 1)[0] for call in database.calls}
    assert called == {"list_containers", "get_container_client", "query_items"}
    assert "get_container_client:linkedin-sessions" not in database.calls
    await source.close()


async def test_cosmos_errors_carry_no_sdk_text() -> None:
    source = CosmosV1Source(FakeDatabase({"drafts": [{"id": "a"}]}, fail=True))
    with pytest.raises(V1SourceError) as caught:
        async for _ in source.iter_items("drafts"):
            pass
    assert "secret" not in str(caught.value)
    with pytest.raises(V1SourceError) as caught:
        await source.read_item("drafts", "a")
    assert "secret" not in str(caught.value)


def test_the_source_module_has_no_write_calls() -> None:
    text = Path(source_module.__file__).read_text(encoding="utf-8")
    pattern = re.compile(
        r"\.(?:create_item|upsert_item|replace_item|delete_item|patch_item|execute_item_batch|"
        r"create_container|delete_container|replace_container|create_database)\s*\("
    )
    assert pattern.search(text) is None


async def test_open_needs_an_endpoint() -> None:
    with pytest.raises(V1SourceError, match="BW_MIGRATE_V1_ENDPOINT"):
        await CosmosV1Source.open(MigrateV1Settings(endpoint=None))


# --------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------


def test_settings_read_the_environment_and_hide_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_MIGRATE_V1_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("BW_MIGRATE_V1_KEY", "a-very-secret-key==")
    settings = MigrateV1Settings()
    assert settings.endpoint == ENDPOINT
    assert settings.key is not None
    assert settings.key.get_secret_value() == "a-very-secret-key=="
    assert "a-very-secret-key" not in repr(settings)
    assert "a-very-secret-key" not in str(settings)
    assert settings.database == "blog-writer"


def test_an_empty_key_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_MIGRATE_V1_KEY", "   ")
    assert MigrateV1Settings().key is None


def test_invalid_settings_do_not_echo_their_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_MIGRATE_V1_ENDPOINT", "http://insecure-v1.example/?key=leaky")
    with pytest.raises(ValueError) as caught:  # noqa: PT011 - pydantic's ValidationError
        MigrateV1Settings()
    assert "leaky" not in str(caught.value)
