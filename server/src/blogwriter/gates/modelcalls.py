"""Model calls of the gates: versioned prompts, data fences, structured results, ``served_by``.

Prompts are package files (``gates/prompts/<name>.v<N>.md``) loaded with
:mod:`importlib.resources`; the version is recorded as ``promptVersion``. Untrusted text (the
draft, brief claims, fetched passages) goes inside ``<tag>...</tag>`` fences the system prompt
declares to be data; a closing fence inside the data is defused. Nothing here logs prompts,
completions or fetched text: only ids, codes and the exception type.

**Blocklist at call time** (contract 1.8, security rule 1.9.5): every request carries owner
material besides the article (stories, POVs, interview answers, the brief, the style guide,
pillar and format-pack settings) that the deterministic gate 3 never read, and the owner may
have added a term after saving it. :func:`run_structured` screens the owner-editable and
model-written text of each request against the current blocklist first; a hit (or a blocklist
never saved) sends nothing (``screened_out``) and the gate is ``skipped``, never a pass.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from typing import Final

from pydantic import BaseModel

from blogwriter.ai.ledger import BrakeEngaged, Origin
from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import ServedByRecord, review_only_reasons
from blogwriter.domain.confidentiality import blocklist_hit
from blogwriter.domain.jobflow import JobDeferred, defer_on_brake
from blogwriter.domain.ports import ModelRunner
from blogwriter.domain.settings import Blocklist

__all__ = ["ModelOutcome", "Prompt", "fence", "load_prompt", "run_structured"]

_logger: Final = logging.getLogger("blogwriter.gates.modelcalls")
register_log_fields("gate_step", "job_id", "prompt_version")


@dataclass(frozen=True, slots=True)
class Prompt:
    text: str
    version: str
    """``<name>.v<N>``, stored as ``promptVersion``."""


@functools.cache
def load_prompt(name: str, version: int) -> Prompt:
    label = f"{name}.v{version}"
    text = (
        resources.files("blogwriter.gates")
        .joinpath("prompts", f"{label}.md")
        .read_text(encoding="utf-8")
    )
    return Prompt(text=text.strip(), version=label)


def fence(tag: str, text: str) -> str:
    """``text`` inside a ``<tag>`` data fence; ``</tag`` inside it cannot close the fence."""
    safe = text.replace(f"</{tag}", f"<\\/{tag}")
    return f"<{tag}>\n{safe}\n</{tag}>"


@dataclass(frozen=True, slots=True)
class ModelOutcome[T: BaseModel]:
    """A structured call's result: the value (``None`` when refused or failed), what served
    it, and why it may be review-only."""

    value: T | None
    record: ServedByRecord | None
    refused: bool = False
    review_only: tuple[str, ...] = ()
    error: str | None = None
    screened_out: bool = False
    """The request matched the blocklist (or it was never saved): nothing was sent."""


async def run_structured[T: BaseModel](
    runner: ModelRunner,
    task: Task,
    *,
    prompt: Prompt,
    user: str,
    output: type[T],
    origin: Origin,
    job_id: str | None,
    step: str,
    now: datetime,
    blocklist: Blocklist,
    screened: Sequence[str] | None = None,
) -> ModelOutcome[T]:
    """One anonymized, tool-free structured call. ``screened`` (default: the whole ``user``
    message) is checked against ``blocklist`` first: a hit sends nothing (``screened_out``).
    ``BrakeEngaged`` becomes a deferral (``defer_on_brake``); other failures come back as
    ``error`` (the gate fails closed)."""
    texts = [user] if screened is None else list(screened)
    if blocklist_hit(texts, blocklist):
        _logger.warning("gates_request_blocklisted", extra={"gate_step": step, "job_id": job_id})
        return ModelOutcome(value=None, record=None, error="blocklist_hit", screened_out=True)
    request = Request(
        system=prompt.text,
        messages=(Message.user(user),),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=output,
    )
    try:
        with defer_on_brake(now):
            result = await runner.run(task, request, origin=origin, job_id=job_id)
    except (JobDeferred, BrakeEngaged):
        raise
    except Exception as exc:  # the gate fails closed; the type is logged, never the message
        _logger.warning(
            "gates_model_failed",
            extra={"gate_step": step, "job_id": job_id, **describe_exception(exc)},
        )
        return ModelOutcome(value=None, record=None, error="model_failed")
    refused = result.refused or bool(result.refusals)
    record = ServedByRecord.from_served_by(
        result.served_by,
        step=step,
        at=to_iso(now),
        refused=refused,
        prompt_version=prompt.version,
        cost_usd=float(result.cost_usd),
    )
    if result.refused:
        return ModelOutcome(value=None, record=record, refused=True, review_only=("refusal",))
    try:
        value = result.output(output)
    except TypeError:
        return ModelOutcome(value=None, record=record, error="no_output")
    return ModelOutcome(value=value, record=record, review_only=review_only_reasons(result))
