"""Pipeline settings (environment prefix ``BW_PIPELINE_``).

``BW_PIPELINE_MAX_REVISE_LOOPS`` (2)
    Rounds of CRITIQUE -> REVISE the ``critique_revise`` step may run on one draft. The plan caps
    it at 2 ("revise (at most 2 loops)"), so larger values are refused; 0 turns the loop into a
    single critique whose notes are kept for the owner.
"""

from __future__ import annotations

import functools
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["MAX_REVISE_LOOPS", "PipelineSettings", "pipeline_settings"]

MAX_REVISE_LOOPS: Final = 2
"""The plan's hard cap on revise loops."""


class PipelineSettings(BaseSettings):
    """Settings for ``blogwriter.pipeline``. The defaults are the plan's."""

    model_config = SettingsConfigDict(
        env_prefix="BW_PIPELINE_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    max_revise_loops: int = Field(default=MAX_REVISE_LOOPS, ge=0, le=MAX_REVISE_LOOPS)


@functools.cache
def pipeline_settings() -> PipelineSettings:
    """The process-wide settings, read from the environment once (tests: ``cache_clear()``)."""
    return PipelineSettings()
