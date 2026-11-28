"""Every status machine of the stored documents, as data, plus the "who writes" rule.

A :class:`StateMachine` is the set of states (a ``StrEnum``), the initial state and the allowed
edges. :func:`transition` (or :meth:`StateMachine.transition`) returns the target state or raises
:class:`TransitionError`, which routes turn into a 409 problem with ``code: invalid_transition``
and the allowed targets. A self-transition is never an edge: updating a field without changing
the state is not a transition.

The machines are the contract between packages. Change an edge only together with the work
order that relies on it (``docs/v2/build/contract.md``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Literal, Protocol

__all__ = [
    "ARTICLE_MACHINE",
    "ARTICLE_WRITER",
    "CONNECTION_MACHINE",
    "ENGAGEMENT_MACHINE",
    "EXPERIMENT_MACHINE",
    "GUIDE_MACHINE",
    "IDEA_MACHINE",
    "INTERVIEW_MACHINE",
    "KIT_MACHINE",
    "MACHINES",
    "OWNER_EDGES",
    "OWNER_WRITES",
    "PLAN_MACHINE",
    "POST_MACHINE",
    "REMINDER_MACHINE",
    "RESEARCH_MACHINE",
    "REVIEW_MACHINE",
    "SAMPLE_MACHINE",
    "SOURCE_MACHINE",
    "SUGGESTION_MACHINE",
    "ArticleStatus",
    "ConnectionStatus",
    "EngagementStatus",
    "ExperimentStatus",
    "GuideStatus",
    "IdeaStatus",
    "InterviewStatus",
    "KitState",
    "MachineTable",
    "PlanStatus",
    "PostStatus",
    "ReminderStatus",
    "ResearchStatus",
    "ReviewStatus",
    "SampleStatus",
    "SourceStatus",
    "StateMachine",
    "SuggestionStatus",
    "TransitionError",
    "TransitionRefused",
    "Writer",
    "WriterActor",
    "WriterConflict",
    "check_article_writer",
    "transition",
]


class TransitionError(ValueError):
    """A status change the machine does not allow. ``allowed`` lists the legal targets."""

    code: ClassVar[str] = "invalid_transition"

    def __init__(self, machine: str, current: str, target: str, allowed: Iterable[str]) -> None:
        self.machine = machine
        self.current = current
        self.target = target
        self.allowed: tuple[str, ...] = tuple(sorted(allowed))
        super().__init__(f"{machine}: {current} -> {target} is not allowed")


class TransitionRefused(ValueError):
    """An edge of the machine that a business rule refuses for this document (409 ``code``).

    For example archiving a live article (``live_article``: unpublish it first) or approving an
    eval article (``eval_article``). ``detail`` is owner-facing and never quotes content.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class StateMachine[S: StrEnum]:
    """States, the initial state and the allowed edges of one status field."""

    name: str
    states: tuple[S, ...]
    initial: S
    edges: Mapping[S, frozenset[S]]

    def __post_init__(self) -> None:
        if len(set(self.states)) != len(self.states) or not self.states:
            raise ValueError(f"{self.name}: states must be unique and non-empty")
        known = set(self.states)
        if self.initial not in known:
            raise ValueError(f"{self.name}: unknown initial state {self.initial!r}")
        for source, targets in self.edges.items():
            if source not in known or not targets <= known:
                raise ValueError(f"{self.name}: an edge names an unknown state")
            if source in targets:
                raise ValueError(f"{self.name}: {source} -> {source} is not a transition")
        object.__setattr__(self, "edges", MappingProxyType(dict(self.edges)))

    @property
    def terminal(self) -> frozenset[S]:
        """States without outgoing edges."""
        return frozenset(state for state in self.states if not self.edges.get(state))

    def allowed(self, current: S) -> frozenset[S]:
        """The states ``current`` may move to."""
        return self.edges.get(current, frozenset())

    def can(self, current: S, target: S) -> bool:
        return target in self.allowed(current)

    def transition(self, current: S, target: S) -> S:
        """Return ``target`` if ``current -> target`` is an edge, else :class:`TransitionError`."""
        if target not in self.allowed(current):
            raise TransitionError(
                self.name, current.value, target.value, (s.value for s in self.allowed(current))
            )
        return target

    def as_table(self) -> dict[str, list[str]]:
        """``{state: [targets...]}`` with sorted targets (for docs, tests and the web)."""
        return {state.value: sorted(t.value for t in self.allowed(state)) for state in self.states}


