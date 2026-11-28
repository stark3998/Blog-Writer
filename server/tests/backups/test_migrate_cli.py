"""``python -m blogwriter.backups.migrate_v1`` with injected ends (no Azure)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from blogwriter.backups.migrate_v1.__main__ import Factories, run_cli
from blogwriter.backups.migrate_v1.settings import MigrateV1Settings
from blogwriter.backups.migrate_v1.source import MemoryV1Source, V1Source
from blogwriter.db import MemoryStore, Store
from blogwriter.domain.ports import BlobStore
from blogwriter.domain.testing import MemoryBlobStore

type V1Data = dict[str, list[dict[str, Any]]]

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
ENDPOINT = "https://cosmos-v1.documents.azure.com:443/"
KEY_CANARY = "v1-account-key-CANARY-0d9e=="


class Ends:
    """In-memory v1 source, v2 store and Blob storage; records what the CLI opened."""

    def __init__(self, data: V1Data) -> None:
        self.data = data
        self.store = MemoryStore()
        self.blobs = MemoryBlobStore()
        self.settings: MigrateV1Settings | None = None
        self.applied: list[bool] = []

    async def source(self, settings: MigrateV1Settings) -> V1Source:
        self.settings = settings
        return MemoryV1Source(self.data)

    async def open_store(self, apply: bool) -> tuple[Store, str | None]:
        self.applied.append(apply)
        return self.store, None

    async def open_blobs(self, apply: bool) -> BlobStore | None:
        return self.blobs if apply else None

    def factories(self) -> Factories:
        return Factories(source=self.source, store=self.open_store, blobs=self.open_blobs)


@pytest.fixture(autouse=True)
def v1_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_MIGRATE_V1_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("BW_MIGRATE_V1_KEY", KEY_CANARY)
    monkeypatch.delenv("BW_MIGRATE_V1_DATABASE", raising=False)


async def run(ends: Ends, *argv: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = await run_cli(list(argv), factories=ends.factories(), out=lines.append, now=NOW)
    return code, lines


async def test_a_dry_run_prints_counts_and_writes_nothing(
    v1_data: V1Data, canaries: tuple[str, str]
) -> None:
    ends = Ends(v1_data)
    code, lines = await run(ends)
    assert code == 0
    assert ends.applied == [False]
    assert ends.blobs.blobs == {}
    assert await ends.store.find("published-blogs") == []
    assert lines[0].startswith("v1 migration, dry run")
    assert any(line.startswith("published-blogs: read 2") for line in lines)
    assert lines[-1] == "result: ok"
    output = "\n".join(lines)
    assert KEY_CANARY not in output
    assert canaries[0] not in output
    assert canaries[1] not in output
    assert ends.settings is not None
    assert ends.settings.key is not None  # the key reached the source, not the output


async def test_apply_writes(v1_data: V1Data) -> None:
    ends = Ends(v1_data)
    code, lines = await run(ends, "--apply")
    assert code == 0, lines
    assert ends.applied == [True]
    assert len(await ends.store.find("published-blogs")) == 2
    assert any(name.startswith("v1-export/2026-11-26/") for (_, name) in ends.blobs.blobs)


async def test_only_and_options(v1_data: V1Data, tmp_path: Path) -> None:
    overrides = tmp_path / "overrides.json"
    overrides.write_text(
        json.dumps({"okta-to-entra-lessons": {"pillar": "governance", "format": "framework"}})
    )
    ends = Ends(v1_data)
    code, lines = await run(
        ends,
        "--apply",
        "--only",
        "published-blogs",
        "--pillar",
        "identity",
        "--overrides",
        str(overrides),
    )
    assert code == 0, lines
    doc = (await ends.store.read("published-blogs", "okta-to-entra-lessons")).body
    assert (doc["pillar"], doc["format"]) == ("governance", "framework")
    assert await ends.store.find("articles") == []


async def test_validation_failures_exit_1(v1_data: V1Data) -> None:
    v1_data["published-blogs"].append({"id": "x", "slug": "x", "mdxContent": ""})
    code, lines = await run(Ends(v1_data))
    assert code == 1
    assert "FAIL published-blogs x: v1_markdown_missing at /mdxContent" in lines


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--only", "linkedin-sessions"], "refused --only: linkedin-sessions"),
        (["--pillar", "nope"], "error: --pillar is not one of the pillar ids in v2 settings"),
    ],
)
async def test_usage_errors_exit_2(v1_data: V1Data, argv: list[str], message: str) -> None:
    code, lines = await run(Ends(v1_data), *argv)
    assert code == 2
    assert message in lines


async def test_a_bad_overrides_file_exits_2(v1_data: V1Data, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"slug": {"pillar": 3, "secret": "x"}}')
    code, lines = await run(Ends(v1_data), "--overrides", str(bad))
    assert code == 2
    assert lines == ["error: the --overrides file is not a valid JSON object of overrides"]


async def test_without_an_endpoint_it_exits_2(
    v1_data: V1Data, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BW_MIGRATE_V1_ENDPOINT")
    code, lines = await run(Ends(v1_data))
    assert code == 2
    assert lines == ["error: set BW_MIGRATE_V1_ENDPOINT to the v1 Cosmos account"]


async def test_an_invalid_endpoint_is_not_echoed(
    v1_data: V1Data, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BW_MIGRATE_V1_ENDPOINT", f"http://v1.example/?key={KEY_CANARY}")
    code, lines = await run(Ends(v1_data))
    assert code == 2
    assert KEY_CANARY not in "\n".join(lines)


async def test_the_real_store_factory_refuses_apply_without_cosmos(
    v1_data: V1Data, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BW_COSMOS_ENDPOINT", raising=False)
    ends = Ends(v1_data)
    lines: list[str] = []
    code = await run_cli(
        ["--apply"],
        factories=Factories(source=ends.source, blobs=ends.open_blobs),
        out=lines.append,
        now=NOW,
    )
    assert code == 2
    assert lines == ["error: --apply needs the v2 store: set BW_COSMOS_ENDPOINT"]


async def test_the_real_store_factory_dry_runs_against_an_empty_store(
    v1_data: V1Data, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BW_COSMOS_ENDPOINT", raising=False)
    ends = Ends(v1_data)
    lines: list[str] = []
    code = await run_cli([], factories=Factories(source=ends.source), out=lines.append, now=NOW)
    assert code == 0
    assert lines[0].startswith("note: BW_COSMOS_ENDPOINT is not set")
