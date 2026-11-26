"""Structured output: per-provider JSON schemas compiled from Pydantic models.

:func:`compile_schema` turns a Pydantic model into the schema one provider accepts, and the
returned :class:`CompiledSchema` validates replies against the *original* model, so the
constraints a provider cannot enforce are still checked here.

Rules shared by both providers
    * Every object gets ``additionalProperties: false``. Objects without declared properties
      (``dict`` fields, ``Any``) cannot be expressed and fail to compile.
    * ``$ref`` is inlined. ``oneOf`` becomes ``anyOf``; a single-schema ``allOf`` is merged.
      ``const`` becomes a one-value ``enum``.
    * Annotations the providers do not use are dropped (``title``, ``default``, ``examples``,
      ``discriminator``, ...). Validation keywords (``minLength``, ``maxLength``, ``minItems``,
      ``maxItems``, ``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``,
      ``multipleOf``, ``pattern``, ``format``, ``uniqueItems``, ``minProperties``,
      ``maxProperties``) are stripped and restated in the description as a hint; Pydantic enforces
      them on the reply. Any other keyword fails to compile, so new Pydantic output surfaces in
      tests rather than as a provider 400.

OpenAI (strict ``json_schema``)
    Every property is required. An optional field that does not already accept ``null`` becomes
    a nullable union, and a ``null`` reply for it is read back as "absent" so Pydantic applies
    the field's default (a one-value literal such as a union tag is filled in instead, because
    discriminated unions need the tag present). Recursive models keep ``$ref`` into ``$defs``.

Anthropic (structured outputs)
    Only the model's required fields are required, plus every one-value literal (such as a
    union tag ``kind: Literal["stat"] = "stat"``): Pydantic's discriminated unions reject a reply
    without the tag even though it has a default, and requiring a constant costs the model
    nothing. Recursive references are stripped: a field (or union branch) that refers back to an
    enclosing model is dropped when it is optional; a required recursive field cannot be
    expressed and fails to compile.

A reply that fails validation gets exactly one repair turn (:func:`repair_request`, or
:func:`validate_with_repair` for the whole loop) that quotes the validation problems; after that
the caller falls back. Problems are rendered as ``location: message`` lines without the offending
input values, and validation errors are raised ``from None``, so model output does not end up in
tracebacks or logs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Literal, NoReturn, TypeGuard, cast

from pydantic import BaseModel, ValidationError

from blogwriter.ai.types import (
    Completion,
    FunctionTool,
    Message,
    ProviderName,
    Request,
    Tool,
    ToolCall,
)

__all__ = [
    "REPAIR_INSTRUCTIONS",
    "CompiledSchema",
    "OutputValidationError",
    "SchemaCompileError",
    "StructuredOutputError",
    "StructuredResult",
    "compile_schema",
    "repair_request",
    "validate_tool_call",
    "validate_with_repair",
]

MAX_REPORTED_PROBLEMS: Final = 20

REPAIR_INSTRUCTIONS: Final = (
    "Your previous reply did not match the required JSON schema.\n"
    "Problems:\n"
    "{problems}\n\n"
    "Reply again with only the corrected JSON object. Keep everything that was valid and fix "
    "only the problems listed."
)

# Keywords that shape the compiled schema.
_STRUCTURAL: Final = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "anyOf", "oneOf"}
    | {"allOf", "enum", "const", "$ref", "description"}
)
# Annotations dropped from the compiled schema.
_DROPPED: Final = frozenset(
    {"title", "default", "examples", "discriminator", "deprecated", "readOnly", "writeOnly"}
    | {"$schema", "$id", "$comment", "contentEncoding", "contentMediaType", "contentSchema"}
)
# Validation keywords stripped and restated as a description hint (order is the hint order).
_CONSTRAINTS: Final = (
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minimum",
    "exclusiveMinimum",
    "maximum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
)
_KNOWN: Final = _STRUCTURAL | _DROPPED | frozenset(_CONSTRAINTS)
_HINTS: Final = {
    "prefixItems": "tuples are not supported; use a model",
    "patternProperties": "pattern properties are not supported; use a model",
}
_SCALAR_TYPES: Final = frozenset({"string", "integer", "number", "boolean", "null"})
_DEF_REF = re.compile(r"#/\$defs/([^/]+)")
_FORMAT_NAME = re.compile(r"[^A-Za-z0-9_-]")


class SchemaCompileError(ValueError):
    """A Pydantic model cannot be expressed as a structured-output schema for a provider."""


class OutputValidationError(ValueError):
    """A reply does not validate against the original Pydantic model.

    ``problems`` are ``location: message`` lines without input values, safe to quote back to the
    model in a repair turn.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(f"{len(self.problems)} validation problem(s)")