def transition[S: StrEnum](machine: StateMachine[S], current: S, target: S) -> S:
    """:meth:`StateMachine.transition` as a function."""
    return machine.transition(current, target)


def _edges[S: StrEnum](table: Mapping[S, Iterable[S]]) -> dict[S, frozenset[S]]:
    return {source: frozenset(targets) for source, targets in table.items()}


# --------------------------------------------------------------------------------------------
# Articles (owner: articles; pipeline and publish move it through their states)
# --------------------------------------------------------------------------------------------


class ArticleStatus(StrEnum):
    PLANNED = "planned"
    RESEARCHING = "researching"
    INTERVIEWING = "interviewing"
    DRAFTING = "drafting"
    GATING = "gating"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"
    UNPUBLISHING = "unpublishing"
    UNPUBLISHED = "unpublished"
    NEEDS_ATTENTION = "needs_attention"
    ARCHIVED = "archived"


_A = ArticleStatus
ARTICLE_MACHINE: Final = StateMachine(
    name="article",
    states=tuple(ArticleStatus),
    initial=_A.PLANNED,
    edges=_edges(
        {
            _A.PLANNED: {_A.RESEARCHING, _A.INTERVIEWING, _A.DRAFTING, _A.IN_REVIEW, _A.ARCHIVED},
            _A.RESEARCHING: {_A.INTERVIEWING, _A.DRAFTING, _A.NEEDS_ATTENTION, _A.ARCHIVED},
            _A.INTERVIEWING: {_A.DRAFTING, _A.NEEDS_ATTENTION, _A.ARCHIVED},
            _A.DRAFTING: {_A.GATING, _A.IN_REVIEW, _A.NEEDS_ATTENTION, _A.ARCHIVED},
            # gating -> approved only on the auto-publish path (gates all pass, calibrated).
            _A.GATING: {_A.IN_REVIEW, _A.APPROVED, _A.NEEDS_ATTENTION},
            # in_review -> researching/interviewing: the owner restarts the pipeline earlier.
            _A.IN_REVIEW: {
                _A.RESEARCHING,
                _A.INTERVIEWING,
                _A.DRAFTING,
                _A.GATING,
                _A.APPROVED,
                _A.ARCHIVED,
            },
            _A.APPROVED: {_A.IN_REVIEW, _A.PUBLISHING},
            _A.PUBLISHING: {_A.PUBLISHED, _A.PUBLISH_FAILED},
            # publish_failed repeats the failed operation (publish.lastOperation): publishing
            # or unpublishing; in_review gives up on a publish.
            _A.PUBLISH_FAILED: {_A.PUBLISHING, _A.UNPUBLISHING, _A.IN_REVIEW},
            # published -> in_review starts a correction; the live version stays up meanwhile.
            _A.PUBLISHED: {_A.IN_REVIEW, _A.PUBLISHING, _A.UNPUBLISHING},
            _A.UNPUBLISHING: {_A.UNPUBLISHED, _A.PUBLISH_FAILED},
            _A.UNPUBLISHED: {_A.IN_REVIEW, _A.ARCHIVED},
            _A.NEEDS_ATTENTION: {
                _A.RESEARCHING,
                _A.INTERVIEWING,
                _A.DRAFTING,
                _A.IN_REVIEW,
                _A.ARCHIVED,
            },
            _A.ARCHIVED: {_A.PLANNED},
        }
    ),
)

type Writer = Literal["owner", "pipeline", "publish", "none"]
"""Who may change an article's Markdown and metadata in a state (plan: "Who writes an article").

``publish`` (publishing, unpublishing): the publish job owns the document, but only its publish
fields, through ``articles.transition(update=...)``; nobody changes the Markdown or the meta, so
the bytes being rendered and verified are the bytes that were approved."""
type WriterActor = Literal["owner", "pipeline", "publish"]

