"""Render settings. Defaults are the production values; every field can be overridden from env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

#: Pinned Mermaid builds. Bump together with vendor/package.json + package-lock.json (Renovate).
#: ``@mermaid-js/tiny`` is the same release without mindmap, architecture and KaTeX: half the bytes,
#: which halves the Playwright driver's memory per job. The full build is loaded only when a diagram
#: needs it (see :func:`render.mermaid.needs_full_bundle`).
MERMAID_VERSION = "12.3.0"
MERMAID_TINY_SHA256 = "c41f5236091b83f524c4940f97a16b2b14a76f21ff84aea4fd6b66421d8a2a9a"
MERMAID_FULL_SHA256 = "336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3"

_SPIKE_ROOT = Path(__file__).resolve().parents[2]
_NODE_MODULES = _SPIKE_ROOT / "vendor" / "node_modules"
_DEFAULT_TINY_BUNDLE = _NODE_MODULES / "@mermaid-js" / "tiny" / "dist" / "mermaid.tiny.js"
_DEFAULT_FULL_BUNDLE = _NODE_MODULES / "mermaid" / "dist" / "mermaid.min.js"


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _env_bool(name: str, default: bool) -> bool:
    """Strict: unset or empty means ``default``; anything unrecognised is an error, never ``False``.

    A typo (``ture``, ``enabled``) must not silently switch a security control such as the Chromium
    sandbox off."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be one of 1/0, true/false, yes/no, on/off (got {raw!r})")


@dataclass(frozen=True, slots=True)
class RenderSettings:
    """Knobs for one render service instance.

    ``max_concurrent_jobs`` enforces the plan's "at most 1-2 pages render at once" with a process-wide
    semaphore (:mod:`render.browser`); each job renders one page at a time. It defaults to 1: on
    1 vCPU a second job only time-slices, and two jobs' pessimistic peak is too close to 2 GiB until
    the container measurement (bench/measure.py, cgroup memory.peak) proves otherwise.
    """

    max_concurrent_jobs: int = 1
    #: Chromium's own sandbox. On by default; ACA may not grant user namespaces (see Dockerfile.draft).
    chromium_sandbox: bool = True
    #: Per Playwright action (set_content, pdf, screenshot, evaluate).
    action_timeout_ms: int = 20_000
    #: Hard wall-clock cap for one job (driver start + browser launch + all pages).
    job_timeout_s: float = 90.0
    #: Mermaid render cap per diagram.
    mermaid_timeout_s: float = 15.0
    #: V8 old-space cap for every renderer (MiB). Bounds a runaway Mermaid layout.
    js_heap_limit_mb: int = 256
    #: V8 without JIT compilers (and so without WebAssembly). The Mermaid page is the only place
    #: where attacker-influenceable input (AI-written diagram source) meets a JS engine; jitless
    #: removes most V8 exploit surface for ~0-15% Mermaid time. Essential if the sandbox is off.
    js_jitless: bool = True
    #: LinkedIn document limit is 100 MB; the plan caps carousels at 10 MB.
    max_pdf_bytes: int = 10 * 1024 * 1024
    max_slides: int = 20
    mermaid_bundle: Path = field(default=_DEFAULT_TINY_BUNDLE)
    mermaid_sha256: str = MERMAID_TINY_SHA256
    mermaid_full_bundle: Path = field(default=_DEFAULT_FULL_BUNDLE)
    mermaid_full_sha256: str = MERMAID_FULL_SHA256

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrent_jobs <= 2:
            raise ValueError("max_concurrent_jobs must be 1 or 2 (plan: at most 1-2 pages at once)")
        if self.js_heap_limit_mb < 64:
            raise ValueError("js_heap_limit_mb must be at least 64")

    @classmethod
    def from_env(cls) -> RenderSettings:
        base = cls()
        overrides: dict[str, object] = {}
        if (v := os.environ.get("BW_RENDER_MAX_CONCURRENT_JOBS")) is not None:
            overrides["max_concurrent_jobs"] = int(v)
        if os.environ.get("BW_RENDER_CHROMIUM_SANDBOX") is not None:
            overrides["chromium_sandbox"] = _env_bool("BW_RENDER_CHROMIUM_SANDBOX", True)
        if (v := os.environ.get("BW_RENDER_JS_HEAP_LIMIT_MB")) is not None:
            overrides["js_heap_limit_mb"] = int(v)
        if (v := os.environ.get("BW_RENDER_MERMAID_JS")) is not None:
            overrides["mermaid_bundle"] = Path(v)
        if (v := os.environ.get("BW_RENDER_MERMAID_FULL_JS")) is not None:
            overrides["mermaid_full_bundle"] = Path(v)
        return replace(base, **overrides)  # type: ignore[arg-type]
