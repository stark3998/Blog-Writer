"""CosmosStore without the network: SDK calls, error mapping, the container check and lifecycle.

Fakes implement the CosmosDatabase / CosmosContainer protocols (the real SDK proxies satisfy them
structurally; pyright checks that in cosmos.py). ``open()`` is exercised with the real
``CosmosClient`` class whose connect and close are patched out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, ClassVar

import pytest
from azure.core import MatchConditions
from azure.core.credentials import AccessToken
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import (
    CosmosClientTimeoutError,
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from blogwriter.db import (
    CONTAINERS,
    Conflict,
    ContainerCheckFailed,
    CosmosSettings,
    CosmosStore,
    Filter,
    InvalidRequest,
    NotFound,
    OrderBy,
    PreconditionFailed,
    Store,
    StoreError,
    StoreUnavailable,
)
from blogwriter.db import cosmos as cosmos_module
from blogwriter.db.cosmos import TRANSIENT_STATUSES, translate_error

ENDPOINT = "https://cosmos-unit-test.documents.azure.com:443/"
SYSTEM = {"_rid": "rid==", "_self": "dbs/x/colls/y/docs/z/", "_attachments": "attachments/"}


def http_error(status: int) -> CosmosHttpResponseError:
    return CosmosHttpResponseError(status_code=status, message="sdk detail: do-not-copy")


class FakeContainer:
    """Stores items like Cosmos (system properties, fresh _etag) and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.error: AzureError | None = None
        self.query_results: list[dict[str, Any]] = []
        self.version = 0

    def _enter(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        if self.error is not None:
            raise self.error

    def _stored(self, body: dict[str, Any]) -> dict[str, Any]:
        self.version += 1
        return {**body, **SYSTEM, "_etag": f'"v{self.version}"', "_ts": 1764150000}

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        self._enter("read_item", item=item, partition_key=partition_key)
        return self.items[(partition_key, item)]

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self._enter("create_item", body=body)
        stored = self._stored(body)
        self.items[(body["id"], body["id"])] = stored
        return stored

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
    ) -> dict[str, Any]:
        self._enter(
            "replace_item", item=item, body=body, etag=etag, match_condition=match_condition
        )
        return self._stored(body)

    async def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self._enter("upsert_item", body=body)
        return self._stored(body)

    async def delete_item(
        self,
        item: str,
        partition_key: str,
        *,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
    ) -> None:
        self._enter(
            "delete_item",
            item=item,
            partition_key=partition_key,
            etag=etag,
            match_condition=match_condition,
        )

    def query_items(
        self,
        query: str,
        *,
        parameters: list[dict[str, object]] | None = None,
        partition_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(
            (
                "query_items",
                {"query": query, "parameters": parameters, "partition_key": partition_key},
            )
        )
        return self._pages()

    async def _pages(self) -> AsyncIterator[dict[str, Any]]:
        for item in self.query_results:
            yield item
        if self.error is not None:
            raise self.error  # failures can surface mid-iteration


def listing(overrides: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    paths = {name: [path] for name, path in CONTAINERS.items()} | (overrides or {})
    return [
        {"id": name, "partitionKey": {"paths": pk, "kind": "Hash", "version": 2}, **SYSTEM}
        for name, pk in paths.items()
    ]


class FakeDatabase:
    def __init__(self, containers: list[dict[str, Any]] | None = None) -> None:
        self.clients: dict[str, FakeContainer] = {}
        self.lookups: list[str] = []
        self.containers = listing() if containers is None else containers
        self.list_error: AzureError | None = None

    def get_container_client(self, container: str) -> FakeContainer:
        self.lookups.append(container)
        return self.clients.setdefault(container, FakeContainer())

    def list_containers(self) -> AsyncIterator[dict[str, Any]]:
        return self._list()

    async def _list(self) -> AsyncIterator[dict[str, Any]]:
        if self.list_error is not None:
            raise self.list_error
        for container in self.containers:
            yield container


class Closer:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


def make_store(database: FakeDatabase | None = None) -> tuple[CosmosStore, FakeDatabase, Closer]:
    db = database or FakeDatabase()
    closer = Closer()
    return CosmosStore(db, database_name="blog-writer", on_close=closer), db, closer


# --------------------------------------------------------------------------------------------
# SDK calls
# --------------------------------------------------------------------------------------------


def test_cosmos_store_satisfies_the_protocol() -> None:
    store, _, _ = make_store()
    typed: Store = store
    assert isinstance(typed, Store)


async def test_create_sends_the_normalised_body_and_strips_system_properties() -> None:
    store, db, _ = make_store()
    doc = await store.create("jobs", {"id": "j1", "pair": (1, 2), "nested": {"k": "v"}})
    assert db.clients["jobs"].calls == [
        ("create_item", {"body": {"id": "j1", "pair": [1, 2], "nested": {"k": "v"}}})
    ]
    assert doc.id == "j1"
    assert doc.partition_key == "j1"
    assert doc.etag == '"v1"'
    assert doc.body == {"id": "j1", "pair": [1, 2], "nested": {"k": "v"}}


async def test_read_passes_id_and_partition_key() -> None:
    store, db, _ = make_store()
    container = db.get_container_client("article-versions")
    container.items[("a1", "v1")] = {"id": "v1", "articleId": "a1", **SYSTEM, "_etag": '"e"'}
    doc = await store.read("article-versions", "v1", partition_key="a1")
    assert container.calls == [("read_item", {"item": "v1", "partition_key": "a1"})]
    assert (doc.id, doc.partition_key, doc.etag) == ("v1", "a1", '"e"')
    assert doc.body == {"id": "v1", "articleId": "a1"}


async def test_replace_sends_if_match_only_when_given() -> None:
    store, db, _ = make_store()
    await store.replace("jobs", {"id": "j1", "n": 1})
    await store.replace("jobs", {"id": "j1", "n": 2}, if_match='"v1"')
    assert db.clients["jobs"].calls == [
        (
            "replace_item",
            {"item": "j1", "body": {"id": "j1", "n": 1}, "etag": None, "match_condition": None},
        ),
        (
            "replace_item",
            {
                "item": "j1",
                "body": {"id": "j1", "n": 2},
                "etag": '"v1"',
                "match_condition": MatchConditions.IfNotModified,
            },
        ),
    ]


async def test_upsert_sends_the_body() -> None:
    store, db, _ = make_store()
    doc = await store.upsert("signals", {"id": "s1", "sourceId": "feed"})
    assert db.clients["signals"].calls == [
        ("upsert_item", {"body": {"id": "s1", "sourceId": "feed"}})
    ]
    assert doc.partition_key == "feed"


async def test_delete_sends_partition_key_and_if_match() -> None:
    store, db, _ = make_store()
    await store.delete("jobs", "j1")
    await store.delete("usage", "u1", partition_key="2026-11", if_match='"v9"')
    assert db.clients["jobs"].calls == [
        (
            "delete_item",
            {"item": "j1", "partition_key": "j1", "etag": None, "match_condition": None},
        )
    ]
    assert db.clients["usage"].calls == [
        (
            "delete_item",
            {
                "item": "u1",
                "partition_key": "2026-11",
                "etag": '"v9"',
                "match_condition": MatchConditions.IfNotModified,
            },
        )
    ]


async def test_find_sends_parameterized_sql_and_maps_items() -> None:
    store, db, _ = make_store()
    container = db.get_container_client("jobs")
    container.query_results = [{"id": "j2", "state": "queued", **SYSTEM, "_etag": '"e2"'}]
    docs = await store.find(
        "jobs", [Filter("state", "==", "queued")], order_by=OrderBy("at"), limit=5
    )
    assert container.calls == [
        (
            "query_items",
            {
                "query": 'SELECT TOP @limit * FROM c WHERE c["state"] = @p0 '
                'AND IS_PRIMITIVE(c["at"]) ORDER BY c["at"] ASC',
                "parameters": [
                    {"name": "@p0", "value": "queued"},
                    {"name": "@limit", "value": 5},
                ],
                "partition_key": None,
            },
        )
    ]
    assert [(doc.id, doc.etag, doc.body) for doc in docs] == [
        ("j2", '"e2"', {"id": "j2", "state": "queued"})
    ]


async def test_find_with_a_projection_selects_and_maps_only_those_fields() -> None:
    store, db, _ = make_store()
    container = db.get_container_client("article-versions")
    container.query_results = [
        {"id": "a1-v00002", "_etag": '"e2"', "articleId": "a1", "version": 2},
        {"id": "a1-v00001", "_etag": '"e1"', "articleId": "a1", "version": 1, "note": None},
    ]
    docs = await store.find(
        "article-versions",
        [Filter("articleId", "==", "a1")],
        order_by=OrderBy("version", descending=True),
        partition_key="a1",
        fields=["version", "note"],
    )
    assert container.calls == [
        (
            "query_items",
            {
                "query": 'SELECT c["id"], c["_etag"], c["articleId"], c["version"], c["note"] '
                'FROM c WHERE c["articleId"] = @p0 AND IS_PRIMITIVE(c["version"]) '
                'ORDER BY c["version"] DESC',
                "parameters": [{"name": "@p0", "value": "a1"}],
                "partition_key": "a1",
            },
        )
    ]
    assert [(doc.id, doc.partition_key, doc.etag, doc.body) for doc in docs] == [
        ("a1-v00002", "a1", '"e2"', {"id": "a1-v00002", "articleId": "a1", "version": 2}),
        (
            "a1-v00001",
            "a1",
            '"e1"',
            {"id": "a1-v00001", "articleId": "a1", "version": 1, "note": None},
        ),
    ]


async def test_find_passes_the_partition_key_when_scoped() -> None:
    store, db, _ = make_store()
    await store.find("article-versions", partition_key="a1")
    (call,) = db.clients["article-versions"].calls
    assert call == (
        "query_items",
        {"query": "SELECT * FROM c", "parameters": [], "partition_key": "a1"},
    )


async def test_container_clients_are_cached() -> None:
    store, db, _ = make_store()
    await store.upsert("jobs", {"id": "j1"})
    await store.upsert("jobs", {"id": "j2"})
    await store.find("jobs")
    assert db.lookups == ["jobs"]


async def test_validation_happens_before_any_sdk_call() -> None:
    store, db, _ = make_store()
    with pytest.raises(InvalidRequest):
        await store.create("jobs", {"id": "a/b"})
    with pytest.raises(InvalidRequest):
        await store.replace("jobs", {"id": "j1"}, if_match="")
    with pytest.raises(InvalidRequest):
        await store.read("article-versions", "v1")
    with pytest.raises(InvalidRequest):
        await store.delete("nope", "x")
    with pytest.raises(InvalidRequest):
        await store.find("jobs", limit=0)
    with pytest.raises(InvalidRequest):
        await store.find("jobs", [Filter("events", "exists", True)])
    with pytest.raises(InvalidRequest):
        await store.find("jobs", fields=["lease.until"])
    with pytest.raises(InvalidRequest):
        await store.find("jobs", fields=["_etag"])
    assert db.lookups == []


async def test_malformed_items_from_cosmos_raise_store_error() -> None:
    store, db, _ = make_store()
    db.get_container_client("jobs").query_results = [{"id": "j1"}]  # no _etag
    with pytest.raises(StoreError):
        await store.find("jobs")


# --------------------------------------------------------------------------------------------
# Error mapping
# --------------------------------------------------------------------------------------------


async def run(store: CosmosStore, op: str) -> object:
    if op == "read":
        return await store.read("jobs", "j1")
    if op == "create":
        return await store.create("jobs", {"id": "j1", "draft": "secret draft text"})
    if op == "replace":
        return await store.replace(
            "jobs", {"id": "j1", "draft": "secret draft text"}, if_match='"e"'
        )
    if op == "upsert":
        return await store.upsert("jobs", {"id": "j1", "draft": "secret draft text"})
    if op == "delete":
        return await store.delete("jobs", "j1", if_match='"e"')
    return await store.find("jobs", [Filter("draft", "==", "secret draft text")])


OPS = ["read", "create", "replace", "upsert", "delete", "find"]


@pytest.mark.parametrize(
    ("status", "op", "expected"),
    [
        (404, "read", NotFound),
        (404, "replace", NotFound),
        (404, "delete", NotFound),
        (404, "create", StoreError),
        (404, "upsert", StoreError),
        (404, "find", StoreError),
        (409, "create", Conflict),
        (409, "upsert", Conflict),
        (412, "replace", PreconditionFailed),
        (412, "delete", PreconditionFailed),
        (400, "find", StoreError),
        (401, "read", StoreError),
        (403, "create", StoreError),
        (413, "upsert", StoreError),
        *[(status, "read", StoreUnavailable) for status in sorted(TRANSIENT_STATUSES)],
        (429, "find", StoreUnavailable),
        (503, "create", StoreUnavailable),
    ],
)
async def test_http_errors_map_to_store_errors(
    status: int, op: str, expected: type[StoreError]
) -> None:
    store, db, _ = make_store()
    db.get_container_client("jobs").error = http_error(status)
    with pytest.raises(StoreError) as caught:
        await run(store, op)
    assert type(caught.value) is expected
    assert caught.value.status == status
    assert isinstance(caught.value.__cause__, CosmosHttpResponseError)


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ServiceRequestError("connection refused"), StoreUnavailable),
        (ServiceResponseError("connection reset"), StoreUnavailable),
        (CosmosClientTimeoutError(), StoreUnavailable),
        (ClientAuthenticationError("no token"), StoreError),
    ],
    ids=["request", "response", "timeout", "auth"],
)
async def test_transport_and_auth_errors(
    op: str, error: AzureError, expected: type[StoreError]
) -> None:
    store, db, _ = make_store()
    db.get_container_client("jobs").error = error
    with pytest.raises(StoreError) as caught:
        await run(store, op)
    assert type(caught.value) is expected
    assert caught.value.status is None