ARTICLE_WRITER: Final[Mapping[ArticleStatus, Writer]] = MappingProxyType(
    {
        _A.PLANNED: "owner",
        _A.RESEARCHING: "pipeline",
        _A.INTERVIEWING: "pipeline",
        _A.DRAFTING: "pipeline",
        _A.GATING: "pipeline",
        _A.IN_REVIEW: "owner",
        _A.APPROVED: "none",
        _A.PUBLISHING: "publish",
        _A.PUBLISHED: "none",
        _A.PUBLISH_FAILED: "none",
        _A.UNPUBLISHING: "publish",
        _A.UNPUBLISHED: "none",
        _A.NEEDS_ATTENTION: "owner",
        _A.ARCHIVED: "none",
    }
)
"""Jobs write only in pipeline states (the editor is read-only and shows the live stream); the
owner writes only in owner states. ``publishing``/``unpublishing`` belong to the publish job,
which writes its publish fields and never the Markdown or the meta."""


class WriterConflict(ValueError):
    """``actor`` may not write an article in ``status`` (409 ``read_only_state``)."""

    code: ClassVar[str] = "read_only_state"

    def __init__(self, status: ArticleStatus, actor: WriterActor) -> None:
        self.status = status
        self.actor: WriterActor = actor
        self.writer: Writer = ARTICLE_WRITER[status]
        super().__init__(f"article in {status.value} is written by {self.writer}, not {actor}")


def check_article_writer(status: ArticleStatus, actor: WriterActor) -> None:
    """Raise :class:`WriterConflict` unless ``actor`` writes articles in ``status``."""
    if ARTICLE_WRITER[status] != actor:
        raise WriterConflict(status, actor)


# --------------------------------------------------------------------------------------------
# Posts and the ready-to-post kit (owner: linkedin)
# --------------------------------------------------------------------------------------------


class PostStatus(StrEnum):
    GENERATING = "generating"
    DRAFT = "draft"
    APPROVED = "approved"
    STAGED = "staged"
    PUBLISHING = "publishing"
    POSTED = "posted"
    KIT = "kit"
    NEEDS_ATTENTION = "needs_attention"
    MISSED = "missed"
    EXPIRED = "expired"
    DELETED = "deleted"
    ARCHIVED = "archived"


_P = PostStatus
POST_MACHINE: Final = StateMachine(
    name="post",
    states=tuple(PostStatus),
    initial=_P.DRAFT,
    edges=_edges(
        {
            _P.GENERATING: {_P.DRAFT, _P.NEEDS_ATTENTION, _P.ARCHIVED},
            _P.DRAFT: {_P.GENERATING, _P.APPROVED, _P.ARCHIVED, _P.EXPIRED},
            # Any edit of an approved post invalidates the approval hash: back to draft.
            _P.APPROVED: {
                _P.DRAFT,
                _P.STAGED,
                _P.PUBLISHING,
                _P.KIT,
                _P.MISSED,
                _P.EXPIRED,
                _P.ARCHIVED,
            },
            _P.STAGED: {_P.DRAFT, _P.APPROVED, _P.PUBLISHING, _P.KIT, _P.MISSED, _P.EXPIRED},
            # The tap: CAS approved|staged -> publishing, intent written, one POST, no retries.
            # posted: 201 + URN. approved: a definite 4xx, LinkedIn created nothing (intent
            # failed). needs_attention: 5xx, timeout or a restart (intent unknown).
            _P.PUBLISHING: {_P.POSTED, _P.APPROVED, _P.NEEDS_ATTENTION},
            # needs_attention: the owner checks LinkedIn and says what happened.
            _P.NEEDS_ATTENTION: {_P.POSTED, _P.APPROVED, _P.KIT, _P.DRAFT, _P.ARCHIVED},
            _P.KIT: {_P.POSTED, _P.APPROVED, _P.MISSED, _P.EXPIRED, _P.ARCHIVED},
            _P.MISSED: {_P.APPROVED, _P.KIT, _P.EXPIRED, _P.ARCHIVED},
            _P.POSTED: {_P.DELETED},
            _P.EXPIRED: {_P.DRAFT, _P.ARCHIVED},
            _P.DELETED: set[PostStatus](),
            _P.ARCHIVED: {_P.DRAFT},
        }
    ),
)


class KitState(StrEnum):
    """Ready-to-post kit progress (``post.kit.state`` while ``post.status == kit``)."""

    PREPARED = "prepared"
    NATIVE_DRAFT_SAVED = "native_draft_saved"
    NATIVELY_SCHEDULED = "natively_scheduled"
    POSTED = "posted"


