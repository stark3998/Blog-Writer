"""The filter DSL: compiled to parameterized Cosmos SQL, or evaluated in Python with the same rules.

Semantics (Cosmos SQL as the live contract suite observed it, mirrored by :func:`apply`):

- Filters are ANDed. A field missing from a document is *undefined*: it fails every comparison
  (``!=`` included) and every ``in``; only ``exists False`` matches it.
- ``==`` and ``in`` match only stored values of the same JSON type (null, boolean, number,
  string) that are equal. Cosmos evaluates a cross-type ``=`` as false (verified live).
- ``!=`` matches every stored *primitive* (null, boolean, number, string) that is not an equal
  value of the same type, so primitives of another type match it. Compiled with an
  ``IS_PRIMITIVE`` guard.
- ``<``, ``<=``, ``>``, ``>=`` match only stored numbers (against a number) or strings (against a
  string). Compiled with an ``IS_NUMBER`` / ``IS_STRING`` guard.
- Arrays and objects never match a scalar filter; only ``exists`` sees them.
- An ordered query returns only documents whose order field is a JSON primitive
  (``IS_PRIMITIVE``), sorted null < false < true < numbers < strings, reversed when descending.
- ``limit`` keeps the first N results after ordering.
- Fields a container excludes from indexing (:data:`~blogwriter.db.base.UNINDEXED_FIELDS`) cannot
  be filtered or ordered on: Cosmos would need a full scan, so :func:`find_spec` refuses them.

SQL text only ever contains validated field-path identifiers (bracket notation, so reserved words
are safe); every value, including the limit, is a query parameter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from blogwriter.db.base import (
    UNINDEXED_FIELDS,
    Doc,
    Filter,
    InvalidRequest,
    JsonScalar,
    OrderBy,
    partition_key_path,
)

__all__ = ["CompiledQuery", "FindSpec", "apply", "compile_query", "find_spec"]

_SQL_OPERATORS: Final[Mapping[str, str]] = {
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}


@dataclass(frozen=True, slots=True)
class FindSpec:
    """Validated arguments of ``Store.find``."""

    filters: tuple[Filter, ...]
    order_by: OrderBy | None
    limit: int | None
    partition_key: str | None


def find_spec(
    container: str,
    filters: object,
    order_by: object,
    limit: object,
    partition_key: object,
) -> FindSpec:
    """Validate ``find`` arguments for ``container`` (both stores call this before any work)."""
    partition_key_path(container)
    unindexed = UNINDEXED_FIELDS[container]
    if isinstance(filters, (str, bytes, Mapping)) or not isinstance(filters, Iterable):
        raise InvalidRequest("filters must be a sequence of Filter")
    checked: list[Filter] = []
    for item in cast(Iterable[object], filters):
        if not isinstance(item, Filter):
            raise InvalidRequest("filters must be a sequence of Filter")
        if item.path[0] in unindexed:
            raise InvalidRequest(
                f"{container} does not index {item.path[0]!r}; cannot filter on it"
            )
        checked.append(item)
    if order_by is not None and not isinstance(order_by, OrderBy):
        raise InvalidRequest("order_by must be an OrderBy or None")
    if order_by is not None and order_by.path[0] in unindexed:
        raise InvalidRequest(f"{container} does not index {order_by.path[0]!r}; cannot sort on it")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise InvalidRequest("limit must be a positive int or None")
    if partition_key is not None and (not isinstance(partition_key, str) or not partition_key):
        raise InvalidRequest("partition_key must be a non-empty string or None")
    return FindSpec(
        filters=tuple(checked),
        order_by=order_by,
        limit=limit,
        partition_key=partition_key,
    )


# --------------------------------------------------------------------------------------------
# Cosmos SQL
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    """SQL text plus its parameters, as ``(name, value)`` pairs."""

    text: str
    parameters: tuple[tuple[str, object], ...]

    def sdk_parameters(self) -> list[dict[str, object]]:
        """Parameters in the shape ``ContainerProxy.query_items(parameters=...)`` expects."""
        return [{"name": name, "value": value} for name, value in self.parameters]


_IDENTIFIER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _ref(path: Sequence[str]) -> str:
    # Filter and OrderBy validate their paths (parse_field_path), but this is the only place a
    # caller-supplied string enters the SQL text, so check again here: a subclass overriding
    # ``path`` (or a mutated instance) can then never break out of the quoted property name.
    # Bracket notation keeps reserved words (value, order...) safe.
    if not path or any(_IDENTIFIER.fullmatch(segment) is None for segment in path):
        raise InvalidRequest("field path segments must match [A-Za-z_][A-Za-z0-9_]*")
    return "c" + "".join(f'["{segment}"]' for segment in path)


def _range_guard(operand: JsonScalar) -> str:
    # Filter validation guarantees range operands are numbers or strings.
    return "IS_STRING" if isinstance(operand, str) else "IS_NUMBER"


def compile_query(spec: FindSpec) -> CompiledQuery:
    """Compile a validated :class:`FindSpec` to a parameterized Cosmos SQL query."""
    parameters: list[tuple[str, object]] = []

    def bind(value: object) -> str:
        name = f"@p{len(parameters)}"
        parameters.append((name, value))
        return name

    clauses: list[str] = []
    for item in spec.filters:
        ref = _ref(item.path)
        if item.op == "exists":
            clauses.append(
                f"IS_DEFINED({ref})" if item.operand is True else f"NOT IS_DEFINED({ref})"
            )
        elif item.op == "in":
            if item.values:
                names = ", ".join(bind(value) for value in item.values)
                clauses.append(f"{ref} IN ({names})")
            else:
                clauses.append("false")
        elif item.op == "==":
            clauses.append(f"{ref} = {bind(item.operand)}")
        else:
            # Type guards pin the semantics in the query itself instead of relying on the
            # engine's cross-type rules (live, cross-type != was true although the docs say
            # undefined).
            guard = "IS_PRIMITIVE" if item.op == "!=" else _range_guard(item.operand)
            operator = _SQL_OPERATORS[item.op]
            clauses.append(f"({guard}({ref}) AND {ref} {operator} {bind(item.operand)})")
    order = ""
    if spec.order_by is not None:
        ref = _ref(spec.order_by.path)
        clauses.append(f"IS_PRIMITIVE({ref})")
        order = f" ORDER BY {ref} {'DESC' if spec.order_by.descending else 'ASC'}"
    select = "SELECT * FROM c"
    if spec.limit is not None:
        select = "SELECT TOP @limit * FROM c"
        parameters.append(("@limit", spec.limit))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return CompiledQuery(text=f"{select}{where}{order}", parameters=tuple(parameters))


# --------------------------------------------------------------------------------------------
# In-memory evaluation (MemoryStore)
# --------------------------------------------------------------------------------------------


class _Undefined:
    __slots__ = ()


_UNDEFINED: Final = _Undefined()

# JSON type ranks; the first four (the primitives) are also the cross-type order of ORDER BY.
_NULL, _BOOL, _NUMBER, _STRING, _ARRAY, _OBJECT, _UNDEFINED_KIND = range(7)


def _lookup(body: Mapping[str, Any], path: Sequence[str]) -> object:
    current: object = body
    for segment in path:
        if not isinstance(current, dict):
            return _UNDEFINED
        mapping = cast(dict[str, object], current)
        if segment not in mapping:
            return _UNDEFINED
        current = mapping[segment]
    return current


def _kind(value: object) -> int:
    if value is _UNDEFINED:
        return _UNDEFINED_KIND
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return _BOOL
    if isinstance(value, (int, float)):
        return _NUMBER
    if isinstance(value, str):
        return _STRING
    if isinstance(value, list):
        return _ARRAY
    return _OBJECT


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(stored: object, value: JsonScalar) -> bool:
    if stored is _UNDEFINED or _kind(stored) != _kind(value):
        return False
    return stored == value


def _compare(stored: object, value: JsonScalar) -> int | None:
    """-1/0/1 when both are strings or both are numbers; ``None`` (undefined) otherwise."""
    if isinstance(stored, str) and isinstance(value, str):
        return (stored > value) - (stored < value)
    if _is_number(stored) and _is_number(value):
        left = cast(float, stored)
        right = cast(float, value)
        return (left > right) - (left < right)
    return None


def _matches(item: Filter, body: Mapping[str, Any]) -> bool:
    stored = _lookup(body, item.path)
    if item.op == "exists":
        return (stored is not _UNDEFINED) == item.operand
    if item.op == "in":
        return any(_equal(stored, candidate) for candidate in item.values)
    operand = item.operand
    if item.op == "==":
        return _equal(stored, operand)
    if item.op == "!=":
        return _kind(stored) <= _STRING and not _equal(stored, operand)
    order = _compare(stored, operand)
    if order is None:
        return False
    if item.op == "<":
        return order < 0
    if item.op == "<=":
        return order <= 0
    if item.op == ">":
        return order > 0
    return order >= 0


def _sort_key(value: object) -> tuple[int, float | str] | None:
    """ORDER BY key of a JSON primitive; ``None`` for undefined, arrays and objects."""
    if value is None:
        return (_NULL, 0.0)
    if isinstance(value, bool):
        return (_BOOL, float(value))
    if isinstance(value, (int, float)):
        return (_NUMBER, float(value))
    if isinstance(value, str):
        return (_STRING, value)
    return None


def apply(spec: FindSpec, docs: Iterable[Doc]) -> list[Doc]:
    """Filter, order and limit ``docs`` exactly as Cosmos runs :func:`compile_query`."""
    selected = [
        doc
        for doc in docs
        if (spec.partition_key is None or doc.partition_key == spec.partition_key)
        and all(_matches(item, doc.body) for item in spec.filters)
    ]
    if spec.order_by is None:
        selected.sort(key=lambda doc: (doc.partition_key, doc.id))
    else:
        path = spec.order_by.path
        keyed: list[tuple[tuple[int, float | str], Doc]] = []
        for doc in selected:
            key = _sort_key(_lookup(doc.body, path))
            if key is not None:
                keyed.append((key, doc))
        keyed.sort(
            key=lambda pair: (pair[0], pair[1].partition_key, pair[1].id),
            reverse=spec.order_by.descending,
        )
        selected = [doc for _, doc in keyed]
    if spec.limit is not None:
        selected = selected[: spec.limit]
    return selected
