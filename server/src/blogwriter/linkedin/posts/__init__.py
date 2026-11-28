"""blogwriter.linkedin.posts: LinkedIn posts from draft to staged (contract work order W-B3).

- Derivation (:mod:`.derive`): at most ``lanes.derivativesMaxPerWeek`` derivatives per flagship,
  native posts from the story and POV bank, quick takes (the owner's take leads), repurposed
  posts, and regeneration of hooks, text or slides; 3 hook variants per post with a ``hookType``
  from the taxonomy, at least one reusing the owner's own words.
- The serializer (:mod:`.serialize`): display text and escaped API payload in lockstep
  (``payload == api_commentary(parse_display(displayText, hashtags))``).
- Checks (:mod:`.checks`): lint (blocks approval), derived claims (numbers exact against the
  article, brief or story; ``FACT_CHECK`` for claims; inline attribution), near-duplicates
  against the last 90 days.
- Carousels and image cards (:mod:`.carousel`): ``blogwriter.render`` specs, the carousel rules,
  rendering through the ``RenderService`` port with the confidentiality gate on the exact bytes.
- Approval bound to the SHA-256 of text plus media (``Post.compute_approval_hash``), staging at
  T-60 and expiry of time-sensitive posts (:mod:`.staging`).

Integration: :func:`get_router`, :func:`register_jobs` (``posts.derive``, ``posts.generate``,
``posts.check``, ``posts.render``, ``posts.stage``, ``posts.expire``) and
:func:`schedule_entries` (none). No settings class (owner values live in ``LaneSettings``,
``ModelPreferences`` and ``CadenceSettings``) and no lifecycle. Nothing here publishes: the
LinkedIn publisher is reached only from the transport's publish route.
"""

from blogwriter.linkedin.posts.carousel import (
    InvalidSpec,
    RenderedMedia,
    RenderRefused,
    parse_spec,
    render_post_media,
)
from blogwriter.linkedin.posts.checks import (
    CHECK_NAMES,
    check_post,
    failing_checks,
    lint_post_text,
    near_duplicate,
)
from blogwriter.linkedin.posts.derive import GenerationFailed, draft_post
from blogwriter.linkedin.posts.jobs import GenerateInput, register_jobs, schedule_entries
from blogwriter.linkedin.posts.routes import get_router
from blogwriter.linkedin.posts.serialize import payload_matches, payload_units, serialize
from blogwriter.linkedin.posts.staging import LinkedInTransport, StagingTransport, TokenCheck

__all__ = [
    "CHECK_NAMES",
    "GenerateInput",
    "GenerationFailed",
    "InvalidSpec",
    "LinkedInTransport",
    "RenderRefused",
    "RenderedMedia",
    "StagingTransport",
    "TokenCheck",
    "check_post",
    "draft_post",
    "failing_checks",
    "get_router",
    "lint_post_text",
    "near_duplicate",
    "parse_spec",
    "payload_matches",
    "payload_units",
    "register_jobs",
    "render_post_media",
    "schedule_entries",
    "serialize",
]