_K = KitState
KIT_MACHINE: Final = StateMachine(
    name="kit",
    states=tuple(KitState),
    initial=_K.PREPARED,
    edges=_edges(
        {
            _K.PREPARED: {_K.NATIVE_DRAFT_SAVED, _K.NATIVELY_SCHEDULED, _K.POSTED},
            _K.NATIVE_DRAFT_SAVED: {_K.NATIVELY_SCHEDULED, _K.POSTED},
            # Unscheduling in LinkedIn leaves a native draft. A new time T is an update, not
            # a transition.
            _K.NATIVELY_SCHEDULED: {_K.NATIVE_DRAFT_SAVED, _K.POSTED},
            _K.POSTED: set[KitState](),
        }
    ),
)

# --------------------------------------------------------------------------------------------
# Ideas (owner: signals), research runs (owner: research)
# --------------------------------------------------------------------------------------------


class IdeaStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    SNOOZED = "snoozed"
    USED = "used"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


_I = IdeaStatus
IDEA_MACHINE: Final = StateMachine(
    name="idea",
    states=tuple(IdeaStatus),
    initial=_I.NEW,
    edges=_edges(
        {
            _I.NEW: {_I.ACCEPTED, _I.SNOOZED, _I.USED, _I.DISMISSED, _I.EXPIRED},
            _I.SNOOZED: {_I.NEW, _I.DISMISSED, _I.EXPIRED},
            _I.ACCEPTED: {_I.USED, _I.DISMISSED, _I.NEW},
            _I.USED: {_I.ACCEPTED},
            _I.DISMISSED: {_I.NEW},
            _I.EXPIRED: {_I.NEW},
        }
    ),
)


class ResearchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


_R = ResearchStatus
RESEARCH_MACHINE: Final = StateMachine(
    name="research_run",
    states=tuple(ResearchStatus),
    initial=_R.QUEUED,
    edges=_edges(
        {
            _R.QUEUED: {_R.RUNNING, _R.CANCELLED},
            _R.RUNNING: {_R.SUCCEEDED, _R.FAILED, _R.QUARANTINED, _R.CANCELLED},
            _R.FAILED: {_R.QUEUED},
            _R.SUCCEEDED: set[ResearchStatus](),
            _R.QUARANTINED: set[ResearchStatus](),
            _R.CANCELLED: set[ResearchStatus](),
        }
    ),
)

# --------------------------------------------------------------------------------------------
# Owner material: stories, POVs (owner: stories), voice samples and guides (owner: voice)
# --------------------------------------------------------------------------------------------


class ReviewStatus(StrEnum):
    """Stories and POV entries: only ``approved`` entries (with a classifier pass) are used."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


_V = ReviewStatus
REVIEW_MACHINE: Final = StateMachine(
    name="review",
    states=tuple(ReviewStatus),
    initial=_V.PROPOSED,
    edges=_edges(
        {
            _V.PROPOSED: {_V.APPROVED, _V.REJECTED},
            _V.APPROVED: {_V.ARCHIVED},
            _V.REJECTED: {_V.PROPOSED},
            _V.ARCHIVED: {_V.APPROVED},
        }
    ),
)


class SampleStatus(StrEnum):
    ACTIVE = "active"
    EXCLUDED = "excluded"


SAMPLE_MACHINE: Final = StateMachine(
    name="voice_sample",
    states=tuple(SampleStatus),
    initial=SampleStatus.ACTIVE,
    edges=_edges(
        {
            SampleStatus.ACTIVE: {SampleStatus.EXCLUDED},
            SampleStatus.EXCLUDED: {SampleStatus.ACTIVE},
        }
    ),
)


class GuideStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


_G = GuideStatus
GUIDE_MACHINE: Final = StateMachine(
    name="style_guide",
    states=tuple(GuideStatus),
    initial=_G.PROPOSED,
    edges=_edges(
        {
            _G.PROPOSED: {_G.ACTIVE, _G.REJECTED},
            _G.ACTIVE: {_G.SUPERSEDED},
            _G.SUPERSEDED: {_G.ACTIVE},
            _G.REJECTED: set[GuideStatus](),
        }
    ),
)


class SuggestionStatus(StrEnum):
    """Voice digest suggestions and insight suggestions: the owner approves each one."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


