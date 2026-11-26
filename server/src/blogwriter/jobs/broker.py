"""In-process pub/sub for job events and token deltas (one broker per process).

Two kinds of messages flow from the runner to SSE streams:

- :class:`JobEventMessage`: a durable job event, published only after the write that stored it
  succeeded. Its id ``<jobId>:<seq>`` matches the event on the job document.
- :class:`DeltaMessage`: a token delta (streamed model text). Never stored, so it has no id.

Every subscriber has one bounded, ordered queue (``queue_size`` items):

- **Token deltas are dropped oldest-first.** When the queue is full, the oldest queued delta is
  dropped and the next delta delivered for that job and step carries ``gap=True`` (the client
  then knows its live text is incomplete and reloads the step output when it lands).
- **Job events are never dropped.** If the queue is full of job events (no delta left to
  drop), the subscription is closed with reason ``overflow`` instead of losing one. The SSE
  stream then ends, and the client's reconnect (``Last-Event-ID``) gets a snapshot.

Publishing never blocks and never waits on a subscriber, so a slow client cannot stall a job.
The broker is single-event-loop: use it from the loop that runs the app.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Final, Self

from pydantic import JsonValue

from blogwriter.jobs.models import Job, JobEvent

__all__ = [
    "MAX_DELTA_CHARS",
    "Broker",
    "BrokerFull",
    "DeltaMessage",
    "JobEventMessage",
    "Message",
    "Subscription",
]

MAX_DELTA_CHARS: Final = 8192


@dataclass(frozen=True, slots=True)
class JobEventMessage:
    """A durable job event (already stored on the job document)."""

    job_id: str
    seq: int
    at: str
    type: str
    step: str | None
    status: str
    """The job's status after the write that stored this event."""
    data: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    @classmethod
    def from_event(cls, job: Job, event: JobEvent) -> JobEventMessage:
        return cls(
            job_id=job.id,
            seq=event.seq,
            at=event.at,
            type=event.type,
            step=event.step,
            status=job.status,
            data=dict(event.data),
        )

    @property
    def event_id(self) -> str:
        """The SSE event id: ``<jobId>:<seq>``."""
        return f"{self.job_id}:{self.seq}"

    def payload(self) -> dict[str, JsonValue]:
        return {
            "jobId": self.job_id,
            "seq": self.seq,
            "at": self.at,
            "type": self.type,
            "step": self.step,
            "status": self.status,
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class DeltaMessage:
    """A token delta for one job step (not stored; may be dropped under pressure)."""

    job_id: str
    step: str
    text: str
    gap: bool = False
    """Some earlier delta of this job step was dropped."""

    def payload(self) -> dict[str, JsonValue]:
        return {"jobId": self.job_id, "step": self.step, "text": self.text, "gap": self.gap}


type Message = JobEventMessage | DeltaMessage


class BrokerFull(Exception):
    """Too many subscribers."""


class Subscription:
    """One subscriber's queue. Use as an async context manager; it unsubscribes on exit."""

    def __init__(self, broker: Broker, job_ids: frozenset[str] | None, capacity: int) -> None:
        self._broker = broker
        self._job_ids = job_ids
        self._capacity = capacity
        self._items: deque[Message] = deque()
        self._ready = asyncio.Event()
        self._gaps: set[tuple[str, str]] = set()
        self._closed = False
        self._close_reason: str | None = None
        self.dropped_deltas = 0

    @property
    def job_ids(self) -> frozenset[str] | None:
        """The jobs this subscription follows; ``None`` means every job."""
        return self._job_ids

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_reason(self) -> str | None:
        """``overflow``, ``shutdown`` or ``closed`` once closed."""
        return self._close_reason

    def __len__(self) -> int:
        return len(self._items)

    def wants(self, job_id: str) -> bool:
        return self._job_ids is None or job_id in self._job_ids

    async def get(self, max_wait: float | None = None) -> Message | None:
        """The next message; ``None`` after ``max_wait`` seconds or once the subscription closed."""
        if not self._items and not self._closed:
            self._ready.clear()
            try:
                async with asyncio.timeout(max_wait):
                    await self._ready.wait()
            except TimeoutError:
                return None
        if self._closed or not self._items:
            return None
        item = self._items.popleft()
        if isinstance(item, DeltaMessage):
            key = (item.job_id, item.step)
            if key in self._gaps:
                self._gaps.discard(key)
                return DeltaMessage(job_id=item.job_id, step=item.step, text=item.text, gap=True)
        return item

    def close(self, reason: str = "closed") -> None:
        """Close the subscription (idempotent); pending and later messages are discarded."""
        if self._closed:
            return
        self._closed = True
        self._close_reason = reason
        self._items.clear()
        self._ready.set()
        self._broker.discard(self)

    def offer(self, message: Message) -> None:
        """Queue ``message`` under the drop policy (called by the broker)."""
        if self._closed:
            return
        if len(self._items) >= self._capacity and not self._drop_oldest_delta():
            if isinstance(message, DeltaMessage):
                # The queue holds only job events: this delta is the oldest one left to drop.
                self._gaps.add((message.job_id, message.step))
                self.dropped_deltas += 1
                return
            self.close("overflow")
            return
        self._items.append(message)
        self._ready.set()

    def _drop_oldest_delta(self) -> bool:
        for index, item in enumerate(self._items):
            if isinstance(item, DeltaMessage):
                del self._items[index]
                self._gaps.add((item.job_id, item.step))
                self.dropped_deltas += 1
                return True
        return False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class Broker:
    """Fan-out of job events and token deltas to subscribers (see the module docs)."""

    def __init__(self, *, queue_size: int = 256, max_subscribers: int = 16) -> None:
        if queue_size < 1 or max_subscribers < 1:
            raise ValueError("queue_size and max_subscribers must be positive")
        self._queue_size = queue_size
        self._max_subscribers = max_subscribers
        self._subscribers: set[Subscription] = set()
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self, job_ids: Iterable[str] | None = None) -> Subscription:
        """A new subscription to ``job_ids`` (``None``: every job). :class:`BrokerFull` if full.

        Subscribing to a closed broker returns a closed subscription.
        """
        selected = None if job_ids is None else frozenset(job_ids)
        subscription = Subscription(self, selected, self._queue_size)
        if self._closed:
            subscription.close("shutdown")
            return subscription
        if len(self._subscribers) >= self._max_subscribers:
            raise BrokerFull(f"more than {self._max_subscribers} subscribers")
        self._subscribers.add(subscription)
        return subscription

    def discard(self, subscription: Subscription) -> None:
        """Forget ``subscription`` (it calls this itself when closed)."""
        self._subscribers.discard(subscription)

    def publish(self, message: Message) -> None:
        """Deliver ``message`` to every interested subscriber without waiting."""
        if self._closed:
            return
        for subscription in tuple(self._subscribers):
            if subscription.wants(message.job_id):
                subscription.offer(message)

    def publish_job_events(self, job: Job, events: Iterable[JobEvent]) -> None:
        """Publish stored events of ``job`` in ``seq`` order."""
        for event in sorted(events, key=lambda item: item.seq):
            self.publish(JobEventMessage.from_event(job, event))

    def publish_delta(self, job_id: str, step: str, text: str) -> None:
        """Publish a token delta (at most :data:`MAX_DELTA_CHARS` characters)."""
        if len(text) > MAX_DELTA_CHARS:
            raise ValueError(f"a delta may carry at most {MAX_DELTA_CHARS} characters")
        if text:
            self.publish(DeltaMessage(job_id=job_id, step=step, text=text))

    def close(self) -> None:
        """Close every subscription (reason ``shutdown``); later publishes are dropped."""
        self._closed = True
        for subscription in tuple(self._subscribers):
            subscription.close("shutdown")
        self._subscribers.clear()