@pytest.mark.parametrize("op", OPS)
async def test_error_messages_carry_no_content(op: str) -> None:
    store, db, _ = make_store()
    db.get_container_client("jobs").error = http_error(500)
    with pytest.raises(StoreError) as caught:
        await run(store, op)
    message = str(caught.value)
    assert "secret" not in message
    assert "do-not-copy" not in message
    assert "jobs" in message
    assert "500" in message


async def test_query_errors_raised_mid_iteration_are_mapped() -> None:
    store, db, _ = make_store()
    container = db.get_container_client("jobs")
    container.query_results = [{"id": "j1", **SYSTEM, "_etag": '"e"'}]
    container.error = http_error(429)
    with pytest.raises(StoreUnavailable):
        await store.find("jobs")


def test_status_falls_back_to_unavailable_without_a_status() -> None:
    error = translate_error(CosmosHttpResponseError(message="no status"), action="read jobs/x")
    assert type(error) is StoreUnavailable


# --------------------------------------------------------------------------------------------
# ensure_containers
# --------------------------------------------------------------------------------------------


async def test_ensure_containers_accepts_the_terraform_layout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = FakeDatabase([*listing(), {"id": "extra", "partitionKey": {"paths": ["/x"]}}])
    store, _, _ = make_store(database)
    caplog.set_level("INFO", logger="blogwriter.db.cosmos")
    await store.ensure_containers()
    assert "cosmos containers verified" in caplog.text


