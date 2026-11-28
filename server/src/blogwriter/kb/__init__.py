"""blogwriter.kb: the knowledge base and policy settings (contract W-A2).

Pillars, format packs, lanes and mix rules, brand kit, blocklist, employer policy, cadence (slots
and modes), model preferences and the autonomy switches are singleton documents in the
``settings`` container (``blogwriter.domain.settings``); kb owns and writes them through
``/api/kb/*`` and serves the plan's defaults until the owner saves (``etag: null``). Everyone
else reads them with ``blogwriter.domain.docstore.read_singleton``.

Public API:

- :func:`get_router`: the routes (``APIRouter(prefix="/api/kb")``).
- :func:`owner_inputs`, :class:`OwnerInputs`, :class:`OwnerInput`: the owner-input checklist.
- :func:`pillar_ids_in_use` (and :func:`pillar_references`): the fail-closed
  ``pillar_in_use`` guard.
- :func:`validate_brand`: what stops a brand kit from rendering (through ``RenderBrand``).
- :data:`PLAN_PILLARS`, :func:`kb_default`, :func:`read_settings`: the plan's defaults.
- ``/api/kb/seeds`` (:mod:`blogwriter.kb.seeds`): the owner's review of the v1 migration's
  keyword seeds (``domain.seeds.KbSeed``); approving one adds its keywords to a pillar's
  vocabulary.

No jobs, schedules, lifecycle or package settings: everything owner-editable lives in the
settings documents.
"""

from blogwriter.kb.checks import validate_brand
from blogwriter.kb.defaults import PLAN_PILLARS, kb_default, read_settings
from blogwriter.kb.inputs import OwnerInput, OwnerInputs, owner_inputs
from blogwriter.kb.routes import get_router
from blogwriter.kb.usage import pillar_ids_in_use, pillar_references

__all__ = [
    "PLAN_PILLARS",
    "OwnerInput",
    "OwnerInputs",
    "get_router",
    "kb_default",
    "owner_inputs",
    "pillar_ids_in_use",
    "pillar_references",
    "read_settings",
    "validate_brand",
]
