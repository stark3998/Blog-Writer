"""The filter DSL: validation, SQL compilation goldens, and the in-memory evaluator's edge cases."""

from __future__ import annotations

import math
from typing import Any, get_args

import pytest

from blogwriter.db import Doc, Filter, FilterOp, InvalidRequest, OrderBy
from blogwriter.db.base import FILTER_OPS, MAX_FIELD_DEPTH
from blogwriter.db.query import CompiledQuery, FindSpec, apply, compile_query, find_spec


def spec(*filters: Filter, order_by: OrderBy | None = None, limit: int | None = None) -> FindSpec:
    return find_spec("jobs", filters, order_by, limit, None)


def doc(item_id: str, **fields: Any) -> Doc:
    return Doc(id=item_id, partition_key=item_id, body={"id": item_id, **fields}, etag='"e"')


def matching(docs: list[Doc], *filters: Filter, order_by: OrderBy | None = None) -> list[str]:
    return [found.id for found in apply(spec(*filters, order_by=order_by), docs)]


# --------------------------------------------------------------------------------------------
# Filter / OrderBy validation
# --------------------------------------------------------------------------------------------


def test_filter_ops_constant_matches_the_literal() -> None:
    assert set(get_args(FilterOp.__value__)) == FILTER_OPS


@pytest.mark.parametrize("field", ["status", "lease.until", "a_b.C1._x", "x." * 7 + "y"])
def test_valid_field_paths(field: str) -> None:
    assert Filter(field, "exists", True).path == tuple(field.split("."))
    assert OrderBy(field).path == tuple(field.split("."))


@pytest.mark.parametrize(
    "field",
    [
        "",
        "a-b",
        "a b",
        'a"]',
        "a['b']",
        "1a",
        "a..b",
        ".a",
        "a.",
        "\N{LATIN SMALL LETTER E WITH ACUTE}",
        "_ts",
        "_etag",
        "_rid.x",
        "x." * MAX_FIELD_DEPTH + "y",
    ],
)
def test_invalid_field_paths(field: str) -> None:
    with pytest.raises(InvalidRequest):
        Filter(field, "exists", True)
    with pytest.raises(InvalidRequest):
        OrderBy(field)


def test_non_string_field_is_rejected() -> None:
    with pytest.raises(InvalidRequest):
        Filter(1, "==", 1)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("op", "value"),
    [
        ("~=", 1),
        ("exists", 1),
        ("exists", None),
        ("in", "abc"),
        ("in", {1, 2}),
        ("in", {"a": 1}),
        ("in", [[1]]),
        ("in", [{"a": 1}]),
        ("in", [math.nan]),
        ("==", [1]),
        ("==", {"a": 1}),
        ("==", b"x"),
        ("==", math.nan),
        ("==", math.inf),
        ("==", 2**53),
        ("<", None),
        ("<", True),
        (">=", False),
        (">", [1]),
    ],
)
def test_invalid_filter_values(op: str, value: object) -> None:
    with pytest.raises(InvalidRequest):
        Filter("f", op, value)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("op", "value"),
    [
        ("==", None),
        ("==", True),
        ("==", "x"),
        ("==", 2**53 - 1),
        ("==", -(2**53) + 1),
        ("!=", 0),
        ("!=", None),
        ("<", 1.5),
        ("<=", "2026-11-26T10:00:00Z"),
        ("in", []),
        ("in", (None, False, 0, "")),
        ("exists", False),
    ],
)
def test_valid_filter_values(op: FilterOp, value: Any) -> None:
    Filter("f", op, value)


def test_in_values_are_normalised_to_a_tuple() -> None:
    item = Filter("f", "in", ["a", 1])
    assert item.value == ("a", 1)
    assert item.values == ("a", 1)
    assert item == Filter("f", "in", ("a", 1))
    assert hash(item) == hash(Filter("f", "in", ("a", 1)))


def test_operand_accessors() -> None:
    assert Filter("f", "==", 3).operand == 3
    assert Filter("f", "==", 3).values == ()
    assert Filter("f", "exists", False).operand is False
    assert Filter("f", "in", [1]).operand is None


