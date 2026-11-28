"""The Store contract: every test runs on MemoryStore, and on Cosmos with ``BW_LIVE_COSMOS=1``.

Plan: "Data" (containers, partition keys, TTL) and "Reliability model" (claims are CAS writes:
replace with If-Match; a 412 means the claim is lost). Live runs write only ``test-`` ids in
``jobs`` and ``ephemeral`` and clean up after themselves (see conftest.py).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
from collections.abc import Sequence
from typing import Any

import pytest

from blogwriter.db import (
    Conflict,
    Doc,
    Filter,
    InvalidRequest,
    NotFound,
    OrderBy,
    PreconditionFailed,
    Store,
)
from blogwriter.db.base import MAX_DOCUMENT_BYTES, SYSTEM_PROPERTIES

JOBS = "jobs"
EPHEMERAL = "ephemeral"


def body(token: str, name: str, **fields: Any) -> dict[str, Any]:
    """A test document: ``test-`` id, this test's ``suite`` marker and a 1 h TTL."""
    return {"id": f"{token}-{name}", "suite": token, "ttl": 3600, **fields}


def names(token: str, docs: Sequence[Doc]) -> list[str]:
    return [doc.id.removeprefix(f"{token}-") for doc in docs]


async def find_names(
    store: Store,
    token: str,
    *filters: Filter,
    order_by: OrderBy | None = None,
    limit: int | None = None,
) -> list[str]:
    docs = await store.find(
        JOBS, [Filter("suite", "==", token), *filters], order_by=order_by, limit=limit
    )
    return names(token, docs)


# --------------------------------------------------------------------------------------------
# Point operations
# --------------------------------------------------------------------------------------------


async def test_create_then_read_round_trips_the_body(store: Store, token: str) -> None:
    original = body(
        token,
        "a",
        title="Caf\N{LATIN SMALL LETTER E WITH ACUTE} \N{ROCKET} \N{CJK UNIFIED IDEOGRAPH-65E5}",
        count=3,
        ratio=0.1,
        big=2**53 - 1,
        negative=-7,
        flag=False,
        nothing=None,
        tags=["a", "b"],
        nested={"lease": {"until": "2026-11-26T10:00:00Z", "fence": 4}, "empty": {}},
    )
    created = await store.create(JOBS, original)
    assert created.id == original["id"]
    assert created.partition_key == original["id"]
    assert created.etag
    assert created.body == original

    read = await store.read(JOBS, original["id"])
    assert read == created


