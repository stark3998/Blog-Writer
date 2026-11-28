"""The voice schedule entry (contract section 8): the weekly digest.

The rule fires weekly at ``cadence.digest`` (Monday 08:00 by default); the job itself turns the
weekly run into a no-op outside the first week of a month once the first quarter is over.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import VOICE_DIGEST, JobInput
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs.scheduler import weekly

__all__ = ["DIGEST_MAX_LATENESS", "DIGEST_SCHEDULE_ID", "schedule_entries"]

DIGEST_SCHEDULE_ID: Final = "sched-voice-digest"
DIGEST_MAX_LATENESS: Final = timedelta(hours=24)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """``sched-voice-digest``: ``voice.digest`` weekly at ``cadence.digest`` in the cadence time
    zone, as background work (pure and deterministic; no I/O)."""
    return [
        ScheduleEntry(
            id=DIGEST_SCHEDULE_ID,
            name="Voice digest",
            job_type=VOICE_DIGEST,
            rule=weekly([cadence.digest.day], cadence.digest.time),
            timezone=cadence.timezone,
            job_input=JobInput(origin="background").to_json(),
            max_lateness=DIGEST_MAX_LATENESS,
        )
    ]