def test_order_by_descending_must_be_bool() -> None:
    with pytest.raises(InvalidRequest):
        OrderBy("f", descending="yes")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("filters", "order_by", "limit", "partition_key"),
    [
        ("status", None, None, None),
        (b"status", None, None, None),
        ({"status": "x"}, None, None, None),
        (42, None, None, None),
        (["status"], None, None, None),
        ([Filter("a", "==", 1), "b"], None, None, None),
        ((), "f", None, None),
        ((), Filter("a", "==", 1), None, None),
        ((), None, 0, None),
        ((), None, -1, None),
        ((), None, True, None),
        ((), None, 1.5, None),
        ((), None, None, ""),
        ((), None, None, 7),
    ],
)
def test_find_spec_rejects_bad_arguments(
    filters: object, order_by: object, limit: object, partition_key: object
) -> None:
    with pytest.raises(InvalidRequest):
        find_spec("jobs", filters, order_by, limit, partition_key)


def test_find_spec_rejects_unknown_containers() -> None:
    with pytest.raises(InvalidRequest):
        find_spec("drafts", (), None, None, None)


def test_find_spec_accepts_any_iterable_of_filters() -> None:
    items = [Filter("a", "==", 1), Filter("b", "exists", True)]
    assert find_spec("jobs", iter(items), None, None, None).filters == tuple(items)
    assert find_spec("jobs", items, OrderBy("a"), 5, "pk").limit == 5


@pytest.mark.parametrize(
    ("container", "field"),
    [
        ("articles", "markdown"),
        ("articles", "embedding.vector"),
        ("published-blogs", "htmlContent"),
        ("published-blogs", "mdxContent"),
        ("posts", "payload.text"),
        ("jobs", "events"),
    ],
)
def test_unindexed_fields_cannot_be_filtered_or_sorted(container: str, field: str) -> None:
    with pytest.raises(InvalidRequest, match="does not index"):
        find_spec(container, [Filter(field, "exists", True)], None, None, None)
    with pytest.raises(InvalidRequest, match="does not index"):
        find_spec(container, (), OrderBy(field), None, None)


def test_fields_unindexed_elsewhere_are_fine() -> None:
    find_spec("articles", [Filter("events", "exists", True)], OrderBy("payload"), None, None)
    find_spec(
        "jobs",
        [Filter("payload", "exists", True), Filter("markdownLength", ">", 1)],
        None,
        None,
        None,
    )


# --------------------------------------------------------------------------------------------
# SQL goldens
# --------------------------------------------------------------------------------------------


def compiled(
    *filters: Filter, order_by: OrderBy | None = None, limit: int | None = None
) -> CompiledQuery:
    return compile_query(spec(*filters, order_by=order_by, limit=limit))


def test_no_filters() -> None:
    query = compiled()
    assert query.text == "SELECT * FROM c"
    assert query.parameters == ()


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ("==", 'SELECT * FROM c WHERE c["n"] = @p0'),
        ("!=", 'SELECT * FROM c WHERE (IS_PRIMITIVE(c["n"]) AND c["n"] != @p0)'),
        ("<", 'SELECT * FROM c WHERE (IS_NUMBER(c["n"]) AND c["n"] < @p0)'),
        ("<=", 'SELECT * FROM c WHERE (IS_NUMBER(c["n"]) AND c["n"] <= @p0)'),
        (">", 'SELECT * FROM c WHERE (IS_NUMBER(c["n"]) AND c["n"] > @p0)'),
        (">=", 'SELECT * FROM c WHERE (IS_NUMBER(c["n"]) AND c["n"] >= @p0)'),
    ],
)
def test_comparisons(op: FilterOp, expected: str) -> None:
    query = compiled(Filter("n", op, 5))
    assert query.text == expected
    assert query.parameters == (("@p0", 5),)


def test_range_guards_follow_the_operand_type() -> None:
    assert compiled(Filter("at", ">=", "2026-11-26")).text == (
        'SELECT * FROM c WHERE (IS_STRING(c["at"]) AND c["at"] >= @p0)'
    )
    assert compiled(Filter("x", "<", 0.5)).text == (
        'SELECT * FROM c WHERE (IS_NUMBER(c["x"]) AND c["x"] < @p0)'
    )
    assert compiled(Filter("x", "!=", None)).text == (
        'SELECT * FROM c WHERE (IS_PRIMITIVE(c["x"]) AND c["x"] != @p0)'
    )


def test_equality_with_null_is_a_parameter() -> None:
    query = compiled(Filter("n", "==", None))
    assert query.text == 'SELECT * FROM c WHERE c["n"] = @p0'
    assert query.parameters == (("@p0", None),)


