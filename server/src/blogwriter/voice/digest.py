"""The diff digest: AI drafts vs. the text the owner actually posted (kit text included).

Plan, "Voice / Learning loop": weekly for the first quarter after the first guide activation,
monthly after that; each suggested guide update needs the owner's approval.

- **Cadence.** The schedule fires weekly (``cadence.digest``, Monday 08:00 by default). Once
  ``VoicePreferences.weeklyUntil`` has passed the cadence is monthly: a scheduled run outside the
  first week of a month (day 1-7) is a no-op. Owner-started runs always run.
- **Period.** A digest covers ``[start, end)``: ``end`` is local midnight of the run's date in
  the cadence time zone (the digest id is ``voice-digest-<that date>``), ``start`` is the end of
  the previous digest (no gaps after a skipped run, at most :data:`MAX_PERIOD_DAYS`), or 7 days
  (weekly) / 35 days (monthly) before ``end`` for the first one.
- **Comparisons** carry a deterministic edit ratio (word-level diff); the model adds notable
  edits and suggestions. Accepting a suggestion adds its rule to a new ``proposed`` guide version
  (:func:`blogwriter.voice.guide.rules_with`, no AI).
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from blogwriter.ai.types import Message, Request, Sensitivity
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import parse_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.confidentiality import blocklist_findings
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist, VoicePreferences
from blogwriter.domain.states import SuggestionStatus
from blogwriter.domain.voice import DraftComparison, GuideRules, GuideSuggestion, VoiceDigest
from blogwriter.domain.weeks import add_days, slot_utc
from blogwriter.voice._prompts import Prompt
from blogwriter.voice.text import clip, neutralize_tags, words

__all__ = [
    "DEFAULT_PERIOD_DAYS",
    "FIRST_WEEK_DAYS",
    "MAX_COMPARISONS",
    "MAX_PERIOD_DAYS",
    "MAX_SUGGESTIONS",
    "Cadence",
    "DigestDraft",
    "DigestPeriod",
    "PostNotes",
    "SuggestionDraft",
    "assemble_digest",
    "diff_hunks",
    "digest_period",
    "digest_request",
    "edit_ratio",
    "effective_cadence",
    "is_due",
    "local_date_of",
    "rule_known",
]

type Cadence = Literal["weekly", "monthly"]

FIRST_WEEK_DAYS: Final = 7
"""In monthly mode a scheduled run only proceeds on days 1-7 of a month."""
DEFAULT_PERIOD_DAYS: Final[Mapping[Cadence, int]] = {"weekly": 7, "monthly": 35}
MAX_PERIOD_DAYS: Final = 62
MAX_COMPARISONS: Final = 50
"""``VoiceDigest.comparisons`` holds at most 50."""
MAX_SUGGESTIONS: Final = 8
MAX_NOTABLE_EDITS: Final = 5
MAX_HUNKS: Final = 12
HUNK_CHARS: Final = 200
_SUGGESTION_LIMITS: Final[Mapping[str, int]] = {
    "prefer": 300,
    "avoid": 300,
    "banned_phrase": 100,
    "vocabulary": 400,
}
_DATA_TAGS: Final = ("posts", "post", "ai_draft", "final", "diff", "current_rules")


def effective_cadence(preferences: VoicePreferences, at: datetime) -> Cadence:
    """Weekly until ``weeklyUntil`` (the first quarter after the first activation), then monthly.

    Before any guide was activated (``weeklyUntil`` unset) the cadence is weekly.
    """
    if preferences.digest_cadence == "monthly":
        return "monthly"
    if preferences.weekly_until is not None and at >= parse_iso(preferences.weekly_until):
        return "monthly"
    return "weekly"


def is_due(cadence: Cadence, local_day: date) -> bool:
    """Whether a scheduled run on ``local_day`` produces a digest."""
    return cadence == "weekly" or local_day.day <= FIRST_WEEK_DAYS


def local_date_of(moment: datetime, timezone: str) -> date:
    """The calendar date of ``moment`` in ``timezone``."""
    return moment.astimezone(ZoneInfo(timezone)).date()


@dataclass(frozen=True, slots=True)
class DigestPeriod:
    start: str
    end: str
    end_date: str
    """The local date the period ends on (exclusive): the digest id's date."""

    @property
    def digest_id(self) -> str:
        return VoiceDigest.id_for(self.end_date)


