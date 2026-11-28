"""blogwriter.signals: sources, feed crawling, signal hashes, scouting and the ideas inbox.

Work order W-A6 of ``docs/v2/build/contract.md``. **Feed items never generate content without an
owner action**: the ``signals.crawl`` and ``signals.scout`` jobs write only ``sources``,
``signals`` and ``ideas`` documents and enqueue nothing.

Integration (``blogwriter.main``):

- ``get_router()``: ``/api/sources*`` and ``/api/ideas*``.
- ``register_jobs(registry)``: ``signals.crawl`` (``fetch``, ``triage``) and ``signals.scout``
  (``search``, ``triage``).
- ``schedule_entries(cadence)``: ``sched-signals-crawl`` and ``sched-signals-scout``.
- No lifecycle: the package uses ``app.state.services`` (``store``, ``jobs``, ``clock``,
  ``egress``, ``ai``) only.

Public Python API for other packages: :func:`mark_idea_used`, :func:`expire_ideas`,
:func:`propose_repurpose` (repurpose ideas: analytics proposes them), :func:`parse_feed`,
:func:`discover_feeds` and :class:`FeedItem`.
"""

from blogwriter.signals.feeds import (
    DiscoveredFeed,
    FeedItem,
    FeedParseError,
    ParsedFeed,
    discover_feed_links,
    discover_feeds,
    parse_feed,
    parse_feed_document,
)
from blogwriter.signals.ideas import expire_ideas, idea_id_for_url, mark_idea_used
from blogwriter.signals.jobs import register_jobs
from blogwriter.signals.repurpose import (
    Repurposed,
    RepurposeKind,
    propose_repurpose,
    repurpose_idea_id,
)
from blogwriter.signals.routes import get_router
from blogwriter.signals.schedule import schedule_entries
from blogwriter.signals.settings import SignalsSettings, get_settings

__all__ = [
    "DiscoveredFeed",
    "FeedItem",
    "FeedParseError",
    "ParsedFeed",
    "RepurposeKind",
    "Repurposed",
    "SignalsSettings",
    "discover_feed_links",
    "discover_feeds",
    "expire_ideas",
    "get_router",
    "get_settings",
    "idea_id_for_url",
    "mark_idea_used",
    "parse_feed",
    "parse_feed_document",
    "propose_repurpose",
    "register_jobs",
    "repurpose_idea_id",
    "schedule_entries",
]