class StructuredOutputError(Exception):
    """No valid structured reply: refused, incomplete, or still invalid after one repair turn."""

    def __init__(
        self, reason: Literal["refusal", "incomplete", "invalid"], problems: Sequence[str] = ()
    ) -> None:
        self.reason: Literal["refusal", "incomplete", "invalid"] = reason
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(f"structured output failed: {reason}")


# --------------------------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------------------------


def _is_obj(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_primitive(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not valid JSON")


# --------------------------------------------------------------------------------------------
# Decode plans (OpenAI): where a null reply means "field absent"
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ObjectPlan:
    keys: frozenset[str]
    literals: Mapping[str, frozenset[str]]
    null_means_absent: frozenset[str]
    props: Mapping[str, _Plan]


@dataclass(frozen=True, slots=True)
class _ArrayPlan:
    items: _Plan


@dataclass(frozen=True, slots=True)
class _UnionPlan:
    variants: tuple[_Plan, ...]


@dataclass(frozen=True, slots=True)
class _RefPlan:
    name: str


type _Plan = _ObjectPlan | _ArrayPlan | _UnionPlan | _RefPlan | None
type _Node = tuple[dict[str, object], _Plan]


def _resolve(
    plan: _Plan, defs: Mapping[str, _Plan]
) -> _ObjectPlan | _ArrayPlan | _UnionPlan | None:
    seen: set[str] = set()
    while isinstance(plan, _RefPlan):
        if plan.name in seen:
            return None
        seen.add(plan.name)
        plan = defs.get(plan.name)
    return plan


def _matches(value: dict[str, object], plan: _ObjectPlan) -> bool:
    """Whether an object reply fits a union branch (strict replies carry every key)."""
    if frozenset(value) != plan.keys:
        return False
    for name, allowed in plan.literals.items():
        member = value.get(name)
        if member is None and name in plan.null_means_absent:
            continue  # absent: Pydantic will use the field's default
        if _dumps(member) not in allowed:
            return False
    return True


def _prune(value: object, plan: _Plan, defs: Mapping[str, _Plan]) -> object:
    """Drop ``null`` members that stand for absent optional fields, recursively."""
    plan = _resolve(plan, defs)
    if plan is None:
        return value
    if isinstance(plan, _UnionPlan):
        if _is_obj(value):
            candidates = [
                resolved
                for variant in plan.variants
                if isinstance(resolved := _resolve(variant, defs), _ObjectPlan)
                and _matches(value, resolved)
            ]
        elif _is_list(value):
            candidates = [
                resolved
                for variant in plan.variants
                if isinstance(resolved := _resolve(variant, defs), _ArrayPlan)
            ]
        else:
            return value
        # Ambiguous or no match: leave the value alone and let Pydantic decide.
        return _prune(value, candidates[0], defs) if len(candidates) == 1 else value
    if isinstance(plan, _ArrayPlan):
        return [_prune(item, plan.items, defs) for item in value] if _is_list(value) else value
    if not _is_obj(value):
        return value
    pruned: dict[str, object] = {}
    for name, member in value.items():
        if member is None and name in plan.null_means_absent:
            allowed = plan.literals.get(name, frozenset[str]())
            if len(allowed) == 1:
                # A one-value literal (a union tag): its only valid value is its default, and
                # Pydantic's discriminated unions need the tag present.
                pruned[name] = json.loads(next(iter(allowed)))
            continue
        pruned[name] = _prune(member, plan.props.get(name), defs)
    return pruned


# --------------------------------------------------------------------------------------------
# Compiler
# --------------------------------------------------------------------------------------------


def _annotate(out: dict[str, object], description: str | None, hints: list[str]) -> None:
    if "$ref" in out:  # a $ref may not carry sibling keywords
        return
    parts = [description] if description else []
    if hints:
        parts.append("Constraints: " + ", ".join(hints))
    if parts:
        out["description"] = "\n".join(parts)


def _nullable(schema: dict[str, object]) -> dict[str, object]:
    inner = dict(schema)
    description = inner.pop("description", None)
    variants = inner["anyOf"] if set(inner) == {"anyOf"} and _is_list(inner["anyOf"]) else [inner]
    out: dict[str, object] = {"anyOf": [*variants, {"type": "null"}]}
    if description is not None:
        out["description"] = description
    return out


class _Compiler:
    def __init__(self, provider: ProviderName, defs: Mapping[str, object]) -> None:
        self.provider: ProviderName = provider
        self.defs = defs
        self.needed: list[str] = []

    def def_name(self, ref: object, path: str) -> str:
        match = _DEF_REF.fullmatch(ref) if isinstance(ref, str) else None
        if match is None or match.group(1) not in self.defs:
            raise SchemaCompileError(f"{path}: unsupported $ref {ref!r}")
        return match.group(1)

    def accepts_null(self, schema: object, seen: frozenset[str] = frozenset()) -> bool:
        if not _is_obj(schema):
            return False
        if "$ref" in schema:
            name = self.def_name(schema["$ref"], "$")
            return name not in seen and self.accepts_null(self.defs[name], seen | {name})
        kind = schema.get("type")
        if kind == "null" or (_is_list(kind) and "null" in kind):
            return True
        if "const" in schema and schema["const"] is None:
            return True
        enum = schema.get("enum")
        if _is_list(enum) and None in enum:
            return True
        for key in ("anyOf", "oneOf"):
            variants = schema.get(key)
            if _is_list(variants) and any(self.accepts_null(v, seen) for v in variants):
                return True
        all_of = schema.get("allOf")
        return _is_list(all_of) and bool(all_of) and all(self.accepts_null(v, seen) for v in all_of)

    def node(self, schema: object, path: str, stack: tuple[str, ...]) -> _Node | None:
        """Compile one schema node; ``None`` means it was stripped (Anthropic recursion)."""
        if not _is_obj(schema):
            raise SchemaCompileError(f"{path}: boolean schemas are not supported")
        source = dict(schema)
        if "$ref" in source:
            name = self.def_name(source.pop("$ref"), path)
            if name in stack:
                if self.provider == "anthropic":
                    return None
                if name not in self.needed:
                    self.needed.append(name)
                return {"$ref": f"#/$defs/{name}"}, _RefPlan(name)
            target = self.defs[name]
            if not _is_obj(target):
                raise SchemaCompileError(f"{path}: $defs entry {name!r} is not a schema")
            # Sibling keywords (such as a field description) override the definition's own.
            return self.node({**target, **source}, path, (*stack, name))

        unknown = sorted(set(source) - _KNOWN)
        if unknown:
            hint = "; ".join(_HINTS[key] for key in unknown if key in _HINTS)
            raise SchemaCompileError(
                f"{path}: unsupported keyword(s) {', '.join(unknown)}"
                + (f" ({hint})" if hint else "")
            )
        description = source.get("description")
        if description is not None and not isinstance(description, str):
            raise SchemaCompileError(f"{path}: description must be a string")
        hints = [f"{key}={_dumps(source[key])}" for key in _CONSTRAINTS if key in source]

        if "allOf" in source:
            all_of = source.pop("allOf")
            if not _is_list(all_of) or len(all_of) != 1 or not _is_obj(all_of[0]):
                raise SchemaCompileError(f"{path}: allOf is supported with exactly one schema")
            return self.node({**all_of[0], **source}, path, stack)

        if "anyOf" in source or "oneOf" in source:
            return self.union(source, path, stack, description, hints)

        kind = source.get("type")
        values = self.literal_values(source, path)
        plan: _Plan = None
        if values is not None:
            out: dict[str, object] = {"enum": values}
            if isinstance(kind, str) and kind in _SCALAR_TYPES:
                out["type"] = kind
        elif kind == "object":
            out, plan = self.object(source, path, stack)
        elif kind == "array":
            items = source.get("items")
            if items is None:
                raise SchemaCompileError(f"{path}: arrays need an item type")
            compiled = self.node(items, f"{path}[]", stack)
            if compiled is None:
                return None
            out = {"type": "array", "items": compiled[0]}
            plan = _ArrayPlan(compiled[1]) if compiled[1] is not None else None
        elif isinstance(kind, str) and kind in _SCALAR_TYPES:
            out = {"type": kind}
        elif kind is None:
            raise SchemaCompileError(f"{path}: untyped values (such as Any) are not supported")
        else:
            raise SchemaCompileError(f"{path}: unsupported type {kind!r}")
        _annotate(out, description, hints)
        return out, plan

    def literal_values(self, source: dict[str, object], path: str) -> list[object] | None:
        if "const" in source:
            values: list[object] = [source["const"]]
        elif "enum" in source:
            enum = source["enum"]
            if not _is_list(enum) or not enum:
                raise SchemaCompileError(f"{path}: enum must be a non-empty list")
            values = list(enum)
        else:
            return None
        if not all(_is_primitive(value) for value in values):
            raise SchemaCompileError(
                f"{path}: enum values must be strings, numbers, booleans or null"
            )
        return values

    def union(
        self,
        source: dict[str, object],
        path: str,
        stack: tuple[str, ...],
        description: str | None,
        hints: list[str],
    ) -> _Node | None:
        if "anyOf" in source and "oneOf" in source:
            raise SchemaCompileError(f"{path}: anyOf and oneOf together are not supported")
        if {"type", "properties", "items", "enum", "const"} & set(source):
            raise SchemaCompileError(f"{path}: a union may not also declare a type")
        variants = source.get("anyOf", source.get("oneOf"))
        if not _is_list(variants) or not variants:
            raise SchemaCompileError(f"{path}: a union needs at least one schema")
        schemas: list[dict[str, object]] = []
        plans: list[_Plan] = []
        for index, variant in enumerate(variants):
            compiled = self.node(variant, f"{path}|{index}", stack)
            if compiled is not None:
                schemas.append(compiled[0])
                plans.append(compiled[1])
        if not schemas:
            return None
        out: dict[str, object]
        plan: _Plan
        if len(schemas) == 1:
            out, plan = dict(schemas[0]), plans[0]
            own = out.get("description")
            if description is None and isinstance(own, str):
                description = own
        else:
            out = {"anyOf": schemas}
            plan = _UnionPlan(tuple(plans)) if any(p is not None for p in plans) else None
        _annotate(out, description, hints)
        return out, plan

    def object(self, source: dict[str, object], path: str, stack: tuple[str, ...]) -> _Node:
        properties = source.get("properties")
        if not _is_obj(properties):
            raise SchemaCompileError(
                f"{path}: objects need declared properties (dict-typed fields are not "
                "supported; use a model)"
            )
        if _is_obj(source.get("additionalProperties")):
            raise SchemaCompileError(f"{path}: dict-typed values are not supported; use a model")
        required_raw = source.get("required", [])
        if not _is_list(required_raw):
            raise SchemaCompileError(f"{path}: required must be a list")
        required = {name for name in required_raw if isinstance(name, str)}

        out_props: dict[str, object] = {}
        plans: dict[str, _Plan] = {}
        literals: dict[str, frozenset[str]] = {}
        constants: set[str] = set()
        null_means_absent: set[str] = set()
        for name, prop in properties.items():
            compiled = self.node(prop, f"{path}.{name}", stack)
            if compiled is None:
                if name in required:
                    raise SchemaCompileError(
                        f"{path}.{name}: a required field refers back to an enclosing model; "
                        f"{self.provider} structured outputs cannot be recursive"
                    )
                continue
            schema, plan = compiled
            enum = schema.get("enum")
            if _is_list(enum):
                literals[name] = frozenset(_dumps(value) for value in enum)
                if len(enum) == 1:
                    constants.add(name)
            if self.provider == "openai" and name not in required and not self.accepts_null(prop):
                schema = _nullable(schema)
                null_means_absent.add(name)
            out_props[name] = schema
            plans[name] = plan

        out: dict[str, object] = {
            "type": "object",
            "properties": out_props,
            "additionalProperties": False,
        }
        if self.provider == "openai":
            out["required"] = list(out_props)
        # Anthropic: a one-value literal (a union tag) is required too, because a discriminated
        # union cannot validate a reply that leaves its tag out.
        elif kept := [name for name in out_props if name in required or name in constants]:
            out["required"] = kept
        plan = _ObjectPlan(
            keys=frozenset(out_props),
            literals=literals,
            null_means_absent=frozenset(null_means_absent),
            props=plans,
        )
        return out, plan


@dataclass(frozen=True, slots=True, eq=False)
class CompiledSchema[ModelT: BaseModel]:
    """A model compiled for one provider. Build it with :func:`compile_schema`.

    ``name`` is the format name OpenAI requires (``[A-Za-z0-9_-]{1,64}``).
    """

    provider: ProviderName
    model: type[ModelT]
    name: str
    schema_text: str = field(repr=False)
    plan: _Plan = field(repr=False)
    def_plans: Mapping[str, _Plan] = field(repr=False)

    def json_schema(self) -> dict[str, object]:
        """A fresh copy of the compiled schema, ready to send."""
        return cast(dict[str, object], json.loads(self.schema_text))

    def validate_json(self, text: str) -> ModelT:
        """Validate a reply with the original model; raise :class:`OutputValidationError`."""
        try:
            data: object = json.loads(text, parse_constant=_reject_constant)
        except ValueError as exc:
            detail = (
                f"{exc.msg} at line {exc.lineno} column {exc.colno}"
                if isinstance(exc, json.JSONDecodeError)
                else str(exc)
            )
            raise OutputValidationError((f"$: the reply is not valid JSON ({detail})",)) from None
        if self.provider == "openai":
            data = _prune(data, self.plan, self.def_plans)
        try:
            return self.model.model_validate_json(json.dumps(data, ensure_ascii=False))
        except ValidationError as exc:
            raise OutputValidationError(tuple(_problems(exc))) from None


def _problems(exc: ValidationError) -> Iterator[str]:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    for error in errors[:MAX_REPORTED_PROBLEMS]:
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error["loc"]
        )
        yield f"{location}: {error['msg']}"
    if len(errors) > MAX_REPORTED_PROBLEMS:
        yield f"... and {len(errors) - MAX_REPORTED_PROBLEMS} more"


def _compile[ModelT: BaseModel](
    provider: ProviderName, model: type[ModelT]
) -> CompiledSchema[ModelT]:
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})
    if not _is_obj(defs):
        raise SchemaCompileError("$defs must be an object")
    compiler = _Compiler(provider, defs)
    compiled = compiler.node(raw, "$", ())
    if compiled is None or compiled[0].get("type") != "object":
        raise SchemaCompileError(
            f"{model.__name__}: the root of a structured output must be an object"
        )
    schema, plan = compiled
    def_plans: dict[str, _Plan] = {}
    if compiler.needed:
        emitted: dict[str, object] = {}
        while pending := [name for name in compiler.needed if name not in emitted]:
            for name in pending:
                compiled_def = compiler.node(defs[name], f"$defs.{name}", (name,))
                if (
                    compiled_def is None
                ):  # pragma: no cover - only Anthropic strips, and it never refers
                    raise SchemaCompileError(f"$defs.{name}: cannot be compiled")
                emitted[name], def_plans[name] = compiled_def
        schema["$defs"] = emitted
    name = _FORMAT_NAME.sub("_", model.__name__)[:64] or "output"
    return CompiledSchema(
        provider=provider,
        model=model,
        name=name,
        schema_text=json.dumps(schema, ensure_ascii=False),
        plan=plan,
        def_plans=def_plans,
    )