def test_in_binds_one_parameter_per_value() -> None:
    query = compiled(Filter("state", "in", ["queued", "running", None]))
    assert query.text == 'SELECT * FROM c WHERE c["state"] IN (@p0, @p1, @p2)'
    assert query.parameters == (("@p0", "queued"), ("@p1", "running"), ("@p2", None))


def test_empty_in_matches_nothing() -> None:
    query = compiled(Filter("state", "in", []), Filter("n", "==", 1))
    assert query.text == 'SELECT * FROM c WHERE false AND c["n"] = @p0'
    assert query.parameters == (("@p0", 1),)


def test_exists() -> None:
    assert compiled(Filter("lease.until", "exists", True)).text == (
        'SELECT * FROM c WHERE IS_DEFINED(c["lease"]["until"])'
    )
    assert compiled(Filter("deletedAt", "exists", False)).text == (
        'SELECT * FROM c WHERE NOT IS_DEFINED(c["deletedAt"])'
    )


def test_reserved_words_are_safe_in_bracket_notation() -> None:
    query = compiled(Filter("value", "==", 1), Filter("order.select", "exists", True))
    assert query.text == (
        'SELECT * FROM c WHERE c["value"] = @p0 AND IS_DEFINED(c["order"]["select"])'
    )


def test_order_by_adds_is_primitive_and_direction() -> None:
    assert compiled(order_by=OrderBy("slot.utc")).text == (
        'SELECT * FROM c WHERE IS_PRIMITIVE(c["slot"]["utc"]) ORDER BY c["slot"]["utc"] ASC'
    )
    assert compiled(order_by=OrderBy("n", descending=True)).text == (
        'SELECT * FROM c WHERE IS_PRIMITIVE(c["n"]) ORDER BY c["n"] DESC'
    )


def test_limit_is_a_parameter() -> None:
    query = compiled(limit=10)
    assert query.text == "SELECT TOP @limit * FROM c"
    assert query.parameters == (("@limit", 10),)


def test_everything_together() -> None:
    query = compiled(
        Filter("state", "==", "queued"),
        Filter("leaseUntil", "<", "2026-11-26T10:00:00Z"),
        Filter("kind", "in", ["crawl", "draft"]),
        order_by=OrderBy("leaseUntil"),
        limit=3,
    )
    assert query.text == (
        'SELECT TOP @limit * FROM c WHERE c["state"] = @p0 '
        'AND (IS_STRING(c["leaseUntil"]) AND c["leaseUntil"] < @p1) '
        'AND c["kind"] IN (@p2, @p3) AND IS_PRIMITIVE(c["leaseUntil"]) '
        'ORDER BY c["leaseUntil"] ASC'
    )
    assert query.sdk_parameters() == [
        {"name": "@p0", "value": "queued"},
        {"name": "@p1", "value": "2026-11-26T10:00:00Z"},
        {"name": "@p2", "value": "crawl"},
        {"name": "@p3", "value": "draft"},
        {"name": "@limit", "value": 3},
    ]


@pytest.mark.parametrize(
    "hostile",
    [
        '" OR 1=1 --',
        "x') OR true OR ('",
        '"]) OR IS_DEFINED(c["id"]',
        "@p9 OR true",
        "\\",
        "'; DROP",
    ],
)
def test_values_never_reach_the_sql_text(hostile: str) -> None:
    query = compiled(Filter("s", "==", hostile), Filter("t", "in", [hostile]))
    assert hostile not in query.text
    assert query.text == 'SELECT * FROM c WHERE c["s"] = @p0 AND c["t"] IN (@p1)'
    assert [value for _, value in query.parameters] == [hostile, hostile]


# --------------------------------------------------------------------------------------------
# In-memory evaluation edge cases (Cosmos semantics)
# --------------------------------------------------------------------------------------------


def test_missing_fields_fail_every_comparison() -> None:
    docs = [doc("has", n=1), doc("lacks")]
    for op in ("==", "!=", "<", "<=", ">", ">="):
        assert "lacks" not in matching(docs, Filter("n", op, 0))  # pyright: ignore[reportArgumentType]
    assert matching(docs, Filter("n", "in", [1, 0])) == ["has"]
    assert matching(docs, Filter("n", "exists", False)) == ["lacks"]