async def test_bodies_never_contain_system_properties(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a"))
    read = await store.read(JOBS, created.id)
    (found,) = await store.find(JOBS, [Filter("suite", "==", token)])
    for doc in (created, read, found):
        assert not SYSTEM_PROPERTIES & doc.body.keys()


async def test_create_existing_id_raises_conflict(store: Store, token: str) -> None:
    await store.create(JOBS, body(token, "a", n=1))
    with pytest.raises(Conflict) as caught:
        await store.create(JOBS, body(token, "a", n=2))
    assert caught.value.status == 409
    assert (await store.read(JOBS, f"{token}-a")).body["n"] == 1


async def test_read_missing_raises_not_found(store: Store, token: str) -> None:
    with pytest.raises(NotFound) as caught:
        await store.read(JOBS, f"{token}-missing")
    assert caught.value.status == 404


async def test_read_with_another_partition_key_raises_not_found(store: Store, token: str) -> None:
    await store.create(JOBS, body(token, "a"))
    with pytest.raises(NotFound):
        await store.read(JOBS, f"{token}-a", partition_key=f"{token}-other")
    assert (await store.read(JOBS, f"{token}-a", partition_key=f"{token}-a")).id == f"{token}-a"


async def test_replace_with_current_etag_writes_and_changes_etag(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a", n=1))
    replaced = await store.replace(JOBS, {**created.body, "n": 2}, if_match=created.etag)
    assert replaced.etag != created.etag
    assert replaced.body["n"] == 2
    assert await store.read(JOBS, created.id) == replaced


async def test_replace_with_stale_etag_raises_and_keeps_the_document(
    store: Store, token: str
) -> None:
    created = await store.create(JOBS, body(token, "a", n=1))
    current = await store.replace(JOBS, {**created.body, "n": 2}, if_match=created.etag)
    with pytest.raises(PreconditionFailed) as caught:
        await store.replace(JOBS, {**created.body, "n": 3}, if_match=created.etag)
    assert caught.value.status == 412
    assert await store.read(JOBS, created.id) == current


async def test_replace_without_if_match_overwrites(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a", n=1))
    replaced = await store.replace(JOBS, body(token, "a", n=5))
    assert replaced.body["n"] == 5
    assert replaced.etag != created.etag


async def test_replace_missing_raises_not_found(store: Store, token: str) -> None:
    with pytest.raises(NotFound):
        await store.replace(JOBS, body(token, "missing"))
    with pytest.raises(NotFound):
        await store.replace(JOBS, body(token, "missing"), if_match='"some-etag"')


async def test_upsert_creates_then_overwrites(store: Store, token: str) -> None:
    first = await store.upsert(JOBS, body(token, "a", n=1))
    second = await store.upsert(JOBS, body(token, "a", n=2))
    assert first.body["n"] == 1
    assert second.body["n"] == 2
    assert second.etag != first.etag
    assert await store.read(JOBS, first.id) == second


async def test_every_write_gets_a_fresh_etag(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a", n=1))
    replaced = await store.replace(JOBS, created.body, if_match=created.etag)
    upserted = await store.upsert(JOBS, replaced.body)
    assert len({created.etag, replaced.etag, upserted.etag}) == 3


async def test_delete_removes_the_document(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a"))
    await store.delete(JOBS, created.id)
    with pytest.raises(NotFound):
        await store.read(JOBS, created.id)


async def test_delete_missing_raises_not_found(store: Store, token: str) -> None:
    with pytest.raises(NotFound):
        await store.delete(JOBS, f"{token}-missing")
    with pytest.raises(NotFound):
        await store.delete(JOBS, f"{token}-missing", if_match='"some-etag"')


async def test_delete_with_if_match(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a", n=1))
    current = await store.replace(JOBS, created.body, if_match=created.etag)
    with pytest.raises(PreconditionFailed):
        await store.delete(JOBS, created.id, if_match=created.etag)
    assert await store.read(JOBS, created.id) == current
    await store.delete(JOBS, created.id, if_match=current.etag)
    with pytest.raises(NotFound):
        await store.read(JOBS, created.id)


async def test_concurrent_replaces_with_one_etag_let_exactly_one_win(
    store: Store, token: str
) -> None:
    created = await store.create(JOBS, body(token, "claim", holder=None, round=0))
    attempts = [
        store.replace(JOBS, {**created.body, "holder": holder, "round": 1}, if_match=created.etag)
        for holder in ("runner-a", "runner-b")
    ]
    results = await asyncio.gather(*attempts, return_exceptions=True)
    winners = [result for result in results if isinstance(result, Doc)]
    losers = [result for result in results if isinstance(result, PreconditionFailed)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert await store.read(JOBS, created.id) == winners[0]


async def test_returned_and_passed_bodies_are_independent_copies(store: Store, token: str) -> None:
    original = body(token, "a", tags=["x"], nested={"k": [1, 2]})
    created = await store.create(JOBS, original)
    original["tags"].append("mutated-input")
    created.body["tags"].append("mutated-output")
    created.body["nested"]["k"].append(3)
    read = await store.read(JOBS, created.id)
    assert read.body["tags"] == ["x"]
    assert read.body["nested"] == {"k": [1, 2]}
    read.body["tags"].clear()
    assert (await store.read(JOBS, created.id)).body["tags"] == ["x"]


async def test_tuples_are_stored_as_lists(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a", pair=(1, "two")))
    assert created.body["pair"] == [1, "two"]
    assert (await store.read(JOBS, created.id)).body["pair"] == [1, "two"]


async def test_the_ephemeral_container_works_too(store: Store, token: str) -> None:
    state = body(token, "oauth-state", expiresAt="2026-11-26T10:10:00Z")
    created = await store.create(EPHEMERAL, state)
    assert (await store.read(EPHEMERAL, created.id)).body == state
    await store.delete(EPHEMERAL, created.id, if_match=created.etag)
    with pytest.raises(NotFound):
        await store.read(EPHEMERAL, created.id)


# --------------------------------------------------------------------------------------------
# Validation: identical on every store, and always before any I/O
# --------------------------------------------------------------------------------------------

BAD_IDS: list[object] = [
    "",
    "test-a/b",
    "test-a\\b",
    "test-a?b",
    "test-a#b",
    "test-a\tb",
    "test-a\nb",
    "test-a\x00b",
    "test-a ",
    " test-a",
    "test-" + "x" * 251,
    123,
    None,
]


@pytest.mark.parametrize("bad_id", BAD_IDS)
async def test_invalid_ids_are_rejected(store: Store, bad_id: object) -> None:
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, {"id": bad_id, "ttl": 3600})
    with pytest.raises(InvalidRequest):
        await store.upsert(JOBS, {"id": bad_id, "ttl": 3600})
    if isinstance(bad_id, str):
        with pytest.raises(InvalidRequest):
            await store.read(JOBS, bad_id)
        with pytest.raises(InvalidRequest):
            await store.delete(JOBS, bad_id)


async def test_the_longest_id_is_accepted(store: Store, token: str) -> None:
    long_name = "x" * (255 - len(token) - 1)
    created = await store.create(JOBS, body(token, long_name))
    assert len(created.id) == 255
    assert (await store.read(JOBS, created.id)).id == created.id


async def test_a_missing_id_is_rejected(store: Store, token: str) -> None:
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, {"suite": token})


async def test_unknown_containers_are_rejected(store: Store, token: str) -> None:
    with pytest.raises(InvalidRequest):
        await store.create("drafts", body(token, "a"))
    with pytest.raises(InvalidRequest):
        await store.read("drafts", f"{token}-a")
    with pytest.raises(InvalidRequest):
        await store.find("drafts")
    with pytest.raises(InvalidRequest):
        await store.delete("drafts", f"{token}-a")


@pytest.mark.parametrize("prop", sorted(SYSTEM_PROPERTIES))
async def test_system_properties_cannot_be_written(store: Store, token: str, prop: str) -> None:
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, body(token, "a", **{prop: "x"}))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(dt.datetime(2026, 11, 26, tzinfo=dt.UTC), id="datetime"),
        pytest.param({1, 2}, id="set"),
        pytest.param(b"bytes", id="bytes"),
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="inf"),
        pytest.param(2**53, id="int-above-2^53"),
        pytest.param(-(2**53), id="int-below-minus-2^53"),
        pytest.param({1: "int key"}, id="non-string-key"),
        pytest.param({"deep": [{"x": [object()]}]}, id="nested-object"),
    ],
)
async def test_non_json_values_are_rejected(store: Store, token: str, value: object) -> None:
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, body(token, "a", value=value))


async def test_nesting_is_capped(store: Store, token: str) -> None:
    deep: object = "leaf"
    for _ in range(70):
        deep = [deep]
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, body(token, "a", deep=deep))


