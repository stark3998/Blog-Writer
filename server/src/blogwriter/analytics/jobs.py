"""The analytics job types (contract section 7).

``analytics.suggest`` (schedule Mon 07:30, owner "run now"): ``compute``, ``suggest``, ``save``
(:mod:`blogwriter.analytics.suggestions`). ``analytics.eval`` (owner): ``brief``, ``draft``,
``judge``, ``notify`` (:mod:`blogwriter.analytics.evals`). Inputs are ``JobInput``
(``{"origin": ...}``); outputs carry ids and counts, never text.
"""

from __future__ import annotations

from blogwriter.analytics.evals import EVAL_STEPS
from blogwriter.analytics.suggestions import SUGGEST_STEPS
from blogwriter.domain.jobtypes import ANALYTICS_EVAL, ANALYTICS_SUGGEST
from blogwriter.jobs import JobRegistry

__all__ = ["register_jobs"]


def register_jobs(registry: JobRegistry) -> None:
    """Register ``analytics.suggest`` and ``analytics.eval`` (once, by ``blogwriter.main``)."""
    registry.register(ANALYTICS_SUGGEST, SUGGEST_STEPS)
    registry.register(ANALYTICS_EVAL, EVAL_STEPS)
