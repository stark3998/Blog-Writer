"""Shared fixtures for the storage tests (no Azure, no network)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from blogwriter.domain.ports import uninstall_services

GOLDENS = Path(__file__).parent / "goldens"


@pytest.fixture
def golden_jpeg() -> bytes:
    """A JPEG carrying EXIF (orientation, make, GPS), XMP, an ICC profile and a comment."""
    return (GOLDENS / "exif-gps.jpg").read_bytes()


@pytest.fixture(autouse=True)
def no_installed_services() -> Iterator[None]:
    uninstall_services()
    yield
    uninstall_services()
