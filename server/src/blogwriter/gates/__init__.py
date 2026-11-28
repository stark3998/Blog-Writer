"""blogwriter.gates: the seven gates over one exact article version, the slop detectors, and
the standards-ID validator with its catalog (contract work order W-A8).

All seven gates must pass on their own for auto-publish; any failure sends the article to
review, where the owner may override overridable findings with a reason (manual publish only).
A gate that cannot run is ``skipped`` or ``error``: never a pass.

Public API::

    SlopDetectors().check(text)                    # the SlopChecker port (voice, posts, engagement)
    RUBRIC_DIMENSIONS, RUBRIC_OVERALL_MIN, RUBRIC_DIMENSION_MIN, RUBRIC_SPECIFICITY_MIN
    await run_gates(article, services=..., origin=..., job_id=..., gates=GATE_ORDER)
    await run_gates_detailed(...)                  # the report plus the embedding to store
    await save_report(store, article_id, report, now=..., embedding=...)
    lint_text(meta, body, brief=..., catalog=..., pillars=..., slop=...)
    validate_standard_ids(text, catalog)
    GatesLifecycle()                               # startup: app.state.slop = SlopDetectors()

Wiring: ``get_router()``, ``register_jobs(registry)``, ``schedule_entries(cadence)``,
``GatesLifecycle``. Settings: :class:`GatesSettings` (``BW_GATES_*``).
"""

from blogwriter.gates.deterministic import lint_text
from blogwriter.gates.jobs import register_jobs
from blogwriter.gates.lifecycle import GatesLifecycle
from blogwriter.gates.routes import get_router
from blogwriter.gates.rubric import (
    RUBRIC_DIMENSION_MIN,
    RUBRIC_DIMENSIONS,
    RUBRIC_OVERALL_MIN,
    RUBRIC_SPECIFICITY_MIN,
)
from blogwriter.gates.run import (
    GatesOutcome,
    GatesView,
    gates_view,
    run_gates,
    run_gates_detailed,
    save_report,
)
from blogwriter.gates.schedule import schedule_entries
from blogwriter.gates.settings import GatesSettings, gates_settings
from blogwriter.gates.slop import SLOP_CODES, SlopDetectors
from blogwriter.gates.standards import validate_standard_ids

__all__ = [
    "RUBRIC_DIMENSIONS",
    "RUBRIC_DIMENSION_MIN",
    "RUBRIC_OVERALL_MIN",
    "RUBRIC_SPECIFICITY_MIN",
    "SLOP_CODES",
    "GatesLifecycle",
    "GatesOutcome",
    "GatesSettings",
    "GatesView",
    "SlopDetectors",
    "gates_settings",
    "gates_view",
    "get_router",
    "lint_text",
    "register_jobs",
    "run_gates",
    "run_gates_detailed",
    "save_report",
    "schedule_entries",
    "validate_standard_ids",
]