@pytest.mark.parametrize("ttl", [0, -2, "60", True, 1.5, None])
async def test_invalid_ttl_is_rejected(store: Store, token: str, ttl: object) -> None:
    document = body(token, "a")
    document["ttl"] = ttl
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, document)


async def test_oversized_documents_are_rejected(store: Store, token: str) -> None:
    with pytest.raises(InvalidRequest):
        await store.create(JOBS, body(token, "a", blob="x" * MAX_DOCUMENT_BYTES))


async def test_partition_key_is_required_for_containers_not_keyed_by_id(
    store: Store, token: str
) -> None:
    with pytest.raises(InvalidRequest):
        await store.read("article-versions", f"{token}-v1")
    with pytest.raises(InvalidRequest):
        await store.delete("signals", f"{token}-s1")
    with pytest.raises(InvalidRequest):
        await store.create("article-versions", body(token, "v1"))
    with pytest.raises(InvalidRequest):
        await store.create("usage", body(token, "u1", month=""))


async def test_if_match_must_be_a_non_empty_string(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a"))
    with pytest.raises(InvalidRequest):
        await store.replace(JOBS, created.body, if_match="")
    with pytest.raises(InvalidRequest):
        await store.delete(JOBS, created.id, if_match="")
    assert await store.read(JOBS, created.id) == created


async def test_invalid_find_arguments_are_rejected(store: Store) -> None:
    with pytest.raises(InvalidRequest):
        await store.find(JOBS, [Filter("events", "exists", True)])
    with pytest.raises(InvalidRequest):
        await store.find("articles", order_by=OrderBy("markdown"))
    with pytest.raises(InvalidRequest):
        await store.find(JOBS, limit=0)
    with pytest.raises(InvalidRequest):
        await store.find(JOBS, partition_key="")
    with pytest.raises(InvalidRequest):
        await store.find(JOBS, ["status"])  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------------------------
# find: the filter DSL
# --------------------------------------------------------------------------------------------


@pytest.fixture
async def dataset(store: Store, token: str) -> Store:
    """Five documents covering numbers, strings, booleans, null, missing fields and nesting."""
    rows = [
        body(token, "a", n=1, s="apple", flag=True, tag="x", nested={"k": 1}, maybe=None),
        body(token, "b", n=2, s="banana", flag=False, tag="y", nested={"k": 2}),
        body(token, "c", n=3.5, s="cherry", tag="x", nested={"k": "1"}),
        body(token, "d", n="3", s="date", flag=1, items=[1, 2]),
        body(token, "e", n=None, s="elder"),
    ]
    for row in rows:
        await store.create(JOBS, row)
    return store


async def test_no_filters_returns_everything_in_scope(dataset: Store, token: str) -> None:
    assert sorted(await find_names(dataset, token)) == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (Filter("n", "==", 1), ["a"]),
        (Filter("n", "==", 2.0), ["b"]),
        (Filter("n", "==", "3"), ["d"]),
        (Filter("n", "==", None), ["e"]),
        (Filter("maybe", "==", None), ["a"]),
        (Filter("flag", "==", True), ["a"]),
        (Filter("flag", "==", 1), ["d"]),
        (Filter("flag", "==", False), ["b"]),
        (Filter("tag", "!=", "x"), ["b"]),
        (Filter("n", "!=", 1), ["b", "c", "d", "e"]),
        (Filter("n", "!=", None), ["a", "b", "c", "d"]),
        (Filter("flag", "!=", True), ["b", "d"]),
        (Filter("items", "!=", 1), []),
        (Filter("nested", "!=", 1), []),
        (Filter("items", ">", 0), []),
        (Filter("n", ">", 1), ["b", "c"]),
        (Filter("n", ">=", 2), ["b", "c"]),
        (Filter("n", "<", 3.5), ["a", "b"]),
        (Filter("n", "<=", 3.5), ["a", "b", "c"]),
        (Filter("s", "<", "c"), ["a", "b"]),
        (Filter("s", ">=", "cherry"), ["c", "d", "e"]),
        (Filter("tag", "in", ["x"]), ["a", "c"]),
        (Filter("n", "in", [1, "3"]), ["a", "d"]),
        (Filter("flag", "in", (True, 1)), ["a", "d"]),
        (Filter("n", "in", [None, 2]), ["b", "e"]),
        (Filter("tag", "in", []), []),
        (Filter("flag", "exists", True), ["a", "b", "d"]),
        (Filter("flag", "exists", False), ["c", "e"]),
        (Filter("maybe", "exists", True), ["a"]),
        (Filter("nested.k", "exists", True), ["a", "b", "c"]),
        (Filter("nested.k", "==", 1), ["a"]),
        (Filter("nested.k", ">", 1), ["b"]),
        (Filter("nested.k", "==", "1"), ["c"]),
        (Filter("items", "==", 1), []),
        (Filter("nested", "==", 1), []),
        (Filter("items.k", "exists", True), []),
    ],
    ids=lambda value: repr(value) if isinstance(value, Filter) else None,
)
async def test_filter_semantics(
    dataset: Store, token: str, condition: Filter, expected: list[str]
) -> None:
    assert sorted(await find_names(dataset, token, condition)) == expected