_compiled: dict[tuple[ProviderName, type[BaseModel]], CompiledSchema[BaseModel]] = {}


def compile_schema[ModelT: BaseModel](
    provider: ProviderName, model: type[ModelT]
) -> CompiledSchema[ModelT]:
    """Compile ``model`` for ``provider`` (cached per provider and model class).

    Raises :class:`SchemaCompileError` when the model cannot be expressed.
    """
    if provider not in ("openai", "anthropic"):  # pyright: ignore[reportUnnecessaryContains]
        raise ValueError(f"unknown provider {provider!r}")
    key: tuple[ProviderName, type[BaseModel]] = (provider, model)
    cached = _compiled.get(key)
    if cached is None:
        cached = _compile(provider, model)
        _compiled[key] = cached
    return cast(CompiledSchema[ModelT], cached)


# --------------------------------------------------------------------------------------------
# Repair
# --------------------------------------------------------------------------------------------


def repair_request(
    request: Request, completion: Completion, error: OutputValidationError
) -> Request:
    """The request for the single repair turn.

    It appends the invalid reply (with its native items, so the provider sees its own turn) and a
    user turn that quotes the validation problems.
    """
    if request.output_schema is None:
        raise ValueError("only structured requests can be repaired")
    problems = "\n".join(f"- {problem}" for problem in error.problems) or "- (unknown)"
    turns: list[Message] = []
    if completion.message is not None:
        turns.append(completion.message)
    elif completion.text.strip():
        turns.append(Message.assistant(completion.text))
    turns.append(Message.user(REPAIR_INSTRUCTIONS.format(problems=problems)))
    return replace(request, messages=(*request.messages, *turns))