async def test_ensure_containers_reports_missing_containers() -> None:
    containers = [c for c in listing() if c["id"] not in {"usage", "jobs"}]
    store, _, _ = make_store(FakeDatabase(containers))
    with pytest.raises(ContainerCheckFailed) as caught:
        await store.ensure_containers()
    assert caught.value.missing == ("jobs", "usage")
    assert caught.value.mismatched == {}
    assert "Terraform" in str(caught.value)


async def test_ensure_containers_reports_partition_key_mismatches() -> None:
    containers = listing({"article-versions": ["/id"], "signals": [], "jobs": ["/id", "/x"]})
    containers.append({"id": "nopk"})
    store, _, _ = make_store(FakeDatabase(containers))
    with pytest.raises(ContainerCheckFailed) as caught:
        await store.ensure_containers()
    assert caught.value.missing == ()
    assert caught.value.mismatched == {
        "article-versions": "/id",
        "jobs": "/id,/x",
        "signals": "(none)",
    }


async def test_ensure_containers_without_the_database() -> None:
    database = FakeDatabase()
    database.list_error = CosmosResourceNotFoundError(status_code=404, message="no db")
    store, _, _ = make_store(database)
    with pytest.raises(ContainerCheckFailed) as caught:
        await store.ensure_containers()
    assert caught.value.missing == tuple(sorted(CONTAINERS))


