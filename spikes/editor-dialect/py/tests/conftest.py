"""Shared paths and the golden-file helper.

Golden files are plain files next to the tests so reviews see real HTML.
Regenerate with ``UPDATE_SNAPSHOTS=1 uv run pytest`` and review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parents[2]
CORPUS_DIR = SPIKE_DIR / "corpus"
NORMALIZED_DIR = CORPUS_DIR / "normalized"
XSS_DIR = SPIKE_DIR / "xss"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
UPDATE_SNAPSHOTS = os.environ.get("UPDATE_SNAPSHOTS") == "1"

MANIFEST = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
CORPUS_FILES = sorted(MANIFEST["files"])


def assert_golden(path: Path, actual: str) -> None:
    if UPDATE_SNAPSHOTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.exists(), f"missing golden {path}; run UPDATE_SNAPSHOTS=1 uv run pytest"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"{path.name} differs from its golden (UPDATE_SNAPSHOTS=1 to accept)"