SUGGESTION_MACHINE: Final = StateMachine(
    name="suggestion",
    states=tuple(SuggestionStatus),
    initial=SuggestionStatus.PROPOSED,
    edges=_edges(
        {
            SuggestionStatus.PROPOSED: {SuggestionStatus.ACCEPTED, SuggestionStatus.DISMISSED},
            SuggestionStatus.ACCEPTED: set[SuggestionStatus](),
            SuggestionStatus.DISMISSED: set[SuggestionStatus](),
        }
    ),
)


class InterviewStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    SKIPPED = "skipped"
    EXPIRED = "expired"


_N = InterviewStatus
INTERVIEW_MACHINE: Final = StateMachine(
    name="interview",
    states=tuple(InterviewStatus),
    initial=_N.PENDING,
    edges=_edges(
        {
            _N.PENDING: {_N.ANSWERED, _N.SKIPPED, _N.EXPIRED},
            _N.EXPIRED: {_N.ANSWERED, _N.SKIPPED},
            _N.ANSWERED: set[InterviewStatus](),
            _N.SKIPPED: set[InterviewStatus](),
        }
    ),
)

# --------------------------------------------------------------------------------------------
# Planner (week plans, reminders), engagement, analytics, sources, LinkedIn connection
# --------------------------------------------------------------------------------------------


class PlanStatus(StrEnum):
    GENERATING = "generating"
    PROPOSED = "proposed"
    APPROVED = "approved"
    CLOSED = "closed"


PLAN_MACHINE: Final = StateMachine(
    name="week_plan",
    states=tuple(PlanStatus),
    initial=PlanStatus.GENERATING,
    edges=_edges(
        {
            PlanStatus.GENERATING: {PlanStatus.PROPOSED},
            PlanStatus.PROPOSED: {PlanStatus.GENERATING, PlanStatus.APPROVED},
            PlanStatus.APPROVED: {PlanStatus.PROPOSED, PlanStatus.CLOSED},
            PlanStatus.CLOSED: set[PlanStatus](),
        }
    ),
)


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


REMINDER_MACHINE: Final = StateMachine(
    name="reminder",
    states=tuple(ReminderStatus),
    initial=ReminderStatus.PENDING,
    edges=_edges(
        {
            ReminderStatus.PENDING: {
                ReminderStatus.SENT,
                ReminderStatus.CANCELLED,
                ReminderStatus.EXPIRED,
            },
            ReminderStatus.SENT: set[ReminderStatus](),
            ReminderStatus.CANCELLED: set[ReminderStatus](),
            ReminderStatus.EXPIRED: set[ReminderStatus](),
        }
    ),
)


class EngagementStatus(StrEnum):
    OPEN = "open"
    FINALIZED = "finalized"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


ENGAGEMENT_MACHINE: Final = StateMachine(
    name="engagement_item",
    states=tuple(EngagementStatus),
    initial=EngagementStatus.OPEN,
    edges=_edges(
        {
            EngagementStatus.OPEN: {
                EngagementStatus.FINALIZED,
                EngagementStatus.DISMISSED,
                EngagementStatus.EXPIRED,
            },
            EngagementStatus.FINALIZED: set[EngagementStatus](),
            EngagementStatus.DISMISSED: set[EngagementStatus](),
            EngagementStatus.EXPIRED: set[EngagementStatus](),
        }
    ),
)


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    CONCLUDED = "concluded"
    ABANDONED = "abandoned"


EXPERIMENT_MACHINE: Final = StateMachine(
    name="experiment",
    states=tuple(ExperimentStatus),
    initial=ExperimentStatus.DRAFT,
    edges=_edges(
        {
            ExperimentStatus.DRAFT: {ExperimentStatus.RUNNING, ExperimentStatus.ABANDONED},
            ExperimentStatus.RUNNING: {ExperimentStatus.CONCLUDED, ExperimentStatus.ABANDONED},
            ExperimentStatus.CONCLUDED: set[ExperimentStatus](),
            ExperimentStatus.ABANDONED: set[ExperimentStatus](),
        }
    ),
)


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    FAILING = "failing"


