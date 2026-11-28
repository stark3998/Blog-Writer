"""blogwriter.engagement: reply and comment helpers, the watchlist and the profile checklist
(contract W-B5; plan "Planner, engagement, insights").

Nothing is automated on LinkedIn: the owner pastes (or screenshots) a post or comment, gets up
to three angles grounded in their own stories, POVs and briefs, drafts and lints a reply, posts
it by hand and finalizes it here. This package never imports the LinkedIn client or publisher
(an import-graph test checks it).

- **Other members' text never outlives 48 hours**: expiry in code, the ``engagement.purge`` job
  every 6 hours, deletion on finalize and dismiss; only ``engagement_record`` and
  ``watch_entry`` are backed up. Screenshots are never stored.
- **Untrusted text gets no tools and at most anonymized data**: every ``Task.ENGAGEMENT``
  request is tool-free and ``Sensitivity.ANONYMIZED``, with the pasted text in a delimited
  section the prompt declares to be data.

Public API:

- :mod:`.helpers`: :func:`create_item`, :func:`generate_angles`, :func:`extract_screenshot`,
  :func:`finalize_item`, :func:`dismiss_item`, :func:`purge_expired`, :func:`is_expired`,
  :func:`gather_grounding`, :func:`clean_angles`.
- :mod:`.lint`: :func:`lint_reply` (restating, links, AI tells, over 1,000 characters,
  blocklist, slop), :func:`is_substantive`.
- Integration: :func:`get_router`, :func:`register_jobs` (``engagement.purge``),
  :func:`schedule_entries` (``sched-engagement-purge``). No lifecycle and no settings class:
  owner-editable values live in documents (the profile checklist singleton).
"""

from blogwriter.engagement.helpers import (
    Extracted,
    Grounding,
    GroundingEntry,
    InvalidField,
    ItemExpired,
    NoOwnerMaterial,
    NothingExtracted,
    clean_angles,
    create_item,
    dismiss_item,
    extract_screenshot,
    finalize_item,
    gather_grounding,
    generate_angles,
    is_expired,
    purge_expired,
    record_id_for,
)
from blogwriter.engagement.jobs import register_jobs
from blogwriter.engagement.lint import is_substantive, lint_reply
from blogwriter.engagement.routes import get_router
from blogwriter.engagement.schedule import PURGE_SCHEDULE_ID, schedule_entries

__all__ = [
    "PURGE_SCHEDULE_ID",
    "Extracted",
    "Grounding",
    "GroundingEntry",
    "InvalidField",
    "ItemExpired",
    "NoOwnerMaterial",
    "NothingExtracted",
    "clean_angles",
    "create_item",
    "dismiss_item",
    "extract_screenshot",
    "finalize_item",
    "gather_grounding",
    "generate_angles",
    "get_router",
    "is_expired",
    "is_substantive",
    "lint_reply",
    "purge_expired",
    "record_id_for",
    "register_jobs",
    "schedule_entries",
]