@pytest.mark.parametrize(
    ("error", "expected"),
    [(http_error(403), StoreError), (ServiceRequestError("down"), StoreUnavailable)],
)
async def test_ensure_containers_other_failures(
    error: AzureError, expected: type[StoreError]
) -> None:
    database = FakeDatabase()
    database.list_error = error
    store, _, _ = make_store(database)
    with pytest.raises(StoreError) as caught:
        await store.ensure_containers()
    assert type(caught.value) is expected


# --------------------------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------------------------


async def test_close_is_idempotent_and_blocks_further_use() -> None:
    store, db, closer = make_store()
    await store.close()
    await store.close()
    assert closer.count == 1
    with pytest.raises(StoreError, match="closed"):
        await store.read("jobs", "j1")
    with pytest.raises(StoreError, match="closed"):
        await store.ensure_containers()
    assert db.lookups == []


class FakeCredential:
    instances: ClassVar[list[FakeCredential]] = []

    def __init__(self) -> None:
        self.closed = 0
        FakeCredential.instances.append(self)

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: Any,
    ) -> AccessToken:
        raise AssertionError("unit tests never request tokens")

    async def close(self) -> None:
        self.closed += 1

    async def __aenter__(self) -> FakeCredential:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()


class ClientSpy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, enter_error: BaseException | None) -> None:
        self.entered: list[str] = []
        self.closed: list[str] = []
        spy = self

        async def enter(client: CosmosClient) -> CosmosClient:
            spy.entered.append(client.client_connection.url_connection)
            if enter_error is not None:
                raise enter_error
            return client

        async def close(client: CosmosClient) -> None:
            spy.closed.append(client.client_connection.url_connection)

        monkeypatch.setattr(CosmosClient, "__aenter__", enter)
        monkeypatch.setattr(CosmosClient, "close", close)