async def test_filters_are_anded(dataset: Store, token: str) -> None:
    found = await find_names(dataset, token, Filter("tag", "==", "x"), Filter("n", ">", 1))
    assert found == ["c"]


async def test_order_by_sorts_types_null_bool_number_string(dataset: Store, token: str) -> None:
    assert await find_names(dataset, token, order_by=OrderBy("n")) == ["e", "a", "b", "c", "d"]
    descending = await find_names(dataset, token, order_by=OrderBy("n", descending=True))
    assert descending == ["d", "c", "b", "a", "e"]


async def test_order_by_skips_missing_arrays_and_objects(dataset: Store, token: str) -> None:
    assert await find_names(dataset, token, order_by=OrderBy("flag")) == ["b", "a", "d"]
    assert await find_names(dataset, token, order_by=OrderBy("nested")) == []
    assert await find_names(dataset, token, order_by=OrderBy("items")) == []
    by_nested = await find_names(dataset, token, order_by=OrderBy("nested.k"))
    assert by_nested == ["a", "b", "c"]


async def test_order_by_with_filters_and_limit(dataset: Store, token: str) -> None:
    found = await find_names(
        dataset, token, Filter("n", "!=", 2), order_by=OrderBy("s", descending=True), limit=3
    )
    assert found == ["e", "d", "c"]
    assert await find_names(dataset, token, order_by=OrderBy("s"), limit=1) == ["a"]


async def test_limit_without_order_caps_the_count(dataset: Store, token: str) -> None:
    found = await find_names(dataset, token, limit=2)
    assert len(found) == 2
    assert set(found) <= {"a", "b", "c", "d", "e"}


async def test_find_can_be_scoped_to_one_partition(dataset: Store, token: str) -> None:
    docs = await dataset.find(JOBS, [Filter("suite", "==", token)], partition_key=f"{token}-c")
    assert names(token, docs) == ["c"]
    assert docs[0].partition_key == f"{token}-c"