def digest_period(
    *, run_at: datetime, timezone: str, cadence: Cadence, last_end: str | None
) -> DigestPeriod:
    """The period a digest run at ``run_at`` covers (module docs). ``last_end`` is the end of
    the newest earlier digest."""
    end_date = local_date_of(run_at, timezone).isoformat()
    end = slot_utc(end_date, "00:00", timezone)
    floor = slot_utc(add_days(end_date, -MAX_PERIOD_DAYS), "00:00", timezone)
    if last_end is not None and last_end < end:
        start = max(last_end, floor)
    else:
        start = slot_utc(add_days(end_date, -DEFAULT_PERIOD_DAYS[cadence]), "00:00", timezone)
    return DigestPeriod(start=start, end=end, end_date=end_date)


# --------------------------------------------------------------------------------------------
# Deterministic comparison
# --------------------------------------------------------------------------------------------


def edit_ratio(ai_draft: str, final: str) -> float:
    """The share of words the owner changed: 0 when the final text equals the draft word for
    word (case aside), 1 when nothing of the draft is left (``1 - SequenceMatcher.ratio``)."""
    before, after = words(ai_draft), words(final)
    if not before and not after:
        return 0.0
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return round(1.0 - matcher.ratio(), 3)


def diff_hunks(
    ai_draft: str, final: str, *, limit: int = MAX_HUNKS, width: int = HUNK_CHARS
) -> list[tuple[str, str]]:
    """Word-level edits as ``(removed, added)`` pairs (either may be empty), in text order."""
    before, after = ai_draft.split(), final.split()
    matcher = difflib.SequenceMatcher(
        a=[token.casefold() for token in before],
        b=[token.casefold() for token in after],
        autojunk=False,
    )
    hunks: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append((clip(" ".join(before[i1:i2]), width), clip(" ".join(after[j1:j2]), width)))
        if len(hunks) >= limit:
            break
    return hunks


# --------------------------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------------------------


class PostNotes(ApiModel):
    post_id: str = Field(description="The id of a post in <posts>.")
    notable_edits: list[str] = Field(description="Up to 5 edits that show a preference.")


class SuggestionDraft(ApiModel):
    kind: Literal["prefer", "avoid", "banned_phrase", "vocabulary"]
    text: str = Field(description="The rule itself, at most 200 characters.")
    rationale: str = Field(description="Which post ids show it and what the writer changed.")


class DigestDraft(ApiModel):
    """The SYNTHESIS output of ``voice.digest`` (lengths are enforced here, not by the schema)."""

    posts: list[PostNotes]
    suggestions: list[SuggestionDraft]


def _rules_block(rules: GuideRules | None, version: int | None) -> str:
    if rules is None:
        return "<current_rules>none</current_rules>"
    lines: list[str] = []
    for heading, items in (
        ("Prefer:", rules.prefer),
        ("Avoid:", rules.avoid),
        ("Banned phrases:", rules.banned_phrases),
        ("Vocabulary:", [f"{term}: {note}" for term, note in sorted(rules.vocabulary.items())]),
    ):
        if items:
            lines += [heading, *(f"- {item}" for item in items)]
    body = neutralize_tags("\n".join(lines) or "none", _DATA_TAGS)
    label = f' version="{version}"' if version is not None else ""
    return f"<current_rules{label}>\n{body}\n</current_rules>"


def _render_post(post: Post) -> str:
    draft = post.ai_draft_text or ""
    final = post.final_text or ""
    hunks = diff_hunks(draft, final)
    diff_lines: list[str] = []
    for removed, added in hunks:
        if removed:
            diff_lines.append(f'- "{removed}"')
        if added:
            diff_lines.append(f'+ "{added}"')
    attributes = (
        f'id="{post.id}" pillar="{post.pillar}" format="{post.format}" '
        f'lane="{post.lane}" editRatio="{edit_ratio(draft, final):.3f}"'
    )
    return "\n".join(
        [
            f"<post {attributes}>",
            "<ai_draft>",
            neutralize_tags(draft, _DATA_TAGS),
            "</ai_draft>",
            "<final>",
            neutralize_tags(final, _DATA_TAGS),
            "</final>",
            "<diff>",
            neutralize_tags("\n".join(diff_lines) or "(no changes)", _DATA_TAGS),
            "</diff>",
            "</post>",
        ]
    )


