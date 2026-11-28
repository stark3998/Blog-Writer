"""Seed documents the v1 migration leaves for the owner to review (status ``proposed``).

The models live in :mod:`blogwriter.domain.seeds` (kb and voice review them); this module
re-exports them for the migration's converters.
"""

from __future__ import annotations

from blogwriter.domain.seeds import KbSeed, VoiceSeed

__all__ = ["KbSeed", "VoiceSeed"]
