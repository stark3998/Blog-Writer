"""``python -m blogwriter.backups.migrate_v1``: migrate v1's data into v2 (dry run by default).

Usage::

    python -m blogwriter.backups.migrate_v1                     # dry run: counts and failures
    python -m blogwriter.backups.migrate_v1 --only published-blogs
    python -m blogwriter.backups.migrate_v1 --apply             # export, convert and write

Environment: ``BW_MIGRATE_V1_ENDPOINT`` (+ ``BW_MIGRATE_V1_KEY`` or a ``DefaultAzureCredential``
sign-in) for v1, read-only; ``BW_COSMOS_*`` and ``BW_STORAGE_*`` for v2. The runbook is
``docs/v2/build/migration.md``.

Exit codes: 0 everything valid, 1 some documents failed validation (see ``FAIL`` lines),
2 configuration or usage error (including ``--only`` naming a session or unknown container).
Output holds names, counts, references and codes, never document content or the key.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from blogwriter.backups.migrate_v1.convert import PostOverride
from blogwriter.backups.migrate_v1.run import MigrationError, MigrationOptions, run_migration
from blogwriter.backups.migrate_v1.settings import MigrateV1Settings
from blogwriter.backups.migrate_v1.source import CosmosV1Source, V1Source, V1SourceError
from blogwriter.db import CosmosSettings, CosmosStore, MemoryStore, Store, StoreError
from blogwriter.domain.ports import BlobStore

__all__ = ["Factories", "build_parser", "main", "run_cli"]

_OVERRIDES: TypeAdapter[dict[str, PostOverride]] = TypeAdapter(dict[str, PostOverride])
_QUIET: tuple[str, ...] = ("azure", "azure.core.pipeline.policies.http_logging_policy")


class UsageError(Exception):
    """A configuration or usage problem (exit code 2). The message holds no secrets."""


async def _open_source(settings: MigrateV1Settings) -> V1Source:
    return await CosmosV1Source.open(settings)


async def _open_store(apply: bool) -> tuple[Store, str | None]:
    if CosmosSettings().endpoint is None:
        if apply:
            raise UsageError("--apply needs the v2 store: set BW_COSMOS_ENDPOINT")
        return MemoryStore(), "BW_COSMOS_ENDPOINT is not set: comparing with an empty v2 store"
    store = await CosmosStore.from_settings()
    return store, None


async def _open_blobs(apply: bool) -> BlobStore | None:
    if not apply:
        return None
    # The CLI is a composition root (like blogwriter.main): it may wire the storage package.
    from blogwriter.storage import AzureBlobStore, StorageError, storage_settings

    if not storage_settings().configured:
        raise UsageError("--apply needs Blob storage: set BW_STORAGE_BLOB_ENDPOINT")
    try:
        return await AzureBlobStore.open()
    except StorageError as exc:
        raise UsageError(f"Blob storage is not usable: {exc}") from None


@dataclass(frozen=True, slots=True)
class Factories:
    """How the CLI opens its three ends (tests inject in-memory ones)."""

    source: Callable[[MigrateV1Settings], Awaitable[V1Source]] = _open_source
    store: Callable[[bool], Awaitable[tuple[Store, str | None]]] = _open_store
    blobs: Callable[[bool], Awaitable[BlobStore | None]] = _open_blobs


async def _close(resource: object) -> None:
    """Close a resource that may have an async ``close`` (the Blob store does; the port not)."""
    close: object = getattr(resource, "close", None)
    if callable(close):
        result: object = close()
        if inspect.isawaitable(result):
            await result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m blogwriter.backups.migrate_v1",
        description="Export v1 (read-only) to Blob and convert it into v2. Dry run by default.",
    )
    parser.add_argument("--apply", action="store_true", help="export and write (default: dry run)")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="CONTAINER",
        help="v1 container to export and convert (repeatable; default: all)",
    )
    parser.add_argument("--pillar", help="pillar id for posts no keyword matches")
    parser.add_argument("--format", help="format-pack id for every migrated post")
    parser.add_argument(
        "--overrides",
        type=Path,
        help='JSON file {"<slug or v1 draft id>": {"pillar": "...", "format": "..."}}',
    )
    return parser


def _overrides(path: Path | None) -> dict[str, PostOverride]:
    if path is None:
        return {}
    try:
        return _OVERRIDES.validate_python(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        raise UsageError("the --overrides file is not a valid JSON object of overrides") from None


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    factories: Factories | None = None,
    out: Callable[[str], None] = print,
    now: datetime | None = None,
) -> int:
    """Parse ``argv``, run the migration and print the report. Returns the exit code."""
    args = build_parser().parse_args(argv)
    opened = factories or Factories()
    source: V1Source | None = None
    store: Store | None = None
    blobs: BlobStore | None = None
    try:
        try:
            settings = MigrateV1Settings()
        except ValidationError:
            raise UsageError("a BW_MIGRATE_V1_* setting is invalid") from None
        if settings.endpoint is None:
            raise UsageError("set BW_MIGRATE_V1_ENDPOINT to the v1 Cosmos account")
        options = MigrationOptions(
            apply=bool(args.apply),
            only=frozenset(args.only),
            pillar=args.pillar,
            format=args.format,
            overrides=_overrides(args.overrides),
        )
        source = await opened.source(settings)
        store, note = await opened.store(options.apply)
        blobs = await opened.blobs(options.apply)
        if note is not None:
            out(f"note: {note}")
        report = await run_migration(source, store, blobs, options=options, now=now)
    except (UsageError, MigrationError, V1SourceError) as exc:
        out(f"error: {exc}")
        return 2
    except StoreError as exc:
        out(f"error: the v2 store failed ({type(exc).__name__})")
        return 2
    finally:
        if source is not None:
            await source.close()
        if store is not None:
            await store.close()
        await _close(blobs)
    for line in report.lines():
        out(line)
    if report.refused:
        return 2
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    return asyncio.run(run_cli(argv))


if __name__ == "__main__":
    sys.exit(main())