def digest_request(
    posts: Sequence[Post], *, rules: GuideRules | None, guide_version: int | None, prompt: Prompt
) -> Request:
    """The SYNTHESIS request (``Sensitivity.ANONYMIZED``, tool-free) for the posts of a period."""
    if not posts:
        raise ValueError("a digest needs at least one post")
    parts = [
        _rules_block(rules, guide_version),
        "<posts>",
        *(_render_post(post) for post in posts[:MAX_COMPARISONS]),
        "</posts>",
        "Compare each draft with what the writer published. Reply with the JSON object only.",
    ]
    return Request(
        system=prompt.text,
        messages=(Message.user("\n".join(parts)),),
        sensitivity=Sensitivity.ANONYMIZED,
        output_schema=DigestDraft,
    )


def rule_known(rules: GuideRules | None, kind: str, text: str) -> bool:
    """Whether ``rules`` already hold the rule ``text`` of ``kind`` (case-insensitive)."""
    if rules is None:
        return False
    folded = " ".join(text.split()).casefold()
    match kind:
        case "prefer":
            return any(item.casefold() == folded for item in rules.prefer)
        case "avoid":
            return any(item.casefold() == folded for item in rules.avoid)
        case "banned_phrase":
            return any(item.casefold() == folded for item in rules.banned_phrases)
        case "vocabulary":
            term = folded.partition(":")[0].strip()
            return any(key.casefold() == term for key in rules.vocabulary)
        case _:
            return False


def _clean(text: str, limit: int, blocklist: Blocklist) -> str | None:
    value = clip(" ".join(text.split()), limit)
    if not value or blocklist_findings(value, blocklist):
        return None
    return value


def assemble_digest(
    draft: DigestDraft,
    *,
    period: DigestPeriod,
    posts: Sequence[Post],
    rules: GuideRules | None,
    guide_version: int | None,
    served_by: ServedByRecord,
    blocklist: Blocklist,
    now: str,
) -> VoiceDigest:
    """The digest document: our edit ratios, the model's notes and suggestions (clipped,
    deduplicated, known rules and anything matching the blocklist left out)."""
    notes: dict[str, list[str]] = {}
    for entry in draft.posts:
        cleaned = [_clean(edit, 300, blocklist) for edit in entry.notable_edits]
        kept = [edit for edit in cleaned if edit is not None][:MAX_NOTABLE_EDITS]
        notes.setdefault(entry.post_id, kept)
    comparisons = [
        DraftComparison(
            target_kind="post",
            target_id=post.id,
            edit_ratio=edit_ratio(post.ai_draft_text or "", post.final_text or ""),
            notable_edits=notes.get(post.id, []),
        )
        for post in posts[:MAX_COMPARISONS]
    ]
    suggestions: list[GuideSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for item in draft.suggestions:
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
        text = _clean(item.text, _SUGGESTION_LIMITS[item.kind], blocklist)
        if text is None or rule_known(rules, item.kind, text):
            continue
        if item.kind == "vocabulary" and not text.partition(":")[0].strip():
            continue
        key = (item.kind, text.casefold())
        if key in seen:
            continue
        seen.add(key)
        rationale = _clean(item.rationale, 500, blocklist)
        suggestions.append(
            GuideSuggestion(
                id=f"s{len(suggestions) + 1}",
                text=text,
                rationale=rationale,
                kind=item.kind,
                status=SuggestionStatus.PROPOSED,
            )
        )
    return VoiceDigest(
        id=period.digest_id,
        created_at=now,
        updated_at=now,
        period_start=period.start,
        period_end=period.end,
        guide_version=guide_version,
        comparisons=comparisons,
        suggestions=suggestions,
        served_by=[served_by],
    )
