"""blogwriter.stories: the story bank, the POV bank, input-side confidentiality and the
confidentiality classifier (contract W-A3; plan "Owner signal").

Public API:

- Intake (:mod:`.intake`): :func:`local_findings` (deterministic: blocklist, e-mail, phone,
  links, IP addresses, handles block; domains, money, exact figures and possible names warn),
  :func:`mask_text`, :func:`propose` (the ``Task.ANONYMIZE`` proposal, ``Sensitivity.PRIVATE``,
  nothing stored or logged), :class:`RedactionProposal`, ``IntakePurpose``.
- The classifier (:mod:`.classify`): :class:`StoriesClassifier`, the
  ``domain.ports.ConfidentialityClassifier`` every package reaches as ``services.classifier``.
- The banks (:mod:`.bank`): :class:`StoryBank`, :func:`load_bank`, :func:`story_gaps`,
  :func:`entry_text`, :func:`needs_classifier`.
- Integration: :func:`get_router`, :func:`register_jobs` (``stories.seed``,
  ``stories.classify``), :func:`schedule_entries` (none), :class:`StoriesLifecycle`
  (``app.state.classifier``), :class:`StoriesSettings` (``BW_STORIES_*``).

**Private interview text is never persisted**: not in documents, job inputs or outputs,
events, logs or blobs. It lives in the ``redact`` request and its anonymizer call only.
"""

from blogwriter.stories.bank import (
    StoryBank,
    entry_text,
    load_bank,
    needs_classifier,
    pov_text,
    story_gaps,
    story_text,
)
from blogwriter.stories.classify import StoriesClassifier
from blogwriter.stories.intake import (
    IntakePurpose,
    RedactionProposal,
    local_findings,
    mask_text,
    propose,
)
from blogwriter.stories.jobs import (
    ClassifyJobInput,
    SeedJobInput,
    register_jobs,
    schedule_entries,
    seed_entry_id,
)
from blogwriter.stories.lifecycle import StoriesLifecycle
from blogwriter.stories.routes import get_router
from blogwriter.stories.settings import StoriesSettings, get_settings

__all__ = [
    "ClassifyJobInput",
    "IntakePurpose",
    "RedactionProposal",
    "SeedJobInput",
    "StoriesClassifier",
    "StoriesLifecycle",
    "StoriesSettings",
    "StoryBank",
    "entry_text",
    "get_router",
    "get_settings",
    "load_bank",
    "local_findings",
    "mask_text",
    "needs_classifier",
    "pov_text",
    "propose",
    "register_jobs",
    "schedule_entries",
    "seed_entry_id",
    "story_gaps",
    "story_text",
]