@dataclass(frozen=True, slots=True)
class StructuredResult[ModelT: BaseModel]:
    """A validated structured reply and the completion that produced it."""

    value: ModelT
    completion: Completion
    repaired: bool


def _require_final(completion: Completion) -> None:
    if completion.finish_reason == "refusal":
        raise StructuredOutputError("refusal")
    if completion.finish_reason != "stop":
        raise StructuredOutputError("incomplete", (f"finish_reason={completion.finish_reason}",))


async def validate_with_repair[ModelT: BaseModel](
    compiled: CompiledSchema[ModelT],
    request: Request,
    completion: Completion,
    retry: Callable[[Request], Awaitable[Completion]],
) -> StructuredResult[ModelT]:
    """Validate ``completion``; if it is invalid, ask once for a corrected reply.

    ``retry`` sends the repair request to the same model (the caller keeps control of limiters
    and the ledger). Refused or incomplete completions are not repaired. Raises
    :class:`StructuredOutputError` when no valid reply results.
    """
    if request.output_schema is not compiled.model:
        raise ValueError("the compiled schema does not belong to this request")
    _require_final(completion)
    try:
        return StructuredResult(compiled.validate_json(completion.text), completion, repaired=False)
    except OutputValidationError as first:
        second_completion = await retry(repair_request(request, completion, first))
    _require_final(second_completion)
    try:
        value = compiled.validate_json(second_completion.text)
    except OutputValidationError as second:
        raise StructuredOutputError("invalid", second.problems) from None
    return StructuredResult(value, second_completion, repaired=True)


def validate_tool_call(provider: ProviderName, tools: Sequence[Tool], call: ToolCall) -> BaseModel:
    """Validate a tool call's arguments with the tool's Pydantic model."""
    for tool in tools:
        if isinstance(tool, FunctionTool) and tool.name == call.name:
            return compile_schema(provider, tool.parameters).validate_json(call.arguments)
    raise OutputValidationError((f"$: unknown tool {call.name!r}",))
