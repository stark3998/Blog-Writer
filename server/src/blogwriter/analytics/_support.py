"""Small helpers shared by the analytics job steps (reading step outputs and job inputs)."""

from __future__ import annotations

from typing import Final

from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.domain.jobtypes import JobInput
from blogwriter.jobs import StepContext

__all__ = ["MAX_ENTITY_EVENTS", "as_object", "as_string", "as_strings", "job_origin"]

MAX_ENTITY_EVENTS: Final = 25
"""``entity.changed`` events one step emits at most (the web refetches lists anyway)."""


def as_object(value: JsonValue) -> dict[str, JsonValue]:
    """``value`` if it is a JSON object, else an empty one."""
    return dict(value) if isinstance(value, dict) else {}


def as_string(value: JsonValue) -> str | None:
    """``value`` if it is a non-empty string."""
    return value if isinstance(value, str) and value else None


def as_strings(value: JsonValue) -> list[str]:
    """The non-empty strings of a JSON array (anything else: empty)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def job_origin(ctx: StepContext) -> Origin:
    """Who started the job (``background`` when the input does not say)."""
    try:
        return JobInput.from_json(ctx.input).origin
    except ValueError:
        return "background"