SOURCE_MACHINE: Final = StateMachine(
    name="source",
    states=tuple(SourceStatus),
    initial=SourceStatus.ACTIVE,
    edges=_edges(
        {
            SourceStatus.ACTIVE: {SourceStatus.PAUSED, SourceStatus.FAILING},
            SourceStatus.FAILING: {SourceStatus.ACTIVE, SourceStatus.PAUSED},
            SourceStatus.PAUSED: {SourceStatus.ACTIVE},
        }
    ),
)


class ConnectionStatus(StrEnum):
    """The LinkedIn connection. Any vault decrypt failure means ``needs_reconnect``."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    NEEDS_RECONNECT = "needs_reconnect"


CONNECTION_MACHINE: Final = StateMachine(
    name="linkedin_connection",
    states=tuple(ConnectionStatus),
    initial=ConnectionStatus.DISCONNECTED,
    edges=_edges(
        {
            ConnectionStatus.DISCONNECTED: {ConnectionStatus.CONNECTED},
            ConnectionStatus.CONNECTED: {
                ConnectionStatus.NEEDS_RECONNECT,
                ConnectionStatus.DISCONNECTED,
            },
            ConnectionStatus.NEEDS_RECONNECT: {
                ConnectionStatus.CONNECTED,
                ConnectionStatus.DISCONNECTED,
            },
        }
    ),
)


class MachineTable(Protocol):
    """The read-only view of a machine that does not depend on its state type."""

    @property
    def name(self) -> str: ...

    @property
    def initial(self) -> str: ...

    def as_table(self) -> dict[str, list[str]]: ...


MACHINES: Final[Mapping[str, MachineTable]] = MappingProxyType(
    {
        machine.name: machine
        for machine in (
            ARTICLE_MACHINE,
            POST_MACHINE,
            KIT_MACHINE,
            IDEA_MACHINE,
            RESEARCH_MACHINE,
            REVIEW_MACHINE,
            SAMPLE_MACHINE,
            GUIDE_MACHINE,
            SUGGESTION_MACHINE,
            INTERVIEW_MACHINE,
            PLAN_MACHINE,
            REMINDER_MACHINE,
            ENGAGEMENT_MACHINE,
            EXPERIMENT_MACHINE,
            SOURCE_MACHINE,
            CONNECTION_MACHINE,
        )
    }
)
"""Every machine by name (``GET /api/meta/machines`` serves :meth:`StateMachine.as_table`)."""


# --------------------------------------------------------------------------------------------
# The owner's part: the edges the owner asks for, the states the owner edits content in
# --------------------------------------------------------------------------------------------

_O = ArticleStatus
_PS = PostStatus

OWNER_EDGES: Final[Mapping[str, Mapping[str, frozenset[str]]]] = MappingProxyType(
    {
        name: MappingProxyType({source: frozenset(targets) for source, targets in table.items()})
        for name, table in {
            # The transition route (articles.OWNER_TRANSITIONS), the pipeline's start, cancel and
            # revise, approve and publish-now from review, unpublish and the planner's veto.
            # The pipeline, the gates and the publish job make the others.
            ARTICLE_MACHINE.name: {
                _O.PLANNED: {_O.RESEARCHING, _O.INTERVIEWING, _O.DRAFTING, _O.ARCHIVED},
                _O.RESEARCHING: {_O.NEEDS_ATTENTION},
                _O.INTERVIEWING: {_O.NEEDS_ATTENTION},
                _O.DRAFTING: {_O.NEEDS_ATTENTION},
                _O.GATING: {_O.NEEDS_ATTENTION},
                _O.IN_REVIEW: {
                    _O.RESEARCHING,
                    _O.INTERVIEWING,
                    _O.DRAFTING,
                    _O.APPROVED,
                    _O.ARCHIVED,
                },
                _O.APPROVED: {_O.IN_REVIEW},
                _O.PUBLISH_FAILED: {_O.UNPUBLISHING, _O.IN_REVIEW},
                _O.PUBLISHED: {_O.IN_REVIEW, _O.UNPUBLISHING},
                _O.UNPUBLISHED: {_O.IN_REVIEW, _O.ARCHIVED},
                _O.NEEDS_ATTENTION: {
                    _O.RESEARCHING,
                    _O.INTERVIEWING,
                    _O.DRAFTING,
                    _O.IN_REVIEW,
                    _O.ARCHIVED,
                },
                _O.ARCHIVED: {_O.PLANNED},
            },
            # Edit, unapprove, regenerate, approve, archive, the go-live tap and mode switch,
            # the kit's "posted", the needs-attention check, re-slotting, deleting a live post.
            # Jobs write, stage, expire and miss posts; LinkedIn's answer settles a tap.
            POST_MACHINE.name: {
                _PS.GENERATING: {_PS.ARCHIVED},
                _PS.DRAFT: {_PS.GENERATING, _PS.APPROVED, _PS.ARCHIVED},
                _PS.APPROVED: {_PS.DRAFT, _PS.PUBLISHING, _PS.KIT, _PS.ARCHIVED},
                _PS.STAGED: {_PS.DRAFT, _PS.APPROVED, _PS.PUBLISHING, _PS.KIT},
                _PS.NEEDS_ATTENTION: {_PS.POSTED, _PS.APPROVED, _PS.DRAFT, _PS.ARCHIVED},
                _PS.KIT: {_PS.POSTED, _PS.APPROVED, _PS.ARCHIVED},
                _PS.MISSED: {_PS.APPROVED, _PS.ARCHIVED},
                _PS.POSTED: {_PS.DELETED},
                _PS.EXPIRED: {_PS.DRAFT, _PS.ARCHIVED},
            },
            KIT_MACHINE.name: {
                KitState.PREPARED: {
                    KitState.NATIVE_DRAFT_SAVED,
                    KitState.NATIVELY_SCHEDULED,
                    KitState.POSTED,
                },
                KitState.NATIVE_DRAFT_SAVED: {KitState.NATIVELY_SCHEDULED, KitState.POSTED},
                KitState.NATIVELY_SCHEDULED: {KitState.NATIVE_DRAFT_SAVED, KitState.POSTED},
            },
            # Accept, snooze, dismiss, bring back. "used" follows an article or post made
            # from the idea; "expired" is the system's.
            IDEA_MACHINE.name: {
                IdeaStatus.NEW: {IdeaStatus.ACCEPTED, IdeaStatus.SNOOZED, IdeaStatus.DISMISSED},
                IdeaStatus.SNOOZED: {IdeaStatus.NEW, IdeaStatus.DISMISSED},
                IdeaStatus.ACCEPTED: {IdeaStatus.DISMISSED, IdeaStatus.NEW},
                IdeaStatus.DISMISSED: {IdeaStatus.NEW},
                IdeaStatus.EXPIRED: {IdeaStatus.NEW},
            },
            RESEARCH_MACHINE.name: {
                ResearchStatus.QUEUED: {ResearchStatus.CANCELLED},
                ResearchStatus.RUNNING: {ResearchStatus.CANCELLED},
            },
            REVIEW_MACHINE.name: {
                ReviewStatus.PROPOSED: {ReviewStatus.APPROVED, ReviewStatus.REJECTED},
                ReviewStatus.APPROVED: {ReviewStatus.ARCHIVED},
                ReviewStatus.REJECTED: {ReviewStatus.PROPOSED},
                ReviewStatus.ARCHIVED: {ReviewStatus.APPROVED},
            },
            SAMPLE_MACHINE.name: {
                SampleStatus.ACTIVE: {SampleStatus.EXCLUDED},
                SampleStatus.EXCLUDED: {SampleStatus.ACTIVE},
            },
            # Activating a version supersedes the active one (the system's edge).
            GUIDE_MACHINE.name: {
                GuideStatus.PROPOSED: {GuideStatus.ACTIVE, GuideStatus.REJECTED},
                GuideStatus.SUPERSEDED: {GuideStatus.ACTIVE},
            },
            SUGGESTION_MACHINE.name: {
                SuggestionStatus.PROPOSED: {SuggestionStatus.ACCEPTED, SuggestionStatus.DISMISSED},
            },
            INTERVIEW_MACHINE.name: {
                InterviewStatus.PENDING: {InterviewStatus.ANSWERED, InterviewStatus.SKIPPED},
                InterviewStatus.EXPIRED: {InterviewStatus.ANSWERED, InterviewStatus.SKIPPED},
            },
            # Approve, and edits (an edit of an approved plan that changes it asks again).
            # The plan job generates and proposes; the week's end closes it.
            PLAN_MACHINE.name: {
                PlanStatus.GENERATING: {PlanStatus.PROPOSED},
                PlanStatus.PROPOSED: {PlanStatus.APPROVED},
                PlanStatus.APPROVED: {PlanStatus.PROPOSED},
            },
            REMINDER_MACHINE.name: {ReminderStatus.PENDING: {ReminderStatus.CANCELLED}},
            ENGAGEMENT_MACHINE.name: {
                EngagementStatus.OPEN: {EngagementStatus.FINALIZED, EngagementStatus.DISMISSED},
            },
            EXPERIMENT_MACHINE.name: {
                ExperimentStatus.DRAFT: {ExperimentStatus.RUNNING, ExperimentStatus.ABANDONED},
                ExperimentStatus.RUNNING: {
                    ExperimentStatus.CONCLUDED,
                    ExperimentStatus.ABANDONED,
                },
            },
            # Pause and resume; a crawl marks a source failing (and active again).
            SOURCE_MACHINE.name: {
                SourceStatus.ACTIVE: {SourceStatus.PAUSED},
                SourceStatus.FAILING: {SourceStatus.ACTIVE, SourceStatus.PAUSED},
                SourceStatus.PAUSED: {SourceStatus.ACTIVE},
            },
            # Connect (the OAuth round trip) and disconnect; a failing token needs reconnecting.
            CONNECTION_MACHINE.name: {
                ConnectionStatus.DISCONNECTED: {ConnectionStatus.CONNECTED},
                ConnectionStatus.CONNECTED: {ConnectionStatus.DISCONNECTED},
                ConnectionStatus.NEEDS_RECONNECT: {
                    ConnectionStatus.CONNECTED,
                    ConnectionStatus.DISCONNECTED,
                },
            },
        }.items()
    }
)
"""Per machine, the edges the owner asks for with a request (``{state: targets}``, a subset of
the machine's edges): the routes of the owner's actions take them. Every other edge is
system-only: jobs, schedules, the answer of a publish attempt, or a side effect of another
document's change. ``GET /api/meta/machines`` serves it, so the web enables an action only
for an edge the owner can take (the route stays the judge)."""

OWNER_WRITES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        ARTICLE_MACHINE.name: frozenset(
            state for state, writer in ARTICLE_WRITER.items() if writer == "owner"
        ),
        # linkedin.posts.routes.EDITABLE (needs_attention only before any publish attempt).
        POST_MACHINE.name: frozenset(
            {_PS.DRAFT, _PS.APPROVED, _PS.STAGED, _PS.EXPIRED, _PS.NEEDS_ATTENTION}
        ),
        KIT_MACHINE.name: frozenset[str](),
        IDEA_MACHINE.name: frozenset(IdeaStatus),
        RESEARCH_MACHINE.name: frozenset[str](),
        REVIEW_MACHINE.name: frozenset(ReviewStatus),
        SAMPLE_MACHINE.name: frozenset(SampleStatus),
        # An edited guide is saved as a new version (``proposed``), never in place.
        GUIDE_MACHINE.name: frozenset[str](),
        SUGGESTION_MACHINE.name: frozenset[str](),
        # The answers, before the interview is answered or skipped.
        INTERVIEW_MACHINE.name: frozenset({InterviewStatus.PENDING, InterviewStatus.EXPIRED}),
        PLAN_MACHINE.name: frozenset(
            {PlanStatus.GENERATING, PlanStatus.PROPOSED, PlanStatus.APPROVED}
        ),
        REMINDER_MACHINE.name: frozenset[str](),
        ENGAGEMENT_MACHINE.name: frozenset[str](),
        # A running experiment changes only the posts in its arms.
        EXPERIMENT_MACHINE.name: frozenset({ExperimentStatus.DRAFT, ExperimentStatus.RUNNING}),
        SOURCE_MACHINE.name: frozenset(SourceStatus),
        CONNECTION_MACHINE.name: frozenset[str](),
    }
)
"""Per machine, the states in which the owner may edit the document's content (the who-writes
rule; for articles :data:`ARTICLE_WRITER`'s ``owner`` states). A route may still refuse an edit
for another reason (a post with a publish attempt to resolve, a plan being generated)."""
