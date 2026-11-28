"""Every stored document class: registry facts and round trips through the store rules."""

from __future__ import annotations

from collections import Counter

import pytest
from pydantic.alias_generators import to_camel

from blogwriter.db import CONTAINERS, MemoryStore
from blogwriter.db.base import UNINDEXED_FIELDS, prepare_write
from blogwriter.domain import DOCUMENT_TYPES, Document
from blogwriter.domain.backups import BACKUP_CONTAINERS, BACKUP_KINDS
from blogwriter.domain.base import SingletonDocument
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.settings import BrandKit
from blogwriter.domain.signals import Signal
from blogwriter.domain.testing import sample_document

IDS = [cls.__name__ for cls in DOCUMENT_TYPES]
OWNERS = {
    "kb",
    "stories",
    "voice",
    "research",
    "signals",
    "articles",
    "gates",
    "linkedin",
    "backups",
    "pipeline",
    "planner",
    "engagement",
    "analytics",
}


def test_kinds_are_unique() -> None:
    kinds = Counter(cls.KIND for cls in DOCUMENT_TYPES)
    assert [kind for kind, count in kinds.items() if count > 1] == []


def test_singleton_ids_are_unique_and_not_the_jobs_boot_log() -> None:
    ids = [cls.SINGLETON_ID for cls in DOCUMENT_TYPES if issubclass(cls, SingletonDocument)]
    assert len(ids) == len(set(ids))
    assert "jobs-boot-log" not in ids


@pytest.mark.parametrize("cls", DOCUMENT_TYPES, ids=IDS)
def test_registry_facts(cls: type[Document]) -> None:
    assert cls.CONTAINER in CONTAINERS
    assert cls.KIND
    assert cls.KIND.lower() == cls.KIND
    assert cls.OWNER in OWNERS
    assert CONTAINERS[cls.CONTAINER] == "/" + to_camel(cls.PARTITION_FIELD)
    assert cls.CONTAINER not in ("jobs", "usage", "published-blogs", "published-blogs-staging")


@pytest.mark.parametrize("cls", DOCUMENT_TYPES, ids=IDS)
def test_round_trip_through_the_store_rules(cls: type[Document]) -> None:
    doc = sample_document(cls)
    body = doc.to_document()
    prepared = prepare_write(cls.CONTAINER, body)
    assert prepared.id == doc.id
    assert prepared.partition_key == doc.partition_key
    again = cls.from_document(prepared.body)
    assert again == doc
    assert again.to_document() == body


@pytest.mark.parametrize("cls", DOCUMENT_TYPES, ids=IDS)
async def test_round_trip_through_the_memory_store(cls: type[Document]) -> None:
    store = MemoryStore()
    doc = sample_document(cls)
    created = await create_doc(store, doc)
    assert created.doc == doc
    partition = None if cls.PARTITION_FIELD == "id" else doc.partition_key
    read = await read_doc(store, cls, doc.id, partition_key=partition)
    assert read.doc == doc
    assert read.etag == created.etag


@pytest.mark.parametrize("cls", DOCUMENT_TYPES, ids=IDS)
def test_unknown_fields_survive(cls: type[Document]) -> None:
    body = {**sample_document(cls).to_document(), "fromNewerRevision": [1, "two"]}
    assert cls.from_document(body).to_document()["fromNewerRevision"] == [1, "two"]


@pytest.mark.parametrize("cls", DOCUMENT_TYPES, ids=IDS)
def test_no_field_can_hold_passages_or_private_text(cls: type[Document]) -> None:
    """Invariant: no fetched passage and no private interview text is ever stored."""
    forbidden = {"passage", "passages", "private", "raw", "transcript", "dictation", "page"}
    for name in cls.model_fields:
        assert not forbidden & set(name.split("_")), f"{cls.__name__}.{name}"


def test_large_text_fields_are_unindexed() -> None:
    """Markdown and payloads use the field names Terraform excludes from indexing."""
    for cls in DOCUMENT_TYPES:
        fields = {to_camel(name) for name in cls.model_fields}
        if "markdown" in fields:
            assert "markdown" in UNINDEXED_FIELDS[cls.CONTAINER]
        if "embedding" in fields:
            assert "embedding" in UNINDEXED_FIELDS[cls.CONTAINER]
        if "payload" in fields:
            assert "payload" in UNINDEXED_FIELDS[cls.CONTAINER]


def test_backup_containers_are_the_durable_ones() -> None:
    assert set(BACKUP_CONTAINERS) <= set(CONTAINERS)
    assert set(CONTAINERS) - set(BACKUP_CONTAINERS) == {"jobs", "ephemeral"}
    # engagement holds durable owner data next to 48-hour third-party text: kinds filter.
    durable_engagement = {cls.KIND for cls in DOCUMENT_TYPES if cls.CONTAINER == "engagement"}
    assert BACKUP_KINDS["engagement"] == {"engagement_record", "watch_entry"}
    assert BACKUP_KINDS["engagement"] <= durable_engagement
    assert set(BACKUP_KINDS) <= set(BACKUP_CONTAINERS)


def test_document_ids_are_unique_across_partitions() -> None:
    """Keyset pages on ``id`` (backups) need ids unique per container, not per partition."""
    url = "a" * 64
    assert Signal.id_for("feed-1", url) != Signal.id_for("feed-2", url)
    assert Signal.id_for("feed-1", url) == Signal.id_for("feed-1", url)


def test_sample_overrides_are_validated() -> None:
    with pytest.raises(ValueError, match="id must be"):
        sample_document(DOCUMENT_TYPES[0], id="not-the-singleton-id")


def test_sample_overrides_accept_either_spelling_on_singletons() -> None:
    by_name = sample_document(BrandKit, author_name="Name")
    by_alias = sample_document(BrandKit, authorName="Alias")
    assert (by_name.author_name, by_alias.author_name) == ("Name", "Alias")
    assert by_name.model_extra == {}
    assert by_alias.model_extra == {}
    assert "author_name" not in by_alias.to_document()
