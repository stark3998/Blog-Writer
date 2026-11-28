"""The v1 -> v2 migration (plan "Migration (Phase 9)"; runbook ``docs/v2/build/migration.md``).

Run it as ``python -m blogwriter.backups.migrate_v1`` (dry run by default, ``--apply`` writes,
``--only <container>`` filters). It reads v1's ``blog-writer`` database from the separate v1
Cosmos account strictly read-only, exports every container but the session and state ones to
Blob ``backups/v1-export/<UTC date>/<container>.jsonl``, converts published posts, drafts,
feeds, crawled articles, keyword lists and voice profiles into v2 documents, validates them and
writes them idempotently by id.

Not imported by the app: the package pulls in the publish renderer and Pillow.
"""

from __future__ import annotations

from blogwriter.backups.migrate_v1.convert import (
    MIGRATED_FROM,
    Conversion,
    ConvertContext,
    PostOverride,
    convert_crawled_article,
    convert_draft,
    convert_feed_source,
    convert_keywords,
    convert_published,
    convert_voice_profile,
    draft_status,
)
from blogwriter.backups.migrate_v1.run import (
    CONVERTED_CONTAINERS,
    MigrationError,
    MigrationOptions,
    MigrationReport,
    export_blob_name,
    run_migration,
)
from blogwriter.backups.migrate_v1.seeds import KbSeed, VoiceSeed
from blogwriter.backups.migrate_v1.settings import MigrateV1Settings
from blogwriter.backups.migrate_v1.source import (
    EXCLUDED_CONTAINERS,
    CosmosV1Source,
    MemoryV1Source,
    V1Source,
    V1SourceError,
    is_excluded_container,
)

__all__ = [
    "CONVERTED_CONTAINERS",
    "EXCLUDED_CONTAINERS",
    "MIGRATED_FROM",
    "Conversion",
    "ConvertContext",
    "CosmosV1Source",
    "KbSeed",
    "MemoryV1Source",
    "MigrateV1Settings",
    "MigrationError",
    "MigrationOptions",
    "MigrationReport",
    "PostOverride",
    "V1Source",
    "V1SourceError",
    "VoiceSeed",
    "convert_crawled_article",
    "convert_draft",
    "convert_feed_source",
    "convert_keywords",
    "convert_published",
    "convert_voice_profile",
    "draft_status",
    "export_blob_name",
    "is_excluded_container",
    "run_migration",
]
