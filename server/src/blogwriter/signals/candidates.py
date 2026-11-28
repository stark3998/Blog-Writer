"""Triage candidates and the signals that deduplicate them.

A **candidate** is a feed item or a scouting citation waiting for triage: its link, title,
publisher and date (never a summary or content), plus where it came from. Candidates are what
the crawl and scout jobs checkpoint between their two steps.

A **signal** is the durable "seen" marker (``signals`` container, partition ``/sourceId``, 90-day
TTL): ``Signal.id_for(partition, url_sha256)``, hashes only. A new item gets a signal before it
is triaged; triage marks it ``triaged`` (with the idea id when one was filed). An item whose
signal exists but was never triaged (the job failed, was paused between its steps, or the
triage batch was refused) is queued again the next time a crawl sees it, so nothing is lost and
nothing is triaged twice once it is decided.

Per-pillar scouting has no ``source`` document: its signals use the partition
``scout-<pillarId>`` (:func:`scout_partition`), which can never collide with a source id
(``feed-...``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from pydantic import JsonValue

from blogwriter.db import Conflict, Filter, NotFound, Store
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.confidentiality import blocklist_hit
from blogwriter.domain.docstore import create_doc, find_docs, update_doc
from blogwriter.domain.hashing import url_sha256
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.signals import Signal
from blogwriter.signals.feeds import FeedItem

__all__ = [
    "SCOUT_PARTITION_PREFIX",
    "Candidate",
    "SignalRecord",
    "candidates_from_json",
    "candidates_to_json",
    "record_signals",
    "scout_partition",
]

SCOUT_PARTITION_PREFIX: Final = "scout-"


def scout_partition(pillar_id: str) -> str:
    """The signal partition of per-pillar scouting (no source document behind it)."""
    return f"{SCOUT_PARTITION_PREFIX}{pillar_id}"


@dataclass(frozen=True, slots=True)
class Candidate:
    """An item waiting for triage. ``partition`` is its signal's partition key (a source id or
    :func:`scout_partition`); ``source_id`` the ``source`` document, if any; ``pillar`` a hint
    (the source's or the scouted pillar)."""

    url: str
    title: str
    publisher: str | None
    published_at: str | None
    partition: str
    source_id: str | None = None
    pillar: str | None = None

    @property
    def url_sha256(self) -> str:
        return url_sha256(self.url)

    @property
    def signal_id(self) -> str:
        return Signal.id_for(self.partition, self.url_sha256)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "url": self.url,
            "title": self.title,
            "publisher": self.publisher,
            "publishedAt": self.published_at,
            "partition": self.partition,
            "sourceId": self.source_id,
            "pillar": self.pillar,
        }

    @classmethod
    def from_json(cls, value: object) -> Candidate:
        if not isinstance(value, Mapping):
            raise ValueError("a candidate is a JSON object")
        body = cast(Mapping[str, object], value)

        def text(key: str, *, required: bool = False) -> str | None:
            item = body.get(key)
            if item is None and not required:
                return None
            if not isinstance(item, str) or not item:
                raise ValueError(f"candidate {key} must be a non-empty string")
            return item

        return cls(
            url=cast(str, text("url", required=True)),
            title=cast(str, text("title", required=True)),
            publisher=text("publisher"),
            published_at=text("publishedAt"),
            partition=cast(str, text("partition", required=True)),
            source_id=text("sourceId"),
            pillar=text("pillar"),
        )


def candidates_to_json(candidates: Iterable[Candidate]) -> list[JsonValue]:
    return [candidate.to_json() for candidate in candidates]


def candidates_from_json(value: object) -> list[Candidate]:
    """Candidates from a step output or checkpoint (``ValueError`` when malformed)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("candidates must be a JSON list")
    return [Candidate.from_json(item) for item in cast(list[object], value)]


@dataclass(frozen=True, slots=True)
class SignalRecord:
    """What :func:`record_signals` did for one source: the candidates to triage, how many items
    were new, how many untriaged ones were queued again and how many the blocklist kept out."""

    candidates: tuple[Candidate, ...]
    new: int
    requeued: int
    blocked: int = 0


def _blocked(item: FeedItem, pillar: str | None, blocklist: Blocklist | None) -> bool:
    if blocklist is None:
        return False
    texts = [item.title, item.url, item.publisher or "", pillar or ""]
    return blocklist_hit([text for text in texts if text], blocklist)


async def _decide_blocked(store: Store, signal_id: str, partition: str, stamp: str) -> None:
    def mutate(signal: Signal) -> Signal | None:
        if signal.triaged:
            return None
        return signal.model_copy(update={"triaged": True, "updated_at": stamp})

    try:
        await update_doc(store, Signal, signal_id, mutate, partition_key=partition)
    except NotFound:
        return


async def record_signals(
    store: Store,
    items: Sequence[FeedItem],
    *,
    partition: str,
    now: datetime,
    source_id: str | None = None,
    pillar: str | None = None,
    blocklist: Blocklist | None = None,
) -> SignalRecord:
    """Create a signal per item not seen in ``partition`` before and return what to triage.

    One query reads the partition's existing signals for these URLs. A ``Conflict`` on create
    means a concurrent crawl created it first: that crawl triages it. Signals are written before
    the caller checkpoints the candidates, so a crash in between re-finds them as untriaged.

    With ``blocklist``, an item whose title, URL, publisher or pillar hint matches it is decided
    at once (its signal is ``triaged`` without an idea): it is never checkpointed, stored or
    sent to a model.
    """
    stamp = to_iso(now)
    by_hash: dict[str, FeedItem] = {}
    for item in items:
        try:
            digest = url_sha256(item.url)
        except ValueError:
            continue
        by_hash.setdefault(digest, item)
    if not by_hash:
        return SignalRecord(candidates=(), new=0, requeued=0)
    existing = {
        stored.doc.url_sha256: stored.doc
        for stored in await find_docs(
            store,
            Signal,
            [Filter("urlSha256", "in", list(by_hash))],
            partition_key=partition,
        )
    }
    out: list[Candidate] = []
    new = 0
    requeued = 0
    blocked = 0
    for digest, item in by_hash.items():
        hit = _blocked(item, pillar, blocklist)
        seen = existing.get(digest)
        if seen is not None:
            if seen.triaged:
                continue
            if hit:
                await _decide_blocked(store, seen.id, partition, stamp)
                blocked += 1
                continue
            requeued += 1
        else:
            signal = Signal(
                id=Signal.id_for(partition, digest),
                source_id=partition,
                url_sha256=digest,
                title_sha256=sha256_hex(item.title),
                item_published_at=item.published_at,
                seen_at=stamp,
                triaged=hit,
                created_at=stamp,
                updated_at=stamp,
            )
            try:
                await create_doc(store, signal)
            except Conflict:
                continue
            new += 1
            if hit:
                blocked += 1
                continue
        out.append(
            Candidate(
                url=item.url,
                title=item.title,
                publisher=item.publisher,
                published_at=item.published_at,
                partition=partition,
                source_id=source_id,
                pillar=pillar,
            )
        )
    return SignalRecord(candidates=tuple(out), new=new, requeued=requeued, blocked=blocked)
