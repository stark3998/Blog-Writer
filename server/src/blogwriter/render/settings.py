"""Render settings (environment prefix ``BW_RENDER_``).

``BW_RENDER_CONCURRENCY``
    Render jobs at once in this process, 1 (default) or 2. Each job is one Playwright driver plus
    one Chromium. The Phase 0 spike measured about 0.8 GiB for one job and about 1.5 GiB for two,
    so 2 is allowed only after the container shows cgroup ``memory.peak`` <= about 1.5 GiB.
``BW_RENDER_CHROMIUM_SANDBOX``
    Chromium's own sandbox, on by default. Unrecognised values are an error, never "off": only
    set it to ``0`` after reviewing the compensating controls (see ``docs/v2/build/render.md``).
``BW_RENDER_MERMAID_JS``
    Path of ``@mermaid-js/tiny``'s ``mermaid.tiny.js``. Default: ``server/vendor/node_modules/...``
    in this checkout (``npm ci --ignore-scripts`` in ``server/vendor``). Images copy the file and
    set this. Its SHA-256 must equal :data:`MERMAID_TINY_SHA256`, or start-up fails.
``BW_RENDER_ACTION_TIMEOUT_MS``, ``BW_RENDER_JOB_TIMEOUT_S``, ``BW_RENDER_MERMAID_TIMEOUT_S``
    Per Playwright action; per job (driver start and browser launch included); per diagram.
``BW_RENDER_JS_HEAP_LIMIT_MB``
    V8 old-space cap for every renderer (default 256). Bounds a runaway Mermaid layout.
``BW_RENDER_MAX_PDF_BYTES``
    At most 10 MB (the plan's carousel limit); may only be lowered.
``BW_RENDER_MEMORY_SAMPLE_INTERVAL_S``
    Process-tree memory sampling during a job (default 0.1 s; 0 turns sampling off and keeps the
    cgroup figures).

V8 always runs ``--jitless`` (no JIT, no WebAssembly); that is not configurable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.render.pdf import MAX_PDF_BYTES

__all__ = [
    "DEFAULT_MERMAID_JS",
    "MERMAID_TINY_SHA256",
    "MERMAID_VERSION",
    "RenderSettings",
]

#: Pinned Mermaid build. Bump together with ``server/vendor/package.json`` and
#: ``package-lock.json``; the bundle's SHA-256 must be re-pinned deliberately.
MERMAID_VERSION: Final = "12.3.0"
MERMAID_TINY_SHA256: Final = "c41f5236091b83f524c4940f97a16b2b14a76f21ff84aea4fd6b66421d8a2a9a"

#: ``server/`` in this repository (``render`` -> ``blogwriter`` -> ``src`` -> ``server``).
_SERVER_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_MERMAID_JS: Final = (
    _SERVER_ROOT / "vendor" / "node_modules" / "@mermaid-js" / "tiny" / "dist" / "mermaid.tiny.js"
)


class RenderSettings(BaseSettings):
    """Settings for ``blogwriter.render``. Defaults are the production values."""

    model_config = SettingsConfigDict(
        env_prefix="BW_RENDER_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    concurrency: int = Field(default=1, ge=1, le=2)
    """Render jobs at once (a process-wide semaphore)."""
    chromium_sandbox: bool = True
    """Chromium's own sandbox. ACA may not grant the user namespaces it needs."""
    mermaid_js: Path = DEFAULT_MERMAID_JS
    """The pinned ``mermaid.tiny.js``."""
    action_timeout_ms: int = Field(default=20_000, ge=1_000, le=120_000)
    """Per Playwright action (set_content, pdf, screenshot, evaluate) and browser launch."""
    job_timeout_s: float = Field(default=90.0, gt=0, le=600)
    """Wall-clock cap for one job: driver start, browser launch and every page."""
    mermaid_timeout_s: float = Field(default=15.0, gt=0, le=120)
    """Per diagram."""
    js_heap_limit_mb: int = Field(default=256, ge=64, le=1024)
    """V8 old-space cap for every renderer process (MiB)."""
    max_pdf_bytes: int = Field(default=MAX_PDF_BYTES, ge=1, le=MAX_PDF_BYTES)
    memory_sample_interval_s: float = Field(default=0.1, ge=0, le=5)
    stale_home_max_age_s: float = Field(default=3600.0, ge=60, le=7 * 86_400)
    """Per-job temp homes older than this are removed at start-up (left behind by SIGKILL)."""

    @field_validator("chromium_sandbox", mode="before")
    @classmethod
    def _strip_flag(cls, value: object) -> object:
        # " off " is "off" and blank is the default (on); anything unrecognised still fails
        # validation, so a typo can never switch the sandbox off.
        if isinstance(value, str):
            return value.strip() or True
        return value