@pytest.fixture
def no_default_credential(monkeypatch: pytest.MonkeyPatch) -> list[FakeCredential]:
    FakeCredential.instances = []
    monkeypatch.setattr(cosmos_module, "DefaultAzureCredential", FakeCredential)
    return FakeCredential.instances


async def test_open_owns_and_closes_the_default_credential(
    monkeypatch: pytest.MonkeyPatch, no_default_credential: list[FakeCredential]
) -> None:
    spy = ClientSpy(monkeypatch, enter_error=None)
    store = await CosmosStore.open(ENDPOINT)
    assert spy.entered == [ENDPOINT]
    (credential,) = no_default_credential
    await store.close()
    await store.close()
    assert spy.closed == [ENDPOINT]
    assert credential.closed == 1


async def test_open_leaves_an_injected_credential_open(
    monkeypatch: pytest.MonkeyPatch, no_default_credential: list[FakeCredential]
) -> None:
    spy = ClientSpy(monkeypatch, enter_error=None)
    injected = FakeCredential()
    store = await CosmosStore.open(ENDPOINT, database="blog-writer", credential=injected)
    await store.close()
    assert spy.closed == [ENDPOINT]
    assert injected.closed == 0
    assert no_default_credential == [injected]  # no DefaultAzureCredential was created


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ServiceRequestError("dns"), StoreUnavailable),
        (ClientAuthenticationError("az login needed"), StoreError),
        (http_error(403), StoreError),
    ],
)
async def test_open_failure_cleans_up_and_maps_the_error(
    monkeypatch: pytest.MonkeyPatch,
    no_default_credential: list[FakeCredential],
    error: AzureError,
    expected: type[StoreError],
) -> None:
    spy = ClientSpy(monkeypatch, enter_error=error)
    with pytest.raises(StoreError) as caught:
        await CosmosStore.open(ENDPOINT)
    assert type(caught.value) is expected
    assert spy.closed == [ENDPOINT]
    assert [credential.closed for credential in no_default_credential] == [1]


async def test_open_reraises_non_azure_errors_after_cleanup(
    monkeypatch: pytest.MonkeyPatch, no_default_credential: list[FakeCredential]
) -> None:
    spy = ClientSpy(monkeypatch, enter_error=RuntimeError("boom"))
    injected = FakeCredential()
    with pytest.raises(RuntimeError, match="boom"):
        await CosmosStore.open(ENDPOINT, credential=injected)
    assert spy.closed == [ENDPOINT]
    assert injected.closed == 0


@pytest.mark.parametrize(
    ("endpoint", "database"),
    [
        ("http://insecure.documents.azure.com/", "blog-writer"),
        ("https://acct.documents.azure.com/dbs/x", "blog-writer"),
        (ENDPOINT, "bad/name"),
        (ENDPOINT, ""),
    ],
)
async def test_open_rejects_bad_arguments_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    no_default_credential: list[FakeCredential],
    endpoint: str,
    database: str,
) -> None:
    spy = ClientSpy(monkeypatch, enter_error=None)
    with pytest.raises(InvalidRequest):
        await CosmosStore.open(endpoint, database=database)
    assert spy.entered == []
    assert no_default_credential == []


async def test_from_settings_requires_an_endpoint(
    monkeypatch: pytest.MonkeyPatch, no_default_credential: list[FakeCredential]
) -> None:
    monkeypatch.delenv("BW_COSMOS_ENDPOINT", raising=False)
    with pytest.raises(StoreError, match="BW_COSMOS_ENDPOINT"):
        await CosmosStore.from_settings()
    assert no_default_credential == []


async def test_from_settings_connects_to_the_configured_account(
    monkeypatch: pytest.MonkeyPatch, no_default_credential: list[FakeCredential]
) -> None:
    spy = ClientSpy(monkeypatch, enter_error=None)
    store = await CosmosStore.from_settings(CosmosSettings(endpoint=ENDPOINT, database="other"))
    await store.close()
    assert spy.entered == [ENDPOINT]
    assert len(no_default_credential) == 1
