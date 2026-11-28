"""The v1 migration run: export v1 to JSONL, convert, validate, write (with ``apply``), report.

1. **Export.** Every v1 container except the session and state ones
   (:func:`~blogwriter.backups.migrate_v1.source.is_excluded_container`) as JSON Lines in Blob
   ``backups/v1-export/<UTC date>/<container>.jsonl`` (items sorted by id, Cosmos system
   properties dropped except ``_ts``).
2. **Convert** (:mod:`.convert`) ``feed-sources``, ``crawled-articles``, ``keywords``,
   ``voice-profiles``, ``drafts`` and ``published-blogs``; every v2 document is validated
   (domain models; ``validate_published_doc`` for published posts) before anything is written.
3. **Write** (only with ``apply``): blobs first (content-addressed, never overwritten), then the
   documents, idempotently by id: a missing document is created, an identical one is left
   alone, one the migration wrote earlier is replaced (If-Match), and one changed in v2 since
   is skipped (``changed_in_v2``, ``v2_stub``, ``slug_owned_by_v2``).

Without ``apply`` nothing is written anywhere (dry run): the report says what would happen.
The report holds container names, counts, document references (ids, slugs or hashes) and error
codes. Never document content.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from blogwriter.backups.migrate_v1.convert import (
    MIGRATED_FROM,
    Conversion,
    ConvertContext,
    PendingBlob,
    PendingDoc,
    PostOverride,
    convert_crawled_article,
    convert_draft,
    convert_feed_source,
    convert_keywords,
    convert_published,
    convert_voice_profile,
)
from blogwriter.backups.migrate_v1.images import CardStyle, V1Image, decode_v1_image
from blogwriter.backups.migrate_v1.markdown import referenced_v1_images
from blogwriter.backups.migrate_v1.source import (
    V1Source,
    is_excluded_container,
    strip_system_properties,
)
from blogwriter.backups.migrate_v1.text import item_str
from blogwriter.db import Conflict, Filter, NotFound, PreconditionFailed, Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_singleton
from blogwriter.domain.ports import BlobExists, BlobStore
from blogwriter.domain.settings import BrandKit, FormatPacks, PillarSettings

__all__ = [
    "CONVERTED_CONTAINERS",
    "EXPORT_CONTENT_TYPE",
    "ContainerReport",
    "Failure",
    "MigrationError",
    "MigrationOptions",
    "MigrationReport",
    "export_blob_name",
    "run_migration",
]

CONVERTED_CONTAINERS: Final = (
    "feed-sources",
    "crawled-articles",
    "keywords",
    "voice-profiles",
    "drafts",
    "published-blogs",
)
"""Converted in this order (signals need the feed ids, drafts the published slugs)."""
EXPORT_CONTENT_TYPE: Final = "application/x-ndjson"
_IMAGE_CACHE_SIZE: Final = 64


class MigrationError(ValueError):
    """A run cannot start (bad options or settings). The message holds no content."""


@dataclass(frozen=True, slots=True)
class MigrationOptions:
    apply: bool = False
    only: frozenset[str] = frozenset()
    """v1 container names to export and convert (empty: all)."""
    pillar: str | None = None
    """Pillar for posts no keyword matches (default: the owner's first pillar)."""
    format: str | None = None
    """Format for every post (default: ``github`` sources -> deep-explainer, else news)."""
    overrides: Mapping[str, PostOverride] = field(default_factory=dict[str, PostOverride])


@dataclass
class ContainerReport:
    name: str
    read: int = 0
    exported_bytes: int = 0
    export_sha256: str | None = None
    converted: int = 0
    written: int = 0
    unchanged: int = 0
    failed: int = 0
    blobs_written: int = 0
    skipped: Counter[str] = field(default_factory=Counter[str])
    notes: Counter[str] = field(default_factory=Counter[str])


@dataclass(frozen=True, slots=True)
class Failure:
    container: str
    ref: str
    code: str
    path: str


@dataclass
class MigrationReport:
    apply: bool
    date: str
    containers: dict[str, ContainerReport] = field(default_factory=dict[str, ContainerReport])
    excluded: list[str] = field(default_factory=list[str])
    refused: list[str] = field(default_factory=list[str])
    """``--only`` names that are session/state containers or do not exist in v1."""
    failures: list[Failure] = field(default_factory=list[Failure])

    @property
    def ok(self) -> bool:
        return not self.failures and not self.refused

    def container(self, name: str) -> ContainerReport:
        return self.containers.setdefault(name, ContainerReport(name))

    def lines(self) -> list[str]:
        """The printable report: names, counts, references and codes only."""
        mode = "apply" if self.apply else "dry run (nothing written)"
        verb = "written" if self.apply else "would write"
        out = [f"v1 migration, {mode}: export to backups/{export_blob_name(self.date, '<c>')}"]
        if self.excluded:
            out.append("never read (sessions and states): " + ", ".join(sorted(self.excluded)))
        if self.refused:
            out.append("refused --only: " + ", ".join(sorted(self.refused)))
        for name in sorted(self.containers):
            report = self.containers[name]
            parts = [f"read {report.read}", f"export {report.exported_bytes} bytes"]
            if name in CONVERTED_CONTAINERS:
                parts += [
                    f"valid {report.converted}",
                    f"failed {report.failed}",
                    f"{verb} {report.written}",
                    f"unchanged {report.unchanged}",
                    f"blobs {report.blobs_written}",
                ]
            if report.skipped:
                parts.append("skipped " + _counts(report.skipped))
            if report.notes:
                parts.append("notes " + _counts(report.notes))
            out.append(f"{name}: " + ", ".join(parts))
        out.extend(
            f"FAIL {failure.container} {failure.ref}: {failure.code}"
            + (f" at {failure.path}" if failure.path else "")
            for failure in self.failures
        )
        out.append("result: ok" if self.ok else f"result: {len(self.failures)} failure(s)")
        return out


def _counts(counter: Counter[str]) -> str:
    return "{" + ", ".join(f"{key}: {value}" for key, value in sorted(counter.items())) + "}"


def export_blob_name(date: str, container: str) -> str:
    """``v1-export/<date>/<container>.jsonl`` in the ``backups`` container."""
    return f"v1-export/{date}/{container}.jsonl"


def _jsonl(items: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        + b"\n"
        for item in items
    )


# --------------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------------


async def _context(
    store: Store,
    options: MigrationOptions,
    *,
    now: datetime,
    published_slugs: frozenset[str],
    feed_ids: frozenset[str],
) -> ConvertContext:
    stamp = to_iso(now)
    pillar_settings, _ = await read_singleton(store, PillarSettings, now=stamp)
    format_packs, _ = await read_singleton(store, FormatPacks, now=stamp)
    brand, _ = await read_singleton(store, BrandKit, now=stamp)
    pillars = tuple(pillar.id for pillar in pillar_settings.pillars)
    formats = tuple(pack.id for pack in format_packs.packs)
    if options.pillar is not None and options.pillar not in pillars:
        raise MigrationError("--pillar is not one of the pillar ids in v2 settings")
    if options.format is not None and options.format not in formats:
        raise MigrationError("--format is not one of the format-pack ids in v2 settings")
    for override in options.overrides.values():
        if override.pillar is not None and override.pillar not in pillars:
            raise MigrationError("an override names a pillar that is not in v2 settings")
        if override.format is not None and override.format not in formats:
            raise MigrationError("an override names a format that is not in v2 settings")
    colors = brand.colors
    return ConvertContext(
        now=now,
        pillars=pillars,
        formats=formats,
        default_pillar=options.pillar or pillars[0],
        format_override=options.format,
        overrides=dict(options.overrides),
        card=CardStyle(
            background=colors.ink,
            text=colors.paper,
            accent=colors.signal,
            muted=colors.muted_on_ink,
            byline=brand.site_name or brand.author_name,
        ),
        published_slugs=published_slugs,
        feed_ids=feed_ids,
    )


# --------------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------------


async def _owner_problem(
    store: Store, pending: PendingDoc, existing: Mapping[str, Any]
) -> str | None:
    """``None`` when the migration may replace ``existing``; else why it must not."""
    if pending.container == "published-blogs":
        if "schemaVersion" not in existing:
            return None  # a legacy v1 copy: replacing it is the point
        if existing.get("status") != "published":
            return "v2_stub"
        owners = await store.find(
            "articles",
            [Filter("kind", "==", "article"), Filter("publish.slug", "==", pending.doc_id)],
            limit=1,
        )
        return "slug_owned_by_v2" if owners else None
    marker: object = existing.get(MIGRATED_FROM)
    if isinstance(marker, dict) and marker.get("updatedAt") == existing.get("updatedAt"):  # pyright: ignore[reportUnknownMemberType]
        return None
    return "changed_in_v2"


async def _write_doc(store: Store, pending: PendingDoc, *, apply: bool) -> str:
    """``written``, ``unchanged`` or ``skipped:<reason>`` (with ``apply`` false: what would be)."""
    try:
        existing = await store.read(
            pending.container, pending.doc_id, partition_key=pending.partition_key
        )
    except NotFound:
        if apply:
            try:
                await store.create(pending.container, pending.body)
            except Conflict:
                return "skipped:conflict"
        return "written"
    if existing.body == pending.body:
        return "unchanged"
    problem = await _owner_problem(store, pending, existing.body)
    if problem is not None:
        return f"skipped:{problem}"
    if apply:
        try:
            await store.replace(pending.container, pending.body, if_match=existing.etag)
        except (PreconditionFailed, NotFound):
            return "skipped:conflict"
    return "written"


async def _write_blob(blobs: BlobStore, pending: PendingBlob) -> bool:
    """Whether the blob was new (content-addressed: an existing name holds the same bytes)."""
    try:
        await blobs.put(
            pending.container, pending.name, pending.data, content_type=pending.content_type
        )
    except BlobExists:
        return False
    return True


async def _record(
    report: MigrationReport,
    conversion: Conversion,
    store: Store,
    blobs: BlobStore | None,
    *,
    apply: bool,
) -> None:
    section = report.container(conversion.container)
    section.notes.update(conversion.notes)
    if conversion.skip is not None:
        section.skipped[conversion.skip] += 1
        return
    if not conversion.ok:
        section.failed += 1
        report.failures.extend(
            Failure(conversion.container, conversion.ref, problem.code, problem.path)
            for problem in conversion.problems
        )
        return
    section.converted += 1
    if apply:
        if blobs is None:  # pragma: no cover - run_migration refuses apply without blobs
            raise MigrationError("apply needs Blob storage")
        for pending_blob in conversion.blobs:
            section.blobs_written += int(await _write_blob(blobs, pending_blob))
    else:
        section.blobs_written += len(conversion.blobs)
    outcomes = [await _write_doc(store, doc, apply=apply) for doc in conversion.docs]
    skipped = [outcome.split(":", 1)[1] for outcome in outcomes if outcome.startswith("skipped:")]
    if skipped:
        section.skipped[skipped[0]] += 1
    elif all(outcome == "unchanged" for outcome in outcomes):
        section.unchanged += 1
    else:
        section.written += 1


# --------------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------------


class _Images:
    """v1 images read by id on demand, with a small LRU (drafts and posts share images)."""

    def __init__(self, source: V1Source) -> None:
        self._source = source
        self._cache: OrderedDict[str, V1Image | None] = OrderedDict()

    async def get(self, image_ids: Sequence[str]) -> dict[str, V1Image | None]:
        found: dict[str, V1Image | None] = {}
        for image_id in image_ids:
            if image_id in self._cache:
                self._cache.move_to_end(image_id)
                found[image_id] = self._cache[image_id]
                continue
            item = await self._source.read_item("images", image_id)
            image = await asyncio.to_thread(decode_v1_image, item) if item is not None else None
            self._cache[image_id] = image
            if len(self._cache) > _IMAGE_CACHE_SIZE:
                self._cache.popitem(last=False)
            found[image_id] = image
        return found


async def _read_all(source: V1Source, container: str) -> list[dict[str, Any]]:
    items = [strip_system_properties(item) async for item in source.iter_items(container)]
    items.sort(key=lambda item: str(item.get("id", "")))
    return items


async def _values(
    source: V1Source,
    kept: Mapping[str, Sequence[Mapping[str, Any]]],
    available: Sequence[str],
    container: str,
    key: str,
) -> frozenset[str]:
    """One field of every item of a container: from what was read already, else read now."""
    if container in kept:
        items: Sequence[Mapping[str, Any]] = kept[container]
    elif container in available:
        items = await _read_all(source, container)
    else:
        return frozenset()
    return frozenset(value for item in items if (value := item_str(item, key)) is not None)


type _Convert = Callable[[Mapping[str, Any], ConvertContext], Conversion]

_SIMPLE: Final[Mapping[str, _Convert]] = {
    "feed-sources": convert_feed_source,
    "crawled-articles": convert_crawled_article,
    "keywords": convert_keywords,
    "voice-profiles": convert_voice_profile,
}


async def run_migration(
    source: V1Source,
    store: Store,
    blobs: BlobStore | None,
    *,
    options: MigrationOptions,
    now: datetime | None = None,
) -> MigrationReport:
    """Export, convert and (with ``options.apply``) write. See the module docs."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if options.apply and blobs is None:
        raise MigrationError("--apply needs Blob storage (BW_STORAGE_BLOB_ENDPOINT)")
    report = MigrationReport(apply=options.apply, date=moment.date().isoformat())
    available = await source.list_containers()
    report.excluded = [name for name in available if is_excluded_container(name)]
    report.refused = sorted(
        name for name in options.only if is_excluded_container(name) or name not in available
    )
    if report.refused:
        return report
    selected = [
        name
        for name in available
        if not is_excluded_container(name) and (not options.only or name in options.only)
    ]

    kept: dict[str, list[dict[str, Any]]] = {}
    for name in selected:
        items = await _read_all(source, name)
        data = await asyncio.to_thread(_jsonl, items)
        section = report.container(name)
        section.read = len(items)
        section.exported_bytes = len(data)
        section.export_sha256 = hashlib.sha256(data).hexdigest()
        if options.apply and blobs is not None:
            await blobs.put(
                "backups",
                export_blob_name(report.date, name),
                data,
                content_type=EXPORT_CONTENT_TYPE,
                overwrite=True,
            )
        if name in CONVERTED_CONTAINERS:
            kept[name] = items
        del data

    to_convert = [name for name in CONVERTED_CONTAINERS if name in kept]
    if not to_convert:
        return report
    published_slugs = (
        await _values(source, kept, available, "published-blogs", "slug")
        if "drafts" in kept
        else frozenset[str]()
    )
    feed_ids = (
        await _values(source, kept, available, "feed-sources", "id")
        if "crawled-articles" in kept
        else frozenset[str]()
    )
    ctx = await _context(
        store, options, now=moment, published_slugs=published_slugs, feed_ids=feed_ids
    )
    images = _Images(source)
    for name in to_convert:
        for item in kept.pop(name):
            if name in _SIMPLE:
                conversion = await asyncio.to_thread(_SIMPLE[name], item, ctx)
            else:
                field_name = "content" if name == "drafts" else "mdxContent"
                markdown: object = item.get(field_name)
                wanted = (
                    await asyncio.to_thread(referenced_v1_images, markdown)
                    if isinstance(markdown, str)
                    else []
                )
                found = await images.get(wanted)
                convert = convert_draft if name == "drafts" else convert_published
                conversion = await asyncio.to_thread(convert, item, found, ctx)
            await _record(report, conversion, store, blobs, apply=options.apply)
    return report