def test_equality_and_ranges_never_cross_types_but_not_equal_does() -> None:
    docs = [doc("t", v=True), doc("one", v=1), doc("s1", v="1"), doc("null", v=None)]
    assert matching(docs, Filter("v", "==", 1)) == ["one"]
    assert matching(docs, Filter("v", "==", True)) == ["t"]
    assert matching(docs, Filter("v", "==", "1")) == ["s1"]
    assert matching(docs, Filter("v", "!=", 0)) == ["null", "one", "s1", "t"]
    assert matching(docs, Filter("v", "!=", 1)) == ["null", "s1", "t"]
    assert matching(docs, Filter("v", "!=", True)) == ["null", "one", "s1"]
    assert matching(docs, Filter("v", "!=", None)) == ["one", "s1", "t"]
    assert matching(docs, Filter("v", ">", 0)) == ["one"]
    assert matching(docs, Filter("v", ">=", "")) == ["s1"]


def test_ints_and_floats_are_one_number_type() -> None:
    docs = [doc("int", v=2), doc("float", v=2.0), doc("half", v=2.5)]
    assert matching(docs, Filter("v", "==", 2.0)) == ["float", "int"]
    assert matching(docs, Filter("v", "==", 2)) == ["float", "int"]
    assert matching(docs, Filter("v", "<", 2.5)) == ["float", "int"]


def test_arrays_and_objects_only_match_exists() -> None:
    docs = [doc("arr", v=[1]), doc("obj", v={"a": 1}), doc("num", v=1)]
    assert matching(docs, Filter("v", "==", 1)) == ["num"]
    assert matching(docs, Filter("v", "in", [1])) == ["num"]
    assert matching(docs, Filter("v", ">=", 1)) == ["num"]
    assert matching(docs, Filter("v", "!=", 1)) == []
    assert matching(docs, Filter("v", "!=", 2)) == ["num"]
    assert matching(docs, Filter("v", "exists", True)) == ["arr", "num", "obj"]


def test_paths_through_non_objects_are_undefined() -> None:
    docs = [doc("arr", a=[{"b": 1}]), doc("str", a="b"), doc("obj", a={"b": 1})]
    assert matching(docs, Filter("a.b", "exists", True)) == ["obj"]
    assert matching(docs, Filter("a.b", "exists", False)) == ["arr", "str"]


def test_strings_compare_ordinally() -> None:
    docs = [doc("upper", s="B"), doc("lower", s="a"), doc("ts1", s="2026-11-26T09:00:00Z")]
    assert matching(docs, Filter("s", "<", "a")) == ["ts1", "upper"]
    assert matching(docs, Filter("s", ">", "2026-11-26T08:59:59Z")) == ["lower", "ts1", "upper"]


def test_unordered_results_are_deterministic() -> None:
    docs = [doc("b"), doc("c"), doc("a")]
    assert matching(docs) == ["a", "b", "c"]


def test_ordering_ties_break_by_partition_key_and_id() -> None:
    docs = [doc("b", n=1), doc("a", n=1.0), doc("c", n=0)]
    assert matching(docs, order_by=OrderBy("n")) == ["c", "a", "b"]
    assert matching(docs, order_by=OrderBy("n", descending=True)) == ["b", "a", "c"]


def test_partition_scope_and_limit() -> None:
    docs = [doc("a", n=1), doc("b", n=2), doc("c", n=3)]
    assert [d.id for d in apply(find_spec("jobs", (), None, None, "b"), docs)] == ["b"]
    descending = find_spec("jobs", (), OrderBy("n", True), 2, None)
    assert [d.id for d in apply(descending, docs)] == ["c", "b"]


def test_a_filter_subclass_cannot_smuggle_sql_through_its_path() -> None:
    """Field paths are the only caller text that reaches the SQL; the compiler re-checks them,
    so even a ``Filter`` subclass that overrides ``path`` cannot break out of the brackets."""

    class Sneaky(Filter):
        @property
        def path(self) -> tuple[str, ...]:
            return ('status"] = "x" OR true OR c["status',)

    sneaky = Sneaky("status", "==", "queued")
    checked = find_spec("jobs", [sneaky], None, None, None)
    with pytest.raises(InvalidRequest, match="field path"):
        compile_query(checked)

    class SneakyOrder(OrderBy):
        @property
        def path(self) -> tuple[str, ...]:
            return ("x] DESC, c[y",)

    ordered = find_spec("jobs", [], SneakyOrder("updatedAt"), None, None)
    with pytest.raises(InvalidRequest, match="field path"):
        compile_query(ordered)
