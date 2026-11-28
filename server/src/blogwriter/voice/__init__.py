"""blogwriter.voice: the voice corpus, AI-tell and slop scoring, the versioned Voice & Style Guide
and the diff digest (contract W-A4; plan "Voice").

- The corpus: portfolio posts and About, 5-10 past LinkedIn posts the owner wrote by hand,
  confirmed interview answers (the pipeline calls :func:`add_sample`) and the final text of posts
  published through the app (the digest adds them). Every sample is scored with the AI-tell lint
  and the structural detectors (:func:`score_text`); flagged passages inform topics and
  terminology only and are never exemplars.
- The guide: versioned, one active, every change approved by the owner; :func:`guide_prompt` is
  the prompt-cached drafting prefix and :func:`load_active_guide` the version in use.
- The digest: AI drafts vs. final posted text, weekly for the first quarter, monthly after it.
- The v1 migration's voice seeds (``domain.seeds.VoiceSeed``, ``/api/voice/seeds``): approving
  one adds the profile's sample text to the corpus (:mod:`blogwriter.voice.seeds`).

Integration: :func:`get_router`, :func:`register_jobs`, :func:`schedule_entries`. No settings
class and no lifecycle (owner values live in ``VoicePreferences``).
"""

from blogwriter.voice.corpus import add_sample, linkedin_sample_count
from blogwriter.voice.guide import guide_prompt, load_active_guide
from blogwriter.voice.jobs import register_jobs
from blogwriter.voice.routes import get_router
from blogwriter.voice.schedule import schedule_entries
from blogwriter.voice.scoring import TextScore, ai_tell_findings, score_text

__all__ = [
    "TextScore",
    "add_sample",
    "ai_tell_findings",
    "get_router",
    "guide_prompt",
    "linkedin_sample_count",
    "load_active_guide",
    "register_jobs",
    "schedule_entries",
    "score_text",
]
