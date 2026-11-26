"""Claim documents: lease + fencing + compare-and-swap writes (plan: "Reliability model").

Jobs and schedule items are documents a replica *claims*. A claim document carries:

- ``owner``: replica name + boot UUID (:func:`new_owner`), ``None`` when unclaimed.
- ``leaseUntil``: when the claim lapses unless renewed (60 s, renewed every 15 s).
- ``fence``: incremented on every acquire, never reset.
- ``writeId``: a token naming the last claim write (see "unknown outcomes" below).

Rules (all writes are ``replace`` with ``If-Match``; a 412 is never ignored):

- **Acquire** a document that has no owner, or whose lease ended more than ``skew`` ago on the
  acquirer's clock. A 412 means another replica won the race.
- **Renew** and **commit** (step progress, outcomes) are CAS writes with the claim's last etag.
  After a 412 the holder re-reads: when ``owner`` and ``fence`` are still its own (the API wrote,
  for example a cancel request) it re-applies its change on the fresh copy; otherwise the claim
  is lost (:class:`ClaimLost`) and the holder must abandon the work without writing again.
  The fence check is what stops a stale holder that re-reads from overwriting a new owner.
- **Release** is a commit that clears ``owner`` and ``leaseUntil``.
- **Unknown outcomes** (a timeout, or a 5xx after the SDK's retries): the write may or may not
  have landed. Each logical write carries one ``writeId`` token; the holder re-reads and adopts
  the stored copy when its token is there. A write whose outcome stays unknown (the re-read
  failed too) is *in doubt*: the claim settles it, with the same token, before it makes any other
  write, so the token check is never defeated by a later write of the same holder (a renewal
  landing on top of it). Retrying with the same token never applies a change twice.
- **Self-fencing:** the holder trusts its lease only until ``leaseUntil - skew``
  (:attr:`Claim.deadline`). A holder that cannot renew by then must stop its work: another
  replica may take over at ``leaseUntil + skew``.

Claims work on containers partitioned by ``/id`` (``jobs``); documents are plain JSON bodies,
so the typed layers (:mod:`blogwriter.jobs.runner`, :mod:`blogwriter.jobs.scheduler`) pass
mutations as functions of the body.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from blogwriter.db import Doc, NotFound, PreconditionFailed, Store, StoreUnavailable
from blogwriter.jobs.clock import Clock, parse_iso, to_iso
from blogwriter.jobs.settings import JobsSettings

__all__ = [
    "CLAIM_FIELDS",
    "MAX_CAS_ROUNDS",
    "Body",
    "Claim",
    "ClaimLost",
    "LeasePolicy",
    "Mutation",
    "acquire",
    "bounded",
    "cas_update",
    "new_owner",
    "new_write_token",
]

type Body = dict[str, Any]
type Mutation = Callable[[Body], Body]

CLAIM_FIELDS: Final = frozenset({"owner", "leaseUntil", "fence", "writeId"})
MAX_CAS_ROUNDS: Final = 8
"""Re-read-and-retry rounds of one logical write before it gives up (StoreUnavailable)."""
_LANDED_TOKENS: Final = 32
"""Tokens of recent writes a claim remembers as applied (a retry with one is a no-op)."""


class ClaimLost(Exception):
    """The claim is gone (taken over, released or deleted). Abandon the work; write nothing."""


def new_owner(replica: str) -> str:
    """A claim owner for this process: ``<replica>:<boot UUID>``."""
    return f"{replica}:{uuid.uuid4().hex}"


def new_write_token() -> str:
    """A fresh ``writeId`` token for one logical write."""
    return uuid.uuid4().hex


async def bounded[T](awaitable: Awaitable[T], seconds: float) -> T:
    """Await ``awaitable`` for at most ``seconds`` (``TimeoutError`` after that)."""
    async with asyncio.timeout(seconds):
        return await awaitable


def _fence_of(body: Mapping[str, Any]) -> int | None:
    value: object = body.get("fence", 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True, slots=True)
class LeasePolicy:
    """Lease timing shared by every claimant."""

    lease: timedelta
    renew_every: timedelta
    skew: timedelta
    store_timeout: float
    """Seconds one store call may take."""

    @classmethod
    def from_settings(cls, settings: JobsSettings) -> LeasePolicy:
        return cls(
            lease=timedelta(seconds=settings.lease_seconds),
            renew_every=timedelta(seconds=settings.renew_seconds),
            skew=timedelta(seconds=settings.skew_seconds),
            store_timeout=settings.store_timeout_seconds,
        )

    def is_claimable(self, body: Mapping[str, Any], now: datetime) -> bool:
        """No owner, or a lease that ended more than ``skew`` before ``now``."""
        if body.get("owner") is None:
            return True
        lease_until: object = body.get("leaseUntil")
        if not isinstance(lease_until, str):
            return True  # an owner without a lease can never renew: recover the document
        try:
            until = parse_iso(lease_until)
        except ValueError:
            return True
        return until + self.skew < now

    def takeover_cutoff(self, now: datetime) -> str:
        """Leases ending before this timestamp may be taken over (query ``leaseUntil < it``)."""
        return to_iso(now - self.skew)


@dataclass(frozen=True, slots=True)
class _Write:
    """One logical claim write: the change, whether it releases the claim, and its token."""

    mutate: Mutation | None
    release: bool
    token: str


class Claim:
    """A held claim on one document. Writes are serialized and fenced; see the module docs."""

    def __init__(
        self,
        store: Store,
        container: str,
        doc: Doc,
        *,
        owner: str,
        fence: int,
        policy: LeasePolicy,
        clock: Clock,
    ) -> None:
        self._store = store
        self._container = container
        self._id = doc.id
        self._owner = owner
        self._fence = fence
        self._policy = policy
        self._clock = clock
        self._body: Body = doc.body
        self._etag = doc.etag
        self._lease_until = self._read_lease(doc.body)
        self._lock = asyncio.Lock()
        self._lost = False
        self._released = False
        self._in_doubt: _Write | None = None
        self._landed: deque[str] = deque(maxlen=_LANDED_TOKENS)

    @property
    def id(self) -> str:
        return self._id

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def fence(self) -> int:
        return self._fence

    @property
    def etag(self) -> str:
        return self._etag

    @property
    def body(self) -> Body:
        """A copy of the document as last written or read."""
        return copy.deepcopy(self._body)

    @property
    def lease_until(self) -> datetime:
        return self._lease_until

    @property
    def deadline(self) -> datetime:
        """The holder must have renewed by this time or stop working (``leaseUntil - skew``)."""
        return self._lease_until - self._policy.skew

    @property
    def held(self) -> bool:
        """Neither lost nor released."""
        return not self._lost and not self._released

    @property
    def lost(self) -> bool:
        return self._lost

    def mark_lost(self) -> None:
        """Self-fence: give the claim up locally (no further writes are attempted)."""
        self._lost = True

    async def renew(self, *, token: str | None = None) -> Body:
        """Extend the lease (``leaseUntil = now + lease``)."""
        return await self.commit(None, token=token)

    async def release(self, mutate: Mutation | None = None, *, token: str | None = None) -> Body:
        """Apply ``mutate`` and clear ``owner``/``leaseUntil`` in one CAS write."""
        return await self.commit(mutate, release=True, token=token)

    async def commit(
        self,
        mutate: Mutation | None = None,
        *,
        release: bool = False,
        token: str | None = None,
    ) -> Body:
        """CAS-write ``mutate(body)``, re-checking owner and fence; returns the stored body.

        ``mutate`` receives a deep copy and may be called more than once (once per round, and
        again later if the write's outcome stays unknown); it must not change ``id`` or the
        claim fields, which the claim sets itself. Pass the same ``token`` when retrying a
        logical write after :class:`StoreUnavailable` or ``TimeoutError`` so it can never be
        applied twice.

        A write whose outcome stays unknown is settled (same mutation, same token) before any
        later write of this claim, whoever makes it; its retry then returns the stored body.

        Raises :class:`ClaimLost` when the document now belongs to someone else (or was
        released or deleted); :class:`StoreUnavailable` or ``TimeoutError`` when the store did
        not answer (the claim is still held; retry or let the lease lapse).
        """
        write = _Write(mutate=mutate, release=release, token=token or new_write_token())
        async with self._lock:
            if self._lost:
                raise ClaimLost(f"{self._container}/{self._id}: claim lost")
            pending = self._in_doubt
            if pending is not None:
                # Settle the earlier write first: once another write of ours lands on top of
                # it, its token is gone from the document and a retry could apply it twice.
                await self._write(pending)
            if write.token in self._landed:
                return copy.deepcopy(self._body)  # this logical write already landed
            if self._released:
                raise ClaimLost(f"{self._container}/{self._id}: claim released")
            return await self._write(write)

    async def _write(self, write: _Write) -> Body:
        """Run one logical write to completion: CAS rounds, each followed by a re-read."""
        base, etag = self._body, self._etag
        for _ in range(MAX_CAS_ROUNDS):
            candidate = self._prepare(base, write.mutate, release=write.release, token=write.token)
            try:
                stored = await bounded(
                    self._store.replace(self._container, candidate, if_match=etag),
                    self._policy.store_timeout,
                )
            except PreconditionFailed:
                pass  # someone wrote first; the re-read decides
            except NotFound:
                self._lost = True
                raise ClaimLost(f"{self._container}/{self._id}: document deleted") from None
            except (StoreUnavailable, TimeoutError):
                self._in_doubt = write  # unknown outcome until a re-read settles it
            else:
                return self._landed_as(write, stored)
            current = await self._reread()
            if current.body.get("writeId") == write.token:
                return self._landed_as(write, current)
            if not self._is_ours(current.body):
                self._lost = True
                raise ClaimLost(f"{self._container}/{self._id}: taken over")
            base, etag = current.body, current.etag
            self._body, self._etag = current.body, current.etag
        raise StoreUnavailable(
            f"{self._container}/{self._id}: gave up after {MAX_CAS_ROUNDS} conflicting writes"
        )

    def _landed_as(self, write: _Write, doc: Doc) -> Body:
        self._in_doubt = None
        self._landed.append(write.token)
        self._adopt(doc, release=write.release)
        return copy.deepcopy(doc.body)

    def _prepare(self, base: Body, mutate: Mutation | None, *, release: bool, token: str) -> Body:
        body = copy.deepcopy(base)
        if mutate is not None:
            body = mutate(body)
        if body.get("id") != self._id:
            raise ValueError("a claim mutation may not change the document id")
        body["owner"] = None if release else self._owner
        body["fence"] = self._fence
        body["leaseUntil"] = None if release else to_iso(self._clock.now() + self._policy.lease)
        body["writeId"] = token
        return body

    async def _reread(self) -> Doc:
        try:
            return await bounded(
                self._store.read(self._container, self._id), self._policy.store_timeout
            )
        except NotFound:
            self._lost = True
            raise ClaimLost(f"{self._container}/{self._id}: document deleted") from None

    def _is_ours(self, body: Mapping[str, Any]) -> bool:
        return body.get("owner") == self._owner and _fence_of(body) == self._fence

    def _adopt(self, doc: Doc, *, release: bool) -> None:
        self._body, self._etag = doc.body, doc.etag
        if release:
            self._released = True
        else:
            self._lease_until = self._read_lease(doc.body)

    def _read_lease(self, body: Mapping[str, Any]) -> datetime:
        value: object = body.get("leaseUntil")
        if isinstance(value, str):
            try:
                return parse_iso(value)
            except ValueError:
                pass
        return self._clock.now()  # unknown: treat the lease as ending now


async def acquire(
    store: Store,
    container: str,
    doc: Doc,
    *,
    owner: str,
    policy: LeasePolicy,
    clock: Clock,
    mutate: Mutation | None = None,
) -> Claim | None:
    """Claim ``doc`` (as found) with a CAS write; ``None`` if it is not claimable or was taken.

    ``mutate`` changes the body in the same write (for a job: status ``running``, attempt + 1,
    a status event). Raises :class:`StoreUnavailable` or ``TimeoutError`` only when the outcome
    is unknown and the follow-up read failed too; if that write landed, the orphaned claim
    lapses after one lease.
    """
    now = clock.now()
    if not policy.is_claimable(doc.body, now):
        return None
    previous = _fence_of(doc.body)
    if previous is None:
        return None  # a corrupt fence cannot be advanced safely
    token = new_write_token()
    body = copy.deepcopy(doc.body)
    if mutate is not None:
        body = mutate(body)
    if body.get("id") != doc.id:
        raise ValueError("a claim mutation may not change the document id")
    fence = previous + 1
    body.update(owner=owner, fence=fence, leaseUntil=to_iso(now + policy.lease), writeId=token)
    try:
        stored = await bounded(
            store.replace(container, body, if_match=doc.etag), policy.store_timeout
        )
    except (PreconditionFailed, NotFound):
        return None
    except (StoreUnavailable, TimeoutError):
        try:
            current = await bounded(store.read(container, doc.id), policy.store_timeout)
        except NotFound:
            return None
        if current.body.get("writeId") != token:
            return None
        stored = current
    return Claim(store, container, stored, owner=owner, fence=fence, policy=policy, clock=clock)


async def cas_update(
    store: Store,
    container: str,
    item_id: str,
    change: Callable[[Body], Body | None],
    *,
    store_timeout: float,
    current: Doc | None = None,
) -> Doc | None:
    """Read, ``change``, replace with ``If-Match``; on 412 read again and retry.

    For transitions that do not hold a claim (API actions, settling an abandoned job).
    ``change`` gets a copy of the body and returns the new body, or ``None`` to leave the
    document alone (then ``None`` is returned). It may raise to abort. ``current`` skips the
    first read. :class:`NotFound` propagates.
    """
    doc = current
    for _ in range(MAX_CAS_ROUNDS):
        if doc is None:
            doc = await bounded(store.read(container, item_id), store_timeout)
        updated = change(copy.deepcopy(doc.body))
        if updated is None:
            return None
        try:
            return await bounded(
                store.replace(container, updated, if_match=doc.etag), store_timeout
            )
        except PreconditionFailed:
            doc = None
    raise StoreUnavailable(
        f"{container}/{item_id}: gave up after {MAX_CAS_ROUNDS} conflicting writes"
    )