async def test_found_documents_carry_etags_usable_for_cas(dataset: Store, token: str) -> None:
    (doc,) = await dataset.find(JOBS, [Filter("suite", "==", token), Filter("s", "==", "apple")])
    assert doc == await dataset.read(JOBS, doc.id)
    updated = await dataset.replace(JOBS, {**doc.body, "n": 10}, if_match=doc.etag)
    assert updated.body["n"] == 10


async def test_values_are_parameters_not_sql(dataset: Store, token: str) -> None:
    hostile = '" OR 1=1 OR c["s"] = "apple'
    assert await find_names(dataset, token, Filter("s", "==", hostile)) == []
    assert await find_names(dataset, token, Filter("s", "in", [hostile, "date"])) == ["d"]


# --------------------------------------------------------------------------------------------
# find: projections (contract I-10)
# --------------------------------------------------------------------------------------------


async def test_a_projection_returns_only_the_listed_fields(dataset: Store, token: str) -> None:
    docs = await dataset.find(
        JOBS,
        [Filter("suite", "==", token)],
        order_by=OrderBy("s"),
        fields=["n", "flag", "nested", "maybe"],
    )
    assert [doc.body for doc in docs] == [
        {"id": f"{token}-a", "n": 1, "flag": True, "nested": {"k": 1}, "maybe": None},
        {"id": f"{token}-b", "n": 2, "flag": False, "nested": {"k": 2}},
        {"id": f"{token}-c", "n": 3.5, "nested": {"k": "1"}},
        {"id": f"{token}-d", "n": "3", "flag": 1},
        {"id": f"{token}-e", "n": None},
    ]


async def test_projected_documents_carry_the_whole_documents_etag(
    dataset: Store, token: str
) -> None:
    (doc,) = await dataset.find(
        JOBS, [Filter("suite", "==", token), Filter("s", "==", "apple")], fields=["s"]
    )
    whole = await dataset.read(JOBS, doc.id)
    assert (doc.id, doc.partition_key, doc.etag) == (whole.id, whole.partition_key, whole.etag)
    assert doc.body == {"id": whole.id, "s": "apple"}
    assert not SYSTEM_PROPERTIES & doc.body.keys()
    replaced = await dataset.replace(JOBS, {**whole.body, "n": 10}, if_match=doc.etag)
    assert replaced.body["n"] == 10


async def test_filters_order_and_limit_still_see_the_whole_document(
    dataset: Store, token: str
) -> None:
    docs = await dataset.find(
        JOBS,
        [Filter("suite", "==", token), Filter("tag", "==", "x")],
        order_by=OrderBy("n", descending=True),
        limit=1,
        fields=["s"],
    )
    assert [doc.body for doc in docs] == [{"id": f"{token}-c", "s": "cherry"}]


async def test_an_empty_projection_returns_ids_only(dataset: Store, token: str) -> None:
    docs = await dataset.find(JOBS, [Filter("suite", "==", token)], fields=[])
    assert sorted(names(token, docs)) == ["a", "b", "c", "d", "e"]
    assert all(doc.body == {"id": doc.id} for doc in docs)
    again = await dataset.find(JOBS, [Filter("suite", "==", token)], fields=["id", "id"])
    assert sorted(doc.body["id"] for doc in again) == sorted(doc.id for doc in docs)


async def test_unindexed_fields_can_be_read_but_not_filtered(store: Store, token: str) -> None:
    created = await store.create(JOBS, body(token, "a", events=[{"seq": 1}], other="x"))
    (doc,) = await store.find(JOBS, [Filter("suite", "==", token)], fields=["events"])
    assert doc.body == {"id": created.id, "events": [{"seq": 1}]}
    with pytest.raises(InvalidRequest):
        await store.find(JOBS, [Filter("events", "exists", True)], fields=["id"])


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param("status", id="a-string"),
        pytest.param(b"status", id="bytes"),
        pytest.param({"status": 1}, id="a-mapping"),
        pytest.param(7, id="not-iterable"),
        pytest.param([""], id="empty-name"),
        pytest.param(["a.b"], id="nested-path"),
        pytest.param(["a-b"], id="not-an-identifier"),
        pytest.param(["_etag"], id="system-property"),
        pytest.param(["_ts"], id="system-property-ts"),
        pytest.param([None], id="none"),
        pytest.param([1], id="int"),
        pytest.param(['s"] FROM c --'], id="sql"),
    ],
)
async def test_invalid_projections_are_rejected(store: Store, fields: object) -> None:
    with pytest.raises(InvalidRequest):
        await store.find(JOBS, fields=fields)  # pyright: ignore[reportArgumentType]
