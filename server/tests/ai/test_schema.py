"""Golden and rule tests for the per-provider structured-output schema compiler.

Plan: "AI layer" > Structured output (Anthropic: strip unsupported constraints; OpenAI strict:
required plus nullable fields, additionalProperties:false; re-validate with Pydantic, one repair
turn, then fall back) and "Verification" > Golden tests (schema compilation on both providers).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeGuard

import jsonschema
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaViolation
from pydantic import BaseModel, ConfigDict, Field, RootModel, create_model

from blogwriter.ai.schema import (
    REPAIR_INSTRUCTIONS,
    CompiledSchema,
    OutputValidationError,
    SchemaCompileError,
    StructuredOutputError,
    compile_schema,
    repair_request,
    validate_tool_call,
    validate_with_repair,
)
from blogwriter.ai.types import (
    Completion,
    FinishReason,
    FunctionTool,
    Message,
    NativeItems,
    ProviderName,
    Refusal,
    Request,
    Sensitivity,
    TextPart,
    ToolCall,
    Usage,
)

PROVIDERS: tuple[ProviderName, ...] = ("openai", "anthropic")
STRIPPED = frozenset(
    {
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "format",
        "uniqueItems",
        "title",
        "default",
        "discriminator",
        "oneOf",
        "const",
    }
)


# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------


class GoldenItem(BaseModel):
    """An item."""

    name: str = Field(description="Display name", max_length=40)
    tags: list[str] = []
    note: str | None = None
    kind: Literal["a", "b"] = "a"


class Severity(StrEnum):
    LOW = "low"
    HIGH = "high"


class Source(BaseModel):
    """A cited source."""

    url: str = Field(description="Canonical URL", max_length=2048, pattern=r"^https://")
    title: str | None = None
    published: date | None = None


class Stat(BaseModel):
    kind: Literal["stat"] = "stat"
    value: float = Field(ge=0)
    unit: str


class Quote(BaseModel):
    kind: Literal["quote"] = "quote"
    text: str = Field(max_length=200)
    speaker: str


class Finding(BaseModel):
    claim: str = Field(min_length=1)
    severity: Severity = Severity.LOW
    evidence: Annotated[Stat | Quote, Field(discriminator="kind")]
    sources: list[Source] = Field(default_factory=list[Source], max_length=5)
    confidence: float = Field(default=0.5, ge=0, le=1)


class Brief(BaseModel):
    """A research brief."""

    summary: str
    findings: list[Finding] = Field(min_length=1)
    open_questions: list[str] = []
    note: str | None = None
    checked_at: datetime | None = None
    labels: set[str] = set()


class Section(BaseModel):
    heading: str
    children: list[Section] = []
    parent: Section | None = None


class Outline(BaseModel):
    title: str
    sections: list[Section]


class Chain(BaseModel):
    value: int
    next: Chain


class Described(BaseModel):
    lead: GoldenItem = Field(description="The lead item")
    format: str = Field(description="A property named like a keyword")
    pattern: int = 0


class Extra(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str


class Aliased(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    display_name: str = Field(alias="displayName")
    count: int = Field(default=1, alias="itemCount")


class WithDict(BaseModel):
    counts: dict[str, int]


class WithAny(BaseModel):
    blob: Any


class WithTuple(BaseModel):
    pair: tuple[int, str]


class Numbers(BaseModel):
    values: list[int]


class Same1(BaseModel):
    x: int = 1


class Same2(BaseModel):
    x: int = 2


class Ambiguous(BaseModel):
    item: Same1 | Same2


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def is_obj(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def at(node: object, *path: str | int) -> object:
    """Walk a JSON document; fails the test if the path does not exist."""
    for key in path:
        if isinstance(key, int):
            assert is_list(node)
            node = node[key]
        else:
            assert is_obj(node)
            node = node[key]
    return node


def obj(node: object, *path: str | int) -> dict[str, object]:
    found = at(node, *path)
    assert is_obj(found)
    return found


def schemas(node: object) -> Iterator[dict[str, object]]:
    """Every schema node (property names are not keywords, so they are not visited as nodes)."""
    if not is_obj(node):
        return
    yield node
    for container in ("properties", "$defs"):
        children = node.get(container)
        if is_obj(children):
            for child in children.values():
                yield from schemas(child)
    yield from schemas(node.get("items"))
    for key in ("anyOf", "allOf"):
        variants = node.get(key)
        if is_list(variants):
            for child in variants:
                yield from schemas(child)


def objects(node: object) -> list[dict[str, object]]:
    return [schema for schema in schemas(node) if schema.get("type") == "object"]


def check(schema: dict[str, object], instance: object) -> None:
    """The compiled schema is valid JSON Schema and accepts ``instance``."""
    Draft202012Validator.check_schema(schema)
    jsonschema.validate(instance, schema, cls=Draft202012Validator)


def brief_instance() -> Brief:
    return Brief(
        summary="Passkeys are replacing passwords.",
        findings=[
            Finding(
                claim="Adoption doubled",
                severity=Severity.HIGH,
                evidence=Stat(value=2.0, unit="x"),
                sources=[Source(url="https://example.com/report", published=date(2026, 11, 1))],
            ),
            Finding(claim="Users like them", evidence=Quote(text="Finally.", speaker="A user")),
        ],
        checked_at=datetime(2026, 11, 26, 10, 0),
        labels={"identity"},
    )


def completion(
    text: str,
    finish: FinishReason = "stop",
    *,
    message: Message | None = None,
    refusal: Refusal | None = None,
) -> Completion:
    return Completion(
        text=text,
        finish_reason=finish,
        usage=Usage(input=10, output=5),
        model="m",
        response_id="r",
        refusal=refusal,
        message=message,
    )


def structured_request(model: type[BaseModel] = GoldenItem) -> Request:
    return Request(
        system="Return JSON.",
        messages=(Message.user("Describe one item."),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=model,
    )


# --------------------------------------------------------------------------------------------
# Golden schemas
# --------------------------------------------------------------------------------------------


def test_golden_openai_schema() -> None:
    compiled = compile_schema("openai", GoldenItem)
    assert compiled.name == "GoldenItem"
    assert compiled.json_schema() == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Display name\nConstraints: maxLength=40"},
            "tags": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "kind": {"anyOf": [{"enum": ["a", "b"], "type": "string"}, {"type": "null"}]},
        },
        "additionalProperties": False,
        "required": ["name", "tags", "note", "kind"],
        "description": "An item.",
    }


def test_golden_anthropic_schema() -> None:
    compiled = compile_schema("anthropic", GoldenItem)
    assert compiled.json_schema() == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Display name\nConstraints: maxLength=40"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "kind": {"enum": ["a", "b"], "type": "string"},
        },
        "additionalProperties": False,
        "required": ["name"],
        "description": "An item.",
    }


# --------------------------------------------------------------------------------------------
# Compilation rules
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_object_forbids_additional_properties(provider: ProviderName) -> None:
    for model in (Brief, Outline, Described, Extra):
        found = objects(compile_schema(provider, model).json_schema())
        assert found
        assert all(schema["additionalProperties"] is False for schema in found)


def test_openai_requires_every_property_and_makes_optional_ones_nullable() -> None:
    schema = compile_schema("openai", Brief).json_schema()
    for node in objects(schema):
        assert node["required"] == list(obj(node, "properties"))
    top = obj(schema, "properties")
    # Required fields stay non-nullable; optional ones accept null exactly once.
    assert top["summary"] == {"type": "string"}
    assert top["open_questions"] == {
        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]
    }
    assert top["note"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert top["checked_at"] == {
        "anyOf": [
            {"type": "string", "description": 'Constraints: format="date-time"'},
            {"type": "null"},
        ]
    }


def test_anthropic_keeps_only_the_models_required_fields() -> None:
    schema = compile_schema("anthropic", Brief).json_schema()
    assert schema["required"] == ["summary", "findings"]
    assert at(schema, "properties", "findings", "items", "required") == ["claim", "evidence"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_unsupported_keywords_are_stripped_and_restated(provider: ProviderName) -> None:
    schema = compile_schema(provider, Brief).json_schema()
    for node in schemas(schema):
        assert not (STRIPPED & set(node)), node
    text = json.dumps(schema)
    assert 'Constraints: maxLength=2048, pattern=\\"^https://\\"' in text
    assert "Constraints: minLength=1" in text
    assert "Constraints: minimum=0, maximum=1" in text
    assert "Constraints: minItems=1" in text
    assert "Constraints: uniqueItems=true" in text
    assert 'Constraints: format=\\"date\\"' in text


@pytest.mark.parametrize("provider", PROVIDERS)
def test_refs_are_inlined_and_unions_use_any_of(provider: ProviderName) -> None:
    schema = compile_schema(provider, Brief).json_schema()
    assert "$defs" not in schema
    assert all("$ref" not in node for node in schemas(schema))
    evidence = obj(schema, "properties", "findings", "items", "properties", "evidence")
    assert set(evidence) == {"anyOf"}
    stat_kind = at(evidence, "anyOf", 0, "properties", "kind")
    quote_kind = at(evidence, "anyOf", 1, "properties", "kind")
    if provider == "openai":  # a defaulted tag is optional, so it is nullable there
        assert stat_kind == {"anyOf": [{"enum": ["stat"], "type": "string"}, {"type": "null"}]}
        assert quote_kind == {"anyOf": [{"enum": ["quote"], "type": "string"}, {"type": "null"}]}
    else:
        assert stat_kind == {"enum": ["stat"], "type": "string"}
        assert quote_kind == {"enum": ["quote"], "type": "string"}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_descriptions_survive_and_ref_siblings_win(provider: ProviderName) -> None:
    schema = compile_schema(provider, Described).json_schema()
    properties = obj(schema, "properties")
    lead = obj(properties, "lead")
    assert lead["type"] == "object"
    assert lead["description"] == "The lead item"  # the field's description beats the docstring
    # Properties named like keywords are data, not keywords.
    assert set(properties) == {"lead", "format", "pattern"}
    assert properties["format"] == {
        "type": "string",
        "description": "A property named like a keyword",
    }


@pytest.mark.parametrize("provider", PROVIDERS)
def test_aliases_name_the_properties(provider: ProviderName) -> None:
    compiled = compile_schema(provider, Aliased)
    assert set(obj(compiled.json_schema(), "properties")) == {"displayName", "itemCount"}
    value = compiled.validate_json(
        '{"displayName": "x", "itemCount": null}'
        if provider == "openai"
        else '{"displayName": "x"}'
    )
    assert value == Aliased(displayName="x", itemCount=1)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_extra_allow_models_still_forbid_additional_properties(provider: ProviderName) -> None:
    assert compile_schema(provider, Extra).json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    ("model", "message"),
    [
        (WithDict, "dict-typed"),
        (WithAny, "untyped values"),
        (WithTuple, "tuples are not supported"),
        (RootModel[list[int]], "root of a structured output must be an object"),
    ],
)
def test_inexpressible_models_fail_to_compile(
    provider: ProviderName, model: type[BaseModel], message: str
) -> None:
    with pytest.raises(SchemaCompileError, match=message):
        compile_schema(provider, model)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_unknown_keywords_fail_closed(provider: ProviderName) -> None:
    custom = create_model("Custom", name=(str, Field(json_schema_extra={"x-custom": 1})))
    with pytest.raises(SchemaCompileError, match="unsupported keyword"):
        compile_schema(provider, custom)
    negated = create_model(
        "Negated", name=(str, Field(json_schema_extra={"not": {"type": "null"}}))
    )
    with pytest.raises(SchemaCompileError, match="not"):
        compile_schema(provider, negated)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_single_all_of_is_merged_and_larger_ones_fail(provider: ProviderName) -> None:
    merged = create_model(
        "Merged", name=(str, Field(json_schema_extra={"allOf": [{"description": "merged"}]}))
    )
    schema = compile_schema(provider, merged).json_schema()
    assert schema["properties"] == {"name": {"type": "string", "description": "merged"}}
    double = create_model(
        "Double",
        name=(str, Field(json_schema_extra={"allOf": [{"type": "string"}, {"minLength": 1}]})),
    )
    with pytest.raises(SchemaCompileError, match="exactly one schema"):
        compile_schema(provider, double)


def test_openai_keeps_recursion_through_defs() -> None:
    schema = compile_schema("openai", Outline).json_schema()
    assert set(obj(schema, "$defs")) == {"Section"}
    section = obj(schema, "$defs", "Section", "properties")
    assert section["children"] == {
        "anyOf": [{"type": "array", "items": {"$ref": "#/$defs/Section"}}, {"type": "null"}]
    }
    assert section["parent"] == {"anyOf": [{"$ref": "#/$defs/Section"}, {"type": "null"}]}
    deep: dict[str, object] = {
        "title": "T",
        "sections": [
            {
                "heading": "A",
                "parent": None,
                "children": [{"heading": "A.1", "parent": None, "children": []}],
            }
        ],
    }
    check(schema, deep)
    value = compile_schema("openai", Outline).validate_json(json.dumps(deep))
    assert value.sections[0].children[0].heading == "A.1"


def test_openai_compiles_required_recursion_and_recursive_roots() -> None:
    chain = compile_schema("openai", Chain).json_schema()
    assert at(chain, "properties", "next") == {"$ref": "#/$defs/Chain"}
    section = compile_schema("openai", Section).json_schema()
    assert section["type"] == "object"
    assert set(obj(section, "$defs")) == {"Section"}


def test_anthropic_strips_optional_recursion() -> None:
    schema = compile_schema("anthropic", Outline).json_schema()
    assert all("$ref" not in node and "$defs" not in node for node in schemas(schema))
    assert at(schema, "properties", "sections", "items") == {
        "type": "object",
        "properties": {"heading": {"type": "string"}, "parent": {"type": "null"}},
        "additionalProperties": False,
        "required": ["heading"],
    }
    value = compile_schema("anthropic", Outline).validate_json(
        '{"title": "T", "sections": [{"heading": "A"}]}'
    )
    assert value.sections[0].children == []


def test_anthropic_rejects_required_recursion() -> None:
    with pytest.raises(SchemaCompileError, match="cannot be recursive"):
        compile_schema("anthropic", Chain)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_compiled_schemas_are_valid_and_accept_real_instances(provider: ProviderName) -> None:
    compiled = compile_schema(provider, Brief)
    instance = brief_instance().model_dump(mode="json", by_alias=True)
    check(compiled.json_schema(), instance)
    assert compiled.validate_json(json.dumps(instance)) == brief_instance()


def test_only_anthropic_accepts_replies_that_omit_optional_fields() -> None:
    evidence = {"kind": "stat", "value": 1, "unit": "u"}
    minimal = {"summary": "s", "findings": [{"claim": "c", "evidence": evidence}]}
    anthropic = compile_schema("anthropic", Brief)
    check(anthropic.json_schema(), minimal)
    # What the schema lets Claude send must also pass the model, defaults filling the rest.
    assert anthropic.validate_json(json.dumps(minimal)) == Brief(
        summary="s", findings=[Finding(claim="c", evidence=Stat(value=1, unit="u"))]
    )
    with pytest.raises(SchemaViolation):
        check(compile_schema("openai", Brief).json_schema(), minimal)


def test_anthropic_requires_union_tags_that_have_defaults() -> None:
    # A discriminated union rejects a reply without its tag even though the tag has a default,
    # so a schema that let Claude leave it out would waste the repair turn on valid-looking
    # output. A one-value literal is required instead; it costs the model nothing.
    schema = compile_schema("anthropic", Brief).json_schema()
    branches = at(schema, "properties", "findings", "items", "properties", "evidence", "anyOf")
    assert is_list(branches)
    assert [at(branch, "required") for branch in branches] == [
        ["kind", "value", "unit"],
        ["kind", "text", "speaker"],
    ]
    untagged = {"summary": "s", "findings": [{"claim": "c", "evidence": {"value": 1, "unit": "u"}}]}
    with pytest.raises(SchemaViolation):
        check(schema, untagged)
    with pytest.raises(OutputValidationError) as invalid:
        compile_schema("anthropic", Brief).validate_json(json.dumps(untagged))
    assert invalid.value.problems == (
        "$.findings[0].evidence: Unable to extract tag using discriminator 'kind'",
    )
    # Multi-value literals with a default stay optional (the golden schema keeps `kind` out).
    assert compile_schema("anthropic", GoldenItem).json_schema()["required"] == ["name"]


def test_compilation_is_cached_and_copies_are_fresh() -> None:
    first = compile_schema("openai", GoldenItem)
    assert compile_schema("openai", GoldenItem) is first
    assert compile_schema("anthropic", GoldenItem) is not first
    copy = first.json_schema()
    copy["properties"] = {}
    assert first.json_schema()["properties"] != {}


def test_format_names_are_sanitized() -> None:
    weird = create_model("My Model!", x=(int, ...))
    assert compile_schema("openai", weird).name == "My_Model_"

    class Page[T](BaseModel):
        items: list[T]

    assert compile_schema("openai", Page[GoldenItem]).name == "Page_GoldenItem_"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        compile_schema("bedrock", GoldenItem)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------------------------
# Re-validation with the original model
# --------------------------------------------------------------------------------------------


def test_openai_null_means_absent_for_optional_fields() -> None:
    value = compile_schema("openai", GoldenItem).validate_json(
        '{"name": "x", "tags": null, "note": null, "kind": null}'
    )
    assert value == GoldenItem(name="x")
    assert value.tags == []
    assert value.kind == "a"


def test_openai_keeps_explicit_null_for_nullable_fields() -> None:
    class Nullable(BaseModel):
        note: str | None = "default"

    value = compile_schema("openai", Nullable).validate_json('{"note": null}')
    assert value.note is None


def test_null_for_a_required_field_is_still_an_error() -> None:
    with pytest.raises(OutputValidationError) as caught:
        compile_schema("openai", GoldenItem).validate_json(
            '{"name": null, "tags": null, "note": null, "kind": null}'
        )
    assert caught.value.problems == ("$.name: Input should be a valid string",)


def test_anthropic_replies_are_validated_as_sent() -> None:
    compiled = compile_schema("anthropic", GoldenItem)
    assert compiled.validate_json('{"name": "x"}') == GoldenItem(name="x")
    with pytest.raises(OutputValidationError) as caught:
        compiled.validate_json('{"name": "x", "tags": null}')
    assert caught.value.problems == ("$.tags: Input should be a valid array",)


def test_openai_pruning_reaches_list_items_and_union_branches() -> None:
    reply = {
        "summary": "s",
        "findings": [
            {
                "claim": "c",
                "severity": None,
                "evidence": {"kind": None, "value": 3, "unit": "%"},
                "sources": [{"url": "https://example.com", "title": None, "published": None}],
                "confidence": None,
            },
            {
                "claim": "d",
                "severity": "high",
                "evidence": {"kind": "quote", "text": "Yes.", "speaker": "B"},
                "sources": None,
                "confidence": 0.9,
            },
        ],
        "open_questions": None,
        "note": None,
        "checked_at": None,
        "labels": None,
    }
    value = compile_schema("openai", Brief).validate_json(json.dumps(reply))
    first, second = value.findings
    assert first.severity is Severity.LOW
    assert first.confidence == 0.5
    assert first.evidence == Stat(value=3, unit="%")
    assert first.sources == [Source(url="https://example.com")]
    assert second.evidence == Quote(text="Yes.", speaker="B")
    assert second.sources == []
    assert value.labels == set()


def test_ambiguous_union_branches_are_left_to_pydantic() -> None:
    compiled = compile_schema("openai", Ambiguous)
    assert compiled.validate_json('{"item": {"x": 5}}').item == Same1(x=5)
    with pytest.raises(OutputValidationError):
        compiled.validate_json('{"item": {"x": null}}')


@pytest.mark.parametrize("provider", PROVIDERS)
def test_constraints_the_provider_cannot_enforce_are_checked(provider: ProviderName) -> None:
    compiled = compile_schema(provider, GoldenItem)
    with pytest.raises(OutputValidationError) as caught:
        compiled.validate_json(
            json.dumps({"name": "n" * 41, "tags": [], "note": None, "kind": "a"})
        )
    assert caught.value.problems == ("$.name: String should have at most 40 characters",)


def test_problems_never_quote_the_reply() -> None:
    secret = "SECRET-interview-text-" * 3
    with pytest.raises(OutputValidationError) as caught:
        compile_schema("openai", GoldenItem).validate_json(
            json.dumps({"name": secret, "tags": [secret], "note": None, "kind": secret})
        )
    error = caught.value
    assert "SECRET" not in str(error)
    assert all("SECRET" not in problem for problem in error.problems)
    assert error.__cause__ is None
    assert error.__suppress_context__


def test_invalid_json_is_a_problem_not_a_crash() -> None:
    compiled = compile_schema("openai", GoldenItem)
    with pytest.raises(OutputValidationError) as caught:
        compiled.validate_json("Sure! Here is the JSON:")
    assert caught.value.problems == (
        "$: the reply is not valid JSON (Expecting value at line 1 column 1)",
    )
    with pytest.raises(OutputValidationError) as caught:
        compiled.validate_json('{"name": NaN}')
    assert caught.value.problems == ("$: the reply is not valid JSON (NaN is not valid JSON)",)


def test_problem_lists_are_capped() -> None:
    with pytest.raises(OutputValidationError) as caught:
        compile_schema("openai", Numbers).validate_json(json.dumps({"values": ["x"] * 25}))
    problems = caught.value.problems
    assert len(problems) == 21
    assert (
        problems[0]
        == "$.values[0]: Input should be a valid integer, unable to parse string as an integer"
    )
    assert problems[-1] == "... and 5 more"


# --------------------------------------------------------------------------------------------
# Repair
# --------------------------------------------------------------------------------------------


def test_repair_request_quotes_the_problems_after_the_invalid_reply() -> None:
    request = structured_request()
    native = Message("assistant", (TextPart('{"name": 1}'),), NativeItems("openai", ({"id": "x"},)))
    error = OutputValidationError(("$.name: Input should be a valid string",))
    repaired = repair_request(request, completion('{"name": 1}', message=native), error)
    assert repaired.messages[:-2] == request.messages
    assert repaired.messages[-2] is native
    assert repaired.messages[-1] == Message.user(
        REPAIR_INSTRUCTIONS.format(problems="- $.name: Input should be a valid string")
    )
    assert repaired.output_schema is GoldenItem
    assert repaired.sensitivity is request.sensitivity


def test_repair_request_without_a_native_turn_replays_the_text() -> None:
    error = OutputValidationError(("$: x",))
    repaired = repair_request(structured_request(), completion("not json"), error)
    assert repaired.messages[-2] == Message.assistant("not json")
    blank = repair_request(structured_request(), completion("  "), error)
    assert [message.role for message in blank.messages] == ["user", "user"]


def test_only_structured_requests_are_repaired() -> None:
    plain = Request(system="", messages=(Message.user("x"),), sensitivity=Sensitivity.PUBLIC)
    with pytest.raises(ValueError, match="only structured"):
        repair_request(plain, completion("x"), OutputValidationError(()))


class Retry:
    def __init__(self, *replies: Completion) -> None:
        self.replies = list(replies)
        self.requests: list[Request] = []

    async def __call__(self, request: Request) -> Completion:
        self.requests.append(request)
        return self.replies.pop(0)


async def test_valid_replies_need_no_repair() -> None:
    retry = Retry()
    result = await validate_with_repair(
        compile_schema("openai", GoldenItem),
        structured_request(),
        completion('{"name": "x", "tags": null, "note": null, "kind": "b"}'),
        retry,
    )
    assert result.value == GoldenItem(name="x", kind="b")
    assert result.repaired is False
    assert retry.requests == []


async def test_one_repair_turn_then_success() -> None:
    fixed = completion('{"name": "x", "tags": [], "note": null, "kind": "a"}')
    retry = Retry(fixed)
    result = await validate_with_repair(
        compile_schema("openai", GoldenItem),
        structured_request(),
        completion('{"name": 7, "tags": [], "note": null, "kind": "a"}'),
        retry,
    )
    assert result.repaired is True
    assert result.completion is fixed
    assert result.value == GoldenItem(name="x")
    (sent,) = retry.requests
    assert "$.name: Input should be a valid string" in sent.messages[-1].text


async def test_a_second_invalid_reply_fails_with_its_problems() -> None:
    retry = Retry(completion('{"name": 8}'))
    with pytest.raises(StructuredOutputError) as caught:
        await validate_with_repair(
            compile_schema("anthropic", GoldenItem),
            structured_request(),
            completion('{"name": 7}'),
            retry,
        )
    assert caught.value.reason == "invalid"
    assert caught.value.problems == ("$.name: Input should be a valid string",)
    assert len(retry.requests) == 1


async def test_refusals_and_truncations_are_not_repaired() -> None:
    compiled = compile_schema("openai", GoldenItem)
    retry = Retry()
    refused = completion("", "refusal", refusal=Refusal("cyber", None))
    with pytest.raises(StructuredOutputError) as caught:
        await validate_with_repair(compiled, structured_request(), refused, retry)
    assert caught.value.reason == "refusal"
    with pytest.raises(StructuredOutputError) as caught:
        await validate_with_repair(
            compiled, structured_request(), completion('{"na', "length"), retry
        )
    assert caught.value.reason == "incomplete"
    assert caught.value.problems == ("finish_reason=length",)
    assert retry.requests == []


async def test_a_truncated_repair_is_incomplete() -> None:
    retry = Retry(completion('{"name"', "length"))
    with pytest.raises(StructuredOutputError) as caught:
        await validate_with_repair(
            compile_schema("openai", GoldenItem), structured_request(), completion("{}"), retry
        )
    assert caught.value.reason == "incomplete"


async def test_the_schema_must_belong_to_the_request() -> None:
    compiled: CompiledSchema[Brief] = compile_schema("openai", Brief)
    with pytest.raises(ValueError, match="does not belong"):
        await validate_with_repair(compiled, structured_request(), completion("{}"), Retry())


def test_tool_calls_are_validated_with_the_tool_model() -> None:
    class FetchArgs(BaseModel):
        url: str
        max_chars: int = 3000

    tools = (FunctionTool("fetch_page", "Fetch a page.", FetchArgs),)
    value = validate_tool_call(
        "openai",
        tools,
        ToolCall("c1", "fetch_page", '{"url": "https://a.example", "max_chars": null}'),
    )
    assert value == FetchArgs(url="https://a.example")
    with pytest.raises(OutputValidationError, match="1 validation problem"):
        validate_tool_call("anthropic", tools, ToolCall("c2", "fetch_page", "{}"))
    with pytest.raises(OutputValidationError) as caught:
        validate_tool_call("openai", tools, ToolCall("c3", "delete_everything", "{}"))
    assert caught.value.problems == ("$: unknown tool 'delete_everything'",)
